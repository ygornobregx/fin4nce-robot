"""Testes da receita recorrente (PRO-102): a tabela receitas_recorrentes
(criada pelo iniciar_banco do bot.py), o módulo recorrentes.py (validação,
nome repetido, lista, criação com chave, edição, exclusão e o lançamento no
dia), o parâmetro novo da registro.registrar_receita, as rotas
GET/POST /receitas-recorrentes e PUT/DELETE /receitas-recorrentes/{id}, o job
das 05:00 do bot (gastos e receitas no mesmo dia, títulos, ciclo, dias 29 a
31), a Sobra pelo calculos.py nos dias de borda da fatura e o texto da renda
na prévia da receita do bot.

Roda com `python testes_receitas_recorrentes.py`. Não usa .env, rede nem
banco real:
  - antes do `import bot`, o load_dotenv vira no-op, o ambiente recebe valores
    vazios e o libsql.connect passa a levantar erro (como no
    testes_registro_caracterizacao.py): qualquer conexão real derruba o teste;
  - as rotas reaproveitam a base do testes_rotas_registro.py (initData
    assinado com o token falso, SQLite em ARQUIVO temporário com uma conexão
    nova por requisição, esperas sem dormir e o "agora" da API fixo em
    05/11/2026 22:30 em Brasília);
  - o job e a prévia rodam as funções REAIS do bot.py, com o
    obter_conexao_db trocado por um SQLite em arquivo, o relógio do bot
    congelado (sempre em Brasília) e um contexto falso do Telegram;
  - o CREATE TABLE receitas_recorrentes vem do próprio bot.py (o esquema do
    teste não diverge do real).

Usuários da base (criar_banco do testes_rotas_registro.py): UID (renda
5.000,00, fechamento 28; categorias Alimentação, Casa e Roupas), OUTRO_UID
(renda 1.000,00, fechamento 10; categoria Lazer), SEM_CADASTRO e INCOMPLETO.
"""
import ast
import asyncio
import contextlib
import io
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# --- COSTURA: nada de .env nem conexão real (antes de qualquer import do projeto) ---
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ['TOKEN'] = ''
os.environ['TURSO_DATABASE_URL'] = ''
os.environ['TURSO_AUTH_TOKEN'] = ''

import libsql  # noqa: E402


def _libsql_proibido(*args, **kwargs):
    raise RuntimeError("Conexão real com o banco é proibida nos testes.")


libsql.connect = _libsql_proibido

from fastapi.testclient import TestClient  # noqa: E402

import bot  # noqa: E402
import calculos  # noqa: E402
import recorrentes  # noqa: E402
import registro  # noqa: E402
from api.main import STATUS_ERRO_REGISTRO, app  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402
from testes_rotas_registro import (  # noqa: E402
    CHAVE, CHAVE_2, ERRO_BUSY, INCOMPLETO, MOMENTO_BRASILIA, OUTRO_UID, SEM_CADASTRO, UID,
    ConexaoEmbrulhada, IAFalsa, PlanoDeFalhas, TesteRegistroBase, comando_chaves_registro, corpo_gasto,
    corpo_receita, criar_banco, resposta_receita,
)

logging.getLogger("api").addHandler(logging.NullHandler())

CHAVE_3 = '0c9e7a52-1b3d-4f6a-a8e2-5d4c3b2a1f00'
CRIADO_ANTES = '2026-10-01 08:00:00'
COLUNAS = 'user_id, descricao, valor, dia_recebimento, criado_em, ultimo_ciclo_recebido'


def comando_receitas_recorrentes():
    """O CREATE TABLE receitas_recorrentes exatamente como está no bot.py."""
    with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
        fonte = f.read()
    achado = re.search(r"'''(CREATE TABLE IF NOT EXISTS receitas_recorrentes \(.*?\))'''", fonte, re.DOTALL)
    assert achado, "CREATE TABLE receitas_recorrentes não encontrado no bot.py"
    return achado.group(1)


def criar_tabela(caminho):
    conn = sqlite3.connect(caminho)
    conn.execute(comando_receitas_recorrentes())
    conn.commit()
    conn.close()


def corpo_rr(**mudancas):
    corpo = {"chave": CHAVE, "dia_recebimento": 5, "valor": 4500.0, "descricao": "Salário"}
    corpo.update(mudancas)
    return corpo


def corpo_edicao(**mudancas):
    corpo = corpo_rr(**mudancas)
    corpo.pop("chave", None)
    return corpo


