"""API local do Mini App: autentica pelo initData do Telegram (PRO-72) e expõe
as rotas de leitura /me, /resumo e /historico (PRO-76; /resumo sem orçamento
por categoria desde a PRO-83; com o total de receitas da fatura desde a
PRO-86), /gastos/recentes (PRO-79, com o
estabelecimento do gasto desde a PRO-81), /gastos/{id} (PRO-80, detalhe de um
gasto, com as parcelas pagas) e /movimentacoes/recentes (PRO-92, gastos e
receitas numa lista só, com o campo "tipo" em cada item). Os números e a
consulta vêm todos do calculos.py; as rotas só buscam os dados e montam o JSON.

Rotas de registro (PRO-88): POST /registro/interpretar (prévia com a IA, sem
gravar), POST /gastos e POST /receitas (gravação confirmada, uma vez só por
chave). As regras ficam no registro.py; aqui só o HTTP (status, JSON, limite).

Rota de cadastro (PRO-96): POST /cadastro cria o cadastro de quem ainda não tem
(renda 0, sem alerta diário, categorias sem repetição) e recusa quem já tem
(409). As regras ficam no cadastro.py, o mesmo módulo que o bot usa.

Também serve, na mesma origem, a home do Mini App em /app (mini-apps/home, PRO-77)
e a página de diagnóstico das rotas em /teste (mini-apps/teste-initdata).

Não importa o bot.py. Configuração e conexão com o banco são resolvidas
sob demanda (dependências), então o módulo importa sem .env e sem rede.
"""
import logging
import math
import os
import threading
import time
from collections import deque
from datetime import datetime
from functools import lru_cache
from typing import Any, NamedTuple, Optional

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import cadastro
import registro
from api.initdata import InitDataError, validate_init_data
from calculos import (
    FUSO_BRASILIA,
    buscar_gasto,
    buscar_gastos_recentes,
    buscar_movimentacoes_recentes,
    calcular_parcelas_pagas,
    calcular_resumo_fatura,
    calcular_serie_gastos_mensais,
    obter_ciclo_atual,
)

logger = logging.getLogger("api")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LIMITE_GASTOS_RECENTES = 10
LIMITE_MOVIMENTACOES_RECENTES = 10  # total, somando gastos e receitas (PRO-92)


class Config(NamedTuple):
    token: str
    turso_url: str
    turso_auth_token: str


@lru_cache(maxsize=1)
def obter_config() -> Config:
    """Lê o .env da raiz do repo (uma vez). Faltando variável, o erro cita só o nome.

    A chave do Gemini (GEMINI_API_KEY) também é carregada do .env para o
    ambiente, mas NÃO é exigida aqui (PRO-88): sem ela, só a rota
    /registro/interpretar responde 503 ia_indisponivel; as leituras e as
    gravações continuam funcionando. Ela é lida só pelo google.genai, no
    servidor (registro.obter_cliente_ia), e nunca vai para resposta nem log."""
    load_dotenv(os.path.join(RAIZ, ".env"))
    valores = {}
    for nome in ("TOKEN", "TURSO_DATABASE_URL", "TURSO_AUTH_TOKEN"):
        valor = os.getenv(nome)
        if not valor:
            raise RuntimeError(f"Variavel de ambiente ausente: {nome}")
        valores[nome] = valor
    return Config(valores["TOKEN"], valores["TURSO_DATABASE_URL"], valores["TURSO_AUTH_TOKEN"])


def obter_conexao_db(config: Config = Depends(obter_config)):
    """Uma conexão por requisição, sempre fechada no final."""
    import libsql
    conn = libsql.connect(database=config.turso_url, auth_token=config.turso_auth_token)
    try:
        yield conn
    finally:
        conn.close()


def obter_agora() -> datetime:
    """Agora no fuso de Brasília; os testes substituem por uma data fixa."""
    return datetime.now(FUSO_BRASILIA)


