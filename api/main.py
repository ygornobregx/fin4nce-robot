"""API local do Mini App: autentica pelo initData do Telegram (PRO-72) e expõe
as rotas de leitura /me, /resumo e /historico (PRO-76; /resumo sem orçamento
por categoria desde a PRO-83; com o total de receitas da fatura desde a
PRO-86) e /gastos/recentes (PRO-79, com o
estabelecimento do gasto desde a PRO-81) e /gastos/{id} (PRO-80, detalhe de um
gasto, com as parcelas pagas). Os números e a consulta vêm todos do
calculos.py; as rotas só buscam os dados e montam o JSON.

Também serve, na mesma origem, a home do Mini App em /app (mini-apps/home, PRO-77)
e a página de diagnóstico das rotas em /teste (mini-apps/teste-initdata).

Não importa o bot.py. Configuração e conexão com o banco são resolvidas
sob demanda (dependências), então o módulo importa sem .env e sem rede.
"""
import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import NamedTuple

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from api.initdata import InitDataError, validate_init_data
from calculos import (
    FUSO_BRASILIA,
    buscar_gasto,
    buscar_gastos_recentes,
    calcular_parcelas_pagas,
    calcular_resumo_fatura,
    calcular_serie_gastos_mensais,
    obter_ciclo_atual,
)

logger = logging.getLogger("api")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LIMITE_GASTOS_RECENTES = 10


class Config(NamedTuple):
    token: str
    turso_url: str
    turso_auth_token: str


@lru_cache(maxsize=1)
def obter_config() -> Config:
    """Lê o .env da raiz do repo (uma vez). Faltando variável, o erro cita só o nome."""
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
