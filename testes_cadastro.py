"""Testes do cadastro (PRO-96): o módulo cadastro.py, a rota POST /cadastro da
API, a porta de entrada do /start do bot (com URL_MINI_APP) e as travas da
prova no Turso (ferramentas/prova_cadastro_turso.py).

Roda com `python testes_cadastro.py`. Não usa .env, rede nem banco real:
  - a API roda pelo TestClient com o token falso (initData assinado no próprio
    teste) e um SQLite em ARQUIVO temporário, com uma conexão nova por
    requisição (como a API real), para os pedidos em duas threads disputarem
    o banco de verdade; as esperas das novas tentativas não dormem;
  - o bot é importado com o load_dotenv desligado e o libsql.connect
    proibido, e a URL_MINI_APP é sempre uma URL FALSA (exemplo.invalid);
  - a prova roda só contra SQLite (--sqlite) ou com uma conexão espiã que
    nunca conecta.

O "agora" da API é 06/11/2026 01:30 em UTC, ou seja, 05/11/2026 22:30 em
Brasília (depois da DATA_CORTE_SOBRA): a data gravada tem de ser 2026-11-05
22:30:00 (em UTC seria 2026-11-06).
"""
import ast
import asyncio
import io
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)
PASTA_FERRAMENTAS = os.path.join(RAIZ, 'ferramentas')
if PASTA_FERRAMENTAS not in sys.path:
    sys.path.insert(0, PASTA_FERRAMENTAS)

from fastapi.testclient import TestClient  # noqa: E402

import cadastro  # noqa: E402
from api.main import Config, app, obter_agora, obter_config, obter_conexao_db  # noqa: E402
from testes_initdata import TOKEN_FALSO, montar_init_data  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402

UID = 4242            # usuário do initData padrão, SEM cadastro no início
CADASTRADO = 5151     # já cadastrado (com alerta e categorias)

AGORA_UTC = datetime(2026, 11, 6, 1, 30, tzinfo=timezone.utc)   # 05/11/2026 22:30 em Brasília
MOMENTO_BRASILIA = '2026-11-05 22:30:00'

URL_FALSA = 'https://exemplo.invalid/app/'

ERRO_BUSY = ('Hrana: stream error: SQLite error: interactive transaction was rolled back because the stream '
             'was idle for too long; retry the transaction, code: SQLITE_BUSY')
MENSAGEM_LIBSQL_PK = ('Hrana: stream error: `Error { message: "SQLite error: UNIQUE constraint failed: '
                      'usuarios.user_id", code: "SQLITE_CONSTRAINT" }`')

# Os avisos "Cadastro recusado: <código>" não poluem a saída (o assertLogs vê tudo).
logging.getLogger("api").addHandler(logging.NullHandler())


def criar_banco(caminho):
    conn = sqlite3.connect(caminho)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    conn.executescript(ESQUEMA)
    conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
                 "VALUES (?, 'Já Cadastrado', 1000.0, 7, '20:00')", (CADASTRADO,))
    for nome in ('Mercado', 'Lazer'):
        conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                     "VALUES (?, ?, 0, 0, '2026-01-01 10:00:00')", (CADASTRADO, nome))
    conn.commit()
    conn.close()


