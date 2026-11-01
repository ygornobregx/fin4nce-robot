"""API local do Mini App: autentica pelo initData do Telegram (PRO-72) e expõe
as rotas de leitura /me, /resumo e /historico (PRO-76) e /gastos/recentes
(PRO-79). Os números e a consulta vêm todos do calculos.py; as rotas só buscam
os dados e montam o JSON.

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
    buscar_gastos_recentes,
    calcular_serie_gastos_mensais,
    calcular_totais_mes,
    montar_categorias_fatura,
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
    _, renda, dia_fechamento = carregar_cadastro(conn, user_id)
    renda = renda or 0.0
    ano_mes = obter_ciclo_atual(dia_fechamento, agora)
    categorias = montar_categorias_fatura(conn, user_id, ano_mes, dia_fechamento)
    comprometido, total_gasto, sobra_mes = calcular_totais_mes(renda, categorias)
    return {
        "ano_mes": ano_mes,
        "dia_fechamento": dia_fechamento,
        "renda": em_reais(renda),
        "comprometido": em_reais(comprometido),
        "total_gasto": em_reais(total_gasto),
        "sobra_mes": em_reais(sobra_mes),
        "categorias": [
            {
                "nome": nome,
                "orcamento": None if orcamento is None else em_reais(orcamento),
                "gasto": em_reais(gasto),
                "sobra": None if sobra is None else em_reais(sobra),
            }
            for nome, orcamento, sobra, gasto in categorias
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
                "categoria": categoria,
                "valor": em_reais(valor),
                "data": data,
                "parcelado": total_parcelas is not None,
                "total_parcelas": total_parcelas,
                "valor_total": None if valor_total is None else em_reais(valor_total),
            }
            for id_gasto, descricao, categoria, valor, data, total_parcelas, valor_total in gastos
        ]
    }


# Mesma origem da API (sem CORS). Montado depois das rotas.
app.mount("/app", StaticFiles(directory=os.path.join(RAIZ, "mini-apps", "teste-initdata"), html=True),
          name="app")