def inserir_rr(caminho, descricao, user_id=UID, valor=4500.0, dia=5, criado_em=CRIADO_ANTES, ultimo_ciclo=None):
    """Receita recorrente gravada direto no banco."""
    conn = sqlite3.connect(caminho)
    try:
        id_novo = conn.execute(
            "INSERT INTO receitas_recorrentes (user_id, descricao, valor, dia_recebimento, criado_em, "
            "ultimo_ciclo_recebido) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, descricao, valor, dia, criado_em, ultimo_ciclo)).lastrowid
        conn.commit()
        return id_novo
    finally:
        conn.close()


def ler(caminho, sql, params=()):
    conn = sqlite3.connect(caminho)
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


# =============================================================================
# BANCO, CONSTANTES E REGISTRO.PY
# =============================================================================
class TestTabelaNoIniciarBanco(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.caminho = os.path.join(self.pasta.name, 'bot.db')
        patcher = mock.patch.object(bot, 'obter_conexao_db', lambda: sqlite3.connect(self.caminho))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.pasta.cleanup()

    def iniciar(self):
        with contextlib.redirect_stdout(io.StringIO()):
            bot.iniciar_banco()

    def test_cria_a_tabela_com_as_colunas_e_restricoes_da_nota(self):
        self.iniciar()
        colunas = [(c[1], c[2], c[3], c[5]) for c in ler(self.caminho, "PRAGMA table_info(receitas_recorrentes)")]
        self.assertEqual(colunas, [
            ('id', 'INTEGER', 0, 1),
            ('user_id', 'INTEGER', 1, 0),
            ('descricao', 'TEXT', 1, 0),
            ('valor', 'REAL', 1, 0),
            ('dia_recebimento', 'INTEGER', 1, 0),
            ('criado_em', 'TEXT', 1, 0),
            ('ultimo_ciclo_recebido', 'TEXT', 0, 0),
        ])
        sql = ler(self.caminho, "SELECT sql FROM sqlite_master WHERE name = 'receitas_recorrentes'")[0][0]
        self.assertIn('AUTOINCREMENT', sql)
        self.assertIn('CHECK (valor > 0)', sql)

    def test_o_banco_recusa_valor_zero_e_campos_nulos(self):
        self.iniciar()
        conn = sqlite3.connect(self.caminho)
        base = "INSERT INTO receitas_recorrentes (user_id, descricao, valor, dia_recebimento, criado_em) VALUES "
        for valores in ("(1, 'Salário', 0, 5, '2026-11-01 10:00:00')", "(1, 'Salário', -1, 5, '2026-11-01 10:00:00')",
                        "(NULL, 'Salário', 10, 5, '2026-11-01 10:00:00')", "(1, NULL, 10, 5, '2026-11-01 10:00:00')",
                        "(1, 'Salário', NULL, 5, '2026-11-01 10:00:00')", "(1, 'Salário', 10, NULL, '2026-11-01 10:00:00')",
                        "(1, 'Salário', 10, 5, NULL)"):
            with self.subTest(valores=valores):
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(base + valores)
        conn.execute(base + "(1, 'Salário', 0.01, 5, '2026-11-01 10:00:00')")  # o mínimo passa
        conn.close()

    def test_rodar_de_novo_nao_muda_nada(self):
        self.iniciar()
        inserir_rr(self.caminho, 'Salário')
        antes = ler(self.caminho, "SELECT type, name, sql FROM sqlite_master ORDER BY name")
        self.iniciar()
        self.assertEqual(ler(self.caminho, "SELECT type, name, sql FROM sqlite_master ORDER BY name"), antes)
        self.assertEqual(len(ler(self.caminho, "SELECT * FROM receitas_recorrentes")), 1)

    def test_aditiva_nenhuma_outra_tabela_muda(self):
        # Banco "de produção" de antes da PRO-102 (o ESQUEMA compartilhado já
        # migrado + chaves_registro), com dados: o iniciar_banco só acrescenta
        # a tabela nova.
        conn = sqlite3.connect(self.caminho)
        conn.executescript(ESQUEMA)
        conn.execute(comando_chaves_registro())
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (1, 'Y', 0, 5)")
        conn.execute("INSERT INTO receitas (user_id, valor, descricao, data_receita) "
                     "VALUES (1, 10, 'x', '2026-11-05 10:00:00')")
        conn.commit()
        antes_esquema = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
        antes_dados = conn.execute("SELECT * FROM receitas").fetchall()
        conn.close()
        self.iniciar()
        depois = ler(self.caminho, "SELECT name, sql FROM sqlite_master ORDER BY name")
        # Desde a PRO-106 o iniciar_banco também cria as tabelas assinaturas e
        # uso_ia (e os índices delas), conferidas no testes_assinaturas.py.
        da_pro106 = ('assinaturas', 'uso_ia', 'sqlite_autoindex_uso_ia_1', 'ux_assinaturas_pagamento',
                     'ux_assinaturas_teste', 'ix_assinaturas_usuario')
        self.assertEqual([linha for linha in depois if linha[0] != 'receitas_recorrentes'
                          and linha[0] != 'sqlite_sequence' and linha[0] not in da_pro106],
                         [linha for linha in antes_esquema if linha[0] != 'sqlite_sequence'])
        self.assertIn('receitas_recorrentes', [linha[0] for linha in depois])
        self.assertEqual(ler(self.caminho, "SELECT * FROM receitas"), antes_dados)

    def test_colunas_esperadas_nao_muda(self):
        self.assertNotIn('receitas_recorrentes', bot.COLUNAS_ESPERADAS)
        self.assertEqual(bot.COLUNAS_ESPERADAS['gastos_recorrentes'], [('estabelecimento', 'TEXT')])


class TestConstantesEModulo(unittest.TestCase):
    def test_tipo_da_chave(self):
        self.assertEqual(recorrentes.TIPO_CHAVE_RECEITA, 'receita_recorrente')
        self.assertEqual(recorrentes.TIPO_CHAVE, 'recorrente')  # o do gasto não muda

    def test_status_404_da_receita_recorrente(self):
        self.assertEqual(STATUS_ERRO_REGISTRO['receita_recorrente_nao_encontrada'], 404)
        self.assertEqual(STATUS_ERRO_REGISTRO['recorrente_nao_encontrado'], 404)

    def test_modulo_continua_sem_telegram_bot_dotenv_nem_libsql(self):
        with open(os.path.join(RAIZ, 'recorrentes.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split('.')[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(no.module.split('.')[0])
        self.assertEqual(importados, {'typing', 'cadastro', 'registro'})

    def test_bot_usa_a_funcao_do_recorrentes(self):
        self.assertIs(bot.lancar_receita_recorrente, recorrentes.lancar_receita_recorrente)

    def test_calculos_nao_conhece_a_receita_recorrente(self):
        with open(os.path.join(RAIZ, 'calculos.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertNotIn('receitas_recorrentes', fonte)


class TestRegistrarReceitaComVinculo(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(ESQUEMA)

    def tearDown(self):
        self.conn.close()

    def linhas(self):
        return self.conn.execute("SELECT user_id, valor, descricao, data_receita, origem, receita_recorrente_id "
                                 "FROM receitas ORDER BY id").fetchall()

    def test_sem_o_parametro_continua_avulsa_com_null(self):
        registro.registrar_receita(self.conn, UID, 200, MOMENTO_BRASILIA, "Venda")
        self.assertEqual(self.linhas(), [(UID, 200.0, 'Venda', MOMENTO_BRASILIA, 'avulsa', None)])

    def test_com_o_parametro_grava_na_coluna(self):
        receita_id = registro.registrar_receita(self.conn, UID, 4500, MOMENTO_BRASILIA, " Salário ",
                                                origem='recorrente', receita_recorrente_id=7)
        self.assertEqual(receita_id, 1)
        self.assertEqual(self.linhas(), [(UID, 4500.0, 'Salário', MOMENTO_BRASILIA, 'recorrente', 7)])

    def test_commit_false_continua_sem_commit(self):
        registro.registrar_receita(self.conn, UID, 10, MOMENTO_BRASILIA, receita_recorrente_id=3, commit=False)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()
        self.assertEqual(self.linhas(), [])


# =============================================================================
# MÓDULO: VALIDAÇÃO E NOME REPETIDO
# =============================================================================
class TestValidarReceitaRecorrente(unittest.TestCase):
    def validar(self, dia=5, valor=4500.0, descricao="Salário"):
        return recorrentes.validar_receita_recorrente(dia, valor, descricao)

    def assertCodigo(self, codigo, **campos):
        with self.assertRaises(ErroRegistro) as ctx:
            self.validar(**campos)
        self.assertEqual(ctx.exception.codigo, codigo)

    def test_pedido_valido_devolve_os_campos_limpos(self):
        self.assertEqual(self.validar(descricao="  Salário  "), (5, 4500.0, "Salário"))
        self.assertEqual(self.validar(valor=10), (5, 10.0, "Salário"))
        self.assertIsInstance(self.validar(valor=10).valor, float)

    def test_dia_invalido(self):
        for dia in (0, 32, -1, 5.0, 5.5, "5", True, False, None, [5]):
            with self.subTest(dia=dia):
                self.assertCodigo('dia_invalido', dia=dia)

    def test_dia_nas_pontas_passa(self):
        for dia in (1, 29, 30, 31):
            with self.subTest(dia=dia):
                self.assertEqual(self.validar(dia=dia).dia_recebimento, dia)

    def test_valor_invalido(self):
        for valor in (0, -5, 0.001, 1_000_000.01, float('nan'), float('inf'), "10", True, None):
            with self.subTest(valor=valor):
                self.assertCodigo('valor_invalido', valor=valor)

    def test_valor_arredondado_e_teto_passa(self):
        self.assertEqual(self.validar(valor=10.456).valor, 10.46)
        self.assertEqual(self.validar(valor=1_000_000).valor, 1_000_000.0)
        self.assertEqual(self.validar(valor=0.01).valor, 0.01)

    def test_descricao_invalida(self):
        for descricao in (None, "", "   ", "\t\n", 5, ["Salário"]):
            with self.subTest(descricao=descricao):
                self.assertCodigo('descricao_invalida', descricao=descricao)

    def test_descricao_longa_conta_depois_de_aparar(self):
        self.assertCodigo('descricao_longa', descricao="x" * 31)
        self.assertEqual(self.validar(descricao="  " + "x" * 30 + "  ").descricao, "x" * 30)

    def test_ordem_da_validacao(self):
        tudo_errado = dict(dia=0, valor=0, descricao="")
        ordem = [('dia_invalido', 'dia', 5), ('valor_invalido', 'valor', 10.0),
                 ('descricao_invalida', 'descricao', "x" * 31), ('descricao_longa', 'descricao', "Ok")]
        for codigo, campo, valor_certo in ordem:
            with self.subTest(codigo=codigo):
                self.assertCodigo(codigo, **tudo_errado)
            tudo_errado[campo] = valor_certo
        self.validar(**tudo_errado)

    def test_mesmas_regras_do_gasto_recorrente(self):
        # A validação comum foi extraída sem mudar o gasto: os mesmos casos
        # dão os mesmos códigos nos dois.
        for dia, valor, descricao in ((0, 1, "a"), (1, 0, "a"), (1, 1, " "), (1, 1, "x" * 31)):
            with self.subTest(dia=dia, valor=valor, descricao=descricao):
                with self.assertRaises(ErroRegistro) as gasto:
                    recorrentes.validar_recorrente(dia, valor, descricao, None, 'Casa', ['Casa'])
                with self.assertRaises(ErroRegistro) as receita:
                    recorrentes.validar_receita_recorrente(dia, valor, descricao)
                self.assertEqual(gasto.exception.codigo, receita.exception.codigo)


class TestReceitaRepetida(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.execute(comando_receitas_recorrentes())
        for user_id, descricao in ((UID, 'Salário'), (UID, '  Aluguel do Quarto  '), (OUTRO_UID, 'Pensão')):
            self.conn.execute("INSERT INTO receitas_recorrentes (user_id, descricao, valor, dia_recebimento, "
                              "criado_em) VALUES (?, ?, 10, 5, ?)", (user_id, descricao, CRIADO_ANTES))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def repetida(self, descricao, ignorar_id=None, user_id=UID):
        return recorrentes.tem_receita_repetida(self.conn, user_id, descricao, ignorar_id)

    def test_maiusculas_e_minusculas_nao_diferenciam(self):
        self.assertTrue(self.repetida('salário'))
        self.assertTrue(self.repetida('SALÁRIO'))
        self.assertTrue(self.repetida('ALUGUEL DO QUARTO'))

    def test_espacos_nas_pontas_da_gravada_nao_contam(self):
        self.assertTrue(self.repetida('Aluguel do Quarto'))

    def test_acento_conta(self):
        self.assertFalse(self.repetida('Salario'))

    def test_outro_usuario_nao_conta(self):
        self.assertFalse(self.repetida('Pensão'))
        self.assertFalse(self.repetida('Salário', user_id=OUTRO_UID))

    def test_ignorar_o_proprio_item(self):
        self.assertFalse(self.repetida('Salário', ignorar_id=1))
        self.assertTrue(self.repetida('Aluguel do Quarto', ignorar_id=1))

    def test_nao_olha_os_gastos_recorrentes(self):
        conn = sqlite3.connect(':memory:')
        conn.executescript(ESQUEMA)
        conn.execute(comando_receitas_recorrentes())
        conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                     "criado_em) VALUES (?, 'Casa', 'Salário', 10, 5, ?)", (UID, CRIADO_ANTES))
        self.assertFalse(recorrentes.tem_receita_repetida(conn, UID, 'Salário'))
        conn.close()


# =============================================================================
# ROTAS
# =============================================================================
class TesteReceitasRecorrentesBase(TesteRegistroBase):
    """A base das rotas de registro, com a tabela nova criada pelo comando do bot."""

    def setUp(self):
        super().setUp()
        criar_tabela(self.caminho)

    def get(self, rota, user_id=UID, client=None):
        return (client or self.client).get(rota, headers=self.cabecalho(user_id))

    def put(self, rota, corpo, user_id=UID, client=None):
        return (client or self.client).put(rota, json=corpo, headers=self.cabecalho(user_id))

    def delete(self, rota, user_id=UID, client=None):
        return (client or self.client).delete(rota, headers=self.cabecalho(user_id))

    def criar(self, user_id=UID, client=None, **mudancas):
        return self.post("/receitas-recorrentes", corpo_rr(**mudancas), user_id=user_id, client=client)

    def inserir(self, descricao, **kwargs):
        return inserir_rr(self.caminho, descricao, **kwargs)

    def linha(self, rr_id):
        linhas = self.ler(f"SELECT {COLUNAS} FROM receitas_recorrentes WHERE id = ?", (rr_id,))
        return linhas[0] if linhas else None

    def tabela(self):
        return self.ler("SELECT * FROM receitas_recorrentes ORDER BY id")


ROTAS = (("GET", "/receitas-recorrentes", None), ("POST", "/receitas-recorrentes", corpo_rr()),
         ("PUT", "/receitas-recorrentes/1", corpo_edicao()), ("DELETE", "/receitas-recorrentes/1", None))


class TestAutenticacaoECadastro(TesteReceitasRecorrentesBase):
    def pedir(self, metodo, rota, corpo, headers):
        if corpo is None:
            return self.client.request(metodo, rota, headers=headers)
        return self.client.request(metodo, rota, json=corpo, headers=headers)

    def test_sem_cabecalho_e_initdata_invalido_401(self):
        self.inserir('Salário')
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, {}), 401, "nao_autorizado")
                self.assertErro(self.pedir(metodo, rota, corpo, {"Authorization": "tma invalido"}),
                                401, "nao_autorizado")
        self.assertEqual(len(self.tabela()), 1)
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_usuario_nao_cadastrado_404_e_cadastro_incompleto_409(self):
        self.inserir('Salário')
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(SEM_CADASTRO)),
                                404, "usuario_nao_cadastrado")
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(INCOMPLETO)),
                                409, "cadastro_incompleto")
        self.assertEqual(len(self.tabela()), 1)

    def test_corpo_que_nao_e_json_422_padrao(self):
        self.inserir('Salário')
        for metodo, rota in (("POST", "/receitas-recorrentes"), ("PUT", "/receitas-recorrentes/1")):
            with self.subTest(metodo=metodo):
                resposta = self.client.request(metodo, rota, content=b"isto nao e json",
                                               headers={**self.cabecalho(), "Content-Type": "application/json"})
                self.assertEqual(resposta.status_code, 422)
                self.assertIsInstance(resposta.json()["detail"], list)

    def test_id_na_url_que_nao_e_inteiro_422_padrao(self):
        for resposta in (self.put("/receitas-recorrentes/abc", corpo_edicao()),
                         self.delete("/receitas-recorrentes/1.5")):
            self.assertEqual(resposta.status_code, 422)
            self.assertIsInstance(resposta.json()["detail"], list)


class TestListar(TesteReceitasRecorrentesBase):
    def test_sem_nenhuma(self):
        resposta = self.get("/receitas-recorrentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"receitas_recorrentes": [], "total_mensal": 0.0})
        self.assertIsInstance(resposta.json()["total_mensal"], float)

    def test_contrato_ordem_por_dia_e_id_total_e_so_do_usuario(self):
        self.inserir('Aluguel do Quarto', dia=20, valor=850.0)
        self.inserir('Salário', dia=5, valor=4500.0)
        self.inserir('Freela Fixo', dia=20, valor=1000.0)
        self.inserir('Pensão', user_id=OUTRO_UID, dia=1, valor=300.0)
        resposta = self.get("/receitas-recorrentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {
            "receitas_recorrentes": [
                {"id": 2, "descricao": "Salário", "valor": 4500.0, "dia_recebimento": 5},
                {"id": 1, "descricao": "Aluguel do Quarto", "valor": 850.0, "dia_recebimento": 20},
                {"id": 3, "descricao": "Freela Fixo", "valor": 1000.0, "dia_recebimento": 20},
            ],
            "total_mensal": 6350.0,
        })
        for item in resposta.json()["receitas_recorrentes"]:
            self.assertEqual(set(item), {"id", "descricao", "valor", "dia_recebimento"})
            self.assertIsInstance(item["id"], int)
            self.assertIsInstance(item["valor"], float)
            self.assertIsInstance(item["dia_recebimento"], int)
        self.assertEqual(self.get("/receitas-recorrentes", user_id=OUTRO_UID).json(),
                         {"receitas_recorrentes": [{"id": 4, "descricao": "Pensão", "valor": 300.0,
                                                    "dia_recebimento": 1}], "total_mensal": 300.0})

    def test_total_arredondado_so_na_resposta(self):
        self.inserir('A', valor=0.1)
        self.inserir('B', valor=0.2)
        self.assertEqual(self.get("/receitas-recorrentes").json()["total_mensal"], 0.3)

    def test_gastos_recorrentes_nao_entram_e_vice_versa(self):
        self.inserir('Salário')
        conn = sqlite3.connect(self.caminho)
        conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                     "criado_em) VALUES (?, 'Casa', 'Internet', 99.9, 10, ?)", (UID, CRIADO_ANTES))
        conn.commit()
        conn.close()
        self.assertEqual([r["descricao"] for r in self.get("/receitas-recorrentes").json()["receitas_recorrentes"]],
                         ["Salário"])
        self.assertEqual([r["descricao"] for r in self.get("/recorrentes").json()["recorrentes"]], ["Internet"])

    def test_erro_inesperado_500_sem_detalhes(self):
        client = TestClient(app, raise_server_exceptions=False)
        with mock.patch.object(recorrentes, "listar_receitas_recorrentes", side_effect=ValueError("boom detalhe")), \
                self.assertLogs("api", level="ERROR") as logs:
            resposta = self.get("/receitas-recorrentes", client=client)
        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta.json(), {"detail": "erro_interno"})
        self.assertIn("Receitas recorrentes falhou: erro_interno (ValueError)", "\n".join(logs.output))
        self.assertNotIn("boom", "\n".join(logs.output))