# --- O MÓDULO cadastro.py ---
class TestImportDoModulo(unittest.TestCase):
    def test_import_sem_env_nao_carrega_telegram_dotenv_libsql_nem_genai(self):
        ambiente = {chave: os.environ[chave] for chave in ('PATH', 'SYSTEMROOT') if chave in os.environ}
        codigo = ("import sys, cadastro; "
                  "print('telegram' in sys.modules, 'dotenv' in sys.modules, 'libsql' in sys.modules, "
                  "'google.genai' in sys.modules, 'bot' in sys.modules)")
        resultado = subprocess.run([sys.executable, '-c', codigo], cwd=RAIZ, env=ambiente,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), "False False False False False")

    def test_imports_do_modulo(self):
        """Por AST: só biblioteca padrão e registro (a erro_passageiro)."""
        with open(os.path.join(RAIZ, 'cadastro.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        modulos = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                modulos += [a.name for a in no.names]
            elif isinstance(no, ast.ImportFrom):
                modulos.append(no.module)
        self.assertEqual(set(modulos), {'time', 'unicodedata', 'typing', 'registro'})
        do_registro = [a.name for no in ast.walk(arvore)
                       if isinstance(no, ast.ImportFrom) and no.module == 'registro' for a in no.names]
        self.assertEqual(do_registro, ['erro_passageiro'])

    def test_bot_usa_as_funcoes_do_modulo(self):
        """O bot.py não tem mais cópia própria: importa do cadastro.py."""
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        definidas = {no.name for no in arvore.body if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertFalse({'_normalizar_texto', 'dia_valido'} & definidas)
        importadas = {(a.name, a.asname) for no in arvore.body
                      if isinstance(no, ast.ImportFrom) and no.module == 'cadastro' for a in no.names}
        self.assertEqual(importadas, {('dia_valido', None), ('gravar_cadastro', None),
                                      ('montar_lista_categorias', None), ('normalizar_texto', '_normalizar_texto')})


class TestRegrasComuns(unittest.TestCase):
    def test_normalizar_texto(self):
        self.assertEqual(cadastro.normalizar_texto('  Alimentação  da   Casa '), 'alimentacao da casa')
        self.assertEqual(cadastro.normalizar_texto('SAÚDE'), 'saude')
        self.assertEqual(cadastro.normalizar_texto('   '), '')

    def test_dia_valido(self):
        self.assertEqual([d for d in range(-1, 34) if cadastro.dia_valido(d)], list(range(1, 32)))

    def test_montar_lista_categorias(self):
        self.assertEqual(
            cadastro.montar_lista_categorias(["lazer", " Lazer ", "LAZER", "", "  ", "Saude", "Saúde",
                                              "alimentação", "ALIMENTACAO", "cAsA"]),
            ["Lazer", "Saude", "Alimentação", "Casa"])
        self.assertEqual(cadastro.montar_lista_categorias([]), [])


class TestGravarCadastro(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(ESQUEMA)

    def tearDown(self):
        self.conn.close()

    def linhas(self, user_id=1):
        return (self.conn.execute("SELECT user_id, nome, renda_mensal, dia_fechamento, horario_alerta "
                                  "FROM usuarios WHERE user_id = ?", (user_id,)).fetchall(),
                self.conn.execute("SELECT nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                                  "WHERE user_id = ? ORDER BY id", (user_id,)).fetchall())

    def test_substituir_true_troca_a_linha_e_recria_as_categorias(self):
        cadastro.gravar_cadastro(self.conn, 1, 'A', 100.0, 5, '20:00', ['Um', 'Dois'], '2026-01-01 00:00:00', True)
        cadastro.gravar_cadastro(self.conn, 1, 'B', 200.0, 6, '20:00', ['Três'], '2026-02-02 00:00:00', True)
        self.assertEqual(self.linhas(), ([(1, 'B', 200.0, 6, '20:00')], [('Três', 0.0, 0.0, '2026-02-02 00:00:00')]))

    def test_substituir_false_e_recusado_pela_chave_primaria(self):
        cadastro.gravar_cadastro(self.conn, 1, 'A', 0.0, 5, None, ['Um'], '2026-01-01 00:00:00', False)
        with self.assertRaises(sqlite3.IntegrityError):
            cadastro.gravar_cadastro(self.conn, 1, 'B', 0.0, 6, None, ['Dois'], '2026-01-01 00:00:00', False)
        self.conn.rollback()
        self.assertEqual(self.linhas(), ([(1, 'A', 0.0, 5, None)], [('Um', 0.0, 0.0, '2026-01-01 00:00:00')]))

    def test_commit_false_deixa_a_transacao_aberta(self):
        cadastro.gravar_cadastro(self.conn, 1, 'A', 0.0, 5, None, ['Um'], '2026-01-01 00:00:00', False,
                                 commit=False)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.linhas(), ([], []))


# --- A ROTA POST /cadastro ---
class ConexaoEmbrulhada:
    """Conexão SQLite com falhas roteirizadas, como as do libsql:
      - 'pk_como_libsql': a chave primária de 'usuarios' vira ValueError com a
        mensagem do Hrana (no lugar do sqlite3.IntegrityError);
      - plano.esconder_prechecagem: as N primeiras consultas de existência
        devolvem "não existe" (simula o toque duplo sem threads);
      - plano.falhas_categoria / plano.falhas_commit / plano.falhas_consulta_depois:
        listas compartilhadas (uma por requisição, na ordem) de exceções a
        levantar no 1º INSERT de categoria, no commit e na consulta feita
        depois de um erro (None = normal). O commit que falha desfaz antes,
        como o Turso."""

    def __init__(self, real, plano):
        self.real = real
        self.plano = plano

    def execute(self, sql, params=()):
        plano = self.plano
        sql_limpo = ' '.join(sql.split())
        with plano.trava:
            if sql_limpo == cadastro.SQL_USUARIO_EXISTE:
                plano.consultas += 1
                if plano.esconder_prechecagem > 0:
                    plano.esconder_prechecagem -= 1
                    return SimpleNamespace(fetchone=lambda: None)
                if plano.consultas > 1 and plano.falhas_consulta_depois:
                    falha = plano.falhas_consulta_depois.pop(0)
                    if falha is not None:
                        raise falha
            if sql_limpo.startswith('INSERT INTO usuarios'):
                plano.insercoes_usuario += 1
            if sql_limpo.startswith('INSERT INTO categorias'):
                plano.insercoes_categoria += 1
                if plano.falhas_categoria:
                    falha = plano.falhas_categoria.pop(0)
                    if falha is not None:
                        raise falha
        try:
            return self.real.execute(sql, params)
        except sqlite3.IntegrityError as erro:
            if plano.pk_como_libsql and 'usuarios.user_id' in str(erro):
                raise ValueError(MENSAGEM_LIBSQL_PK) from None
            raise

    def commit(self):
        with self.plano.trava:
            self.plano.commits += 1
            falha = self.plano.falhas_commit.pop(0) if self.plano.falhas_commit else None
        if falha is not None:
            self.real.rollback()
            raise falha
        self.real.commit()

    def rollback(self):
        self.real.rollback()

    def close(self):
        self.real.close()


class Plano:
    def __init__(self, falhas_commit=(), falhas_categoria=(), falhas_consulta_depois=(), esconder_prechecagem=0,
                 pk_como_libsql=False):
        self.falhas_commit = list(falhas_commit)
        self.falhas_categoria = list(falhas_categoria)
        self.falhas_consulta_depois = list(falhas_consulta_depois)
        self.esconder_prechecagem = esconder_prechecagem
        self.pk_como_libsql = pk_como_libsql
        self.consultas = 0
        self.commits = 0
        self.insercoes_usuario = 0
        self.insercoes_categoria = 0
        self.trava = threading.Lock()


class TesteRotaBase(unittest.TestCase):
    plano = None

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.caminho = os.path.join(self.pasta.name, 'teste.db')
        criar_banco(self.caminho)

        def conexao():
            conn = sqlite3.connect(self.caminho, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA synchronous = OFF")
            if self.plano is not None:
                conn = ConexaoEmbrulhada(conn, self.plano)
            try:
                yield conn
            finally:
                conn.close()

        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://falso", "falso")
        app.dependency_overrides[obter_conexao_db] = conexao
        app.dependency_overrides[obter_agora] = lambda: AGORA_UTC
        self.client = TestClient(app)
        self.esperas = []
        patcher = mock.patch.object(cadastro, '_esperar', self.esperas.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.pasta.cleanup()

    def cabecalho(self, user_id=UID):
        init_data = montar_init_data(auth_date=int(time.time()), user={"id": user_id, "first_name": "Teste"})
        return {"Authorization": "tma " + init_data}

    def cadastrar(self, corpo=None, user_id=UID, client=None, **mudancas):
        corpo = dict(corpo or {"nome": "Ygor", "dia_fechamento": 10, "categorias": ["Alimentação", "Casa", "Lazer", "Pet"]})
        corpo.update(mudancas)
        return (client or self.client).post("/cadastro", json=corpo, headers=self.cabecalho(user_id))

    def ler(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def usuario(self, user_id=UID):
        return self.ler("SELECT user_id, nome, renda_mensal, dia_fechamento, horario_alerta, typeof(horario_alerta) "
                        "FROM usuarios WHERE user_id = ?", (user_id,))

    def categorias(self, user_id=UID):
        return self.ler("SELECT nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                        "WHERE user_id = ? ORDER BY id", (user_id,))

    def tudo(self):
        return (self.ler("SELECT * FROM usuarios ORDER BY user_id"), self.ler("SELECT * FROM categorias ORDER BY id"))

    def assertErro(self, resposta, status, codigo):
        self.assertEqual(resposta.status_code, status, resposta.text)
        self.assertEqual(resposta.json(), {"detail": codigo})


class TestCadastroCriado(TesteRotaBase):
    def test_201_contrato_e_linhas_gravadas(self):
        resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json(), {"user_id": UID, "nome": "Ygor", "dia_fechamento": 10,
                                           "categorias": ["Alimentação", "Casa", "Lazer", "Pet"]})
        self.assertEqual(self.usuario(), [(UID, "Ygor", 0.0, 10, None, 'null')])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM usuarios WHERE user_id = ? AND horario_alerta IS NULL",
                                  (UID,)), [(1,)])
        self.assertEqual(self.categorias(), [(nome, 0.0, 0.0, MOMENTO_BRASILIA)
                                             for nome in ("Alimentação", "Casa", "Lazer", "Pet")])

    def test_nome_aparado_e_gravado_como_veio(self):
        resposta = self.cadastrar(nome="  ana PAULA de Souza  ")
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["nome"], "ana PAULA de Souza")
        self.assertEqual(self.usuario()[0][1], "ana PAULA de Souza")

    def test_categorias_repetidas_e_acentos_viram_uma_so_na_ordem(self):
        resposta = self.cadastrar(categorias=["lazer", " Lazer ", "LAZER", "Saude", "Saúde", "alimentação"])
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["categorias"], ["Lazer", "Saude", "Alimentação"])
        self.assertEqual([linha[0] for linha in self.categorias()], ["Lazer", "Saude", "Alimentação"])

    def test_limites_aceitos(self):
        nome = "N" * 30
        categorias = [f"Categoria {i:02d}" for i in range(20)] + ["categoria 00", "C" * 30]
        categorias.remove("Categoria 19")   # 19 + "C"*30 = 20 depois de juntar a repetida
        for dia in (1, 31):
            with self.subTest(dia=dia):
                user_id = 100 + dia
                resposta = self.cadastrar(user_id=user_id, nome=nome, dia_fechamento=dia, categorias=categorias)
                self.assertEqual(resposta.status_code, 201, resposta.text)
                self.assertEqual(len(resposta.json()["categorias"]), 20)
                self.assertEqual(self.usuario(user_id)[0][1:4], (nome, 0.0, dia))

    def test_campos_a_mais_sao_ignorados(self):
        corpo = {"nome": "Ygor", "dia_fechamento": 10, "categorias": ["Alimentação", "Casa", "Lazer", "Pet"],
                 "user_id": 999, "renda_mensal": 5000.0, "horario_alerta": "20:00",
                 "data_criacao": "2020-01-01 00:00:00"}
        resposta = self.cadastrar(corpo=corpo, user_id=UID)
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["user_id"], UID)
        self.assertEqual(self.usuario(), [(UID, "Ygor", 0.0, 10, None, 'null')])
        self.assertEqual(self.usuario(999), [])
        self.assertEqual({linha[3] for linha in self.categorias()}, {MOMENTO_BRASILIA})

    def test_corpo_que_nao_e_json_422_padrao(self):
        resposta = self.client.post("/cadastro", content="isto não é json",
                                    headers={**self.cabecalho(), "Content-Type": "application/json"})
        self.assertEqual(resposta.status_code, 422)
        self.assertIsInstance(resposta.json()["detail"], list)


class TestAutenticacao(TesteRotaBase):
    def test_sem_cabecalho_401(self):
        antes = self.tudo()
        resposta = self.client.post("/cadastro", json={"nome": "Ygor", "dia_fechamento": 10, "categorias": ["Casa"]})
        self.assertErro(resposta, 401, "nao_autorizado")
        self.assertEqual(self.tudo(), antes)

    def test_initdata_invalido_401(self):
        init_data = montar_init_data(token="999:OUTRO-TOKEN", auth_date=int(time.time()))
        for cabecalho in ({"Authorization": "tma " + init_data}, {"Authorization": "Bearer abc"},
                          {"Authorization": "tma "}):
            with self.subTest(cabecalho=cabecalho):
                resposta = self.client.post("/cadastro", json={"nome": "Ygor", "dia_fechamento": 10,
                                                               "categorias": ["Casa"]}, headers=cabecalho)
                self.assertErro(resposta, 401, "nao_autorizado")
        self.assertEqual(self.usuario(), [])

    def test_initdata_expirado_401(self):
        init_data = montar_init_data(auth_date=int(time.time()) - 3601)
        resposta = self.client.post("/cadastro", json={"nome": "Ygor", "dia_fechamento": 10, "categorias": ["Casa"]},
                                    headers={"Authorization": "tma " + init_data})
        self.assertErro(resposta, 401, "nao_autorizado")


class TestValidacao(TesteRotaBase):
    def recusar(self, codigo, **mudancas):
        antes = self.tudo()
        with self.assertLogs("api", level="WARNING") as logs:
            resposta = self.cadastrar(**mudancas)
        self.assertErro(resposta, 422, codigo)
        self.assertEqual(self.tudo(), antes)
        self.assertEqual(logs.output, [f"WARNING:api:Cadastro recusado: {codigo}"])

    def test_nome_invalido(self):
        for nome in ("", "   ", "N" * 31, 123, None, ["Ygor"], {"nome": "Ygor"}, True):
            with self.subTest(nome=nome):
                self.recusar("nome_invalido", nome=nome)

    def test_nome_ausente(self):
        antes = self.tudo()
        resposta = self.cadastrar(corpo={"dia_fechamento": 10, "categorias": ["Casa"]})
        self.assertErro(resposta, 422, "nome_invalido")
        self.assertEqual(self.tudo(), antes)

    def test_dia_invalido(self):
        for dia in (0, 32, -1, 100, True, False, 5.5, 10.0, "5", None, [5]):
            with self.subTest(dia=dia):
                self.recusar("dia_invalido", dia_fechamento=dia)

    def test_categorias_invalidas(self):
        casos = {
            "texto em vez de lista": "Casa, Lazer",
            "lista vazia": [],
            "item vazio": ["Casa", ""],
            "item só com espaços": ["Casa", "   "],
            "item que não é texto": ["Casa", 3],
            "item nulo": ["Casa", None],
            "31 caracteres": ["Casa", "C" * 31],
            "31 caracteres com espaços nas pontas": ["  " + "C" * 31 + "  "],
            "21 categorias": [f"Categoria {i:02d}" for i in range(21)],
            "nulo": None,
            "objeto": {"nome": "Casa"},
        }
        for descricao, categorias in casos.items():
            with self.subTest(caso=descricao):
                self.recusar("categorias_invalidas", categorias=categorias)

    def test_30_caracteres_com_espacos_nas_pontas_passa(self):
        resposta = self.cadastrar(categorias=["  " + "c" * 30 + "  "])
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json()["categorias"], ["C" + "c" * 29])

    def test_validacao_vem_antes_do_cadastro_existente(self):
        """Corpo inválido de quem já tem cadastro: 422 (nada é consultado nem gravado)."""
        antes = self.tudo()
        resposta = self.cadastrar(user_id=CADASTRADO, dia_fechamento=0)
        self.assertErro(resposta, 422, "dia_invalido")
        self.assertEqual(self.tudo(), antes)


class TestJaCadastrado(TesteRotaBase):
    def test_409_sem_alterar_nada(self):
        antes = self.tudo()
        with self.assertLogs("api", level="WARNING") as logs:
            resposta = self.cadastrar(user_id=CADASTRADO, nome="Outro", dia_fechamento=20,
                                      categorias=["Nova", "Outra"])
        self.assertErro(resposta, 409, "usuario_ja_cadastrado")
        self.assertEqual(self.tudo(), antes)
        self.assertEqual(self.usuario(CADASTRADO), [(CADASTRADO, "Já Cadastrado", 1000.0, 7, "20:00", "text")])
        self.assertEqual([linha[0] for linha in self.categorias(CADASTRADO)], ["Mercado", "Lazer"])
        self.assertEqual(logs.output, ["WARNING:api:Cadastro recusado: usuario_ja_cadastrado"])

    def test_segundo_cadastro_do_mesmo_usuario_409(self):
        self.assertEqual(self.cadastrar().status_code, 201)
        antes = self.tudo()
        self.assertErro(self.cadastrar(nome="Outro", categorias=["Nova"]), 409, "usuario_ja_cadastrado")
        self.assertEqual(self.tudo(), antes)

    def test_chave_primaria_do_sqlite_409(self):
        """A pré-checagem não vê o usuário (o outro pedido gravou no meio): o
        INSERT bate na chave primária (sqlite3.IntegrityError) e vira 409."""
        self.plano = Plano(esconder_prechecagem=1)
        resposta = self.cadastrar(user_id=CADASTRADO, categorias=["Nova"])
        self.assertErro(resposta, 409, "usuario_ja_cadastrado")
        self.assertEqual([linha[0] for linha in self.categorias(CADASTRADO)], ["Mercado", "Lazer"])
        self.assertEqual(self.esperas, [])

    def test_chave_primaria_do_libsql_409(self):
        """Mesmo caso com o erro do libsql (ValueError com SQLITE_CONSTRAINT)."""
        self.plano = Plano(esconder_prechecagem=1, pk_como_libsql=True)
        resposta = self.cadastrar(user_id=CADASTRADO, categorias=["Nova"])
        self.assertErro(resposta, 409, "usuario_ja_cadastrado")
        self.assertEqual([linha[0] for linha in self.categorias(CADASTRADO)], ["Mercado", "Lazer"])
        self.assertEqual(self.plano.consultas, 2)   # a pré-checagem escondida e a consulta depois do erro

    def test_toque_duplo_em_duas_threads(self):
        """Os dois pedidos passam pela pré-checagem (nenhum acha nada) antes de
        qualquer um gravar: só a chave primária decide."""
        original = cadastro.usuario_existe
        barreira = threading.Barrier(2, timeout=10)
        contagem = [0]
        trava = threading.Lock()

        def existe_com_barreira(conn, user_id):
            achado = original(conn, user_id)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= 2:
                barreira.wait()
            return achado

        respostas = []

        def pedir():
            respostas.append(self.cadastrar(client=TestClient(app)))

        with mock.patch.object(cadastro, "usuario_existe", existe_com_barreira):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [201, 409])
        self.assertEqual(sorted(str(r.json()) for r in respostas if r.status_code == 409),
                         ["{'detail': 'usuario_ja_cadastrado'}"])
        self.assertEqual(len(self.usuario()), 1)
        self.assertEqual([linha[0] for linha in self.categorias()], ["Alimentação", "Casa", "Lazer", "Pet"])
        self.assertGreaterEqual(contagem[0], 3)   # o perdedor consultou de novo depois do erro


class TestBancoOcupado(TesteRotaBase):
    def test_commit_busy_uma_vez_tenta_de_novo_e_grava(self):
        self.plano = Plano(falhas_commit=[ValueError(ERRO_BUSY)])
        resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.esperas, [0.25])
        self.assertEqual((self.plano.commits, self.plano.insercoes_usuario), (2, 2))
        self.assertEqual(len(self.usuario()), 1)
        self.assertEqual([linha[0] for linha in self.categorias()], ["Alimentação", "Casa", "Lazer", "Pet"])

    def test_busy_no_meio_das_categorias_desfaz_e_grava_um_jogo_so(self):
        self.plano = Plano(falhas_categoria=[None, None, ValueError(ERRO_BUSY)])
        resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.esperas, [0.25])
        self.assertEqual(self.plano.insercoes_usuario, 2)
        self.assertEqual([linha[0] for linha in self.categorias()], ["Alimentação", "Casa", "Lazer", "Pet"])

    def test_database_is_locked_tambem_e_passageiro(self):
        self.plano = Plano(falhas_commit=[sqlite3.OperationalError("database is locked")])
        self.assertEqual(self.cadastrar().status_code, 201)
        self.assertEqual(self.esperas, [0.25])

    def test_busy_nas_4_tentativas_503_banco_ocupado(self):
        self.plano = Plano(falhas_commit=[ValueError(ERRO_BUSY)] * 4)
        with self.assertLogs("api", level="WARNING") as logs:
            resposta = self.cadastrar()
        self.assertErro(resposta, 503, "banco_ocupado")
        self.assertEqual(self.esperas, [0.25, 0.5, 1.0])
        self.assertEqual(self.plano.commits, 4)
        self.assertEqual((self.usuario(), self.categorias()), ([], []))
        self.assertEqual(logs.output, ["WARNING:api:Cadastro recusado: banco_ocupado"])

    def test_consulta_depois_do_erro_falha_e_tratada_como_passageira(self):
        """O commit falha com um erro comum e a consulta seguinte também falha
        (stream quebrado): conta como passageiro e tenta de novo."""
        self.plano = Plano(falhas_commit=[ValueError("Hrana: stream error: conexão encerrada")],
                           falhas_consulta_depois=[ValueError("Hrana: stream error: conexão encerrada")])
        resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.esperas, [0.25])
        self.assertEqual(len(self.usuario()), 1)

    def test_erro_inesperado_500_sem_detalhes_e_sem_nova_tentativa(self):
        self.plano = Plano(falhas_categoria=[None, RuntimeError("SQL secreto: INSERT INTO categorias Alimentação")])
        client = TestClient(app, raise_server_exceptions=False)
        with self.assertLogs("api", level="ERROR") as logs:
            resposta = self.cadastrar(client=client)
        self.assertErro(resposta, 500, "erro_interno")
        self.assertNotIn("secreto", resposta.text)
        self.assertNotIn("Alimentação", resposta.text)
        self.assertEqual(logs.output, ["ERROR:api:Cadastro falhou: erro_interno (RuntimeError)"])
        self.assertEqual(self.esperas, [])
        self.assertEqual(self.plano.insercoes_usuario, 1)
        self.assertEqual((self.usuario(), self.categorias()), ([], []))   # tudo ou nada

    def test_stream_error_sozinho_nao_e_passageiro(self):
        self.plano = Plano(falhas_commit=[ValueError("Hrana: stream error: conexão encerrada")])
        client = TestClient(app, raise_server_exceptions=False)
        resposta = self.cadastrar(client=client)
        self.assertErro(resposta, 500, "erro_interno")
        self.assertEqual(self.esperas, [])
        self.assertEqual(self.usuario(), [])


