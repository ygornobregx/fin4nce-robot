"""Testes das rotas de registro da API (PRO-88): POST /registro/interpretar,
POST /gastos e POST /receitas.

Roda com `python testes_rotas_registro.py`. Não usa .env, rede nem banco
real: o token é falso (initData assinado no próprio teste), a IA é falsa
(registro.definir_cliente_ia), as esperas das novas tentativas não dormem e
o banco é um SQLite em ARQUIVO temporário, com uma conexão nova por
requisição (como a API real), para os pedidos em duas threads disputarem o
banco de verdade. A tabela chaves_registro é criada com o MESMO comando do
bot.py (lido do código-fonte), para o esquema do teste não divergir.

O "agora" da API é 06/11/2026 01:30 em UTC, ou seja, 05/11/2026 22:30 em
Brasília: a data gravada tem de ser 2026-11-05 (em UTC seria 2026-11-06).
Fechamento dia 28: a fatura vigente é 2026-11.
"""
import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from fastapi.testclient import TestClient  # noqa: E402

import calculos  # noqa: E402
import registro  # noqa: E402
from api import main as api_main  # noqa: E402
from api.main import (  # noqa: E402
    Config, LimiteDeChamadas, app, obter_agora, obter_config, obter_conexao_db, obter_limite_interpretacao,
)
from testes_initdata import TOKEN_FALSO, montar_init_data  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402

UID = 4242          # usuário do initData padrão (testes_initdata.USER_ID)
OUTRO_UID = 5151    # segundo usuário cadastrado
SEM_CADASTRO = 777
INCOMPLETO = 888    # cadastrado sem dia de fechamento

AGORA_UTC = datetime(2026, 11, 6, 1, 30, tzinfo=timezone.utc)   # 05/11/2026 22:30 em Brasília
MOMENTO_BRASILIA = '2026-11-05 22:30:00'
DATA_BRASILIA = '2026-11-05'

CHAVE = '3f2b8c1e-9d4a-4b7e-8f21-6a5c0d9e1b23'
CHAVE_2 = 'a1b2c3d4-e5f6-4a7b-9c8d-0e1f2a3b4c5d'

# Os avisos "Registro recusado: <código>" não poluem a saída (o assertLogs
# continua vendo tudo).
logging.getLogger("api").addHandler(logging.NullHandler())

MENSAGEM_LIBSQL = ('Hrana: stream error: `Error { message: "SQLite error: UNIQUE constraint failed: '
                   'chaves_registro.user_id, chaves_registro.chave", code: "SQLITE_CONSTRAINT" }`')


def comando_chaves_registro():
    """O CREATE TABLE chaves_registro exatamente como está no bot.py."""
    with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
        fonte = f.read()
    achado = re.search(r"'''(CREATE TABLE IF NOT EXISTS chaves_registro \(.*?\))'''", fonte, re.DOTALL)
    assert achado, "CREATE TABLE chaves_registro não encontrado no bot.py"
    return achado.group(1)


def criar_banco(caminho):
    conn = sqlite3.connect(caminho)
    conn.execute("PRAGMA synchronous = OFF")      # só velocidade na montagem do banco
    conn.execute("PRAGMA journal_mode = MEMORY")  # (vale só para esta conexão)
    conn.executescript(ESQUEMA)
    conn.execute(comando_chaves_registro())
    for user_id, nome, renda, dia in ((UID, 'Ygor', 5000.0, 28), (OUTRO_UID, 'Outro', 1000.0, 10),
                                      (INCOMPLETO, 'Sem Fechamento', 1000.0, None)):
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, ?, ?, ?)",
                     (user_id, nome, renda, dia))
    for user_id, nome in ((UID, 'Alimentação'), (UID, 'Casa'), (UID, 'Roupas'), (OUTRO_UID, 'Lazer')):
        conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                     "VALUES (?, ?, 0, 0, '2026-01-01 10:00:00')", (user_id, nome))
    conn.commit()
    conn.close()


class ErroFalsoDaIA(Exception):
    pass


