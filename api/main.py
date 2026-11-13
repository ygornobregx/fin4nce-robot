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

Rotas dos gastos recorrentes (PRO-87): GET /categorias, GET /recorrentes,
POST /recorrentes (uma vez só por chave), PUT /recorrentes/{id} e
DELETE /recorrentes/{id}. As regras ficam no recorrentes.py.

Rotas das receitas recorrentes (PRO-102): GET /receitas-recorrentes,
POST /receitas-recorrentes (uma vez só por chave), PUT
/receitas-recorrentes/{id} e DELETE /receitas-recorrentes/{id}. As regras
também ficam no recorrentes.py; o lançamento no dia é do job do bot.

Rotas das categorias (PRO-104): GET /categorias/ranking (o gasto de cada
categoria na fatura vigente, com o id), POST /categorias (uma vez só por
chave), PUT /categorias/{id} (renomeia e propaga o nome) e
DELETE /categorias/{id}. As regras ficam no categorias.py, o mesmo módulo que
o bot usa. O GET /categorias (só os nomes, PRO-87) não muda.

Assinatura (PRO-106): GET /assinatura (situação, teste, preço, uso de IA do
dia e os campos do selo), POST /assinaturas/teste (teste grátis de 10 dias),
o bloco "assinatura" no GET /me (só com ASSINATURAS_LIGADAS), a dependência
acesso_liberado (403 assinatura_vencida ou assinatura_ausente nas rotas de
escrita e de IA, só com a chave ligada; as leituras usam a leitura_liberada e
nunca checam plano: modo somente leitura) e o limite diário de registros com IA no
/registro/interpretar (429 limite_diario_ia, sempre ligado). As regras ficam
no assinaturas.py, o mesmo módulo que o bot usa.

Pagamento em Telegram Stars (PRO-107): POST /assinaturas/fatura (link da
fatura de assinatura pelo createInvoiceLink), POST /assinaturas/renovacao
(cancela ou reativa a renovação pelo editUserStarSubscription) e GET /termos
(público). As três ficam fora da checagem de acesso. A Bot API é chamada pelo
HTTPX (BotApiTelegram, trocada por um dublê nos testes); o Pro só nasce no
bot, no successful_payment. Nenhuma rota grava pagamento.

Trava de cadastro (PRO-109): com CADASTRO_ABERTO fechada, o POST /cadastro de
quem ainda não tem cadastro responde 403 cadastro_fechado (o admin passa;
quem já tem conta continua com o 409 de sempre); o POST /assinaturas/teste
responde 409 vagas_teste_esgotadas quando o dia já tem LIMITE_TESTES_DIA
testes novos; o GET /assinatura e o bloco "assinatura" do /me trazem o campo
"cadastro_aberto". As regras ficam no assinaturas.py.

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

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import assinaturas
import cadastro
import categorias
import recorrentes
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

FORMATO_LOG = "%(asctime)s %(name)s %(levelname)s %(message)s"


def configurar_logs_do_dominio(nomes=("registro", "assinaturas")) -> None:
    """Log dos módulos de regra no terminal (PRO-106): um StreamHandler só nos
    loggers 'registro' (tokens de cada chamada à IA, INFO) e 'assinaturas',
    nível INFO e propagate=False (não duplica no root). O 'api' fica como
    estava (os WARNING dele já aparecem; os testes os calam). Não mexe no
    root: o httpx loga cada requisição em INFO. Idempotente: o handler leva
    uma marca e não é posto duas vezes (o módulo pode ser importado de novo
    nos testes)."""
    for nome in nomes:
        alvo = logging.getLogger(nome)
        if any(getattr(h, "_log_do_dominio", False) for h in alvo.handlers):
            continue
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(FORMATO_LOG))
        handler._log_do_dominio = True
        alvo.addHandler(handler)
        alvo.setLevel(logging.INFO)
        alvo.propagate = False


configurar_logs_do_dominio()

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


def leitura_liberada(usuario: dict = Depends(usuario_autenticado)) -> dict:
    """Rotas de LEITURA (todo GET com dados do usuário): só a autenticação.
    Leitura nunca checa plano (PRO-106, modo somente leitura): quem venceu, e
    quem nunca escolheu um plano, continua vendo os próprios dados; só não
    grava nem usa a IA (acesso_liberado). Dependência explícita para a regra
    ficar visível em cada rota."""
    return usuario