class TestCriar(TesteReceitasRecorrentesBase):
    def test_contrato_201_e_o_que_fica_gravado(self):
        resposta = self.criar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json(), {"id": 1, "descricao": "Salário", "valor": 4500.0, "dia_recebimento": 5,
                                           "repetido": False})
        # criado_em do servidor, em Brasília; nada lançado ainda (D4).
        self.assertEqual(self.linha(1), (UID, 'Salário', 4500.0, 5, MOMENTO_BRASILIA, None))
        self.assertEqual(self.ler("SELECT user_id, chave, tipo, lancamento_id, criado_em FROM chaves_registro"),
                         [(UID, CHAVE, 'receita_recorrente', 1, MOMENTO_BRASILIA)])
        self.assertEqual((self.contar("receitas"), self.contar("transacoes"), self.contar("gastos_recorrentes")),
                         (0, 0, 0))

    def test_criar_com_o_dia_de_hoje_nao_lanca_na_hora(self):
        # "Agora" da API: 05/11/2026; criar com dia 5 não grava receita.
        self.assertEqual(self.criar(dia_recebimento=5).status_code, 201)
        self.assertEqual(self.contar("receitas"), 0)
        self.assertIsNone(self.linha(1)[5])

    def test_descricao_aparada_e_chave_em_minusculas(self):
        resposta = self.criar(descricao="  Salário  ", chave=CHAVE.upper())
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json()["descricao"], "Salário")
        self.assertEqual(self.ler("SELECT chave FROM chaves_registro"), [(CHAVE,)])

    def test_campos_a_mais_sao_ignorados(self):
        corpo = corpo_rr(user_id=OUTRO_UID, criado_em="2000-01-01 00:00:00", ultimo_ciclo_recebido="2026-11",
                         id=99, categoria="Casa", estabelecimento="Empresa")
        resposta = self.post("/receitas-recorrentes", corpo)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json(), {"id": 1, "descricao": "Salário", "valor": 4500.0, "dia_recebimento": 5,
                                           "repetido": False})
        self.assertEqual(self.linha(1), (UID, 'Salário', 4500.0, 5, MOMENTO_BRASILIA, None))

    def test_codigos_de_validacao_sem_gravar(self):
        casos = [
            ('chave_invalida', dict(chave="nao-e-uuid")), ('chave_invalida', dict(chave=None)),
            ('dia_invalido', dict(dia_recebimento=0)), ('dia_invalido', dict(dia_recebimento=32)),
            ('dia_invalido', dict(dia_recebimento=5.0)), ('dia_invalido', dict(dia_recebimento="5")),
            ('dia_invalido', dict(dia_recebimento=True)), ('dia_invalido', dict(dia_recebimento=None)),
            ('valor_invalido', dict(valor=0)), ('valor_invalido', dict(valor=-10)),
            ('valor_invalido', dict(valor=1_000_000.01)), ('valor_invalido', dict(valor="4500")),
            ('valor_invalido', dict(valor=None)),
            ('descricao_invalida', dict(descricao="   ")), ('descricao_invalida', dict(descricao=None)),
            ('descricao_invalida', dict(descricao=30)),
            ('descricao_longa', dict(descricao="x" * 31)),
        ]
        for codigo, mudancas in casos:
            with self.subTest(codigo=codigo, mudancas=mudancas):
                self.assertErro(self.criar(**mudancas), 422, codigo)
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (0, 0))

    def test_ordem_dos_422(self):
        self.inserir("Repetida")
        corpo = dict(chave="x", dia_recebimento=0, valor=0, descricao="")
        ordem = [('chave_invalida', 'chave', CHAVE), ('dia_invalido', 'dia_recebimento', 1),
                 ('valor_invalido', 'valor', 10.0), ('descricao_invalida', 'descricao', "x" * 31),
                 ('descricao_longa', 'descricao', "repetida"), ('descricao_repetida', 'descricao', "Nova")]
        for codigo, campo, valor_certo in ordem:
            with self.subTest(codigo=codigo):
                self.assertErro(self.post("/receitas-recorrentes", corpo), 422, codigo)
            corpo[campo] = valor_certo
        self.assertEqual(self.post("/receitas-recorrentes", corpo).status_code, 201)

    def test_limites_aceitos(self):
        resposta = self.criar(dia_recebimento=31, valor=1_000_000, descricao="x" * 30)
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.criar(chave=CHAVE_2, dia_recebimento=1, valor=0.01, descricao="y").status_code, 201)

    def test_toque_duplo_em_sequencia(self):
        primeira = self.criar()
        segunda = self.criar()
        self.assertEqual((primeira.status_code, segunda.status_code), (201, 200))
        self.assertEqual(segunda.json(), dict(primeira.json(), repetido=True))
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))

    def test_mesma_chave_com_outro_corpo_devolve_a_original_como_esta_agora(self):
        self.criar()
        self.assertEqual(self.put("/receitas-recorrentes/1", corpo_edicao(valor=4800.0)).status_code, 200)
        resposta = self.criar(valor=999.0, dia_recebimento=0, descricao="")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"id": 1, "descricao": "Salário", "valor": 4800.0, "dia_recebimento": 5,
                                           "repetido": True})
        self.assertEqual(len(self.tabela()), 1)

    def test_reenvio_que_passa_da_consulta_bate_no_nome_e_devolve_repetido(self):
        """O reenvio do mesmo pedido, quando a consulta da chave antes de
        gravar não a acha: a checagem do nome, DENTRO da gravação, levanta
        descricao_repetida; a gravar_com_chave desfaz, acha a chave e devolve
        200 repetido, nunca 422 descricao_repetida."""
        primeira = self.criar()
        original_buscar = registro.buscar_chave
        chamadas = [0]

        def buscar(conn, user_id, chave):
            chamadas[0] += 1
            return None if chamadas[0] == 1 else original_buscar(conn, user_id, chave)

        resultados = []
        original_repetida = recorrentes.tem_receita_repetida

        def repetida(*args, **kwargs):
            resultados.append(original_repetida(*args, **kwargs))
            return resultados[-1]

        with mock.patch.object(registro, "buscar_chave", buscar), \
                mock.patch.object(recorrentes, "tem_receita_repetida", repetida):
            segunda = self.criar()
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(segunda.json(), dict(primeira.json(), repetido=True))
        self.assertEqual(resultados, [True])
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))

    def test_toque_duplo_em_duas_threads(self):
        """Os dois pedidos passam pela consulta da chave (nenhum acha nada)
        antes de qualquer um gravar: só a chave primária (ou o nome repetido,
        dentro da gravação) decide, e o perdedor devolve a original."""
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
            respostas.append(self.criar(client=TestClient(app)))

        with mock.patch.object(registro, "buscar_chave", buscar_com_barreira):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 201], [r.text for r in respostas])
        self.assertEqual(len({r.json()["id"] for r in respostas}), 1)
        self.assertEqual(sorted(r.json()["repetido"] for r in respostas), [False, True])
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))
        self.assertGreaterEqual(contagem[0], 3)

    def test_chave_de_gasto_receita_ou_gasto_recorrente_da_chave_invalida(self):
        self.assertEqual(self.post("/gastos", corpo_gasto(parcelas=1)).status_code, 201)
        self.assertEqual(self.post("/receitas", corpo_receita(chave=CHAVE_2)).status_code, 201)
        self.assertEqual(self.post("/recorrentes", {"chave": CHAVE_3, "dia_cobranca": 10, "valor": 99.9,
                                                    "descricao": "Internet", "categoria": "Casa"}).status_code, 201)
        for chave in (CHAVE, CHAVE_2, CHAVE_3):
            with self.subTest(chave=chave):
                self.assertErro(self.criar(chave=chave), 422, "chave_invalida")
        self.assertEqual(len(self.tabela()), 0)

    def test_chave_de_receita_recorrente_nao_serve_para_as_outras_rotas(self):
        self.assertEqual(self.criar().status_code, 201)
        self.assertErro(self.post("/gastos", corpo_gasto(parcelas=1)), 422, "chave_invalida")
        self.assertErro(self.post("/receitas", corpo_receita()), 422, "chave_invalida")
        self.assertErro(self.post("/recorrentes", {"chave": CHAVE, "dia_cobranca": 10, "valor": 99.9,
                                                   "descricao": "Internet", "categoria": "Casa"}),
                        422, "chave_invalida")
        self.assertEqual((self.contar("transacoes"), self.contar("receitas"), self.contar("gastos_recorrentes")),
                         (0, 0, 0))

    def test_chave_de_receita_recorrente_apagada_da_chave_invalida(self):
        self.criar()
        self.assertEqual(self.delete("/receitas-recorrentes/1").status_code, 200)
        self.assertErro(self.criar(), 422, "chave_invalida")
        self.assertEqual(len(self.tabela()), 0)

    def test_mesma_chave_de_outro_usuario_grava_separado(self):
        primeira = self.criar()
        outra = self.criar(user_id=OUTRO_UID)
        self.assertEqual(outra.status_code, 201)
        self.assertNotEqual(outra.json()["id"], primeira.json()["id"])
        self.assertEqual(self.ler("SELECT user_id, chave, tipo FROM chaves_registro ORDER BY user_id"),
                         [(UID, CHAVE, 'receita_recorrente'), (OUTRO_UID, CHAVE, 'receita_recorrente')])