def usuario_autenticado(authorization: str | None = Header(default=None),
                        config: Config = Depends(obter_config)) -> dict:
    """Valida `Authorization: tma <initData>` e devolve o user do Telegram."""
    if not authorization:
        logger.warning("Autenticacao negada: cabecalho ausente")
        raise HTTPException(status_code=401, detail="nao_autorizado")
    esquema, _, init_data = authorization.partition(" ")
    if esquema != "tma" or not init_data:
        logger.warning("Autenticacao negada: esquema invalido")
        raise HTTPException(status_code=401, detail="nao_autorizado")
    try:
        return validate_init_data(init_data, config.token)
    except InitDataError as erro:
        logger.warning("Autenticacao negada: %s", erro)
        raise HTTPException(status_code=401, detail="nao_autorizado")


app = FastAPI(title="Finance API")


@app.get("/saude")
def saude():
    return {"status": "ok"}


@app.get("/me")
def me(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db)):
    user_id = usuario["id"]
    linha = conn.execute("SELECT nome FROM usuarios WHERE user_id = ?", (user_id,)).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="usuario_nao_cadastrado")
    return {"user_id": user_id, "nome": linha[0]}


def em_reais(valor: float) -> float:
    """Valor em reais para a resposta: float arredondado a 2 casas (único
    arredondamento do fluxo; os cálculos do calculos.py não arredondam)."""
    return round(float(valor), 2)


def carregar_cadastro(conn, user_id: int) -> tuple:
    """(nome, renda_mensal, dia_fechamento) do usuário. 404 se não há cadastro;
    409 se falta o dia de fechamento (sem ele não há fatura nem números)."""
    linha = conn.execute(
        "SELECT nome, renda_mensal, dia_fechamento FROM usuarios WHERE user_id = ?", (user_id,)
    ).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="usuario_nao_cadastrado")
    if not linha[2]:
        raise HTTPException(status_code=409, detail="cadastro_incompleto")
    return linha


@app.get("/resumo")
def resumo(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
           agora: datetime = Depends(obter_agora)):
    user_id = usuario["id"]
    _, _, dia_fechamento = carregar_cadastro(conn, user_id)
    ano_mes = obter_ciclo_atual(dia_fechamento, agora)
    # Mesma função do "Ver Resumo" do bot. sobra_mes é None (null no JSON)
    # quando a fatura vigente é anterior à data de corte da Sobra, e já vem com
    # as receitas da fatura somadas (PRO-86). total_receitas vem sempre, 0.0
    # sem receita (contrato estável para a PRO-88 e a PRO-89).
    resumo_fatura = calcular_resumo_fatura(conn, user_id, ano_mes, dia_fechamento)
    return {
        "ano_mes": ano_mes,
        "dia_fechamento": dia_fechamento,
        "renda": em_reais(resumo_fatura.renda),
        "total_gasto": em_reais(resumo_fatura.total_gasto),
        "sobra_mes": None if resumo_fatura.sobra_mes is None else em_reais(resumo_fatura.sobra_mes),
        "total_receitas": em_reais(resumo_fatura.total_receitas),
        "categorias": [
            {"nome": nome, "gasto": em_reais(gasto)}
            for nome, gasto in resumo_fatura.categorias
        ],
    }


@app.get("/historico")
def historico(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
              agora: datetime = Depends(obter_agora)):
    user_id = usuario["id"]
    _, _, dia_fechamento = carregar_cadastro(conn, user_id)
    serie = calcular_serie_gastos_mensais(conn, user_id, dia_fechamento, agora)
    return {
        "ano_mes_atual": obter_ciclo_atual(dia_fechamento, agora),
        "meses": [
            {"ano_mes": ano_mes, "total_gasto": em_reais(total_gasto), "situacao": situacao}
            for ano_mes, total_gasto, situacao in serie
        ],
    }


@app.get("/gastos/recentes")
def gastos_recentes(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db)):
    user_id = usuario["id"]
    carregar_cadastro(conn, user_id)
    gastos = buscar_gastos_recentes(conn, user_id, LIMITE_GASTOS_RECENTES)
    return {
        "gastos": [
            {
                "id": id_gasto,
                "descricao": descricao,
                "estabelecimento": estabelecimento,
                "categoria": categoria,
                "valor": em_reais(valor),
                "data": data,
                "parcelado": total_parcelas is not None,
                "total_parcelas": total_parcelas,
                "valor_total": None if valor_total is None else em_reais(valor_total),
            }
            for id_gasto, descricao, categoria, valor, data, total_parcelas, valor_total, estabelecimento in gastos
        ]
    }