def acesso_liberado(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
                    agora: datetime = Depends(obter_agora)) -> dict:
    """Checagem de plano (PRO-106), depois da usuario_autenticado, só nas
    rotas de ESCRITA e de IA (todo POST, PUT e DELETE com dados, e o
    /registro/interpretar). As leituras usam a leitura_liberada (nunca checam
    plano); ficam fora de qualquer checagem /saude, /me, /assinatura,
    /assinaturas/teste, /cadastro e, na PRO-107, /assinaturas/fatura,
    /assinaturas/renovacao e /termos. Com ASSINATURAS_LIGADAS desligada (o
    padrão), não consulta nada e deixa passar, como antes. Ligada: o admin
    (ADMIN_IDS) sempre passa; quem tem teste ou Pro valendo passa; o resto
    recebe 403 assinatura_vencida (teve teste ou Pro e venceu) ou 403
    assinatura_ausente (nunca escolheu), ANTES de qualquer consulta de dados e
    antes da IA. Devolve o mesmo 'usuario' da autenticação."""
    if not assinaturas.assinaturas_ligadas():
        return usuario
    user_id = usuario["id"]
    if assinaturas.e_admin(user_id):
        return usuario
    try:
        situacao = assinaturas.situacao_acesso(conn, user_id, agora, admin=False)
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None
    if situacao.tem_acesso:
        return usuario
    codigo = "assinatura_ausente" if situacao.estado == "sem_plano" else "assinatura_vencida"
    logger.warning("Acesso recusado: %s", codigo)
    raise HTTPException(status_code=403, detail=codigo)


app = FastAPI(title="Finance API")


@app.get("/saude")
def saude():
    return {"status": "ok"}


@app.get("/me")
def me(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
       agora: datetime = Depends(obter_agora)):
    """Com ASSINATURAS_LIGADAS desligada, exatamente como antes da PRO-106
    ({"user_id", "nome"}). Ligada, traz também o bloco "assinatura" (o mesmo
    JSON do GET /assinatura, para a home não fazer chamada nova); se montar o
    bloco falhar, "assinatura": null e só o código do erro no log: o /me nunca
    falha por causa do bloco."""
    user_id = usuario["id"]
    linha = conn.execute("SELECT nome FROM usuarios WHERE user_id = ?", (user_id,)).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail="usuario_nao_cadastrado")
    resposta = {"user_id": user_id, "nome": linha[0]}
    if assinaturas.assinaturas_ligadas():
        try:
            resposta["assinatura"] = assinaturas.montar_bloco_assinatura(conn, user_id, agora, ligada=True)
        except Exception as erro:
            logger.error("Bloco da assinatura falhou: erro_interno (%s)", type(erro).__name__)
            resposta["assinatura"] = None
    return resposta


# --- ASSINATURA (PRO-106) ---
# Contrato na nota da PRO-106 (base das telas da PRO-108). Nenhuma das duas
# rotas exige cadastro em 'usuarios' (o teste começa antes do POST /cadastro).
# A palavra "admin" nunca aparece na resposta: o admin vem como Pro.
@app.get("/assinatura")
def assinatura(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
               agora: datetime = Depends(obter_agora)):
    """Situação da assinatura da pessoa (estado, datas, teste, preço, uso de IA
    do dia e os campos do selo e da folha Minha Assinatura), calculada no
    servidor, em Brasília. Desde a PRO-109, também "cadastro_aberto" (true
    para o admin; senão, o CADASTRO_ABERTO), lido pela escolha inicial do
    Mini App mesmo com a chave desligada."""
    try:
        return assinaturas.montar_bloco_assinatura(conn, usuario["id"], agora)
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None