class TestNomeRepetido(TesteReceitasRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.salario = self.inserir('Salário', dia=5)
        self.aluguel = self.inserir('  Aluguel do Quarto  ', dia=10, ultimo_ciclo='2026-11')

    def test_post_recusa_o_mesmo_nome(self):
        for descricao in ('Salário', 'salário', 'SALÁRIO', '  salário  ', 'aluguel do quarto', 'ALUGUEL DO QUARTO '):
            with self.subTest(descricao=descricao):
                self.assertErro(self.criar(descricao=descricao), 422, "descricao_repetida")
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (2, 0))

    def test_post_aceita_com_acento_diferente(self):
        self.assertEqual(self.criar(descricao='Salario').status_code, 201)

    def test_post_de_outro_usuario_nao_conta(self):
        self.assertEqual(self.criar(user_id=OUTRO_UID, descricao='Salário').status_code, 201)

    def test_put_que_mantem_o_proprio_nome_passa(self):
        resposta = self.put(f"/receitas-recorrentes/{self.salario}", corpo_edicao(descricao='SALÁRIO', valor=4800.0))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["descricao"], 'SALÁRIO')

    def test_put_com_o_nome_de_outra_recusa_sem_alterar_nada(self):
        antes = self.tabela()
        resposta = self.put(f"/receitas-recorrentes/{self.aluguel}",
                            corpo_edicao(descricao='salário', valor=999.99, dia_recebimento=28))
        self.assertErro(resposta, 422, "descricao_repetida")
        self.assertEqual(self.tabela(), antes)
        self.assertEqual(self.linha(self.aluguel)[4:], (CRIADO_ANTES, '2026-11'))

    def test_dois_iguais_gravados_direto_put_sem_trocar_o_nome_recusa(self):
        outro = self.inserir('salário')
        self.assertErro(self.put(f"/receitas-recorrentes/{outro}", corpo_edicao(descricao='salário')),
                        422, "descricao_repetida")
        self.assertEqual(self.put(f"/receitas-recorrentes/{outro}", corpo_edicao(descricao='Salário 2')).status_code,
                         200)