@app.get("/movimentacoes/recentes")
def movimentacoes_recentes(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db)):
    """Gastos e receitas numa lista só, para a home (PRO-92). Os dois tipos
    têm os mesmos campos; o front decide pela 'tipo' ("gasto" ou "receita").
    O valor vai sempre positivo: o sinal e a cor são da tela. Na receita,
    estabelecimento, categoria, total_parcelas e valor_total vêm null e
    parcelado false; descrição vazia vem null (nunca texto substituto)."""
    user_id = usuario["id"]
    carregar_cadastro(conn, user_id)
    movimentacoes = buscar_movimentacoes_recentes(conn, user_id, LIMITE_MOVIMENTACOES_RECENTES)
    return {
        "movimentacoes": [
            {
                "tipo": tipo,
                "id": id_movimentacao,
                "descricao": descricao,
                "estabelecimento": estabelecimento,
                "categoria": categoria,
                "valor": em_reais(valor),
                "data": data,
                "parcelado": total_parcelas is not None,
                "total_parcelas": total_parcelas,
                "valor_total": None if valor_total is None else em_reais(valor_total),
            }
            for (tipo, id_movimentacao, descricao, categoria, valor, data,
                 total_parcelas, valor_total, estabelecimento) in movimentacoes
        ]
    }