@app.post("/assinaturas/teste", status_code=201)
def comecar_teste(response: Response, usuario: dict = Depends(usuario_autenticado),
                  conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Começa o teste grátis de 10 dias agora (o início é o toque). Sem corpo:
    o que vier é ignorado (nenhum campo dá plano). 201 com {"inicio", "fim",
    "repetido": false}; 200 com "repetido": true se já existe um teste ativo
    (toque duplo, mesmo com as vagas do dia esgotadas); 409 teste_ja_usado
    (venceu) ou plano_ativo (Pro ativo ou admin); 409 vagas_teste_esgotadas
    (PRO-109: o dia já tem LIMITE_TESTES_DIA testes novos; a regra é do
    iniciar_teste); 503 banco_ocupado; 500 erro_interno."""
    try:
        teste = assinaturas.iniciar_teste(conn, usuario["id"], agora)
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None
    if teste.repetido:
        response.status_code = 200
    return {"inicio": assinaturas.data_texto(teste.inicio), "fim": assinaturas.data_texto(teste.fim),
            "repetido": teste.repetido}


# --- PAGAMENTO EM TELEGRAM STARS (PRO-107) ---
# A API só pede ao Telegram o link da fatura e liga ou desliga a renovação; o
# Pro só nasce no bot, quando chega o successful_payment conferido (nenhuma
# rota grava pagamento). As três rotas ficam fora da checagem de acesso (quem
# venceu precisa pagar). O token do bot só entra na URL da Bot API: nunca vai
# para log nem resposta, e o erro do HTTPX é engolido sem a mensagem (ela pode
# trazer a URL).
URL_BOT_API = "https://api.telegram.org"
TEMPO_BOT_API = 10.0   # segundos

# O httpx loga cada requisição (com a URL, que leva o token) em INFO: aqui
# fica sempre em WARNING ou acima, mesmo se alguém ligar o INFO no root.
for _nome_log in ("httpx", "httpcore"):
    logging.getLogger(_nome_log).setLevel(logging.WARNING)


class TelegramRecusou(Exception):
    """A Bot API respondeu e recusou o pedido (ok: false, erro 4xx)."""


class TelegramIndisponivel(Exception):
    """A Bot API não respondeu (rede, tempo esgotado, fora do ar ou 5xx/429)."""


class BotApiTelegram:
    """As chamadas da API à Bot API do Telegram, pelo HTTPX (já no
    requirements-api.txt). Os testes trocam por um dublê (obter_bot_api), sem
    rede. Nada aqui escreve no log: quem chama registra só o código."""

    def __init__(self, token: str):
        self._token = token

    def __repr__(self) -> str:
        return "BotApiTelegram(token=***)"

    def _chamar(self, metodo: str, dados: Optional[dict] = None):
        try:
            with httpx.Client(timeout=TEMPO_BOT_API) as cliente:
                resposta = cliente.post(f"{URL_BOT_API}/bot{self._token}/{metodo}", json=dados or {})
            corpo = resposta.json()
        except Exception:
            raise TelegramIndisponivel() from None
        if isinstance(corpo, dict) and corpo.get("ok") is True:
            return corpo.get("result")
        codigo = corpo.get("error_code") if isinstance(corpo, dict) else None
        if isinstance(codigo, int) and 400 <= codigo < 500 and codigo != 429:
            raise TelegramRecusou()
        raise TelegramIndisponivel()

    def obter_bot(self) -> dict:
        """getMe (usado pela trava do testes_pagamento_stars.py --telegram-real)."""
        return self._chamar("getMe")

    def criar_link_fatura(self, fatura: dict) -> str:
        """createInvoiceLink com os campos da assinaturas.montar_fatura_pro."""
        return self._chamar("createInvoiceLink", fatura)

    def editar_assinatura(self, user_id: int, pagamento_id: str, cancelar: bool) -> None:
        """editUserStarSubscription (is_canceled = cancelar)."""
        self._chamar("editUserStarSubscription", {"user_id": user_id, "telegram_payment_charge_id": pagamento_id,
                                                  "is_canceled": cancelar})


def obter_bot_api(config: Config = Depends(obter_config)) -> BotApiTelegram:
    """A Bot API com o token do .env; os testes trocam por um dublê."""
    return BotApiTelegram(config.token)


def telegram_indisponivel(erro: Exception) -> HTTPException:
    """502 {"detail": "telegram_indisponivel"}, sem detalhes. O log leva só o
    nome da classe do erro (nunca o token, o payload nem a resposta)."""
    logger.warning("Bot API falhou: telegram_indisponivel (%s)", type(erro).__name__)
    return HTTPException(status_code=502, detail="telegram_indisponivel")


@app.post("/assinaturas/fatura")
def pedir_fatura(usuario: dict = Depends(usuario_autenticado), conn=Depends(obter_conexao_db),
                 agora: datetime = Depends(obter_agora), bot_api=Depends(obter_bot_api)):
    """O link da fatura de assinatura do Plano Pro (createInvoiceLink, XTR,
    30 dias), para o Mini App abrir com openInvoice. Sem corpo (o que vier é
    ignorado). 200 {"link": "https://t.me/$...", "stars": 150}. 409
    plano_ativo (Pro de Stars renovando, ou admin); 409
    assinaturas_desligadas (ASSINATURAS_LIGADAS desligada: ninguém assina);
    502 telegram_indisponivel; 500 erro_interno. Não grava nada."""
    try:
        fatura = assinaturas.preparar_fatura(conn, usuario["id"], agora)
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None
    try:
        link = bot_api.criar_link_fatura(fatura)
    except Exception as erro:
        raise telegram_indisponivel(erro) from None
    if not isinstance(link, str) or not link.startswith("https://t.me/"):
        raise telegram_indisponivel(ValueError())
    return {"link": link, "stars": fatura["prices"][0]["amount"]}


class PedidoRenovacao(BaseModel):
    """{"cancelar": true} ou {"cancelar": false}. Campo solto (Any): quem
    confere é a rota (booleano de verdade). Campos a mais são ignorados."""
    cancelar: Any = None


@app.post("/assinaturas/renovacao")
def mudar_renovacao(pedido: PedidoRenovacao, usuario: dict = Depends(usuario_autenticado),
                    conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora),
                    bot_api=Depends(obter_bot_api)):
    """Cancela ou reativa a renovação da assinatura em Stars da pessoa
    (editUserStarSubscription com o id da PRIMEIRA cobrança). Resposta: o
    mesmo JSON do GET /assinatura (montar_bloco_assinatura). Repetir o mesmo
    pedido não muda nada e responde 200 (sem chamar o Telegram). 422
    cancelar_invalido; 409 sem_assinatura_stars (teste, Pro dado, admin ou
    vencido); 409 reativacao_recusada (o Telegram recusou reativar: nada muda
    no banco); 502 telegram_indisponivel (sem gravar)."""
    cancelar = pedido.cancelar
    if not isinstance(cancelar, bool):
        raise HTTPException(status_code=422, detail="cancelar_invalido")
    user_id = usuario["id"]
    try:
        alvo = assinaturas.preparar_renovacao(conn, user_id, cancelar, agora)
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None
    recusou = False
    if not alvo.ja_no_estado:
        try:
            bot_api.editar_assinatura(user_id, alvo.pagamento_id, cancelar)
        except TelegramRecusou:
            recusou = True
        except Exception as erro:
            raise telegram_indisponivel(erro) from None
    try:
        assinaturas.concluir_renovacao(conn, alvo, cancelar, telegram_recusou=recusou)
        return assinaturas.montar_bloco_assinatura(conn, user_id, agora)
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Assinaturas") from None


@app.get("/termos")
def termos():
    """Pública (sem initData): {"texto": "..."}, o mesmo texto do /terms do
    bot (assinaturas.texto_termos, constante única), em texto puro."""
    return {"texto": assinaturas.texto_termos()}


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
def resumo(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db),
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
def historico(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db),
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
def gastos_recentes(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db)):
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
def movimentacoes_recentes(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db)):
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
def detalhe_gasto(id_gasto: int, usuario: dict = Depends(leitura_liberada),
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
STATUS_ERRO_REGISTRO = {"resposta_ia_invalida": 502, "ia_indisponivel": 503, "banco_ocupado": 503,
                        "recorrente_nao_encontrado": 404,            # PRO-87
                        "receita_recorrente_nao_encontrada": 404,    # PRO-102
                        "categoria_inexistente": 404,                # PRO-104
                        "teste_ja_usado": 409, "plano_ativo": 409,   # PRO-106
                        "assinaturas_desligadas": 409, "sem_assinatura_stars": 409,   # PRO-107
                        "reativacao_recusada": 409,
                        "vagas_teste_esgotadas": 409}               # PRO-109


def erro_registro(erro: registro.ErroRegistro) -> HTTPException:
    """HTTPException {"detail": codigo}. O log leva só o código: nunca o
    texto do usuário, o initData, a chave da IA nem a mensagem da IA."""
    logger.warning("Registro recusado: %s", erro.codigo)
    return HTTPException(status_code=STATUS_ERRO_REGISTRO.get(erro.codigo, 422), detail=erro.codigo)


def erro_interno(erro: Exception, rotulo: str = "Registro") -> HTTPException:
    """Erro inesperado nas rotas que gravam: 500 {"detail": "erro_interno"}.
    Nem a resposta nem o log levam a mensagem (a do banco pode trazer SQL ou
    dados) nem o traceback; o log leva só o código e o nome da classe.
    'rotulo' diz de que rota veio ("Registro", desde a PRO-96 "Cadastro",
    desde a PRO-87 "Recorrentes", desde a PRO-102 "Receitas recorrentes" e,
    desde a PRO-104, "Categorias")."""
    logger.error("%s falhou: erro_interno (%s)", rotulo, type(erro).__name__)
    return HTTPException(status_code=500, detail="erro_interno")


def devolver_vaga_ia(conn, user_id: int, dia: str) -> None:
    """Devolve a vaga do dia (a IA não respondeu). Nunca derruba a resposta:
    se a devolução falhar, fica só o código no log."""
    try:
        assinaturas.devolver_uso_ia(conn, user_id, dia)
    except Exception as erro:
        logger.error("Devolucao da vaga de IA falhou: erro_interno (%s)", type(erro).__name__)


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
async def registro_interpretar(pedido: PedidoInterpretar, usuario: dict = Depends(acesso_liberado),
                               conn=Depends(obter_conexao_db),
                               limite: LimiteDeChamadas = Depends(obter_limite_interpretacao),
                               agora: datetime = Depends(obter_agora)):
    """Prévia do texto livre pela IA, SEM gravar. Assíncrona: o banco roda em
    thread (run_in_threadpool) e a IA também (registro.gerar_conteudo_com_retry),
    para o servidor não travar enquanto o Gemini responde.

    Ordem (PRO-106): autenticação -> acesso (acesso_liberado, se a assinatura
    estiver ligada) -> texto válido -> cadastro -> limite por minuto (429
    limite_excedido) -> reserva da vaga do dia (assinaturas.reservar_uso_ia;
    sem vaga, 429 limite_diario_ia com Retry-After até 00:00 de Brasília, SEM
    chamar a IA) -> IA. O limite diário vale sempre, mesmo com a assinatura
    desligada. A vaga é devolvida se a IA não respondeu (503 ia_indisponivel)
    ou se o pedido parou antes de chegar à IA; resposta da IA que não serve
    (502, 422) conta."""
    user_id = usuario["id"]
    dia_reservado = None
    chegou_na_ia = False
    try:
        tipo, texto = registro.validar_pedido_interpretacao(pedido.tipo, pedido.texto)
        await run_in_threadpool(carregar_cadastro, conn, user_id)
        espera = limite.registrar(user_id)
        if espera is not None:
            logger.warning("Registro recusado: limite_excedido")
            raise HTTPException(status_code=429, detail="limite_excedido", headers={"Retry-After": str(espera)})
        dia_reservado = await run_in_threadpool(assinaturas.reservar_uso_ia, conn, user_id, agora)
        if dia_reservado is None:
            logger.warning("Registro recusado: limite_diario_ia")
            raise HTTPException(status_code=429, detail="limite_diario_ia",
                                headers={"Retry-After": str(assinaturas.segundos_ate_meia_noite(agora))})
        categorias = await run_in_threadpool(registro.listar_categorias, conn, user_id)
        chegou_na_ia = True
        previa = await registro.interpretar_previa(tipo, texto, categorias)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        if dia_reservado is not None and (not chegou_na_ia or erro.codigo == "ia_indisponivel"):
            await run_in_threadpool(devolver_vaga_ia, conn, user_id, dia_reservado)
        raise erro_registro(erro) from None
    except Exception as erro:
        if dia_reservado is not None and not chegou_na_ia:
            await run_in_threadpool(devolver_vaga_ia, conn, user_id, dia_reservado)
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
def gravar_gasto(pedido: PedidoGasto, response: Response, usuario: dict = Depends(acesso_liberado),
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
def gravar_receita(pedido: PedidoReceita, response: Response, usuario: dict = Depends(acesso_liberado),
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
    detalhes.

    Cadastros fechados (PRO-109): com CADASTRO_ABERTO fechada, quem não é
    admin e ainda não tem cadastro recebe 403 cadastro_fechado ANTES de
    qualquer validação e gravação (o log leva só o código). Quem já tem
    cadastro segue para o 409 de sempre. O cadastro.py não muda."""
    user_id = usuario["id"]
    if not assinaturas.cadastro_esta_aberto() and not assinaturas.e_admin(user_id):
        try:
            ja_tem = assinaturas.tem_cadastro(conn, user_id)
        except Exception as erro:
            raise erro_interno(erro, "Cadastro") from None
        if not ja_tem:
            logger.warning("Cadastro recusado: cadastro_fechado")
            raise HTTPException(status_code=403, detail="cadastro_fechado")
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


# --- GASTOS RECORRENTES (PRO-87) ---
# Contrato na nota da PRO-87 (base das telas da PRO-101). Os erros do
# recorrentes.py são registro.ErroRegistro: 404 recorrente_nao_encontrado,
# 503 banco_ocupado e 422 nos demais (STATUS_ERRO_REGISTRO). O id da URL vai
# sempre junto com o user_id do initData na consulta.
class PedidoRecorrente(BaseModel):
    """Corpo do PUT. Campos soltos (Any): quem confere é o recorrentes.py, com
    os códigos do contrato (ex.: o dia tem de ser inteiro de verdade, não 12.0
    nem "12"). O estabelecimento é texto ou null, como no /gastos. Campos a
    mais (user_id, criado_em, ultimo_ciclo_cobrado...) são ignorados."""
    dia_cobranca: Any = None
    valor: Any = None
    descricao: Any = None
    estabelecimento: Optional[str] = None
    categoria: Any = None


class PedidoNovoRecorrente(PedidoRecorrente):
    """Corpo do POST: o do PUT mais a chave de toque duplo (UUID v4)."""
    chave: Any = None


def recorrente_json(recorrente: recorrentes.Recorrente) -> dict:
    """Formato de um recorrente, o mesmo em todas as rotas."""
    return {
        "id": recorrente.id,
        "descricao": recorrente.descricao,
        "estabelecimento": recorrente.estabelecimento,
        "categoria": recorrente.categoria,
        "valor": em_reais(recorrente.valor),
        "dia_cobranca": recorrente.dia_cobranca,
    }


@app.get("/categorias")
def listar_categorias(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db)):
    """Nomes das categorias do usuário, na ordem da registro.listar_categorias
    (a mesma do prompt da IA e da checagem da categoria)."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        categorias = registro.listar_categorias(conn, user_id)
    except HTTPException:
        raise
    except Exception as erro:
        raise erro_interno(erro, "Recorrentes") from None
    return {"categorias": categorias}


@app.get("/recorrentes")
def listar_recorrentes(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db)):
    """Os gastos recorrentes do usuário, por dia de cobrança (e id), e o total
    mensal (soma dos valores; 0.0 sem nenhum)."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        lista = recorrentes.listar_recorrentes(conn, user_id)
    except HTTPException:
        raise
    except Exception as erro:
        raise erro_interno(erro, "Recorrentes") from None
    return {
        "recorrentes": [recorrente_json(recorrente) for recorrente in lista.recorrentes],
        "total_mensal": em_reais(lista.total_mensal),
    }