class TestEditar(TesteReceitasRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.id = self.inserir('Salário', valor=4500.0, dia=5, ultimo_ciclo='2026-11')
        self.alheio = self.inserir('Pensão', user_id=OUTRO_UID)

    def test_contrato_e_os_3_campos_sem_mexer_no_ciclo_nem_no_criado_em(self):
        resposta = self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao(
            dia_recebimento=6, valor=4800.555, descricao="  Salário Novo  ", user_id=OUTRO_UID,
            criado_em="2000-01-01 00:00:00", ultimo_ciclo_recebido=None, chave=CHAVE))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"id": self.id, "descricao": "Salário Novo", "valor": 4800.56,
                                           "dia_recebimento": 6})
        self.assertEqual(self.linha(self.id), (UID, 'Salário Novo', 4800.56, 6, CRIADO_ANTES, '2026-11'))
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_mesmo_put_repetido_da_o_mesmo_resultado(self):
        primeira = self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao())
        segunda = self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao())
        self.assertEqual((primeira.status_code, segunda.status_code), (200, 200))
        self.assertEqual(primeira.json(), segunda.json())

    def test_nao_mexe_nas_receitas_ja_lancadas(self):
        conn = sqlite3.connect(self.caminho)
        conn.execute("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem, receita_recorrente_id) "
                     "VALUES (?, 4500, 'Salário', '2026-11-05 05:00:00', 'recorrente', ?)", (UID, self.id))
        conn.commit()
        conn.close()
        self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao(valor=9000.0, descricao="Outro"))
        self.assertEqual(self.ler("SELECT valor, descricao, receita_recorrente_id FROM receitas"),
                         [(4500.0, 'Salário', self.id)])

    def test_codigos_de_validacao_sem_gravar(self):
        antes = self.tabela()
        casos = [('dia_invalido', dict(dia_recebimento=0)), ('dia_invalido', dict(dia_recebimento=5.0)),
                 ('valor_invalido', dict(valor=0)), ('valor_invalido', dict(valor=1_000_000.01)),
                 ('descricao_invalida', dict(descricao="")), ('descricao_longa', dict(descricao="x" * 31))]
        for codigo, mudancas in casos:
            with self.subTest(codigo=codigo):
                self.assertErro(self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao(**mudancas)), 422, codigo)
        self.assertEqual(self.tabela(), antes)

    def test_ordem_campos_depois_existencia_depois_nome_repetido(self):
        self.inserir('Freela')
        self.assertErro(self.put("/receitas-recorrentes/999", corpo_edicao(dia_recebimento=0)), 422, "dia_invalido")
        self.assertErro(self.put("/receitas-recorrentes/999", corpo_edicao(descricao="x" * 31)), 422,
                        "descricao_longa")
        self.assertErro(self.put("/receitas-recorrentes/999", corpo_edicao(descricao="Freela")), 404,
                        "receita_recorrente_nao_encontrada")
        self.assertErro(self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao(descricao="Freela")), 422,
                        "descricao_repetida")

    def test_id_de_outro_usuario_404_sem_mexer_em_nada(self):
        antes = self.tabela()
        self.assertErro(self.put(f"/receitas-recorrentes/{self.alheio}", corpo_edicao()), 404,
                        "receita_recorrente_nao_encontrada")
        self.assertErro(self.put(f"/receitas-recorrentes/{self.id}", corpo_edicao(), user_id=OUTRO_UID), 404,
                        "receita_recorrente_nao_encontrada")
        self.assertEqual(self.tabela(), antes)

    def test_id_enorme_404(self):
        self.assertErro(self.put("/receitas-recorrentes/99999999999999999999", corpo_edicao()), 404,
                        "receita_recorrente_nao_encontrada")
        self.assertErro(self.delete("/receitas-recorrentes/99999999999999999999"), 404,
                        "receita_recorrente_nao_encontrada")


class TestApagar(TesteReceitasRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.id = self.inserir('Salário')
        self.alheio = self.inserir('Pensão', user_id=OUTRO_UID)

    def test_contrato_e_404_no_repetido(self):
        resposta = self.delete(f"/receitas-recorrentes/{self.id}")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"id": self.id, "apagado": True})
        self.assertIsNone(self.linha(self.id))
        self.assertErro(self.delete(f"/receitas-recorrentes/{self.id}"), 404, "receita_recorrente_nao_encontrada")
        self.assertErro(self.delete("/receitas-recorrentes/999"), 404, "receita_recorrente_nao_encontrada")

    def test_id_de_outro_usuario_404_sem_apagar(self):
        self.assertErro(self.delete(f"/receitas-recorrentes/{self.alheio}"), 404,
                        "receita_recorrente_nao_encontrada")
        self.assertIsNotNone(self.linha(self.alheio))
        self.assertEqual(self.get("/receitas-recorrentes", user_id=OUTRO_UID).json()["receitas_recorrentes"][0]["id"],
                         self.alheio)

    def test_receitas_ja_lancadas_ficam_com_o_id_antigo(self):
        conn = sqlite3.connect(self.caminho)
        conn.execute("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem, receita_recorrente_id) "
                     "VALUES (?, 4500, 'Salário', '2026-11-05 05:00:00', 'recorrente', ?)", (UID, self.id))
        conn.commit()
        conn.close()
        self.assertEqual(self.delete(f"/receitas-recorrentes/{self.id}").status_code, 200)
        self.assertEqual(self.ler("SELECT valor, origem, receita_recorrente_id FROM receitas"),
                         [(4500.0, 'recorrente', self.id)])


class TestBancoOcupado(TesteReceitasRecorrentesBase):
    def planejar(self, falhas_commit=(), falhas_consulta=()):
        self.plano = PlanoDeFalhas(falhas_commit, falhas_consulta)
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=self.plano)
        return self.plano

    def test_post_busy_uma_vez_tenta_de_novo_e_grava(self):
        plano = self.planejar([ValueError(ERRO_BUSY)])
        resposta = self.criar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))
        self.assertEqual(plano.commits, 2)
        self.assertEqual(self.esperas_banco, [0.25])

    def test_put_e_delete_busy_uma_vez_tentam_de_novo(self):
        rr_id = self.inserir('Salário')
        self.planejar([ValueError(ERRO_BUSY)])
        resposta = self.put(f"/receitas-recorrentes/{rr_id}", corpo_edicao(valor=4800.0))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(self.linha(rr_id)[2], 4800.0)
        self.planejar([sqlite3.OperationalError("database is locked")])
        self.assertEqual(self.delete(f"/receitas-recorrentes/{rr_id}").status_code, 200)
        self.assertIsNone(self.linha(rr_id))
        self.assertEqual(self.esperas_banco, [0.25, 0.25])

    def test_busy_em_todas_as_tentativas_503_banco_ocupado(self):
        rr_id = self.inserir('Salário')
        pedidos = (("POST", lambda: self.criar(descricao="Freela")),
                   ("PUT", lambda: self.put(f"/receitas-recorrentes/{rr_id}", corpo_edicao(valor=4800.0))),
                   ("DELETE", lambda: self.delete(f"/receitas-recorrentes/{rr_id}")))
        antes = self.tabela()
        for metodo, pedir in pedidos:
            with self.subTest(metodo=metodo):
                self.esperas_banco.clear()
                plano = self.planejar([ValueError(ERRO_BUSY)] * 4)
                with self.assertLogs("api", level="WARNING") as logs:
                    resposta = pedir()
                self.assertErro(resposta, 503, "banco_ocupado")
                self.assertEqual(plano.commits, 4)
                self.assertEqual(self.esperas_banco, [0.25, 0.5, 1.0])
                self.assertNotIn("Hrana", "\n".join(logs.output))
        self.assertEqual(self.tabela(), antes)
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_erro_nao_passageiro_500_erro_interno_sem_nova_tentativa(self):
        rr_id = self.inserir('Salário')
        client = TestClient(app, raise_server_exceptions=False)
        pedidos = (("POST", lambda: self.criar(client=client, descricao="Freela")),
                   ("PUT", lambda: self.put(f"/receitas-recorrentes/{rr_id}", corpo_edicao(valor=4800.0),
                                            client=client)),
                   ("DELETE", lambda: self.delete(f"/receitas-recorrentes/{rr_id}", client=client)))
        antes = self.tabela()
        for metodo, pedir in pedidos:
            with self.subTest(metodo=metodo):
                plano = self.planejar([ValueError("boom: Hrana detalhe-do-banco")])
                with self.assertLogs("api", level="ERROR") as logs:
                    resposta = pedir()
                self.assertEqual(resposta.status_code, 500)
                self.assertEqual(resposta.json(), {"detail": "erro_interno"})
                self.assertNotIn("boom", resposta.text)
                saida_log = "\n".join(logs.output)
                self.assertIn("Receitas recorrentes falhou: erro_interno (ValueError)", saida_log)
                self.assertNotIn("detalhe-do-banco", saida_log)
                self.assertEqual(plano.commits, 1)
                self.assertEqual(self.esperas_banco, [])
        self.assertEqual(self.tabela(), antes)


# =============================================================================
# LANÇAMENTO NO DIA (MÓDULO)
# =============================================================================
class ConexaoQueFalha:
    """SQLite que levanta erro num SQL escolhido ou no commit (como o banco
    caindo no meio do lançamento)."""

    def __init__(self, real, falhar_no_sql=None, falhar_no_commit=False):
        self.real = real
        self.falhar_no_sql = falhar_no_sql
        self.falhar_no_commit = falhar_no_commit

    def _checar(self, sql):
        if self.falhar_no_sql and sql.lstrip().startswith(self.falhar_no_sql):
            raise ValueError("Hrana: stream error: conexão encerrada")

    def execute(self, sql, params=()):
        self._checar(sql)
        return self.real.execute(sql, params)

    def cursor(self):
        conexao = self
        real = self.real.cursor()

        class Cursor:
            def execute(self, sql, params=()):
                conexao._checar(sql)
                real.execute(sql, params)
                return self

            def fetchone(self):
                return real.fetchone()

            @property
            def lastrowid(self):
                return real.lastrowid

        return Cursor()

    def commit(self):
        if self.falhar_no_commit:
            raise ValueError(ERRO_BUSY)
        self.real.commit()

    def rollback(self):
        self.real.rollback()