class TestLeiturasDepoisDoCadastro(TesteRotaBase):
    def test_me_resumo_historico_e_movimentacoes(self):
        self.assertEqual(self.cadastrar().status_code, 201)
        cabecalho = self.cabecalho()
        me = self.client.get("/me", headers=cabecalho)
        self.assertEqual((me.status_code, me.json()), (200, {"user_id": UID, "nome": "Ygor"}))
        resumo = self.client.get("/resumo", headers=cabecalho)
        self.assertEqual(resumo.status_code, 200, resumo.text)
        corpo = resumo.json()
        self.assertEqual((corpo["renda"], corpo["total_gasto"], corpo["total_receitas"], corpo["sobra_mes"]),
                         (0.0, 0.0, 0.0, 0.0))
        self.assertEqual((corpo["ano_mes"], corpo["dia_fechamento"]), ("2026-11", 10))
        historico = self.client.get("/historico", headers=cabecalho)
        self.assertEqual(historico.status_code, 200, historico.text)
        recentes = self.client.get("/movimentacoes/recentes", headers=cabecalho)
        self.assertEqual((recentes.status_code, recentes.json()), (200, {"movimentacoes": []}))

    def test_antes_do_cadastro_me_404(self):
        self.assertEqual(self.client.get("/me", headers=self.cabecalho()).status_code, 404)