@app.post("/recorrentes", status_code=201)
def criar_recorrente(pedido: PedidoNovoRecorrente, response: Response, usuario: dict = Depends(acesso_liberado),
                     conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Cria o gasto recorrente (sem IA). 201 se gravou; 200 com repetido=true
    se a mesma chave já tinha sido gravada (o recorrente original, como está
    agora). criado_em do servidor, em Brasília; nada é lançado na hora (o job
    das 05:00 lança no próximo dia de cobrança). Banco ocupado depois das
    novas tentativas: 503 banco_ocupado (a tela reenvia o MESMO corpo, com a
    MESMA chave). Erro inesperado: 500 erro_interno, sem detalhes."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        recorrente = recorrentes.criar_recorrente_api(
            conn, user_id, pedido.chave, pedido.dia_cobranca, pedido.valor, pedido.descricao,
            pedido.estabelecimento, pedido.categoria, registro.momento_agora(agora)
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Recorrentes") from None
    if recorrente.repetido:
        response.status_code = 200
    return dict(recorrente_json(recorrente), repetido=recorrente.repetido)


@app.put("/recorrentes/{recorrente_id}")
def editar_recorrente(recorrente_id: int, pedido: PedidoRecorrente, usuario: dict = Depends(acesso_liberado),
                      conn=Depends(obter_conexao_db)):
    """Edita os 5 campos do recorrente do usuário: 422 dos campos antes da
    existência; depois 404 recorrente_nao_encontrado (inexistente ou de outro
    usuário, sem dizer qual); depois 422 descricao_repetida. Não mexe no
    ciclo já cobrado nem na data de criação. 200 com o recorrente como ficou."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        recorrente = recorrentes.editar_recorrente(
            conn, user_id, recorrente_id, pedido.dia_cobranca, pedido.valor, pedido.descricao,
            pedido.estabelecimento, pedido.categoria
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Recorrentes") from None
    return recorrente_json(recorrente)


@app.delete("/recorrentes/{recorrente_id}")
def apagar_recorrente(recorrente_id: int, usuario: dict = Depends(acesso_liberado),
                      conn=Depends(obter_conexao_db)):
    """Apaga o recorrente do usuário (as transações já lançadas ficam). 404
    recorrente_nao_encontrado se não existe, é de outro usuário ou já foi
    apagado (a tela trata o 404 repetido como "já apagado")."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        apagado = recorrentes.apagar_recorrente(conn, user_id, recorrente_id)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Recorrentes") from None
    return {"id": apagado, "apagado": True}