class TestLancarReceitaRecorrente(unittest.TestCase):
    MOMENTO = '2026-12-05 05:00:00'

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(ESQUEMA)
        self.conn.execute(comando_receitas_recorrentes())
        self.conn.execute("INSERT INTO receitas_recorrentes (user_id, descricao, valor, dia_recebimento, criado_em) "
                          "VALUES (?, 'Salário', 4500, 5, ?)", (UID, CRIADO_ANTES))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def receitas(self):
        return self.conn.execute("SELECT user_id, valor, descricao, data_receita, origem, receita_recorrente_id "
                                 "FROM receitas ORDER BY id").fetchall()

    def ciclo(self):
        return self.conn.execute("SELECT ultimo_ciclo_recebido FROM receitas_recorrentes WHERE id = 1").fetchone()[0]

    def test_grava_a_receita_e_marca_o_ciclo(self):
        lancada = recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO)
        self.assertEqual(lancada, (1, 'Salário', 4500.0))
        self.assertEqual(self.receitas(), [(UID, 4500.0, 'Salário', self.MOMENTO, 'recorrente', 1)])
        self.assertEqual(self.ciclo(), '2026-12')
        self.assertFalse(self.conn.in_transaction)  # um commit só, já feito

    def test_uma_vez_por_ciclo(self):
        recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO)
        self.assertIsNone(recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', '2026-12-05 06:00:00'))
        self.assertEqual(len(self.receitas()), 1)
        # O mês seguinte lança de novo.
        self.assertIsNotNone(recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2027-01',
                                                                   '2027-01-05 05:00:00'))
        self.assertEqual([r[3] for r in self.receitas()], [self.MOMENTO, '2027-01-05 05:00:00'])
        self.assertEqual(self.ciclo(), '2027-01')

    def test_inexistente_ou_de_outro_usuario_nao_grava(self):
        self.assertIsNone(recorrentes.lancar_receita_recorrente(self.conn, UID, 99, '2026-12', self.MOMENTO))
        self.assertIsNone(recorrentes.lancar_receita_recorrente(self.conn, OUTRO_UID, 1, '2026-12', self.MOMENTO))
        self.assertEqual(self.receitas(), [])
        self.assertIsNone(self.ciclo())

    def test_le_o_valor_e_a_descricao_de_agora(self):
        # Editada depois de agendada: lança o que está gravado na hora.
        self.conn.execute("UPDATE receitas_recorrentes SET valor = 4800, descricao = 'Salário Novo' WHERE id = 1")
        self.conn.commit()
        self.assertEqual(recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO),
                         (1, 'Salário Novo', 4800.0))

    def test_erro_no_meio_desfaz_tudo_e_a_excecao_sobe(self):
        for falha in (dict(falhar_no_sql='UPDATE receitas_recorrentes'), dict(falhar_no_sql='INSERT INTO receitas'),
                      dict(falhar_no_commit=True)):
            with self.subTest(falha=falha):
                with self.assertRaises(ValueError):
                    recorrentes.lancar_receita_recorrente(ConexaoQueFalha(self.conn, **falha), UID, 1, '2026-12',
                                                          self.MOMENTO)
                self.assertFalse(self.conn.in_transaction)
                self.assertEqual(self.receitas(), [])
                self.assertIsNone(self.ciclo())
        # Depois do erro, o lançamento normal funciona.
        self.assertIsNotNone(recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO))

    def test_editar_nao_mexe_no_ciclo_ja_lancado(self):
        recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO)
        recorrentes.editar_receita_recorrente(self.conn, UID, 1, 20, 5000, "Salário")
        self.assertEqual(self.ciclo(), '2026-12')
        self.assertIsNone(recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', '2026-12-20 05:00:00'))

    def test_aparece_nas_movimentacoes_como_receita(self):
        recorrentes.lancar_receita_recorrente(self.conn, UID, 1, '2026-12', self.MOMENTO)
        self.assertEqual(calculos.buscar_movimentacoes_recentes(self.conn, UID, 10),
                         [('receita', 1, 'Salário', None, 4500.0, '2026-12-05', None, None, None)])


# =============================================================================
# JOB DAS 05:00 (BOT.PY REAL, CONTEXTO FALSO)
# =============================================================================
def relogio(ano, mes, dia, hora=5, minuto=0):
    """datetime do bot congelado num momento de Brasília."""
    fixo = datetime(ano, mes, dia, hora, minuto, tzinfo=FUSO_BRASILIA)

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixo.astimezone(tz) if tz is not None else fixo.replace(tzinfo=None)

    return Relogio


class JobQueueFalsa:
    def __init__(self):
        self.agendados = []

    def run_once(self, callback, when, **kwargs):
        self.agendados.append(SimpleNamespace(callback=callback, when=when, **kwargs))


class BotFalso:
    def __init__(self):
        self.enviadas = []
        self.editadas = []
        self.apagadas = []
        self.proximo_id = 900

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.proximo_id += 1
        self.enviadas.append((chat_id, text, parse_mode))
        return SimpleNamespace(message_id=self.proximo_id)

    async def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode=None):
        self.editadas.append((chat_id, message_id, text, parse_mode))

    async def delete_message(self, chat_id, message_id):
        self.apagadas.append((chat_id, message_id))