@app.get("/gastos/{id_gasto}")
def detalhe_gasto(id_gasto: int, usuario: dict = Depends(usuario_autenticado),
                  conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    user_id = usuario["id"]
    _, _, dia_fechamento = carregar_cadastro(conn, user_id)
    # Sempre buscado com o user_id do initData: gasto de outro usuário, inexistente,
    # receita ou sem descrição dão o mesmo 404, sem dizer qual foi o caso.
    gasto = buscar_gasto(conn, user_id, id_gasto)
    if gasto is None:
        raise HTTPException(status_code=404, detail="gasto_nao_encontrado")
    (id_gasto, descricao, categoria, valor, data, total_parcelas, valor_total,
     estabelecimento, ciclo_referencia, data_criacao) = gasto
    parcelado = total_parcelas is not None
    return {
        "id": id_gasto,
        "descricao": descricao,
        "estabelecimento": estabelecimento,
        "categoria": categoria,
        "valor": em_reais(valor),
        "data": data,
        "parcelado": parcelado,
        "total_parcelas": total_parcelas,
        "valor_total": None if valor_total is None else em_reais(valor_total),
        "parcelas_pagas": calcular_parcelas_pagas(
            ciclo_referencia, data_criacao, total_parcelas, dia_fechamento, agora
        ) if parcelado else None,
    }


# --- REGISTRO (PRO-88) ---
class LimiteDeChamadas:
    """Limite de chamadas por usuário numa janela deslizante, em memória (D8):
    no máximo 'maximo' chamadas nos últimos 'janela' segundos. Vale por
    processo e zera quando a API reinicia (a API roda num processo só).
    Seguro entre threads. 'relogio' é trocável nos testes."""

    def __init__(self, maximo: int = 10, janela: float = 60.0, relogio=time.monotonic):
        self.maximo = maximo
        self.janela = janela
        self.relogio = relogio
        self._chamadas: dict = {}
        self._trava = threading.Lock()

    def registrar(self, user_id: int) -> Optional[int]:
        """Conta a chamada e devolve None; se o limite já foi atingido, não
        conta e devolve quantos segundos (inteiro, no mínimo 1) faltam para
        a chamada mais antiga sair da janela (cabeçalho Retry-After)."""
        agora = self.relogio()
        with self._trava:
            fila = self._chamadas.setdefault(user_id, deque())
            while fila and fila[0] <= agora - self.janela:
                fila.popleft()
            if len(fila) >= self.maximo:
                return max(1, math.ceil(fila[0] + self.janela - agora))
            fila.append(agora)
            return None


LIMITE_INTERPRETACAO = LimiteDeChamadas(maximo=10, janela=60.0)


def obter_limite_interpretacao() -> LimiteDeChamadas:
    """O limite da rota /registro/interpretar; os testes trocam por um novo."""
    return LIMITE_INTERPRETACAO


# Códigos do registro.ErroRegistro que não são 422.
STATUS_ERRO_REGISTRO = {"resposta_ia_invalida": 502, "ia_indisponivel": 503, "banco_ocupado": 503}


def erro_registro(erro: registro.ErroRegistro) -> HTTPException:
    """HTTPException {"detail": codigo}. O log leva só o código: nunca o
    texto do usuário, o initData, a chave da IA nem a mensagem da IA."""
    logger.warning("Registro recusado: %s", erro.codigo)
    return HTTPException(status_code=STATUS_ERRO_REGISTRO.get(erro.codigo, 422), detail=erro.codigo)


def erro_interno(erro: Exception, rotulo: str = "Registro") -> HTTPException:
    """Erro inesperado nas rotas que gravam: 500 {"detail": "erro_interno"}.
    Nem a resposta nem o log levam a mensagem (a do banco pode trazer SQL ou
    dados) nem o traceback; o log leva só o código e o nome da classe.
    'rotulo' diz de que rota veio ("Registro" ou, desde a PRO-96, "Cadastro")."""
    logger.error("%s falhou: erro_interno (%s)", rotulo, type(erro).__name__)
    return HTTPException(status_code=500, detail="erro_interno")


class PedidoInterpretar(BaseModel):
    """Campos soltos (Any): quem confere é o registro.py, com os códigos de
    erro do contrato. Campos a mais são ignorados (o user_id vem só do initData)."""
    tipo: Any = None
    texto: Any = None


class PedidoGasto(BaseModel):
    chave: Any = None
    valor: Any = None
    descricao: Optional[str] = None
    estabelecimento: Optional[str] = None
    categoria: Any = None
    metodo_pagamento: Any = None
    parcelas: Any = None


class PedidoReceita(BaseModel):
    chave: Any = None
    valor: Any = None
    descricao: Optional[str] = None


@app.post("/registro/interpretar")
async def registro_interpretar(pedido: PedidoInterpretar, usuario: dict = Depends(usuario_autenticado),
                               conn=Depends(obter_conexao_db),
                               limite: LimiteDeChamadas = Depends(obter_limite_interpretacao)):
    """Prévia do texto livre pela IA, SEM gravar. Assíncrona: o banco roda em
    thread (run_in_threadpool) e a IA também (registro.gerar_conteudo_com_retry),
    para o servidor não travar enquanto o Gemini responde."""
    user_id = usuario["id"]
    try:
        tipo, texto = registro.validar_pedido_interpretacao(pedido.tipo, pedido.texto)
        await run_in_threadpool(carregar_cadastro, conn, user_id)
        espera = limite.registrar(user_id)
        if espera is not None:
            logger.warning("Registro recusado: limite_excedido")
            raise HTTPException(status_code=429, detail="limite_excedido", headers={"Retry-After": str(espera)})
        categorias = await run_in_threadpool(registro.listar_categorias, conn, user_id)
        previa = await registro.interpretar_previa(tipo, texto, categorias)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro) from None
    if previa.tipo == "receita":
        return {"tipo": "receita", "valor": em_reais(previa.valor), "descricao": previa.descricao}
    return {
        "tipo": "gasto",
        "valor": em_reais(previa.valor),
        "descricao": previa.descricao,
        "estabelecimento": previa.estabelecimento,
        "categoria": previa.categoria,
        "metodo_pagamento": None,  # D5: a tela pede a escolha
    }


@app.post("/gastos", status_code=201)
def gravar_gasto(pedido: PedidoGasto, response: Response, usuario: dict = Depends(usuario_autenticado),
                 conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Grava o gasto confirmado (sem IA). 201 se gravou; 200 com
    repetido=true se a mesma chave já tinha sido gravada (lançamento original).
    Data e hora do servidor, em Brasília. Banco ocupado depois das novas
    tentativas: 503 banco_ocupado (a tela reenvia o MESMO corpo, com a MESMA
    chave). Erro inesperado: 500 erro_interno, sem detalhes."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        gasto = registro.gravar_gasto_api(
            conn, user_id, pedido.chave, pedido.valor, pedido.descricao, pedido.estabelecimento,
            pedido.categoria, pedido.metodo_pagamento, pedido.parcelas, registro.momento_agora(agora)
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro) from None
    if gasto.repetido:
        response.status_code = 200
    return {
        "id": gasto.id,
        "tipo": "gasto",
        "valor": em_reais(gasto.valor),
        "valor_parcela": em_reais(gasto.valor_parcela),
        "parcelas": gasto.parcelas,
        "descricao": gasto.descricao,
        "estabelecimento": gasto.estabelecimento,
        "categoria": gasto.categoria,
        "metodo_pagamento": gasto.metodo_pagamento,
        "data": gasto.data,
        "repetido": gasto.repetido,
    }