# --- RECEITAS RECORRENTES (PRO-102) ---
# Contrato na nota da PRO-102 (base das telas da PRO-103), com as mesmas
# regras das rotas /recorrentes: erros do recorrentes.py como
# registro.ErroRegistro (404 receita_recorrente_nao_encontrada, 503
# banco_ocupado, 422 nos demais); o id da URL vai sempre junto com o user_id
# do initData na consulta.
class PedidoReceitaRecorrente(BaseModel):
    """Corpo do PUT. Campos soltos (Any): quem confere é o recorrentes.py, com
    os códigos do contrato. Campos a mais (user_id, categoria,
    estabelecimento, ultimo_ciclo_recebido...) são ignorados."""
    dia_recebimento: Any = None
    valor: Any = None
    descricao: Any = None


class PedidoNovaReceitaRecorrente(PedidoReceitaRecorrente):
    """Corpo do POST: o do PUT mais a chave de toque duplo (UUID v4)."""
    chave: Any = None


def receita_recorrente_json(receita: recorrentes.ReceitaRecorrente) -> dict:
    """Formato de uma receita recorrente, o mesmo em todas as rotas."""
    return {
        "id": receita.id,
        "descricao": receita.descricao,
        "valor": em_reais(receita.valor),
        "dia_recebimento": receita.dia_recebimento,
    }


