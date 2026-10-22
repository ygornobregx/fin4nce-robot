"""Testes da validação do initData e da API local (PRO-72).

Roda com `python testes_initdata.py`. Não usa .env nem rede: o token é falso,
gerado no próprio teste, e o banco é um SQLite em memória.
"""
import hashlib
import hmac
import json
import sqlite3
import unittest
from urllib.parse import parse_qsl, urlencode

from fastapi.testclient import TestClient

from api.initdata import InitDataError, validate_init_data
from api.main import Config, app, obter_config, obter_conexao_db

TOKEN_FALSO = "123456:TOKEN-FALSO-SO-PARA-TESTE"
AGORA = 1_800_000_000
USER_ID = 4242


def montar_init_data(token=TOKEN_FALSO, auth_date=None, user=None, extras=None, hash_override=None):
    """Monta um initData assinado como o Telegram faria."""
    campos = {
        "auth_date": str(AGORA if auth_date is None else auth_date),
        "query_id": "AAFAKEQUERY",
    }
    if user is not False:
        campos["user"] = json.dumps(user if user is not None else {"id": USER_ID, "first_name": "Teste"})
    if extras:
        campos.update(extras)
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(campos.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    campos["hash"] = hash_override or hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(campos)


class TestValidacao(unittest.TestCase):
    def test_valido_devolve_user(self):
        user = validate_init_data(montar_init_data(), TOKEN_FALSO, now=AGORA)
        self.assertEqual(user["id"], USER_ID)

    def test_campo_adulterado(self):
        pares = dict(parse_qsl(montar_init_data()))
        pares["user"] = json.dumps({"id": 999})
        with self.assertRaises(InitDataError):
            validate_init_data(urlencode(pares), TOKEN_FALSO, now=AGORA)

    def test_hash_errado(self):
        with self.assertRaises(InitDataError):
            validate_init_data(montar_init_data(hash_override="0" * 64), TOKEN_FALSO, now=AGORA)

    def test_hash_ausente(self):
        pares = dict(parse_qsl(montar_init_data()))
        pares.pop("hash")
        with self.assertRaises(InitDataError):
            validate_init_data(urlencode(pares), TOKEN_FALSO, now=AGORA)

    def test_token_diferente(self):
        with self.assertRaises(InitDataError):
            validate_init_data(montar_init_data(), "999:outro-token-falso", now=AGORA)

    def test_expirado(self):
        init_data = montar_init_data(auth_date=AGORA - 3601)
        with self.assertRaises(InitDataError):
            validate_init_data(init_data, TOKEN_FALSO, now=AGORA)

    def test_auth_date_no_futuro(self):
        init_data = montar_init_data(auth_date=AGORA + 120)
        with self.assertRaises(InitDataError):
            validate_init_data(init_data, TOKEN_FALSO, now=AGORA)

    def test_sem_user(self):
        with self.assertRaises(InitDataError):
            validate_init_data(montar_init_data(user=False), TOKEN_FALSO, now=AGORA)

    def test_user_id_invalido(self):
        for user in ({"id": True}, {"id": "1"}, {"nome": "x"}):
            with self.assertRaises(InitDataError):
                validate_init_data(montar_init_data(user=user), TOKEN_FALSO, now=AGORA)

    def test_chave_duplicada(self):
        init_data = montar_init_data() + "&auth_date=1"
        with self.assertRaises(InitDataError):
            validate_init_data(init_data, TOKEN_FALSO, now=AGORA)


class TestApi(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.execute("CREATE TABLE usuarios (user_id INTEGER, nome TEXT)")
        self.conn.execute("INSERT INTO usuarios VALUES (?, ?)", (USER_ID, "Ygor"))
        self.conn.commit()
        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://falso", "falso")
        app.dependency_overrides[obter_conexao_db] = lambda: self.conn
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.conn.close()

    def _cabecalho(self, init_data):
        return {"Authorization": "tma " + init_data}

    def test_saude(self):
        resposta = self.client.get("/saude")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"status": "ok"})

    def test_me_sem_cabecalho(self):
        resposta = self.client.get("/me")
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": "nao_autorizado"})

    def test_me_esquema_errado(self):
        resposta = self.client.get("/me", headers={"Authorization": "Bearer abc"})
        self.assertEqual(resposta.status_code, 401)

    def test_me_adulterado(self):
        pares = dict(parse_qsl(montar_init_data(auth_date=self._agora())))
        pares["query_id"] = "ADULTERADO"
        resposta = self.client.get("/me", headers=self._cabecalho(urlencode(pares)))
        self.assertEqual(resposta.status_code, 401)

    def test_me_valido(self):
        init_data = montar_init_data(auth_date=self._agora())
        resposta = self.client.get("/me", headers=self._cabecalho(init_data))
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"user_id": USER_ID, "nome": "Ygor"})

    def test_me_usuario_nao_cadastrado(self):
        init_data = montar_init_data(auth_date=self._agora(), user={"id": 777})
        resposta = self.client.get("/me", headers=self._cabecalho(init_data))
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json(), {"detail": "usuario_nao_cadastrado"})

    @staticmethod
    def _agora():
        import time
        return int(time.time())


if __name__ == "__main__":
    unittest.main(verbosity=2)