class IAFalsa:
    """Grava o modelo e o prompt de cada chamada e devolve as respostas
    roteirizadas (texto, objeto com .text) ou levanta as exceções da lista."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.models = self

    def generate_content(self, model, contents):
        self.chamadas.append((model, contents))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, str):
            return SimpleNamespace(text=resposta)
        return resposta


def resposta_gasto(valor=20, categoria='alimentação', descricao='Pão na Padaria', estabelecimento=None, **extra):
    dados = {'tipo': 'despesa', 'valor': valor, 'categoria': categoria, 'descricao': descricao,
             'estabelecimento': estabelecimento}
    dados.update(extra)
    return json.dumps(dados, ensure_ascii=False)


def resposta_receita(valor=500, descricao='Venda da Bicicleta'):
    return json.dumps({'tipo': 'receita', 'valor': valor, 'categoria': None, 'descricao': descricao,
                       'estabelecimento': None}, ensure_ascii=False)


class ConexaoEmbrulhada:
    """Conexão SQLite que imita o libsql: a chave repetida vira ValueError com
    a mensagem do Hrana (no lugar do sqlite3.IntegrityError). Com
    'falha_na_chave', o INSERT em chaves_registro levanta um ValueError que
    NÃO é de chave repetida (a rede caiu), sem gravar nada.

    'plano' (PlanoDeFalhas, compartilhado entre as conexões das requisições)
    injeta falhas no commit e na consulta da chave, e conta as chamadas."""

    def __init__(self, real, falha_na_chave=False, plano=None):
        self.real = real
        self.falha_na_chave = falha_na_chave
        self.plano = plano

    def _executar(self, alvo, sql, params=()):
        if self.falha_na_chave and sql.lstrip().startswith('INSERT INTO chaves_registro'):
            raise ValueError('Hrana: stream error: conexão encerrada')
        if self.plano is not None:
            self.plano.antes_de_executar(sql)
        try:
            return alvo.execute(sql, params)
        except sqlite3.IntegrityError as erro:
            if 'chaves_registro' in str(erro):
                raise ValueError(MENSAGEM_LIBSQL) from None
            raise

    def execute(self, sql, params=()):
        return self._executar(self.real, sql, params)

    def cursor(self):
        conexao = self
        real = self.real.cursor()

        class Cursor:
            def execute(self, sql, params=()):
                conexao._executar(real, sql, params)
                return self

            def fetchone(self):
                return real.fetchone()

            def fetchall(self):
                return real.fetchall()

            @property
            def lastrowid(self):
                return real.lastrowid

        return Cursor()

    def commit(self):
        if self.plano is not None:
            falha = self.plano.proxima_falha_do_commit()
            if callable(falha):
                falha = falha(self)  # pode só segurar o commit e devolver None
            if falha is not None:
                self.real.rollback()  # como o Turso: a transação foi desfeita no servidor
                raise falha
        self.real.commit()
        if self.plano is not None:
            self.plano.commitou.set()

    def rollback(self):
        self.real.rollback()

    def close(self):
        self.real.close()


ERRO_BUSY = ('Hrana: stream error: SQLite error: interactive transaction was rolled back because the stream '
             'was idle for too long; retry the transaction, code: SQLITE_BUSY')
CONSULTA_CHAVE = 'SELECT tipo, lancamento_id FROM chaves_registro'


class PlanoDeFalhas:
    """Falhas roteirizadas, na ordem em que acontecem, em todas as conexões:
    'falhas_commit' (cada item: None = commit normal; exceção = levantada
    depois de desfazer; função(conexao) = chamada antes, e o que ela devolver,
    se não for None, é levantado depois de desfazer) e 'falhas_consulta' (consultas da chave; None = normal).
    Conta os commits, as consultas da chave e os INSERT em transacoes."""

    def __init__(self, falhas_commit=(), falhas_consulta=()):
        self.falhas_commit = list(falhas_commit)
        self.falhas_consulta = list(falhas_consulta)
        self.commits = 0
        self.consultas = 0
        self.insercoes_transacao = 0
        self.commitou = threading.Event()
        self.trava = threading.Lock()

    def proxima_falha_do_commit(self):
        with self.trava:
            self.commits += 1
            return self.falhas_commit.pop(0) if self.falhas_commit else None

    def antes_de_executar(self, sql):
        sql = sql.lstrip()
        with self.trava:
            if sql.startswith('INSERT INTO transacoes'):
                self.insercoes_transacao += 1
            if not sql.startswith(CONSULTA_CHAVE):
                return
            self.consultas += 1
            falha = self.falhas_consulta.pop(0) if self.falhas_consulta else None
        if falha is not None:
            raise falha


class TesteRegistroBase(unittest.TestCase):
    """Banco em arquivo, uma conexão por requisição, IA falsa, esperas sem
    dormir, "agora" fixo e limite de chamadas novo a cada teste."""
    embrulho = None  # função que embrulha a conexão de cada requisição

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.caminho = os.path.join(self.pasta.name, 'teste.db')
        criar_banco(self.caminho)
        self.limite = LimiteDeChamadas(maximo=10, janela=60.0)

        def conexao():
            conn = sqlite3.connect(self.caminho, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA synchronous = OFF")  # só velocidade: as travas do arquivo continuam as mesmas
            if self.embrulho is not None:
                conn = self.embrulho(conn)
            try:
                yield conn
            finally:
                conn.close()

        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://falso", "falso")
        app.dependency_overrides[obter_conexao_db] = conexao
        app.dependency_overrides[obter_agora] = lambda: AGORA_UTC
        app.dependency_overrides[obter_limite_interpretacao] = lambda: self.limite
        self.client = TestClient(app)

        self.cliente_anterior = registro._cliente_ia
        self.esperas = []

        async def sleep_falso(segundos, result=None):
            self.esperas.append(segundos)
            return result

        # Só o asyncio visto pelo registro.py: o loop do TestClient continua normal.
        patcher = mock.patch.object(registro, 'asyncio', SimpleNamespace(sleep=sleep_falso, to_thread=asyncio.to_thread))
        patcher.start()
        self.addCleanup(patcher.stop)

        # Esperas entre as tentativas de gravar com o banco ocupado: gravadas, sem dormir.
        self.esperas_banco = []
        patcher = mock.patch.object(registro, '_esperar', self.esperas_banco.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        app.dependency_overrides.clear()
        registro.definir_cliente_ia(self.cliente_anterior)
        self.pasta.cleanup()

    def instalar(self, *respostas):
        ia = IAFalsa(*respostas)
        registro.definir_cliente_ia(ia)
        return ia

    def cabecalho(self, user_id=UID):
        init_data = montar_init_data(auth_date=int(time.time()), user={"id": user_id, "first_name": "Teste"})
        return {"Authorization": "tma " + init_data}

    def post(self, rota, corpo, user_id=UID, client=None):
        return (client or self.client).post(rota, json=corpo, headers=self.cabecalho(user_id))

    def ler(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def contar(self, tabela):
        return self.ler(f"SELECT COUNT(*) FROM {tabela}")[0][0]

    def assertErro(self, resposta, status, codigo):
        self.assertEqual(resposta.status_code, status, resposta.text)
        self.assertEqual(resposta.json(), {"detail": codigo})


def corpo_gasto(**mudancas):
    corpo = {"chave": CHAVE, "valor": 600.0, "descricao": "Geladeira", "estabelecimento": "Casas Bahia",
             "categoria": "Casa", "metodo_pagamento": "Crédito", "parcelas": 3}
    corpo.update(mudancas)
    return corpo


def corpo_receita(**mudancas):
    corpo = {"chave": CHAVE, "valor": 500.0, "descricao": "Venda da Bicicleta"}
    corpo.update(mudancas)
    return corpo


# --- AUTENTICAÇÃO E CADASTRO (as três rotas) ---
class TestAutenticacaoECadastro(TesteRegistroBase):
    ROTAS = (("/registro/interpretar", {"tipo": "gasto", "texto": "pão 20"}),
             ("/gastos", corpo_gasto()), ("/receitas", corpo_receita()))

    def test_sem_cabecalho_e_initdata_invalido_401(self):
        for rota, corpo in self.ROTAS:
            with self.subTest(rota=rota):
                self.assertErro(self.client.post(rota, json=corpo), 401, "nao_autorizado")
                adulterado = montar_init_data(auth_date=int(time.time()), hash_override="0" * 64)
                self.assertErro(self.client.post(rota, json=corpo, headers={"Authorization": "tma " + adulterado}),
                                401, "nao_autorizado")
                vencido = montar_init_data(auth_date=int(time.time()) - 3601)  # D10: 1 hora
                self.assertErro(self.client.post(rota, json=corpo, headers={"Authorization": "tma " + vencido}),
                                401, "nao_autorizado")
        self.assertEqual(self.contar("transacoes") + self.contar("receitas") + self.contar("chaves_registro"), 0)

    def test_usuario_nao_cadastrado_404_e_cadastro_incompleto_409(self):
        self.instalar()  # nenhuma chamada à IA é esperada
        for rota, corpo in self.ROTAS:
            with self.subTest(rota=rota):
                self.assertErro(self.post(rota, corpo, user_id=SEM_CADASTRO), 404, "usuario_nao_cadastrado")
                self.assertErro(self.post(rota, corpo, user_id=INCOMPLETO), 409, "cadastro_incompleto")
        self.assertEqual(self.contar("transacoes") + self.contar("receitas") + self.contar("chaves_registro"), 0)

    def test_corpo_que_nao_e_json_422_padrao(self):
        for rota, _ in self.ROTAS:
            with self.subTest(rota=rota):
                resposta = self.client.post(rota, content=b"isto nao e json",
                                            headers={**self.cabecalho(), "Content-Type": "application/json"})
                self.assertEqual(resposta.status_code, 422)
                self.assertIsInstance(resposta.json()["detail"], list)


# --- POST /registro/interpretar ---
class TestInterpretar(TesteRegistroBase):
    def interpretar(self, tipo="gasto", texto="pão na padaria 20", user_id=UID):
        return self.post("/registro/interpretar", {"tipo": tipo, "texto": texto}, user_id)

    def test_gasto_contrato_e_mesmo_prompt_do_bot(self):
        ia = self.instalar(resposta_gasto())
        resposta = self.interpretar(texto="  pão na padaria 20  ")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"tipo": "gasto", "valor": 20.0, "descricao": "Pão na Padaria",
                                           "estabelecimento": None, "categoria": "Alimentação",
                                           "metodo_pagamento": None})
        self.assertEqual(len(ia.chamadas), 1)
        modelo, prompt = ia.chamadas[0]
        self.assertEqual(modelo, registro.MODELO_IA)
        # Regra crítica 2 e D5: o prompt é o do bot, com as categorias do usuário e o texto aparado.
        self.assertEqual(prompt, registro.montar_prompt_gasto(['Alimentação', 'Casa', 'Roupas'], "pão na padaria 20"))
        self.assertEqual(self.contar("transacoes") + self.contar("chaves_registro"), 0)  # não grava

    def test_receita_contrato(self):
        self.instalar(resposta_receita())
        resposta = self.interpretar(tipo="receita", texto="vendi a bicicleta por 500")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"tipo": "receita", "valor": 500.0, "descricao": "Venda da Bicicleta"})
        self.assertEqual(self.contar("receitas"), 0)

    def test_valor_arredondado_textos_aparados_e_cortados(self):
        self.instalar(resposta_gasto(valor="1234.567", descricao="  " + "D" * 120, estabelecimento="  " + "E" * 70),
                      resposta_gasto(descricao="   ", estabelecimento="  "))
        corpo = self.interpretar().json()
        self.assertEqual(corpo["valor"], 1234.57)
        self.assertEqual(corpo["descricao"], "D" * 100)
        self.assertEqual(corpo["estabelecimento"], "E" * 60)
        corpo = self.interpretar().json()
        self.assertIsNone(corpo["descricao"])
        self.assertIsNone(corpo["estabelecimento"])

    def test_estabelecimento_preenchido(self):
        self.instalar(resposta_gasto(valor=12, descricao="Pão na Padaria Cisne Branco", estabelecimento="Cisne Branco"))
        corpo = self.interpretar(texto="pão na padaria cisne branco 12").json()
        self.assertEqual(corpo["estabelecimento"], "Cisne Branco")

    def test_texto_invalido(self):
        ia = self.instalar()
        for texto in ("", "    ", "x" * 201, None, 123, ["pão 20"]):
            with self.subTest(texto=texto):
                self.assertErro(self.interpretar(texto=texto), 422, "texto_invalido")
        self.assertEqual(ia.chamadas, [])

    def test_texto_com_200_caracteres_passa(self):
        ia = self.instalar(resposta_gasto())
        self.assertEqual(self.interpretar(texto="x" * 200).status_code, 200)
        self.assertEqual(len(ia.chamadas), 1)

    def test_tipo_invalido(self):
        ia = self.instalar()
        for tipo in ("despesa", "GASTO", "", None, 1):
            with self.subTest(tipo=tipo):
                self.assertErro(self.interpretar(tipo=tipo), 422, "tipo_invalido")
        self.assertEqual(ia.chamadas, [])

    def test_tipo_divergente_nos_dois_sentidos(self):
        self.instalar(resposta_receita(valor=200), resposta_gasto())
        self.assertErro(self.interpretar(tipo="gasto", texto="recebi 200 de reembolso"), 422, "tipo_divergente")
        self.assertErro(self.interpretar(tipo="receita", texto="pão 20"), 422, "tipo_divergente")

    def test_valor_nao_entendido(self):
        for valor in ("abc", None, 0, -5, 1_000_000.01, True, [20]):
            with self.subTest(valor=valor):
                self.instalar(resposta_gasto(valor=valor))
                self.assertErro(self.interpretar(), 422, "valor_nao_entendido")
        self.instalar(resposta_receita(valor="nada"))
        self.assertErro(self.interpretar(tipo="receita", texto="ganhei um presente"), 422, "valor_nao_entendido")

    def test_valor_no_teto_passa(self):
        self.instalar(resposta_gasto(valor=1_000_000))
        self.assertEqual(self.interpretar().json()["valor"], 1_000_000.0)

    def test_categoria_nao_encontrada(self):
        for categoria in ("Viagem", None, "Lazer"):  # Lazer é do outro usuário
            with self.subTest(categoria=categoria):
                self.instalar(resposta_gasto(categoria=categoria))
                self.assertErro(self.interpretar(), 422, "categoria_nao_encontrada")

    def test_resposta_ia_invalida(self):
        casos = ["Não entendi a mensagem.", '{"valor": 10}', '{"tipo": "despesa", "categoria": "Casa"}',
                 '{"tipo": "transferencia", "valor": 10}', '{"tipo": "despesa", "valor": 10}', '[1, 2]',
                 '{"tipo": 5, "valor": 10}', SimpleNamespace(text=None)]
        for caso in casos:
            with self.subTest(caso=caso):
                self.instalar(caso)
                self.assertErro(self.interpretar(), 502, "resposta_ia_invalida")

    def test_ia_indisponivel_depois_das_novas_tentativas_sem_detalhes(self):
        segredo = "503 UNAVAILABLE detalhe-interno-da-ia"
        ia = self.instalar(ErroFalsoDaIA(segredo), ErroFalsoDaIA(segredo), ErroFalsoDaIA(segredo))
        with self.assertLogs("api", level="WARNING") as logs:
            resposta = self.interpretar(texto="texto-privado-do-usuario 20")
        self.assertErro(resposta, 503, "ia_indisponivel")
        self.assertEqual(len(ia.chamadas), 3)
        self.assertEqual(self.esperas, [2, 4])
        self.assertNotIn("detalhe-interno", resposta.text)
        saida_log = "\n".join(logs.output)
        self.assertIn("ia_indisponivel", saida_log)
        self.assertNotIn("detalhe-interno", saida_log)
        self.assertNotIn("texto-privado", saida_log)

    def test_ia_429_e_erro_comum_tambem_503(self):
        self.instalar(ErroFalsoDaIA("429"), ErroFalsoDaIA("429"), ErroFalsoDaIA("429 RESOURCE_EXHAUSTED"))
        self.assertErro(self.interpretar(), 503, "ia_indisponivel")
        self.instalar(RuntimeError("400 INVALID_ARGUMENT chave=abc"))
        resposta = self.interpretar()
        self.assertErro(resposta, 503, "ia_indisponivel")
        self.assertNotIn("abc", resposta.text)

    def test_sem_chave_da_ia_503_e_o_resto_da_api_funciona(self):
        registro.definir_cliente_ia(None)

        class ClienteSemChave:
            def __init__(self):
                raise ValueError("Missing key inputs argument! detalhe-interno")

        genai_falso = types.ModuleType('google.genai')
        genai_falso.Client = ClienteSemChave
        google_falso = types.ModuleType('google')
        google_falso.genai = genai_falso
        with mock.patch.dict(sys.modules, {'google': google_falso, 'google.genai': genai_falso}):
            resposta = self.interpretar()
            self.assertErro(resposta, 503, "ia_indisponivel")
            self.assertNotIn("detalhe-interno", resposta.text)
            # As gravações e as leituras não dependem da IA.
            self.assertEqual(self.post("/gastos", corpo_gasto()).status_code, 201)
            self.assertEqual(self.client.get("/resumo", headers=self.cabecalho()).status_code, 200)

    def test_obter_config_nao_exige_a_chave_da_ia(self):
        api_main.obter_config.cache_clear()
        ambiente = {"TOKEN": "t", "TURSO_DATABASE_URL": "libsql://x", "TURSO_AUTH_TOKEN": "y"}
        try:
            with mock.patch.dict(os.environ, ambiente, clear=True), \
                    mock.patch.object(api_main, "load_dotenv", lambda *a, **k: False):
                self.assertEqual(api_main.obter_config(), Config("t", "libsql://x", "y"))
        finally:
            api_main.obter_config.cache_clear()


# --- LIMITE DE CHAMADAS (D8) ---
class TestLimite(TesteRegistroBase):
    def setUp(self):
        super().setUp()
        self.relogio = [1000.0]
        self.limite = LimiteDeChamadas(maximo=10, janela=60.0, relogio=lambda: self.relogio[0])

    def interpretar(self, user_id=UID, texto="pão 20"):
        return self.post("/registro/interpretar", {"tipo": "gasto", "texto": texto}, user_id)

    def test_onze_chamadas_no_minuto_a_ultima_429_com_retry_after(self):
        ia = self.instalar(*[resposta_gasto()] * 11)
        for segundo in range(10):
            self.relogio[0] = 1000.0 + segundo
            self.assertEqual(self.interpretar().status_code, 200)
        self.relogio[0] = 1030.5
        resposta = self.interpretar()
        self.assertErro(resposta, 429, "limite_excedido")
        self.assertEqual(resposta.headers["Retry-After"], "30")  # a 1ª (1000) sai da janela em 1060
        self.assertEqual(len(ia.chamadas), 10)  # o 429 não chama a IA
        # Janela deslizante: em 1060 a primeira saiu, cabe mais uma.
        self.relogio[0] = 1060.0
        self.assertEqual(self.interpretar().status_code, 200)

    def test_limite_por_usuario(self):
        self.instalar(*[resposta_gasto()] * 10 + [resposta_gasto(categoria="Lazer")])
        for _ in range(10):
            self.assertEqual(self.interpretar().status_code, 200)
        self.assertErro(self.interpretar(), 429, "limite_excedido")
        self.assertEqual(self.interpretar(user_id=OUTRO_UID).status_code, 200)

    def test_pedido_invalido_e_gravacoes_nao_contam(self):
        self.instalar(*[resposta_gasto()] * 10)
        for _ in range(15):
            self.assertErro(self.interpretar(texto=""), 422, "texto_invalido")
        for indice in range(15):
            chave = f"00000000-0000-4000-8000-{indice:012d}"
            self.assertEqual(self.post("/gastos", corpo_gasto(chave=chave)).status_code, 201)
        for _ in range(10):
            self.assertEqual(self.interpretar().status_code, 200)

    def test_retry_after_minimo_1(self):
        limite = LimiteDeChamadas(maximo=1, janela=60.0, relogio=lambda: self.relogio[0])
        self.assertIsNone(limite.registrar(UID))
        self.relogio[0] = 1059.99
        self.assertEqual(limite.registrar(UID), 1)


# --- POST /gastos ---
class TestGravarGasto(TesteRegistroBase):
    def gasto(self, **mudancas):
        return self.post("/gastos", corpo_gasto(**mudancas))

    def transacoes(self):
        return self.ler("SELECT id, user_id, categoria_nome, valor, data_transacao, metodo_pagamento, descricao, "
                        "parcelamento_id, estabelecimento FROM transacoes ORDER BY id")

    def chaves(self):
        return self.ler("SELECT user_id, chave, tipo, lancamento_id, criado_em FROM chaves_registro ORDER BY rowid")

    def test_parcelado_contrato_e_linhas(self):
        resposta = self.gasto()
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json(), {
            "id": 1, "tipo": "gasto", "valor": 600.0, "valor_parcela": 200.0, "parcelas": 3,
            "descricao": "Geladeira", "estabelecimento": "Casas Bahia", "categoria": "Casa",
            "metodo_pagamento": "Crédito", "data": DATA_BRASILIA, "repetido": False})
        self.assertEqual(self.transacoes(),
                         [(1, UID, 'Casa', 200.0, MOMENTO_BRASILIA, 'Crédito', 'Geladeira', 1, 'Casas Bahia')])
        self.assertEqual(
            self.ler("SELECT user_id, categoria_nome, descricao, valor_total, valor_parcela, total_parcelas, "
                     "parcelas_pagas, data_criacao, ciclo_referencia, ativo FROM parcelamentos"),
            [(UID, 'Casa', 'Geladeira', 600.0, 200.0, 3, 1, MOMENTO_BRASILIA, '2026-11', 1)])
        self.assertEqual(self.chaves(), [(UID, CHAVE, 'gasto', 1, MOMENTO_BRASILIA)])

    def test_debito_forca_uma_parcela(self):
        for indice, parcelas in enumerate((5, "abc", None, 0)):
            with self.subTest(parcelas=parcelas):
                chave = f"00000000-0000-4000-8000-00000000000{indice}"
                resposta = self.gasto(chave=chave, metodo_pagamento="Débito/Pix", parcelas=parcelas, valor=59.9)
                self.assertEqual(resposta.status_code, 201, resposta.text)
                corpo = resposta.json()
                self.assertEqual((corpo["parcelas"], corpo["valor"], corpo["valor_parcela"]), (1, 59.9, 59.9))
                self.assertEqual(corpo["metodo_pagamento"], "Débito/Pix")
        self.assertEqual(self.contar("parcelamentos"), 0)
        self.assertEqual({linha[5] for linha in self.transacoes()}, {'Débito/Pix'})

    def test_credito_1x_grava_a_vista(self):
        resposta = self.gasto(parcelas=1, valor=100)
        self.assertEqual(resposta.status_code, 201)
        corpo = resposta.json()
        self.assertEqual((corpo["parcelas"], corpo["valor"], corpo["valor_parcela"]), (1, 100.0, 100.0))
        self.assertEqual(self.contar("parcelamentos"), 0)
        self.assertEqual(self.transacoes(),
                         [(1, UID, 'Casa', 100.0, MOMENTO_BRASILIA, 'Crédito', 'Geladeira', None, 'Casas Bahia')])

    def test_parcelas_de_2_a_12(self):
        for indice, (parcelas, valor, parcela) in enumerate(((2, 80.0, 40.0), (7, 100.0, 14.29), (12, 1200.0, 100.0))):
            with self.subTest(parcelas=parcelas):
                corpo = self.gasto(chave=f"00000000-0000-4000-8000-00000000000{indice}", parcelas=parcelas,
                                   valor=valor).json()
                self.assertEqual((corpo["parcelas"], corpo["valor"], corpo["valor_parcela"]), (parcelas, valor, parcela))

    def test_descricao_e_estabelecimento_aparados_vazios_viram_null(self):
        corpo = self.gasto(descricao="   ", estabelecimento="  ", parcelas=1).json()
        self.assertIsNone(corpo["descricao"])
        self.assertIsNone(corpo["estabelecimento"])
        corpo = self.gasto(chave=CHAVE_2, descricao="  Geladeira  ", estabelecimento=" Casas Bahia ").json()
        self.assertEqual((corpo["descricao"], corpo["estabelecimento"]), ("Geladeira", "Casas Bahia"))

    def test_limites_de_tamanho_e_teto_aceitos(self):
        corpo = self.gasto(descricao="D" * 100, estabelecimento="E" * 60, valor=1_000_000, parcelas=1).json()
        self.assertEqual((len(corpo["descricao"]), len(corpo["estabelecimento"]), corpo["valor"]),
                         (100, 60, 1_000_000.0))

    def test_valor_arredondado_a_duas_casas(self):
        self.assertEqual(self.gasto(valor=10.006, parcelas=1).json()["valor"], 10.01)

    def assertRecusado(self, codigo, **mudancas):
        self.assertErro(self.gasto(**mudancas), 422, codigo)
        self.assertEqual(self.contar("transacoes") + self.contar("parcelamentos") + self.contar("chaves_registro"), 0)

    def test_chave_invalida(self):
        for chave in (None, "", "abc", 123, "3f2b8c1e-9d4a-1b7e-8f21-6a5c0d9e1b23",  # versão 1
                      "3f2b8c1e-9d4a-4b7e-7f21-6a5c0d9e1b23", CHAVE + "0", " " + CHAVE):
            with self.subTest(chave=chave):
                self.assertRecusado("chave_invalida", chave=chave)

    def test_chave_em_maiusculas_vale_a_mesma(self):
        self.assertEqual(self.gasto(chave=CHAVE.upper()).status_code, 201)
        resposta = self.gasto(chave=CHAVE)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.json()["repetido"])
        self.assertEqual(self.chaves()[0][1], CHAVE)

    def test_valor_invalido(self):
        for valor in (0, -1, -0.0, 0.004, "600", True, None, 1_000_000.01, 1e12, [600]):
            with self.subTest(valor=valor):
                self.assertRecusado("valor_invalido", valor=valor)

    def test_descricao_longa_e_estabelecimento_longo(self):
        self.assertRecusado("descricao_longa", descricao="D" * 101)
        self.assertRecusado("estabelecimento_longo", estabelecimento="E" * 61)

    def test_descricao_que_nao_e_texto_422_padrao(self):
        resposta = self.gasto(descricao=123)
        self.assertEqual(resposta.status_code, 422)
        self.assertIsInstance(resposta.json()["detail"], list)
        self.assertEqual(self.contar("transacoes"), 0)

    def test_metodo_pagamento_invalido(self):
        for metodo in ("Pix", "Débito", "credito", None, "", 1):
            with self.subTest(metodo=metodo):
                self.assertRecusado("metodo_pagamento_invalido", metodo_pagamento=metodo)

    def test_parcelas_invalidas_no_credito(self):
        for parcelas in (0, 13, -1, "3", 2.0, True, None):
            with self.subTest(parcelas=parcelas):
                self.assertRecusado("parcelas_invalidas", parcelas=parcelas)

    def test_categoria_nao_encontrada(self):
        for categoria in ("Viagem", "casa", "Lazer", None, ""):  # Lazer é do outro usuário
            with self.subTest(categoria=categoria):
                self.assertRecusado("categoria_nao_encontrada", categoria=categoria)

    def test_categoria_apagada_no_meio_nada_fica_gravado(self):
        # A lista lida na validação ainda tem "Fantasma", mas ela já não existe no banco.
        with mock.patch.object(registro, "listar_categorias", lambda conn, user_id: ["Casa", "Fantasma"]):
            for parcelas in (1, 3):
                with self.subTest(parcelas=parcelas):
                    self.assertRecusado("categoria_nao_encontrada", categoria="Fantasma", parcelas=parcelas)

    def test_user_id_e_data_do_corpo_sao_ignorados(self):
        resposta = self.gasto(user_id=OUTRO_UID, data="2020-01-01", data_transacao="2020-01-01 10:00:00")
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json()["data"], DATA_BRASILIA)
        self.assertEqual([(linha[1], linha[4]) for linha in self.transacoes()], [(UID, MOMENTO_BRASILIA)])

    def test_toque_duplo_em_sequencia(self):
        primeira = self.gasto()
        segunda = self.gasto()
        self.assertEqual((primeira.status_code, segunda.status_code), (201, 200))
        esperado = dict(primeira.json(), repetido=True)
        self.assertEqual(segunda.json(), esperado)
        self.assertEqual((self.contar("transacoes"), self.contar("parcelamentos"), self.contar("chaves_registro")),
                         (1, 1, 1))

    def test_repetida_com_outro_corpo_devolve_o_original(self):
        self.gasto()
        resposta = self.gasto(valor=999.0, categoria="Viagem", parcelas=99)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual((resposta.json()["valor"], resposta.json()["categoria"]), (600.0, "Casa"))
        self.assertEqual(self.contar("transacoes"), 1)

    def test_toque_duplo_em_duas_threads(self):
        """Os dois pedidos passam pela consulta da chave (nenhum acha nada)
        antes de qualquer um gravar: só a chave primária decide."""
        original = registro.buscar_chave
        barreira = threading.Barrier(2, timeout=10)
        contagem = [0]
        trava = threading.Lock()

        def buscar_com_barreira(conn, user_id, chave):
            achado = original(conn, user_id, chave)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= 2:
                barreira.wait()
            return achado

        respostas = []

        def pedir():
            respostas.append(self.post("/gastos", corpo_gasto(), client=TestClient(app)))

        with mock.patch.object(registro, "buscar_chave", buscar_com_barreira):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 201])
        self.assertEqual(len({r.json()["id"] for r in respostas}), 1)
        self.assertEqual(sorted(r.json()["repetido"] for r in respostas), [False, True])
        self.assertEqual((self.contar("transacoes"), self.contar("parcelamentos"), self.contar("chaves_registro")),
                         (1, 1, 1))
        self.assertGreaterEqual(contagem[0], 3)  # o perdedor consultou a chave de novo depois do erro

    def test_chave_de_outro_usuario_nunca_devolve_o_lancamento_alheio(self):
        primeira = self.gasto()
        outra = self.post("/gastos", corpo_gasto(categoria="Lazer", descricao="Cinema", estabelecimento=None,
                                                 valor=40.0, parcelas=1), user_id=OUTRO_UID)
        self.assertEqual(outra.status_code, 201)
        self.assertNotEqual(outra.json()["id"], primeira.json()["id"])
        self.assertEqual(outra.json()["descricao"], "Cinema")
        self.assertEqual(self.ler("SELECT user_id, chave FROM chaves_registro ORDER BY user_id"),
                         [(UID, CHAVE), (OUTRO_UID, CHAVE)])

    def test_chave_usada_numa_receita_da_chave_invalida(self):
        self.assertEqual(self.post("/receitas", corpo_receita()).status_code, 201)
        self.assertErro(self.gasto(), 422, "chave_invalida")
        self.assertEqual(self.contar("transacoes"), 0)

    def test_lancamento_da_chave_apagado_da_chave_invalida(self):
        self.gasto(parcelas=1)
        conn = sqlite3.connect(self.caminho)
        conn.execute("DELETE FROM transacoes")
        conn.commit()
        conn.close()
        self.assertErro(self.gasto(parcelas=1), 422, "chave_invalida")


# --- CHAVE REPETIDA COMO NO LIBSQL (ValueError) E ERRO QUE NÃO É CHAVE ---
class TestChaveRepetidaPorClasseDeErro(TesteRegistroBase):
    def pular_a_primeira_consulta(self):
        """A consulta antes de gravar não acha a chave (como se o outro pedido
        ainda não tivesse gravado); as seguintes são as reais."""
        original = registro.buscar_chave
        chamadas = [0]

        def buscar(conn, user_id, chave):
            chamadas[0] += 1
            return None if chamadas[0] == 1 else original(conn, user_id, chave)

        return mock.patch.object(registro, "buscar_chave", buscar)

    def repetir(self, rota, corpo):
        self.assertEqual(self.post(rota, corpo).status_code, 201)
        with self.pular_a_primeira_consulta():
            resposta = self.post(rota, corpo)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertTrue(resposta.json()["repetido"])
        return resposta

    def test_sqlite_integrity_error(self):
        self.repetir("/gastos", corpo_gasto())
        self.repetir("/receitas", corpo_receita(chave=CHAVE_2))
        self.assertEqual((self.contar("transacoes"), self.contar("parcelamentos"), self.contar("receitas"),
                          self.contar("chaves_registro")), (1, 1, 1, 2))

    def test_libsql_value_error(self):
        self.embrulho = ConexaoEmbrulhada
        self.repetir("/gastos", corpo_gasto())
        self.repetir("/receitas", corpo_receita(chave=CHAVE_2))
        self.assertEqual((self.contar("transacoes"), self.contar("parcelamentos"), self.contar("receitas"),
                          self.contar("chaves_registro")), (1, 1, 1, 2))

    def test_erro_que_nao_e_chave_repetida_sobe_500_sem_detalhes(self):
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, falha_na_chave=True)
        client = TestClient(app, raise_server_exceptions=False)
        for rota, corpo in (("/gastos", corpo_gasto()), ("/gastos", corpo_gasto(parcelas=1)),
                            ("/receitas", corpo_receita())):
            with self.subTest(rota=rota, corpo=corpo):
                resposta = self.post(rota, corpo, client=client)
                self.assertEqual(resposta.status_code, 500)
                self.assertEqual(resposta.json(), {"detail": "erro_interno"})
                self.assertNotIn("Hrana", resposta.text)
                self.assertNotIn("Geladeira", resposta.text)
        self.assertEqual((self.contar("transacoes"), self.contar("parcelamentos"), self.contar("receitas"),
                          self.contar("chaves_registro")), (0, 0, 0, 0))
        self.assertEqual(self.esperas_banco, [])  # "stream error" sozinho não é passageiro


# --- BANCO OCUPADO: NOVAS TENTATIVAS (defeito do teste humano, passo 5) ---
class TestBancoOcupado(TesteRegistroBase):
    def planejar(self, falhas_commit=(), falhas_consulta=()):
        self.plano = PlanoDeFalhas(falhas_commit, falhas_consulta)
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=self.plano)
        return self.plano

    def contagens(self):
        return (self.contar("transacoes"), self.contar("parcelamentos"), self.contar("receitas"),
                self.contar("chaves_registro"))

    def test_commit_busy_uma_vez_tenta_de_novo_e_grava(self):
        plano = self.planejar([ValueError(ERRO_BUSY)])
        resposta = self.post("/gastos", corpo_gasto())
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertFalse(resposta.json()["repetido"])
        self.assertEqual(self.contagens(), (1, 1, 0, 1))
        self.assertEqual(self.ler("SELECT lancamento_id FROM chaves_registro"), [(resposta.json()["id"],)])
        self.assertEqual((plano.commits, plano.insercoes_transacao), (2, 2))
        self.assertEqual(self.esperas_banco, [0.25])

    def test_commit_busy_e_a_chave_aparece_devolve_repetido(self):
        def outro_pedido_gravou(conexao):
            # O servidor desfez a transação deste pedido e, enquanto isso, o
            # outro pedido com a mesma chave gravou.
            conexao.real.rollback()
            outra = sqlite3.connect(self.caminho)
            id_gasto = outra.execute(
                "INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, metodo_pagamento, descricao, "
                "estabelecimento) VALUES (?, 'Casa', 600.0, ?, 'Crédito', 'Geladeira', 'Casas Bahia')",
                (UID, MOMENTO_BRASILIA)).lastrowid
            outra.execute("INSERT INTO chaves_registro VALUES (?, ?, 'gasto', ?, ?)",
                          (UID, CHAVE, id_gasto, MOMENTO_BRASILIA))
            outra.commit()
            outra.close()
            return ValueError(ERRO_BUSY)

        self.planejar([outro_pedido_gravou])
        resposta = self.post("/gastos", corpo_gasto(parcelas=1))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertTrue(resposta.json()["repetido"])
        self.assertEqual(self.contagens(), (1, 0, 0, 1))
        self.assertEqual(self.ler("SELECT lancamento_id FROM chaves_registro"), [(resposta.json()["id"],)])
        self.assertEqual(self.esperas_banco, [])

    def test_busy_em_todas_as_tentativas_503_banco_ocupado(self):
        for rota, corpo in (("/gastos", corpo_gasto()), ("/receitas", corpo_receita())):
            with self.subTest(rota=rota):
                self.esperas_banco.clear()
                plano = self.planejar([ValueError(ERRO_BUSY)] * 4)
                with self.assertLogs("api", level="WARNING") as logs:
                    resposta = self.post(rota, corpo)
                self.assertErro(resposta, 503, "banco_ocupado")
                self.assertEqual(self.contagens(), (0, 0, 0, 0))
                self.assertEqual(plano.commits, 4)
                self.assertEqual(self.esperas_banco, [0.25, 0.5, 1.0])
                self.assertNotIn("Hrana", "\n".join(logs.output))

    def test_database_is_locked_do_sqlite_tambem_e_passageiro(self):
        self.planejar([sqlite3.OperationalError("database is locked")])
        self.assertEqual(self.post("/receitas", corpo_receita()).status_code, 201)
        self.assertEqual(self.contagens(), (0, 0, 1, 1))
        self.assertEqual(self.esperas_banco, [0.25])

    def test_consulta_da_chave_falha_uma_vez_e_tratada_como_passageira(self):
        # 1ª consulta = a de antes de gravar (normal); 2ª = a do except (falha).
        plano = self.planejar([ValueError("Hrana: stream error: conexão encerrada")],
                              [None, ValueError("Hrana: stream error: stream quebrado")])
        resposta = self.post("/gastos", corpo_gasto())
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.contagens(), (1, 1, 0, 1))
        self.assertEqual(plano.commits, 2)
        self.assertEqual(self.esperas_banco, [0.25])

    def test_erro_nao_passageiro_500_erro_interno_sem_nova_tentativa(self):
        client = TestClient(app, raise_server_exceptions=False)
        for rota, corpo in (("/gastos", corpo_gasto()), ("/receitas", corpo_receita())):
            with self.subTest(rota=rota):
                plano = self.planejar([ValueError("boom: Hrana detalhe-do-banco")])
                with self.assertLogs("api", level="ERROR") as logs:
                    resposta = self.post(rota, corpo, client=client)
                self.assertEqual(resposta.status_code, 500)
                self.assertEqual(resposta.json(), {"detail": "erro_interno"})
                self.assertNotIn("boom", resposta.text)
                self.assertNotIn("Hrana", resposta.text)
                saida_log = "\n".join(logs.output)
                self.assertIn("Registro falhou: erro_interno (ValueError)", saida_log)
                self.assertNotIn("boom", saida_log)
                self.assertNotIn("detalhe-do-banco", saida_log)
                self.assertEqual(self.contagens(), (0, 0, 0, 0))
                self.assertEqual(plano.commits, 1)
                self.assertEqual(self.esperas_banco, [])

    def test_erro_inesperado_na_interpretacao_500_erro_interno(self):
        self.instalar(resposta_gasto())
        client = TestClient(app, raise_server_exceptions=False)
        with mock.patch.object(registro, "listar_categorias", side_effect=ValueError("boom do banco")):
            resposta = client.post("/registro/interpretar", json={"tipo": "gasto", "texto": "pão 20"},
                                   headers=self.cabecalho())
        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta.json(), {"detail": "erro_interno"})
        self.assertNotIn("boom", resposta.text)

    def test_duas_threads_commit_do_segundo_falha_com_busy_antes_do_primeiro_commitar(self):
        """Reproduz o passo 5 do teste humano. Os dois pedidos passam pela
        consulta da chave sem achar nada. O primeiro commit a acontecer (pedido
        A) falha com SQLITE_BUSY: o servidor desfez a transação. O pedido B
        grava, mas só commita DEPOIS que A desfez, consultou a chave e NÃO a
        achou (B ainda não tinha commitado). Antes da correção, A dava 500
        nesse ponto. Agora A espera, tenta de novo, bate na chave primária de
        B e devolve repetido."""
        a_nao_achou = threading.Event()

        def falha_imediata(conexao):
            return ValueError(ERRO_BUSY)

        def segura_o_commit_de_b(conexao):
            a_nao_achou.wait(5)  # sem a correção, nunca acontece: B commita depois de 5 s
            return None

        def esperar(segundos):
            self.esperas_banco.append(segundos)
            a_nao_achou.set()          # A já desfez, consultou e não achou a chave de B
            plano.commitou.wait(5)     # dá tempo de B commitar antes da nova tentativa

        plano = self.planejar([falha_imediata, segura_o_commit_de_b])
        original = registro.buscar_chave
        barreira = threading.Barrier(2, timeout=10)
        contagem = [0]
        trava = threading.Lock()

        def buscar_com_barreira(conn, user_id, chave):
            achado = original(conn, user_id, chave)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= 2:
                barreira.wait()
            return achado

        respostas = []

        def pedir():
            respostas.append(self.post("/gastos", corpo_gasto(), client=TestClient(app)))

        with mock.patch.object(registro, "buscar_chave", buscar_com_barreira), \
                mock.patch.object(registro, "_esperar", esperar):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 201], [r.text for r in respostas])
        self.assertEqual(len({r.json()["id"] for r in respostas}), 1)
        self.assertEqual(sorted(r.json()["repetido"] for r in respostas), [False, True])
        self.assertEqual(self.contagens(), (1, 1, 0, 1))
        self.assertEqual(plano.commits, 2)          # A falhou; B commitou; a nova tentativa de A parou na chave
        self.assertEqual(self.esperas_banco, [0.25])  # A não achou a chave na 1ª consulta e esperou
        self.assertGreaterEqual(contagem[0], 4)     # 2 consultas antes + a de A sem achar + a de A achando


# --- POST /receitas ---
class TestGravarReceita(TesteRegistroBase):
    def receita(self, user_id=UID, **mudancas):
        return self.post("/receitas", corpo_receita(**mudancas), user_id)

    def test_contrato_e_linhas(self):
        resposta = self.receita()
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json(), {"id": 1, "tipo": "receita", "valor": 500.0,
                                           "descricao": "Venda da Bicicleta", "data": DATA_BRASILIA,
                                           "repetido": False})
        self.assertEqual(self.ler("SELECT user_id, valor, descricao, data_receita, origem FROM receitas"),
                         [(UID, 500.0, 'Venda da Bicicleta', MOMENTO_BRASILIA, 'avulsa')])
        self.assertEqual(self.ler("SELECT user_id, chave, tipo, lancamento_id, criado_em FROM chaves_registro"),
                         [(UID, CHAVE, 'receita', 1, MOMENTO_BRASILIA)])
        self.assertEqual(self.contar("transacoes"), 0)

    def test_descricao_vazia_vira_null_e_100_caracteres_passa(self):
        self.assertIsNone(self.receita(descricao="   ").json()["descricao"])
        self.assertEqual(len(self.receita(chave=CHAVE_2, descricao="R" * 100).json()["descricao"]), 100)

    def test_valor_arredondado(self):
        self.assertEqual(self.receita(valor=10.006).json()["valor"], 10.01)

    def assertRecusado(self, codigo, **mudancas):
        self.assertErro(self.receita(**mudancas), 422, codigo)
        self.assertEqual(self.contar("receitas") + self.contar("chaves_registro"), 0)

    def test_chave_invalida(self):
        for chave in (None, "abc", 1, CHAVE[:-1]):
            with self.subTest(chave=chave):
                self.assertRecusado("chave_invalida", chave=chave)

    def test_valor_invalido(self):
        for valor in (0, -200, "500", True, None, 1_000_000.01):
            with self.subTest(valor=valor):
                self.assertRecusado("valor_invalido", valor=valor)

    def test_descricao_longa(self):
        self.assertRecusado("descricao_longa", descricao="R" * 101)

    def test_toque_duplo_em_sequencia(self):
        primeira = self.receita()
        segunda = self.receita(valor=1.0)
        self.assertEqual((primeira.status_code, segunda.status_code), (201, 200))
        self.assertEqual(segunda.json(), dict(primeira.json(), repetido=True))
        self.assertEqual((self.contar("receitas"), self.contar("chaves_registro")), (1, 1))

    def test_toque_duplo_em_duas_threads(self):
        original = registro.buscar_chave
        barreira = threading.Barrier(2, timeout=10)
        contagem = [0]
        trava = threading.Lock()

        def buscar_com_barreira(conn, user_id, chave):
            achado = original(conn, user_id, chave)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= 2:
                barreira.wait()
            return achado

        respostas = []

        def pedir():
            respostas.append(self.post("/receitas", corpo_receita(), client=TestClient(app)))

        with mock.patch.object(registro, "buscar_chave", buscar_com_barreira):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 201])
        self.assertEqual(len({r.json()["id"] for r in respostas}), 1)
        self.assertEqual((self.contar("receitas"), self.contar("chaves_registro")), (1, 1))

    def test_chave_de_outro_usuario(self):
        primeira = self.receita()
        outra = self.receita(user_id=OUTRO_UID, valor=80.0, descricao="Freela")
        self.assertEqual(outra.status_code, 201)
        self.assertNotEqual(outra.json()["id"], primeira.json()["id"])
        self.assertEqual((outra.json()["valor"], outra.json()["descricao"]), (80.0, "Freela"))

    def test_chave_usada_num_gasto_da_chave_invalida(self):
        self.assertEqual(self.post("/gastos", corpo_gasto()).status_code, 201)
        self.assertErro(self.receita(), 422, "chave_invalida")
        self.assertEqual(self.contar("receitas"), 0)


# --- O RESUMO VÊ O QUE A API GRAVOU ---
class TestResumoDepoisDaGravacao(TesteRegistroBase):
    def test_resumo_soma_o_gasto_e_a_receita_gravados(self):
        corte = mock.patch.object(calculos, "DATA_CORTE_SOBRA", date(2026, 10, 28))
        corte.start()
        self.addCleanup(corte.stop)
        self.assertEqual(self.post("/gastos", corpo_gasto()).status_code, 201)           # parcela 1 = 200
        self.assertEqual(self.post("/gastos", corpo_gasto(chave=CHAVE_2, valor=59.9, categoria="Alimentação",
                                                          metodo_pagamento="Débito/Pix")).status_code, 201)
        self.assertEqual(self.post("/receitas", corpo_receita(chave="11111111-2222-4333-8444-555555555555")).status_code,
                         201)
        resumo = self.client.get("/resumo", headers=self.cabecalho()).json()
        self.assertEqual(resumo["ano_mes"], "2026-11")
        self.assertEqual(resumo["total_gasto"], 259.9)        # 200 + 59,90
        self.assertEqual(resumo["total_receitas"], 500.0)
        self.assertEqual(resumo["sobra_mes"], 5240.1)         # 5.000 + 500 − 259,90
        # O mesmo cálculo do "Ver Resumo" do bot (calcular_resumo_fatura).
        conn = sqlite3.connect(self.caminho)
        try:
            direto = calculos.calcular_resumo_fatura(conn, UID, "2026-11", 28)
        finally:
            conn.close()
        self.assertEqual((round(direto.total_gasto, 2), round(direto.sobra_mes, 2)), (259.9, 5240.1))


if __name__ == "__main__":
    unittest.main()