class TesteJobBase(unittest.TestCase):
    """Banco em arquivo (o bot abre uma conexão nova a cada função), o
    obter_conexao_db do bot apontando para ele e um bot falso do Telegram."""

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.caminho = os.path.join(self.pasta.name, 'job.db')
        criar_banco(self.caminho)
        criar_tabela(self.caminho)
        patcher = mock.patch.object(bot, 'obter_conexao_db', lambda: sqlite3.connect(self.caminho))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bot_falso = BotFalso()
        self.user_data = {}
        self.saida = io.StringIO()

    def tearDown(self):
        self.pasta.cleanup()

    def executar(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            id_novo = conn.execute(sql, params).lastrowid
            conn.commit()
            return id_novo
        finally:
            conn.close()

    def ler(self, sql, params=()):
        return ler(self.caminho, sql, params)

    def gasto_recorrente(self, descricao, dia, valor=99.9, user_id=UID, categoria='Casa', criado_em=CRIADO_ANTES,
                         ultimo_ciclo=None):
        return self.executar(
            "INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em, "
            "ultimo_ciclo_cobrado) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, categoria, descricao, valor, dia, criado_em, ultimo_ciclo))

    def receita_recorrente(self, descricao, dia, valor=4500.0, user_id=UID, criado_em=CRIADO_ANTES,
                           ultimo_ciclo=None):
        return inserir_rr(self.caminho, descricao, user_id=user_id, valor=valor, dia=dia, criado_em=criado_em,
                          ultimo_ciclo=ultimo_ciclo)

    def verificar(self, ano, mes, dia):
        """Roda o verificar_gastos_recorrentes real no dia, às 05:00 de Brasília."""
        fila = JobQueueFalsa()
        with mock.patch.object(bot, 'datetime', relogio(ano, mes, dia)), contextlib.redirect_stdout(self.saida):
            asyncio.run(bot.verificar_gastos_recorrentes(SimpleNamespace(job_queue=fila)))
        return fila.agendados

    def rodar_job(self, agendado, ano, mes, dia, user_data=None):
        """Roda o job agendado (o _lancar_gastos_recorrentes_job real)."""
        contexto = SimpleNamespace(job=SimpleNamespace(data=agendado.data, chat_id=agendado.chat_id),
                                   user_data=self.user_data if user_data is None else user_data,
                                   bot=self.bot_falso)
        with mock.patch.object(bot, 'datetime', relogio(ano, mes, dia)), contextlib.redirect_stdout(self.saida):
            asyncio.run(agendado.callback(contexto))
        return contexto

    def dia_completo(self, ano, mes, dia):
        """O dia inteiro: verifica às 05:00 e roda cada job agendado."""
        agendados = self.verificar(ano, mes, dia)
        for agendado in agendados:
            self.rodar_job(agendado, ano, mes, dia, user_data={})
        return agendados

    def receitas(self):
        return self.ler("SELECT user_id, valor, descricao, data_receita, origem, receita_recorrente_id "
                        "FROM receitas ORDER BY id")


class TestJobAgendamento(TesteJobBase):
    def test_gastos_e_receitas_do_dia_num_job_so_por_usuario(self):
        internet = self.gasto_recorrente('Internet', 5)
        salario = self.receita_recorrente('Salário', 5)
        freela = self.receita_recorrente('Freela', 5, valor=1000.0, criado_em='2026-10-02 08:00:00')
        self.gasto_recorrente('Luz', 6)
        self.receita_recorrente('Aluguel Recebido', 6)
        pensao = self.receita_recorrente('Pensão', 5, user_id=OUTRO_UID, valor=300.0)
        agendados = self.verificar(2026, 12, 5)
        self.assertEqual(len(agendados), 2)
        por_usuario = {a.chat_id: a for a in agendados}
        meu = por_usuario[UID]
        self.assertEqual(meu.data, {'gr_ids': [internet], 'rr_ids': [salario, freela], 'ciclo': '2026-12'})
        self.assertEqual((meu.when, meu.user_id, meu.name), (0, UID, f"recorrentes_{UID}_2026-12"))
        self.assertIs(meu.callback, bot._lancar_gastos_recorrentes_job)
        self.assertEqual(por_usuario[OUTRO_UID].data, {'gr_ids': [], 'rr_ids': [pensao], 'ciclo': '2026-12'})
        self.assertIn("1 gasto(s) e 3 receita(s) recorrente(s) de 2 usuário(s)", self.saida.getvalue())

    def test_ordem_das_receitas_por_criado_em(self):
        nova = self.receita_recorrente('Nova', 5, criado_em='2026-11-03 10:00:00')
        antiga = self.receita_recorrente('Antiga', 5, criado_em='2026-10-01 10:00:00')
        self.assertEqual(self.verificar(2026, 12, 5)[0].data['rr_ids'], [antiga, nova])

    def test_receita_ja_lancada_no_ciclo_nao_agenda(self):
        self.receita_recorrente('Salário', 5, ultimo_ciclo='2026-12')
        self.assertEqual(self.verificar(2026, 12, 5), [])
        self.receita_recorrente('Freela', 5, ultimo_ciclo='2026-11')
        self.assertEqual(len(self.verificar(2026, 12, 5)[0].data['rr_ids']), 1)

    def test_sem_nada_no_dia_nao_agenda_nem_imprime(self):
        self.receita_recorrente('Salário', 6)
        self.assertEqual(self.verificar(2026, 12, 5), [])
        self.assertEqual(self.saida.getvalue(), '')

    def test_dias_29_a_31_em_meses_curtos_lancam_no_ultimo_dia(self):
        casos = [  # (dia do recebimento, ano, mês, dia em que lança)
            (31, 2027, 2, 28), (30, 2027, 2, 28), (29, 2027, 2, 28),
            (31, 2028, 2, 29), (30, 2028, 2, 29), (29, 2028, 2, 29),
            (31, 2027, 4, 30), (30, 2027, 4, 30), (31, 2027, 1, 31),
        ]
        for dia_receita, ano, mes, dia_esperado in casos:
            with self.subTest(dia_receita=dia_receita, ano=ano, mes=mes):
                self.executar("DELETE FROM receitas_recorrentes")
                self.executar("DELETE FROM receitas")
                rr_id = self.receita_recorrente('Salário', dia_receita)
                ultimo = (datetime(ano + (mes == 12), mes % 12 + 1, 1) - datetime(ano, mes, 1)).days
                dias_com_lancamento = [dia for dia in range(1, ultimo + 1) if self.dia_completo(ano, mes, dia)]
                self.assertEqual(dias_com_lancamento, [dia_esperado])
                momento = f"{ano:04d}-{mes:02d}-{dia_esperado:02d} 05:00:00"
                self.assertEqual(self.receitas(), [(UID, 4500.0, 'Salário', momento, 'recorrente', rr_id)])
                self.assertEqual(self.ler("SELECT ultimo_ciclo_recebido FROM receitas_recorrentes"),
                                 [(f"{ano:04d}-{mes:02d}",)])

    def test_uma_vez_por_mes(self):
        rr_id = self.receita_recorrente('Salário', 5)
        self.assertEqual(len(self.dia_completo(2026, 12, 5)), 1)
        self.assertEqual(self.verificar(2026, 12, 5), [])  # de novo no mesmo dia: nada
        self.assertEqual(len(self.dia_completo(2027, 1, 5)), 1)
        self.assertEqual([r[3] for r in self.receitas()], ['2026-12-05 05:00:00', '2027-01-05 05:00:00'])
        self.assertEqual([r[5] for r in self.receitas()], [rr_id, rr_id])


class TestJobLancamento(TesteJobBase):
    TEXTO_GASTO = "Descrição: <b>Internet</b>\nCategoria: <b>Casa</b>\nValor: <b>R$ 99,90</b>"
    TEXTO_SALARIO = "Descrição: <b>Salário</b>\nValor: <b>R$ 4.500,00</b>"

    def preparar_dia(self):
        self.internet = self.gasto_recorrente('Internet', 5)
        self.salario = self.receita_recorrente('Salário', 5)
        self.freela = self.receita_recorrente('Freela <b>', 5, valor=1234.5, criado_em='2026-10-02 08:00:00')
        return self.verificar(2026, 12, 5)[0]

    def test_gastos_primeiro_depois_receitas_na_mesma_mensagem(self):
        agendado = self.preparar_dia()
        self.rodar_job(agendado, 2026, 12, 5)
        # Banco: o gasto como antes e as receitas em 'receitas', com o vínculo.
        self.assertEqual(self.ler("SELECT user_id, categoria_nome, valor, descricao, data_transacao FROM transacoes"),
                         [(UID, 'Casa', 99.9, 'Internet', '2026-12-05 05:00:00')])
        self.assertEqual(self.receitas(), [
            (UID, 4500.0, 'Salário', '2026-12-05 05:00:00', 'recorrente', self.salario),
            (UID, 1234.5, 'Freela <b>', '2026-12-05 05:00:00', 'recorrente', self.freela),
        ])
        self.assertEqual(self.ler("SELECT ultimo_ciclo_cobrado FROM gastos_recorrentes"), [('2026-12',)])
        self.assertEqual(self.ler("SELECT ultimo_ciclo_recebido FROM receitas_recorrentes ORDER BY id"),
                         [('2026-12',), ('2026-12',)])
        # Mensagem: uma só, na primeira página (a cobrança).
        self.assertEqual(self.bot_falso.enviadas, [
            (UID, "1-3\n\nCobrança Recorrente Lançada\n\n" + self.TEXTO_GASTO, 'HTML')])
        estado = self.user_data['recorrente_diario']
        self.assertEqual(estado['data'], '2026-12-05')
        self.assertEqual(estado['pagina_atual'], 0)
        self.assertEqual(estado['itens'], [
            {'titulo': 'Cobrança Recorrente Lançada', 'corpo': self.TEXTO_GASTO, 'posicao': 1, 'total_dia': 3},
            {'titulo': 'Receita Recorrente Lançada', 'corpo': self.TEXTO_SALARIO, 'posicao': 2, 'total_dia': 3},
            {'titulo': 'Receita Recorrente Lançada',
             'corpo': "Descrição: <b>Freela &lt;b&gt;</b>\nValor: <b>R$ 1.234,50</b>", 'posicao': 3, 'total_dia': 3},
        ])

    def test_paginas_e_texto_do_fim(self):
        self.rodar_job(self.preparar_dia(), 2026, 12, 5)
        editadas = []
        fechou = []

        async def edit_text(texto, reply_markup=None, parse_mode=None):
            editadas.append(texto)

        async def nada(*args, **kwargs):
            return None

        async def fechar(context, chat_id, message_id):
            fechou.append((chat_id, message_id))

        query = SimpleNamespace(message=SimpleNamespace(chat_id=UID, message_id=901, edit_text=edit_text))
        update = SimpleNamespace(callback_query=query)
        contexto = SimpleNamespace(user_data=self.user_data, bot=self.bot_falso)
        with mock.patch.object(bot, 'responder_callback', nada), \
                mock.patch.object(bot, '_fechar_recorrente_diario', fechar), \
                mock.patch.object(bot, 'asyncio', SimpleNamespace(sleep=nada)):
            for _ in range(3):
                asyncio.run(bot.recorrente_diario_proxima(update, contexto))
        self.assertEqual(editadas, [
            "2-3\n\nReceita Recorrente Lançada\n\n" + self.TEXTO_SALARIO,
            "3-3\n\nReceita Recorrente Lançada\n\nDescrição: <b>Freela &lt;b&gt;</b>\nValor: <b>R$ 1.234,50</b>",
            "Sem Mais Recorrentes Lançados Hoje, Voltando ao Menu Principal.",
        ])
        self.assertEqual(fechou, [(UID, 901)])

    def test_rodar_de_novo_no_mesmo_ciclo_nao_lanca_nem_avisa(self):
        agendado = self.preparar_dia()
        self.rodar_job(agendado, 2026, 12, 5)
        self.bot_falso.enviadas.clear()
        self.rodar_job(agendado, 2026, 12, 5)
        self.assertEqual((len(self.receitas()), len(self.ler("SELECT * FROM transacoes"))), (2, 1))
        self.assertEqual((self.bot_falso.enviadas, self.bot_falso.editadas), ([], []))

    def test_so_receitas(self):
        rr_id = self.receita_recorrente('Pensão', 5, user_id=OUTRO_UID, valor=300.0)
        agendado = self.verificar(2026, 12, 5)[0]
        self.rodar_job(agendado, 2026, 12, 5)
        self.assertEqual(self.bot_falso.enviadas, [
            (OUTRO_UID, "1-1\n\nReceita Recorrente Lançada\n\nDescrição: <b>Pensão</b>\nValor: <b>R$ 300,00</b>",
             'HTML')])
        self.assertEqual(self.receitas(), [(OUTRO_UID, 300.0, 'Pensão', '2026-12-05 05:00:00', 'recorrente', rr_id)])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM transacoes"), [(0,)])

    def test_job_agendado_antes_do_deploy_sem_rr_ids(self):
        self.gasto_recorrente('Internet', 5)
        self.receita_recorrente('Salário', 5)
        agendado = SimpleNamespace(callback=bot._lancar_gastos_recorrentes_job, chat_id=UID,
                                   data={'gr_ids': [1], 'ciclo': '2026-12'})
        self.rodar_job(agendado, 2026, 12, 5)
        self.assertEqual(self.bot_falso.enviadas, [(UID, "1-1\n\nCobrança Recorrente Lançada\n\n" + self.TEXTO_GASTO,
                                                    'HTML')])
        self.assertEqual(self.receitas(), [])

    def test_item_antigo_sem_titulo_usa_o_da_cobranca(self):
        # Estado do user_data de antes do deploy: um item sem 'titulo' já na
        # mensagem de hoje; a receita entra como página 2.
        self.user_data['recorrente_diario'] = {
            'data': '2026-12-05', 'message_id': 777, 'pagina_atual': 0,
            'itens': [{'corpo': 'Descrição: <b>Antigo</b>', 'posicao': 1, 'total_dia': 1}]}
        self.receita_recorrente('Salário', 5)
        self.rodar_job(self.verificar(2026, 12, 5)[0], 2026, 12, 5)
        itens = self.user_data['recorrente_diario']['itens']
        self.assertEqual(bot._montar_texto_recorrente_diario(itens[0]),
                         "1-2\n\nCobrança Recorrente Lançada\n\nDescrição: <b>Antigo</b>")
        self.assertEqual(self.bot_falso.editadas, [
            (UID, 777, "2-2\n\nReceita Recorrente Lançada\n\n" + self.TEXTO_SALARIO, 'HTML')])

    def test_receita_que_falha_vai_para_o_log_e_o_job_segue(self):
        primeira = self.receita_recorrente('Salário', 5)
        segunda = self.receita_recorrente('Freela', 5, valor=1000.0, criado_em='2026-10-02 08:00:00')
        real = recorrentes.lancar_receita_recorrente

        def lancar(conn, user_id, rr_id, ciclo, momento):
            if rr_id == primeira:
                raise ValueError("banco caiu")
            return real(conn, user_id, rr_id, ciclo, momento)

        agendado = self.verificar(2026, 12, 5)[0]
        with mock.patch.object(bot, 'lancar_receita_recorrente', lancar):
            self.rodar_job(agendado, 2026, 12, 5)
        self.assertIn(f"Receita recorrente {primeira} não lançada para o usuário {UID}", self.saida.getvalue())
        self.assertEqual([(r[2], r[5]) for r in self.receitas()], [('Freela', segunda)])
        self.assertEqual(self.ler("SELECT ultimo_ciclo_recebido FROM receitas_recorrentes ORDER BY id"),
                         [(None,), ('2026-12',)])
        self.assertEqual(len(self.bot_falso.enviadas), 1)
        self.assertIn("1-1\n\nReceita Recorrente Lançada", self.bot_falso.enviadas[0][1])

    def test_receita_apagada_depois_de_agendada_e_pulada(self):
        agendado = self.preparar_dia()
        self.executar("DELETE FROM receitas_recorrentes WHERE id = ?", (self.salario,))
        self.rodar_job(agendado, 2026, 12, 5)
        self.assertEqual([r[2] for r in self.receitas()], ['Freela <b>'])
        self.assertEqual(self.user_data['recorrente_diario']['itens'][-1]['total_dia'], 2)

    def test_lancar_item_recorrente_do_gasto_nao_mudou(self):
        # A função do gasto continua com a mesma assinatura e o mesmo corpo.
        self.gasto_recorrente('Internet', 5)
        conn = sqlite3.connect(self.caminho)
        try:
            with mock.patch.object(bot, 'datetime', relogio(2026, 12, 5)):
                corpo = bot._lancar_item_recorrente(conn, UID, 1, '2026-12')
        finally:
            conn.close()
        self.assertEqual(corpo, self.TEXTO_GASTO)


# =============================================================================
# SOBRA PELO CALCULOS.PY (FATURA CERTA NOS DIAS DE BORDA)
# =============================================================================
class TestSobraPeloCalculos(TesteJobBase):
    def resumo(self, ano_mes, dia_fechamento):
        conn = sqlite3.connect(self.caminho)
        try:
            return calculos.calcular_resumo_fatura(conn, UID, ano_mes, dia_fechamento)
        finally:
            conn.close()

    def conferir(self, dia_fechamento, dia_receita, ano, mes, dia_lancamento, fatura_certa, outras_faturas):
        self.executar("UPDATE usuarios SET dia_fechamento = ? WHERE user_id = ?", (dia_fechamento, UID))
        self.executar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, descricao) "
                      "VALUES (?, 'Casa', 250.5, ?, 'Mercado')", (UID, f"{ano:04d}-{mes:02d}-01 12:00:00"))
        self.receita_recorrente('Salário', dia_receita, valor=4500.0)
        faturas = [fatura_certa] + outras_faturas
        antes = {f: self.resumo(f, dia_fechamento) for f in faturas}
        self.assertEqual(len(self.dia_completo(ano, mes, dia_lancamento)), 1)
        depois = {f: self.resumo(f, dia_fechamento) for f in faturas}
        self.assertEqual(depois[fatura_certa].total_receitas - antes[fatura_certa].total_receitas, 4500.0)
        self.assertEqual(depois[fatura_certa].sobra_mes - antes[fatura_certa].sobra_mes, 4500.0)
        self.assertEqual(depois[fatura_certa].total_gasto, antes[fatura_certa].total_gasto)
        for fatura in outras_faturas:
            self.assertEqual(depois[fatura], antes[fatura])
        # A fatura da data, pela regra do gasto (calcular_ciclo_fatura).
        data = datetime(ano, mes, dia_lancamento).date()
        self.assertEqual(calculos.calcular_ciclo_fatura(data, dia_fechamento), fatura_certa)

    def test_salario_antes_do_fechamento_entra_na_fatura_do_mes(self):
        # Fechamento 29, salário dia 5: fatura 2026-12 (29/11 a 28/12).
        self.conferir(29, 5, 2026, 12, 5, '2026-12', ['2027-01', '2026-11'])

    def test_vespera_do_fechamento(self):
        self.conferir(10, 9, 2026, 12, 9, '2026-12', ['2027-01'])

    def test_no_proprio_dia_do_fechamento_entra_na_fatura_nova(self):
        self.conferir(10, 10, 2026, 12, 10, '2027-01', ['2026-12'])

    def test_dia_31_em_fevereiro(self):
        # Lança em 28/02/2027; com fechamento 29 (que em fevereiro vira 28),
        # o dia 28 já é da fatura nova.
        self.conferir(29, 31, 2027, 2, 28, '2027-03', ['2027-02'])

    def test_dia_31_em_abril(self):
        # Lança em 30/04/2027; com fechamento 31 (em abril vira 30): fatura nova.
        self.conferir(31, 31, 2027, 4, 30, '2027-05', ['2027-04'])

    def test_dia_31_em_abril_com_fechamento_no_comeco_do_mes(self):
        # Fechamento 5: o dia 30 também fica na fatura de maio (30 >= 5).
        self.conferir(5, 31, 2027, 4, 30, '2027-05', ['2027-04', '2027-06'])

    def test_receita_avulsa_e_recorrente_somam_juntas(self):
        self.executar("INSERT INTO receitas (user_id, valor, descricao, data_receita) "
                      "VALUES (?, 200, 'Venda', '2026-12-10 12:00:00')", (UID,))
        self.conferir(28, 5, 2026, 12, 5, '2026-12', [])
        self.assertEqual(self.resumo('2026-12', 28).total_receitas, 4700.0)