@app.post("/receitas", status_code=201)
def gravar_receita(pedido: PedidoReceita, response: Response, usuario: dict = Depends(usuario_autenticado),
                   conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Grava a receita confirmada. 201 ou 200 (repetido), como /gastos."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        receita = registro.gravar_receita_api(
            conn, user_id, pedido.chave, pedido.valor, pedido.descricao, registro.momento_agora(agora)
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro) from None
    if receita.repetido:
        response.status_code = 200
    return {
        "id": receita.id,
        "tipo": "receita",
        "valor": em_reais(receita.valor),
        "descricao": receita.descricao,
        "data": receita.data,
        "repetido": receita.repetido,
    }


# --- CADASTRO (PRO-96) ---
# Códigos do cadastro.ErroCadastro que não são 422.
STATUS_ERRO_CADASTRO = {"usuario_ja_cadastrado": 409, "banco_ocupado": 503}


def erro_cadastro(erro: cadastro.ErroCadastro) -> HTTPException:
    """HTTPException {"detail": codigo}. O log leva só o código: nunca o nome,
    as categorias nem o initData."""
    logger.warning("Cadastro recusado: %s", erro.codigo)
    return HTTPException(status_code=STATUS_ERRO_CADASTRO.get(erro.codigo, 422), detail=erro.codigo)


class PedidoCadastro(BaseModel):
    """Campos soltos (Any): quem confere é o cadastro.py, com os códigos de
    erro do contrato (ex.: o dia tem de ser inteiro de verdade, não 5.5 nem
    "5"). Campos a mais (user_id, renda_mensal, horario_alerta...) são
    ignorados: o user_id vem só do initData, a renda é 0 e não há alerta."""
    nome: Any = None
    dia_fechamento: Any = None
    categorias: Any = None


@app.post("/cadastro", status_code=201)
def cadastrar(pedido: PedidoCadastro, usuario: dict = Depends(usuario_autenticado),
              conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Cria o cadastro pelo Mini App: 'usuarios' com renda 0.0 e
    horario_alerta NULL (nenhum alerta diário) e as categorias, numa transação
    só; data de criação do servidor, em Brasília. 201 com o que foi gravado
    (as categorias como ficaram, sem repetição, na ordem). Já cadastrado
    (antes ou durante a gravação, como no toque duplo): 409
    usuario_ja_cadastrado, sem alterar nada. Banco ocupado depois das novas
    tentativas: 503 banco_ocupado. Erro inesperado: 500 erro_interno, sem
    detalhes."""
    user_id = usuario["id"]
    try:
        criado = cadastro.cadastrar_pela_api(
            conn, user_id, pedido.nome, pedido.dia_fechamento, pedido.categorias,
            registro.momento_agora(agora)
        )
    except cadastro.ErroCadastro as erro:
        raise erro_cadastro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Cadastro") from None
    return {
        "user_id": criado.user_id,
        "nome": criado.nome,
        "dia_fechamento": criado.dia_fechamento,
        "categorias": criado.categorias,
    }


class ArquivosSemCache(StaticFiles):
    """StaticFiles com `Cache-Control: no-cache`: o navegador pode guardar a
    página, mas sempre pergunta ao servidor se ela mudou (ETag; sem mudança,
    a resposta é um 304 curto). Sem isso, o WebView do Telegram reaproveita
    uma versão antiga por horas (PRO-77)."""

    async def get_response(self, path, scope):
        resposta = await super().get_response(path, scope)
        resposta.headers["Cache-Control"] = "no-cache"
        return resposta


# Mesma origem da API (sem CORS). Montado depois das rotas.
app.mount("/teste", ArquivosSemCache(directory=os.path.join(RAIZ, "mini-apps", "teste-initdata"), html=True), name="teste")
app.mount("/app", ArquivosSemCache(directory=os.path.join(RAIZ, "mini-apps", "home"), html=True), name="app")