@app.get("/receitas-recorrentes")
def listar_receitas_recorrentes(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db)):
    """As receitas recorrentes do usuário, por dia de recebimento (e id), e o
    total mensal (soma dos valores; 0.0 sem nenhuma)."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        lista = recorrentes.listar_receitas_recorrentes(conn, user_id)
    except HTTPException:
        raise
    except Exception as erro:
        raise erro_interno(erro, "Receitas recorrentes") from None
    return {
        "receitas_recorrentes": [receita_recorrente_json(receita) for receita in lista.receitas_recorrentes],
        "total_mensal": em_reais(lista.total_mensal),
    }


@app.post("/receitas-recorrentes", status_code=201)
def criar_receita_recorrente(pedido: PedidoNovaReceitaRecorrente, response: Response,
                             usuario: dict = Depends(acesso_liberado), conn=Depends(obter_conexao_db),
                             agora: datetime = Depends(obter_agora)):
    """Cria a receita recorrente (sem IA). 201 se gravou; 200 com
    repetido=true se a mesma chave já tinha sido gravada (a original, como
    está agora). criado_em do servidor, em Brasília; nada é lançado na hora
    (o job das 05:00 do bot lança no próximo dia de recebimento). Banco
    ocupado depois das novas tentativas: 503 banco_ocupado (a tela reenvia o
    MESMO corpo, com a MESMA chave). Erro inesperado: 500 erro_interno."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        receita = recorrentes.criar_receita_recorrente_api(
            conn, user_id, pedido.chave, pedido.dia_recebimento, pedido.valor, pedido.descricao,
            registro.momento_agora(agora)
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Receitas recorrentes") from None
    if receita.repetido:
        response.status_code = 200
    return dict(receita_recorrente_json(receita), repetido=receita.repetido)


