"""API local do Mini App (PRO-72): autentica pelo initData do Telegram.

Não importa o bot.py. Configuração e conexão com o banco são resolvidas
sob demanda (dependências), então o módulo importa sem .env e sem rede.
"""
import logging
import os
from functools import lru_cache
from typing import NamedTuple

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from api.initdata import InitDataError, validate_init_data

logger = logging.getLogger("api")

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


# Mesma origem da API (sem CORS). Montado depois das rotas.
app.mount("/app", StaticFiles(directory=os.path.join(RAIZ, "mini-apps", "teste-initdata"), html=True),
          name="app")