# --- O /start DO BOT (PORTA DE ENTRADA) ---
def importar_bot():
    """Importa o bot.py sem .env (load_dotenv desligado) e sem conexão real."""
    if 'bot' in sys.modules:
        return sys.modules['bot']
    import dotenv
    import libsql
    with mock.patch.object(dotenv, 'load_dotenv', lambda *a, **k: False):
        import bot
    libsql.connect = _libsql_proibido
    return bot


def _libsql_proibido(*args, **kwargs):
    raise RuntimeError("Conexão real com o banco é proibida nos testes.")


class JobQueueFalsa:
    def __init__(self):
        self.agendados = []

    def get_jobs_by_name(self, nome):
        return []

    def run_daily(self, callback, time, **kwargs):
        self.agendados.append((callback.__name__, kwargs.get('name')))


class ConexaoSemFechar:
    def __init__(self, real):
        self.real = real

    def __getattr__(self, nome):
        return getattr(self.real, nome)

    def close(self):
        pass


class TestPortaDeEntrada(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bot = importar_bot()

    def setUp(self):
        bot = self.bot
        self.real = sqlite3.connect(':memory:')
        self.real.executescript(ESQUEMA)
        self.real.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
                          "VALUES (?, 'Já Cadastrado', 1000.0, 7, '20:00')", (CADASTRADO,))
        self.real.commit()
        self.enviados = []

        async def enviar_fluxo_falso(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
            self.enviados.append((chat_id, texto, None if reply_markup is None else reply_markup.to_dict()))

        for alvo, valor in (('obter_conexao_db', lambda: ConexaoSemFechar(self.real)),
                            ('enviar_fluxo', enviar_fluxo_falso)):
            patcher = mock.patch.object(bot, alvo, valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.real.close)
        self.job_queue = JobQueueFalsa()
        self.context = SimpleNamespace(user_data={}, application=SimpleNamespace(handlers={}), bot=None,
                                       job_queue=self.job_queue)

    def start(self, user_id=UID, url=None):
        async def apagar():
            pass
        update = SimpleNamespace(message=SimpleNamespace(text="/start", delete=apagar),
                                 effective_user=SimpleNamespace(id=user_id),
                                 effective_chat=SimpleNamespace(id=user_id), callback_query=None)
        ambiente = {} if url is None else {'URL_MINI_APP': url}
        with mock.patch.dict(os.environ, ambiente):
            if url is None:
                os.environ.pop('URL_MINI_APP', None)
            return asyncio.run(self.bot.start(update, self.context))

    def contagens(self):
        return tuple(self.real.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ('usuarios', 'categorias'))

    def test_com_url_sem_cadastro_mostra_a_porta_de_entrada_e_nao_grava(self):
        antes = self.contagens()
        estado = self.start(url=URL_FALSA)
        self.assertEqual(estado, self.bot.ConversationHandler.END)
        self.assertEqual(self.enviados, [(
            UID,
            "<b>Bem-vindo ao L1d3r!</b> Toque no botão abaixo para abrir o seu painel.",
            {"inline_keyboard": [[{"text": "Abrir o L1d3r", "web_app": {"url": URL_FALSA}}]]},
        )])
        self.assertEqual(self.contagens(), antes)
        self.assertEqual(self.context.user_data, {})
        self.assertEqual(self.job_queue.agendados, [])

    def test_porta_de_entrada_de_novo_continua_igual(self):
        self.start(url=URL_FALSA)
        self.start(url=URL_FALSA)
        self.assertEqual(len(self.enviados), 2)
        self.assertEqual(self.enviados[0], self.enviados[1])
        self.assertEqual(self.contagens(), (1, 0))

    def test_url_com_espacos_nas_pontas_vale(self):
        self.start(url="  " + URL_FALSA + "  ")
        self.assertEqual(self.enviados[0][2]["inline_keyboard"][0][0]["web_app"], {"url": URL_FALSA})

    def test_url_sem_https_segue_o_cadastro_antigo(self):
        for url in ("http://exemplo.invalid/app/", "exemplo.invalid/app/", "", "   ", "https://",
                    "HTTPS://exemplo.invalid/app/", "ftp://exemplo.invalid/"):
            with self.subTest(url=url):
                self.enviados.clear()
                estado = self.start(url=url)
                self.assertEqual(estado, self.bot.NOME_USUARIO)
                self.assertEqual(len(self.enviados), 1)
                self.assertTrue(self.enviados[0][1].startswith("Boas-vindas ao seu Gestor Financeiro!"))
                self.assertIsNone(self.enviados[0][2])

    def test_sem_url_segue_o_cadastro_antigo(self):
        estado = self.start(url=None)
        self.assertEqual(estado, self.bot.NOME_USUARIO)
        self.assertTrue(self.enviados[0][1].startswith("Boas-vindas ao seu Gestor Financeiro!"))

    def test_com_cadastro_abre_o_menu_principal_com_ou_sem_url(self):
        for url in (URL_FALSA, None):
            with self.subTest(url=url):
                self.enviados.clear()
                estado = self.start(user_id=CADASTRADO, url=url)
                self.assertEqual(estado, self.bot.ConversationHandler.END)
                self.assertEqual(self.enviados[0][1], "Menu Principal")
                textos = [b["text"] for linha in self.enviados[0][2]["inline_keyboard"] for b in linha]
                self.assertEqual(textos, ["Registrar Gasto", "Registrar Gasto Recorrente", "Ver Resumo", "Alerta"])

    def test_url_lida_na_hora_do_start(self):
        self.start(url=None)
        self.start(url=URL_FALSA)
        self.assertTrue(self.enviados[0][1].startswith("Boas-vindas"))
        self.assertTrue(self.enviados[1][1].startswith("<b>Bem-vindo ao L1d3r!</b>"))

    def test_url_nunca_escrita_no_codigo(self):
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertNotIn("ngrok", fonte.lower())
        self.assertIn("os.getenv('URL_MINI_APP')", fonte)


# --- TRAVAS DA PROVA NO TURSO (sem rede) ---
import prova_cadastro_turso as prova  # noqa: E402

URL_TESTE = "libsql://fin4ncetest-l1d3r.aws-us-east-1.turso.io"
URL_PRODUCAO = "libsql://fin4nce-l1d3r.aws-us-east-1.turso.io"
TOKEN_ENV_FALSO = "tokenfalso.abc123.segredo"


def criar_env(pasta, url):
    caminho = os.path.join(pasta, ".env.prova")
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(f"TURSO_DATABASE_URL={url}\nTURSO_AUTH_TOKEN={TOKEN_ENV_FALSO}\n")
    return caminho


class Espia:
    def __init__(self):
        self.chamadas = 0

    def __call__(self, url, token):
        self.chamadas += 1
        raise AssertionError("a prova não pode conectar neste teste")


class TestTravasDaProva(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.pasta.cleanup)

    def banco_sqlite(self, preparo=()):
        caminho = os.path.join(self.pasta.name, f"prova_{len(os.listdir(self.pasta.name))}.db")
        conn = sqlite3.connect(caminho)
        conn.executescript(ESQUEMA)
        conn.execute("INSERT INTO usuarios (user_id, nome) VALUES (4242, 'Pessoa Real')")
        conn.execute("INSERT INTO categorias (user_id, nome) VALUES (4242, 'Mercado')")
        for sql, params in preparo:
            conn.execute(sql, params)
        conn.commit()
        conn.close()
        return caminho

    def rodar(self, argv, conectar=None):
        saida, erro = io.StringIO(), io.StringIO()
        argumentos = {'saida': saida, 'erro': erro}
        if conectar is not None:
            argumentos['conectar'] = conectar
        codigo = prova.main(argv, **argumentos)
        return codigo, saida.getvalue(), erro.getvalue()

    def test_usa_a_trava_da_ferramenta_do_banco_de_teste(self):
        import banco_teste
        for nome in ('escolher_arquivo_env', 'carregar_credenciais', 'e_banco_de_teste', 'ocultar_segredos',
                     'conectar_turso'):
            self.assertIs(getattr(prova, nome), getattr(banco_teste, nome), nome)

    def test_recusa_url_que_nao_e_do_fin4ncetest_antes_de_conectar(self):
        espia = Espia()
        codigo, saida, erro = self.rodar(["--env", criar_env(self.pasta.name, URL_PRODUCAO)], conectar=espia)
        self.assertEqual(codigo, 3)
        self.assertEqual(espia.chamadas, 0)
        self.assertIn("ABORTADO", erro)
        for texto in (saida, erro):
            self.assertNotIn(TOKEN_ENV_FALSO, texto)
            self.assertNotIn("fin4nce-l1d3r", texto)

    def test_env_ausente_aborta_sem_conectar(self):
        espia = Espia()
        codigo, _, erro = self.rodar(["--env", os.path.join(self.pasta.name, "nao_existe.env")], conectar=espia)
        self.assertEqual((codigo, espia.chamadas), (3, 0))

    def test_aceita_o_fin4ncetest_e_erro_da_conexao_nao_vaza_segredo(self):
        def conectar_com_erro(url, token):
            raise ValueError(f"falha ao conectar em {url} com {token}")
        codigo, saida, erro = self.rodar(["--env", criar_env(self.pasta.name, URL_TESTE)], conectar=conectar_com_erro)
        self.assertEqual(codigo, 1)
        self.assertNotIn(TOKEN_ENV_FALSO, saida + erro)
        self.assertNotIn("fin4ncetest-l1d3r", saida + erro)

    def test_faixa_de_ids(self):
        for user_id in (-9600999, -9600500, -9600001):
            self.assertTrue(prova.id_na_faixa(user_id))
        for user_id in (-9601000, -9600000, 0, 1, 4242, 9600001, True, "-9600001", -9600001.0, None):
            with self.subTest(user_id=user_id):
                self.assertFalse(prova.id_na_faixa(user_id))
                with self.assertRaises(prova.ErroProva) as erro:
                    prova.exigir_id_na_faixa(user_id)
                self.assertEqual(erro.exception.codigo, 4)

    def test_trava_recusa_user_id_fora_da_faixa_antes_de_gravar(self):
        caminho = self.banco_sqlite()
        trava = prova.Trava()
        conn = prova.ConexaoTravada(sqlite3.connect(caminho), trava)
        for user_id in (4242, 1, 0, -9600000, -9601000):
            with self.subTest(user_id=user_id):
                with self.assertRaises(prova.ViolacaoDaTrava):
                    cadastro.gravar_cadastro(conn, user_id, 'X', 0.0, 10, None, ['Casa'], '2026-01-01 00:00:00',
                                             substituir=False)
                conn.rollback()
        conn.close()
        leitura = sqlite3.connect(caminho)
        self.assertEqual(leitura.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
        self.assertEqual(leitura.execute("SELECT COUNT(*) FROM categorias").fetchone()[0], 1)
        leitura.close()
        self.assertEqual(len(trava.violacoes), 5)

    def test_trava_bloqueia_outros_comandos(self):
        caminho = self.banco_sqlite()
        trava = prova.Trava()
        conn = prova.ConexaoTravada(sqlite3.connect(caminho), trava)
        bloqueados = [
            ("DELETE FROM categorias WHERE user_id = ?", (-9600001,)),          # só no P8
            ("INSERT OR REPLACE INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
             "VALUES (?, ?, ?, ?, ?)", (-9600001, 'X', 0.0, 10, None)),          # só no P8
            ("DELETE FROM usuarios WHERE user_id = ?", (-9600001,)),
            ("UPDATE usuarios SET nome = 'X' WHERE user_id = ?", (-9600001,)),
            ("INSERT INTO transacoes (user_id, categoria_nome, valor) VALUES (?, ?, ?)", (-9600001, 'X', 1.0)),
            ("DROP TABLE usuarios", ()),
            ("SELECT 1; DELETE FROM usuarios", ()),
            ("WITH x AS (SELECT 1) DELETE FROM usuarios", ()),
            ("PRAGMA table_info(usuarios)", ()),
        ]
        for sql, params in bloqueados:
            with self.subTest(sql=sql):
                with self.assertRaises(prova.ViolacaoDaTrava):
                    conn.execute(sql, params)
                with self.assertRaises(prova.ViolacaoDaTrava):
                    conn.cursor().execute(sql, params)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
        conn.close()

    def test_caminho_antigo_so_liberado_no_p8_e_so_na_faixa(self):
        caminho = self.banco_sqlite()
        trava = prova.Trava()
        conn = prova.ConexaoTravada(sqlite3.connect(caminho), trava)
        trava.caminho_antigo_liberado = True
        cadastro.gravar_cadastro(conn, -9600001, 'A', 3000.0, 10, '20:00', ['Um'], '2026-01-01 00:00:00', True)
        with self.assertRaises(prova.ViolacaoDaTrava):
            cadastro.gravar_cadastro(conn, 4242, 'A', 3000.0, 10, '20:00', ['Um'], '2026-01-01 00:00:00', True)
        conn.rollback()
        conn.close()
        leitura = sqlite3.connect(caminho)
        self.assertEqual(leitura.execute("SELECT nome FROM usuarios WHERE user_id = 4242").fetchall(),
                         [('Pessoa Real',)])
        self.assertEqual(leitura.execute("SELECT nome FROM categorias WHERE user_id = 4242").fetchall(),
                         [('Mercado',)])
        leitura.close()

    def test_post_da_prova_recusa_user_id_fora_da_faixa_antes_do_pedido(self):
        p = prova.Prova(lambda: None, io.StringIO(), [])
        for user_id in (4242, 1, -9600000):
            with self.subTest(user_id=user_id):
                with self.assertRaises(prova.ErroProva):
                    p.post_cadastro(user_id, {"nome": "X", "dia_fechamento": 10, "categorias": ["Casa"]})

    def test_escolhe_o_primeiro_bloco_livre(self):
        blocos = prova.blocos_da_faixa()
        self.assertEqual(blocos[0], (-9600100, -9600001))
        self.assertEqual(blocos[1], (-9600200, -9600101))
        self.assertEqual(len(blocos), 9)
        self.assertTrue(all(prova.FAIXA_INICIO <= menor <= maior <= prova.FAIXA_FIM for menor, maior in blocos))
        caminho = self.banco_sqlite([
            ("INSERT INTO usuarios (user_id, nome) VALUES (?, 'X')", (-9600050,)),
            ("INSERT INTO categorias (user_id, nome) VALUES (?, 'X')", (-9600150,)),
        ])
        conn = sqlite3.connect(caminho)
        self.assertEqual(prova.escolher_bloco(conn), (-9600300, -9600201))
        conn.close()

    def test_aborta_sem_bloco_livre(self):
        preparo = [("INSERT INTO categorias (user_id, nome) VALUES (?, 'X')", (maior,))
                   for _, maior in prova.blocos_da_faixa()]
        caminho = self.banco_sqlite(preparo)
        codigo, saida, erro = self.rodar(["--sqlite", caminho])
        self.assertEqual(codigo, 5)
        self.assertIn("nenhum bloco livre", erro)
        conn = sqlite3.connect(caminho)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0], 1)
        conn.close()

    def test_dia_nunca_hoje_nem_amanha(self):
        inicio = datetime(2026, 1, 1, 12, 0, tzinfo=prova.FUSO_BRASILIA)
        for dias in range(366):
            agora = inicio + timedelta(days=dias)
            dia = prova.escolher_dia(agora)
            self.assertNotIn(dia, {agora.day, (agora + timedelta(days=1)).day})
            self.assertTrue(1 <= dia <= 28)

    def test_init_data_da_prova_valida_com_o_token_falso(self):
        from api.initdata import validate_init_data
        usuario = validate_init_data(prova.assinar_init_data(-9600001), prova.TOKEN_FALSO)
        self.assertEqual(usuario["id"], -9600001)

    def test_conexao_intermediaria_esconde_e_injeta_contra_sqlite(self):
        caminho = self.banco_sqlite()
        trava = prova.Trava()
        base = prova.ConexaoTravada(sqlite3.connect(caminho), trava)
        intermediaria = prova.ConexaoIntermediaria(base, esconder_prechecagem=1,
                                                   falha_na_categoria=(2, RuntimeError("artificial")),
                                                   falhar_uma_vez=True)
        self.assertIsNone(intermediaria.execute(cadastro.SQL_USUARIO_EXISTE, (4242,)).fetchone())
        self.assertIsNotNone(intermediaria.execute(cadastro.SQL_USUARIO_EXISTE, (4242,)).fetchone())
        self.assertEqual(intermediaria.prechecagens_escondidas, 1)
        sql = ('INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) '
               'VALUES (?, ?, ?, ?, ?)')
        intermediaria.execute(sql, (-9600001, 'Um', 0.0, 0.0, 'x'))
        with self.assertRaises(RuntimeError):
            intermediaria.execute(sql, (-9600001, 'Dois', 0.0, 0.0, 'x'))
        intermediaria.execute(sql, (-9600001, 'Três', 0.0, 0.0, 'x'))
        self.assertEqual((intermediaria.insercoes_categoria, intermediaria.falhas_injetadas), (3, 1))
        # Erro vindo do banco (chave primária): repassado e registrado.
        sql_usuario = ('INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) '
                       'VALUES (?, ?, ?, ?, ?)')
        intermediaria.execute(sql_usuario, (-9600001, 'A', 0.0, 10, None))
        with self.assertRaises(sqlite3.IntegrityError):
            intermediaria.execute(sql_usuario, (-9600001, 'B', 0.0, 10, None))
        self.assertEqual([type(e).__name__ for e in intermediaria.erros], ['IntegrityError'])
        self.assertEqual(intermediaria.insercoes_usuario, 2)
        intermediaria.rollback()
        intermediaria.close()
        self.assertEqual(trava.violacoes, [])

    def test_prova_inteira_contra_sqlite_passa_e_nao_toca_fora_da_faixa(self):
        """Os cenários P0 a P9, inclusive a conexão intermediária de P3 a P5,
        rodam contra SQLite: todos OK, código 0, nada fora da faixa mudou."""
        caminho = self.banco_sqlite()
        antes = sqlite3.connect(caminho)
        fora_antes = (antes.execute("SELECT * FROM usuarios").fetchall(),
                      antes.execute("SELECT * FROM categorias").fetchall())
        antes.close()
        codigo, saida, erro = self.rodar(["--sqlite", caminho])
        self.assertEqual(codigo, 0, saida + erro)
        for cenario in ("P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9"):
            linha = next(l for l in saida.splitlines() if l.startswith(f"| {cenario} - "))
            self.assertTrue(linha.endswith("| OK |"), linha)
        self.assertNotIn("FALHOU", saida)
        self.assertIn("DELETE FROM categorias WHERE user_id BETWEEN -9600999 AND -9600001;", saida)
        self.assertIn("DELETE FROM usuarios WHERE user_id BETWEEN -9600999 AND -9600001;", saida)
        self.assertIn("BLOCO DESTA RODADA: -9600100 a -9600001", saida)
        depois = sqlite3.connect(caminho)
        fora_depois = (depois.execute("SELECT * FROM usuarios WHERE user_id = 4242").fetchall(),
                       depois.execute("SELECT * FROM categorias WHERE user_id = 4242").fetchall())
        fora_da_faixa = depois.execute("SELECT COUNT(*) FROM usuarios WHERE user_id NOT BETWEEN -9600999 AND -9600001 "
                                       "UNION ALL SELECT COUNT(*) FROM categorias WHERE user_id NOT BETWEEN -9600999 "
                                       "AND -9600001").fetchall()
        # P4: nada ficou gravado para o id do cenário (o terceiro id do bloco).
        p4 = depois.execute("SELECT COUNT(*) FROM usuarios WHERE user_id = -9600003 UNION ALL "
                            "SELECT COUNT(*) FROM categorias WHERE user_id = -9600003").fetchall()
        depois.close()
        self.assertEqual(fora_depois, fora_antes)
        self.assertEqual(fora_da_faixa, [(1,), (1,)])
        self.assertEqual(p4, [(0,), (0,)])
        # Uma segunda rodada usa o bloco seguinte.
        codigo, saida, erro = self.rodar(["--sqlite", caminho])
        self.assertEqual(codigo, 0, saida + erro)
        self.assertIn("BLOCO DESTA RODADA: -9600200 a -9600101", saida)


if __name__ == "__main__":
    unittest.main()