@app.put("/receitas-recorrentes/{receita_recorrente_id}")
def editar_receita_recorrente(receita_recorrente_id: int, pedido: PedidoReceitaRecorrente,
                              usuario: dict = Depends(acesso_liberado), conn=Depends(obter_conexao_db)):
    """Edita os 3 campos da receita recorrente do usuário: 422 dos campos
    antes da existência; depois 404 receita_recorrente_nao_encontrada
    (inexistente ou de outro usuário, sem dizer qual); depois 422
    descricao_repetida. Não mexe no ciclo já recebido nem na data de criação.
    200 com a receita recorrente como ficou."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        receita = recorrentes.editar_receita_recorrente(
            conn, user_id, receita_recorrente_id, pedido.dia_recebimento, pedido.valor, pedido.descricao
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Receitas recorrentes") from None
    return receita_recorrente_json(receita)


@app.delete("/receitas-recorrentes/{receita_recorrente_id}")
def apagar_receita_recorrente(receita_recorrente_id: int, usuario: dict = Depends(acesso_liberado),
                              conn=Depends(obter_conexao_db)):
    """Apaga a receita recorrente do usuário (as receitas já lançadas ficam).
    404 receita_recorrente_nao_encontrada se não existe, é de outro usuário ou
    já foi apagada (a tela trata o 404 repetido como "já apagada")."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        apagado = recorrentes.apagar_receita_recorrente(conn, user_id, receita_recorrente_id)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Receitas recorrentes") from None
    return {"id": apagado, "apagado": True}