# =============================================================================
# PRÉVIA DA RECEITA NO BOT (D5)
# =============================================================================
COM_RENDA = "A sua renda mensal já entra na Sobra. Registre aqui só o dinheiro extra."
SEM_RENDA = "O salário e as entradas fixas ficam em Recorrentes, no Mini App. Registre aqui só o dinheiro extra."


class TestPreviaDaReceita(TesteJobBase):
    def test_texto_pela_renda(self):
        for renda, esperado in ((5000.0, COM_RENDA), (0.01, COM_RENDA), (0, SEM_RENDA), (0.0, SEM_RENDA),
                                (None, SEM_RENDA)):
            with self.subTest(renda=renda):
                texto, teclado = bot._montar_tela_previa_receita(200.0, "Venda", renda)
                self.assertEqual(texto, "Entendi Assim:\n\nReceita: <b>R$ 200,00</b>\nDescrição: Venda\n\n" + esperado)
                self.assertEqual([[b.callback_data for b in linha] for linha in teclado.inline_keyboard],
                                 [['receita_confirmar'], ['receita_cancelar']])

    def rodar_registro(self, renda):
        self.executar("UPDATE usuarios SET renda_mensal = ? WHERE user_id = ?", (renda, UID))
        textos = []

        async def enviar_fluxo(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
            textos.append(texto)

        async def apagar():
            return None

        update = SimpleNamespace(
            message=SimpleNamespace(text="recebi 200 da venda", date=datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc),
                                    delete=apagar),
            effective_user=SimpleNamespace(id=UID))
        contexto = SimpleNamespace(user_data={})
        anterior = registro._cliente_ia
        registro.definir_cliente_ia(IAFalsa(resposta_receita(200, 'Venda')))
        try:
            with mock.patch.object(bot, 'enviar_fluxo', enviar_fluxo):
                estado = asyncio.run(bot.salvar_gasto_livre(update, contexto))
        finally:
            registro.definir_cliente_ia(anterior)
        self.assertEqual(estado, bot.CONFIRMAR_RECEITA)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM receitas"), [(0,)])  # a prévia não grava
        return textos[-1]

    def test_handler_real_com_renda_zero(self):
        self.assertTrue(self.rodar_registro(0.0).endswith(SEM_RENDA))

    def test_handler_real_com_renda_nula(self):
        self.assertTrue(self.rodar_registro(None).endswith(SEM_RENDA))

    def test_handler_real_com_renda(self):
        self.assertTrue(self.rodar_registro(5000.0).endswith(COM_RENDA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