# --- CATEGORIAS (PRO-104) ---
# Contrato na nota da PRO-104 (base das telas da PRO-105). Os erros do
# categorias.py são registro.ErroRegistro: 404 categoria_inexistente, 503
# banco_ocupado e 422 nos demais (STATUS_ERRO_REGISTRO). O id da URL vai
# sempre junto com o user_id do initData na consulta.
class PedidoCategoria(BaseModel):
    """Corpo do PUT. Campo solto (Any): quem confere é o categorias.py, com os
    códigos do contrato. Campos a mais (user_id, descricao, tipo,
    temporaria...) são ignorados e nunca gravados (D14)."""
    nome: Any = None


class PedidoNovaCategoria(PedidoCategoria):
    """Corpo do POST: o do PUT mais a chave de toque duplo (UUID v4)."""
    chave: Any = None


@app.get("/categorias/ranking")
def ranking_categorias(usuario: dict = Depends(leitura_liberada), conn=Depends(obter_conexao_db),
                       agora: datetime = Depends(obter_agora)):
    """O gasto de cada categoria na fatura vigente (a mesma do /resumo, D4),
    com o id de cada uma. 'ano_mes' e 'total_gasto' são os do /resumo
    (calcular_resumo_fatura). Ordem: gasto decrescente, nome sem acento e id.
    Categoria excluída que ainda tem gasto na fatura: id null e ativa false
    (D5). 'limite': o máximo de categorias (D11)."""
    user_id = usuario["id"]
    try:
        _, _, dia_fechamento = carregar_cadastro(conn, user_id)
        ranking = categorias.listar_ranking(conn, user_id, dia_fechamento, agora)
    except HTTPException:
        raise
    except Exception as erro:
        raise erro_interno(erro, "Categorias") from None
    return {
        "ano_mes": ranking.ano_mes,
        "total_gasto": em_reais(ranking.total_gasto),
        "categorias": [
            {"id": item.id, "nome": item.nome, "gasto": em_reais(item.gasto), "ativa": item.ativa}
            for item in ranking.categorias
        ],
        "limite": ranking.limite,
    }


@app.post("/categorias", status_code=201)
def criar_categoria(pedido: PedidoNovaCategoria, response: Response, usuario: dict = Depends(acesso_liberado),
                    conn=Depends(obter_conexao_db), agora: datetime = Depends(obter_agora)):
    """Cria a categoria. 201 se gravou; 200 com repetido=true se a mesma chave
    já tinha sido gravada (a categoria como está agora). data_criacao do
    servidor, em Brasília. Banco ocupado depois das novas tentativas: 503
    banco_ocupado (a tela reenvia o MESMO corpo, com a MESMA chave). Erro
    inesperado: 500 erro_interno, sem detalhes."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        categoria = categorias.criar_categoria_api(
            conn, user_id, pedido.chave, pedido.nome, registro.momento_agora(agora)
        )
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Categorias") from None
    if categoria.repetido:
        response.status_code = 200
    return {"id": categoria.id, "nome": categoria.nome, "repetido": categoria.repetido}


@app.put("/categorias/{categoria_id}")
def renomear_categoria(categoria_id: int, pedido: PedidoCategoria, usuario: dict = Depends(acesso_liberado),
                       conn=Depends(obter_conexao_db)):
    """Renomeia a categoria do usuário e propaga o nome para os gastos,
    parcelamentos, recorrentes e transferências dele (D7: sempre). 422
    nome_invalido antes da existência; depois 404 categoria_inexistente
    (inexistente ou de outro usuário, sem dizer qual); o mesmo nome de agora:
    200 sem mudar nada; depois 422 nome_repetido. 200 com a categoria como
    ficou."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        categoria = categorias.renomear_categoria_api(conn, user_id, categoria_id, pedido.nome)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Categorias") from None
    return {"id": categoria.id, "nome": categoria.nome}


@app.delete("/categorias/{categoria_id}")
def excluir_categoria(categoria_id: int, usuario: dict = Depends(acesso_liberado),
                      conn=Depends(obter_conexao_db)):
    """Exclui só a linha da categoria do usuário (D8: os gastos ficam no
    histórico e no total). 404 categoria_inexistente se não existe, é de
    outro usuário ou já foi excluída (a tela trata como "já excluída"); 422
    categoria_com_recorrentes (D9) e ultima_categoria (D10), sem apagar nada."""
    user_id = usuario["id"]
    try:
        carregar_cadastro(conn, user_id)
        excluida = categorias.excluir_categoria(conn, user_id, categoria_id)
    except HTTPException:
        raise
    except registro.ErroRegistro as erro:
        raise erro_registro(erro) from None
    except Exception as erro:
        raise erro_interno(erro, "Categorias") from None
    return {"id": excluida, "excluida": True}


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
