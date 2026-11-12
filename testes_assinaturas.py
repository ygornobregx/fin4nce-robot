"""Testes da assinatura (PRO-106): o módulo assinaturas.py (admin por
ADMIN_IDS, regras de acesso com relógio fixo, teste único, regra única de soma
da D17, retirada de Pro, comandos de admin e o limite diário de registros com
IA), as rotas GET /assinatura, POST /assinaturas/teste, o bloco "assinatura"
do GET /me, a dependência acesso_liberado e o 429 limite_diario_ia do
/registro/interpretar, e as partes do bot (tabelas no iniciar_banco, porteiro,
botão do teste, comandos de admin, jobs e o limite nos pontos que chamam a IA),
mais o log dos tokens no registro.py.

Roda com `python testes_assinaturas.py`. Não usa .env, rede nem banco real:
  - antes do `import bot`, o load_dotenv vira no-op, o ambiente recebe valores
    vazios e o libsql.connect passa a levantar erro (como no
    testes_categorias.py); ADMIN_IDS, ASSINATURAS_LIGADAS, LIMITE_IA_DIA e
    PRECO_PRO_STARS saem do ambiente e cada teste põe o que precisa;
  - as rotas reaproveitam a base do testes_rotas_registro.py (initData
    assinado com o token falso, SQLite em ARQUIVO temporário com uma conexão
    nova por requisição, IA falsa, esperas sem dormir), com o "agora" da API
    trocado em cada teste;
  - o bot roda as funções REAIS do bot.py, com o obter_conexao_db apontando
    para o mesmo SQLite em arquivo, o relógio do bot fixo e as funções de
    mensagem do Telegram trocadas por versões que só guardam o texto.

Datas dos cenários da D17: todo lançamento ao meio-dia de Brasília.
"""
import ast
import asyncio
import contextlib
import io
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
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
VARIAVEIS = ('ADMIN_IDS', 'ASSINATURAS_LIGADAS', 'LIMITE_IA_DIA', 'PRECO_PRO_STARS', 'URL_MINI_APP')
for _nome in VARIAVEIS:
    os.environ.pop(_nome, None)

import libsql  # noqa: E402


def _libsql_proibido(*args, **kwargs):
    raise RuntimeError("Conexão real com o banco é proibida nos testes.")


libsql.connect = _libsql_proibido

from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import assinaturas  # noqa: E402
import bot  # noqa: E402
import registro  # noqa: E402
from api import main as api_main  # noqa: E402
from api.main import LimiteDeChamadas, STATUS_ERRO_REGISTRO, app, obter_agora  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402
from testes_rotas_registro import (  # noqa: E402
    CHAVE, INCOMPLETO, OUTRO_UID, SEM_CADASTRO, UID, ErroFalsoDaIA, IAFalsa, TesteRegistroBase,
    comando_chaves_registro, corpo_gasto, corpo_receita, resposta_gasto,
)

logging.getLogger("api").addHandler(logging.NullHandler())
logging.getLogger("assinaturas").addHandler(logging.NullHandler())
logging.getLogger("registro").addHandler(logging.NullHandler())

F = FUSO_BRASILIA


def br(ano, mes, dia, hora=12, minuto=0, segundo=0):
    """Momento em Brasília (os lançamentos dos cenários são ao meio-dia)."""
    return datetime(ano, mes, dia, hora, minuto, segundo, tzinfo=F)


def novo_banco_memoria(usuarios=((UID, 'Ygor'), (OUTRO_UID, 'Maria'))):
    conn = sqlite3.connect(':memory:')
    conn.executescript(ESQUEMA)
    conn.executescript(assinaturas.SQL_CRIAR_TABELAS)
    for user_id, nome in usuarios:
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, ?, 0, 28)",
                     (user_id, nome))
    conn.commit()
    return conn


class AmbienteLimpo(unittest.TestCase):
    """Cada teste começa sem as variáveis da assinatura e põe só as suas."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        for nome in VARIAVEIS:
            os.environ.pop(nome, None)

    def ambiente(self, **valores):
        for nome, valor in valores.items():
            if valor is None:
                os.environ.pop(nome, None)
            else:
                os.environ[nome] = str(valor)


def linhas_resumidas(conn, user_id=UID):
    """(origem, estado, início dd/mm, fim dd/mm) das linhas Pro, em ordem."""
    return [(l.origem, l.estado, l.inicio.strftime('%d/%m'), l.fim.strftime('%d/%m'))
            for l in assinaturas.ler_linhas(conn, user_id) if l.plano == 'pro']


def pagar(conn, agora, pagamento_id, user_id=UID, valor=150):
    """Uma cobrança de Stars como a PRO-107 vai gravar: 30 dias, renova_em =
    a cobrança + 30 dias (o subscription_expiration_date do Telegram)."""
    return assinaturas.adicionar_pro(conn, user_id, 30, 'stars', agora, renovacao=True,
                                     renova_em=agora + timedelta(days=30), pagamento_id=pagamento_id,
                                     assinatura_ref='ref-' + str(user_id), valor=valor, moeda='XTR')


# =============================================================================
# MÓDULO, CONSTANTES E TABELAS
# =============================================================================
class TestModuloETabelas(AmbienteLimpo):
    def test_constantes(self):
        self.assertEqual(assinaturas.DIAS_TESTE, 10)
        self.assertEqual(assinaturas.PRECO_REAIS_APROXIMADO, 19)
        self.assertEqual(assinaturas.LIMITE_IA_DIA_PADRAO, 17)
        self.assertEqual(assinaturas.DIAS_PRO_PADRAO, 30)
        self.assertEqual(assinaturas.DIAS_PRO_MAXIMO, 366)
        self.assertEqual(assinaturas.TOLERANCIA_RENOVACAO, timedelta(hours=24))

    def test_modulo_sem_telegram_bot_dotenv_nem_libsql_e_sem_ler_o_ambiente_no_import(self):
        with open(os.path.join(RAIZ, 'assinaturas.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split('.')[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(no.module.split('.')[0])
        self.assertEqual(importados, {'logging', 'math', 'os', 're', 'datetime', 'typing', 'recorrentes',
                                      'registro', 'calculos'})
        # Nenhuma leitura do ambiente fora de função (só na hora da chamada).
        for no in arvore.body:
            if isinstance(no, (ast.FunctionDef, ast.ClassDef)):
                continue
            for filho in ast.walk(no):
                if isinstance(filho, ast.Attribute) and filho.attr in ('getenv', 'environ'):
                    self.fail(f"leitura do ambiente no import (linha {filho.lineno})")

    def test_status_dos_codigos(self):
        self.assertEqual(STATUS_ERRO_REGISTRO['teste_ja_usado'], 409)
        self.assertEqual(STATUS_ERRO_REGISTRO['plano_ativo'], 409)

    def iniciar_banco_do_bot(self, caminho):
        with mock.patch.object(bot, 'obter_conexao_db', lambda: sqlite3.connect(caminho)), \
                contextlib.redirect_stdout(io.StringIO()):
            bot.iniciar_banco()

    def test_iniciar_banco_cria_as_tabelas_e_os_indices_com_o_texto_do_modulo(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            caminho = os.path.join(pasta, 'bot.db')
            self.iniciar_banco_do_bot(caminho)
            conn = sqlite3.connect(caminho)
            do_bot = dict(conn.execute("SELECT name, sql FROM sqlite_master WHERE name IN "
                                       "('assinaturas', 'uso_ia', 'ux_assinaturas_pagamento', 'ux_assinaturas_teste', "
                                       "'ix_assinaturas_usuario')").fetchall())
            colunas = [(c[1], c[2], c[3], c[4]) for c in conn.execute('PRAGMA table_info(assinaturas)')]
            colunas_uso = [(c[1], c[2], c[3], c[4], c[5]) for c in conn.execute('PRAGMA table_info(uso_ia)')]
            conn.close()
        referencia = sqlite3.connect(':memory:')
        referencia.executescript(assinaturas.SQL_CRIAR_TABELAS)
        esperado = dict(referencia.execute("SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
        referencia.close()
        self.assertEqual(do_bot, esperado)
        self.assertEqual(len(do_bot), 5)
        self.assertEqual(colunas, [
            ('id', 'INTEGER', 0, None), ('user_id', 'INTEGER', 1, None), ('plano', 'TEXT', 1, None),
            ('origem', 'TEXT', 1, None), ('inicio', 'TEXT', 1, None), ('fim', 'TEXT', 1, None),
            ('estado', 'TEXT', 1, "'ativa'"), ('dias', 'INTEGER', 1, None), ('renovacao', 'INTEGER', 1, '0'),
            ('renova_em', 'TEXT', 0, None), ('pagamento_id', 'TEXT', 0, None), ('assinatura_ref', 'TEXT', 0, None),
            ('valor', 'REAL', 0, None), ('moeda', 'TEXT', 0, None), ('ultimo_aviso', 'TEXT', 0, None),
            ('criado_em', 'TEXT', 1, None), ('criado_por', 'INTEGER', 0, None),
        ])
        self.assertEqual(colunas_uso, [('user_id', 'INTEGER', 1, None, 1), ('dia', 'TEXT', 1, None, 2),
                                       ('chamadas', 'INTEGER', 1, '0', 0)])
        self.assertIn("WHERE pagamento_id IS NOT NULL", esperado['ux_assinaturas_pagamento'])
        self.assertIn("WHERE origem = 'teste'", esperado['ux_assinaturas_teste'])

    def test_iniciar_banco_de_novo_nao_muda_nada_e_nenhuma_outra_tabela_muda(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            caminho = os.path.join(pasta, 'bot.db')
            conn = sqlite3.connect(caminho)
            conn.executescript(ESQUEMA)
            conn.execute(comando_chaves_registro())
            conn.execute("INSERT INTO usuarios (user_id, nome) VALUES (1, 'Y')")
            conn.commit()
            antes = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            conn.close()
            self.iniciar_banco_do_bot(caminho)
            conn = sqlite3.connect(caminho)
            conn.execute("INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, dias, criado_em) "
                         "VALUES (1, 'teste', 'teste', '2026-11-11 14:00:00', '2026-11-21 14:00:00', 10, 'x')")
            conn.commit()
            depois_1 = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            conn.close()
            self.iniciar_banco_do_bot(caminho)
            conn = sqlite3.connect(caminho)
            depois_2 = conn.execute("SELECT name, sql FROM sqlite_master ORDER BY name").fetchall()
            linhas = conn.execute("SELECT COUNT(*) FROM assinaturas").fetchone()[0]
            usuarios = conn.execute("SELECT * FROM usuarios").fetchall()
            conn.close()
        novos = {'assinaturas', 'uso_ia', 'sqlite_autoindex_uso_ia_1', 'ux_assinaturas_pagamento',
                 'ux_assinaturas_teste', 'ix_assinaturas_usuario', 'receitas_recorrentes', 'sqlite_sequence'}
        self.assertEqual([l for l in depois_1 if l[0] not in novos], [l for l in antes if l[0] not in novos])
        self.assertEqual(depois_2, depois_1)
        self.assertEqual(linhas, 1)
        self.assertEqual(usuarios, [(1, 'Y', None, None, None, None)])
        self.assertNotIn('assinaturas', bot.COLUNAS_ESPERADAS)

    def test_bot_cria_as_tabelas_dentro_do_iniciar_banco(self):
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        funcao = next(no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name == 'iniciar_banco')
        chamadas = {no.func.id for no in ast.walk(funcao) if isinstance(no, ast.Call) and isinstance(no.func, ast.Name)}
        self.assertIn('comandos_criar_tabelas', chamadas)
        self.assertTrue(all(c.startswith('CREATE ') and 'IF NOT EXISTS' in c for c in assinaturas.comandos_criar_tabelas()))

    def test_indice_unico_do_teste_e_do_pagamento(self):
        conn = novo_banco_memoria()
        base = ("INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, dias, pagamento_id, criado_em) "
                "VALUES (?, ?, ?, '2026-11-11 12:00:00', '2026-11-21 12:00:00', 10, ?, 'x')")
        conn.execute(base, (UID, 'teste', 'teste', None))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(base, (UID, 'teste', 'teste', None))
        conn.execute(base, (OUTRO_UID, 'teste', 'teste', None))     # outra pessoa pode
        conn.execute(base, (UID, 'pro', 'manual', None))            # várias linhas Pro sem pagamento
        conn.execute(base, (UID, 'pro', 'manual', None))
        conn.execute(base, (UID, 'pro', 'stars', 'cobranca-1'))
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(base, (OUTRO_UID, 'pro', 'stars', 'cobranca-1'))


# =============================================================================
# VARIÁVEIS DE AMBIENTE
# =============================================================================
class TestVariaveis(AmbienteLimpo):
    def test_admin_ids_com_espacos_e_separadores(self):
        self.ambiente(ADMIN_IDS=' 7803589807 , 123;456  789 ')
        self.assertEqual(assinaturas.ler_admin_ids(), frozenset({7803589807, 123, 456, 789}))
        self.assertTrue(assinaturas.e_admin(7803589807))
        self.assertFalse(assinaturas.e_admin(9096703955))

    def test_admin_ids_entrada_invalida_ignorada_com_aviso_sem_o_valor(self):
        self.ambiente(ADMIN_IDS='abc, 7803589807, -5, 0, 1.5, 12x, ٣')
        with self.assertLogs('assinaturas', 'WARNING') as logs:
            ids = assinaturas.ler_admin_ids()
        self.assertEqual(ids, frozenset({7803589807}))
        texto = '\n'.join(logs.output)
        self.assertIn('6 entrada(s) ignorada(s)', texto)
        for valor in ('abc', '-5', '1.5', '12x'):
            self.assertNotIn(valor, texto)

    def test_admin_ids_vazia_ou_ausente_ninguem_e_admin(self):
        for valor in (None, '', '   ', ',,'):
            with self.subTest(valor=valor):
                self.ambiente(ADMIN_IDS=valor)
                self.assertEqual(assinaturas.ler_admin_ids(), frozenset())
                self.assertFalse(assinaturas.e_admin(UID))

    def test_admin_lido_na_hora_e_id_tem_de_ser_inteiro(self):
        self.assertFalse(assinaturas.e_admin(UID))
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertTrue(assinaturas.e_admin(UID))
        for falso in (str(UID), float(UID), True, None):
            self.assertFalse(assinaturas.e_admin(falso))
        self.ambiente(ADMIN_IDS='1')
        self.assertFalse(assinaturas.e_admin(True))   # bool não é id
        self.assertTrue(assinaturas.e_admin(1))
        self.ambiente(ADMIN_IDS=None)
        self.assertFalse(assinaturas.e_admin(UID))

    def test_assinaturas_ligadas(self):
        for valor, esperado in ((None, False), ('', False), ('0', False), ('nao', False), ('1', True),
                                (' 1 ', True), ('true', True), ('TRUE', True), ('sim', True)):
            with self.subTest(valor=valor):
                self.ambiente(ASSINATURAS_LIGADAS=valor)
                self.assertEqual(assinaturas.assinaturas_ligadas(), esperado)

    def test_limite_ia_dia_invalido_vale_17(self):
        for valor, esperado in ((None, 17), ('', 17), ('abc', 17), ('0', 17), ('-3', 17), ('17.5', 17),
                                ('١٧', 17), ('25', 25), (' 5 ', 5), ('1', 1), ('100000', 100000)):
            with self.subTest(valor=valor):
                self.ambiente(LIMITE_IA_DIA=valor)
                self.assertEqual(assinaturas.limite_ia_dia(), esperado)

    def test_preco_pro_stars(self):
        for valor, esperado in ((None, 150), ('abc', 150), ('0', 150), ('10001', 150), ('1', 1), ('190', 190),
                                ('10000', 10000)):
            with self.subTest(valor=valor):
                self.ambiente(PRECO_PRO_STARS=valor)
                self.assertEqual(assinaturas.preco_pro_stars(), esperado)


# =============================================================================
# RELÓGIO, DIAS RESTANTES E ACESSO DO TESTE
# =============================================================================
class TestRelogioEDias(AmbienteLimpo):
    def test_agora_em_brasilia_sem_microssegundos(self):
        utc = datetime(2026, 11, 11, 17, 0, 0, 999999, tzinfo=timezone.utc)
        self.assertEqual(assinaturas.agora_brasilia(utc), br(2026, 11, 11, 14))
        self.assertEqual(assinaturas.agora_brasilia(datetime(2026, 11, 11, 14, 0)), br(2026, 11, 11, 14))

    def test_dias_restantes(self):
        fim = br(2026, 11, 21, 14)
        for agora, esperado in ((br(2026, 11, 11, 14), 10), (br(2026, 11, 14, 9), 7), (br(2026, 11, 20, 23, 59), 1),
                                (br(2026, 11, 21, 0, 0), 0), (br(2026, 11, 21, 13, 59), 0), (br(2026, 11, 22), 0),
                                (br(2027, 1, 1), 0)):
            with self.subTest(agora=agora):
                self.assertEqual(assinaturas.dias_restantes(fim, agora), esperado)
        self.assertIsNone(assinaturas.dias_restantes(None, br(2026, 11, 11)))

    def test_dias_restantes_vira_as_00_00_de_brasilia(self):
        fim = br(2026, 11, 21, 14)
        self.assertEqual(assinaturas.dias_restantes(fim, br(2026, 11, 13, 23, 59, 59)), 8)
        self.assertEqual(assinaturas.dias_restantes(fim, br(2026, 11, 14, 0, 0, 0)), 7)
        # 02:59 UTC do dia 14 ainda é 23:59 do dia 13 em Brasília.
        self.assertEqual(assinaturas.dias_restantes(fim, datetime(2026, 11, 14, 2, 59, tzinfo=timezone.utc)), 8)
        self.assertEqual(assinaturas.dias_restantes(fim, datetime(2026, 11, 14, 3, 0, tzinfo=timezone.utc)), 7)

    def test_segundos_ate_meia_noite(self):
        self.assertEqual(assinaturas.segundos_ate_meia_noite(br(2026, 11, 14, 23, 59, 30)), 30)
        self.assertEqual(assinaturas.segundos_ate_meia_noite(br(2026, 11, 14, 12)), 12 * 3600)
        self.assertEqual(assinaturas.segundos_ate_meia_noite(br(2026, 11, 14, 0, 0, 0)), 24 * 3600)
        self.assertEqual(assinaturas.segundos_ate_meia_noite(br(2026, 11, 14, 23, 59, 59) + timedelta(microseconds=999999)), 1)
        # 02:59:30 UTC = 23:59:30 em Brasília: faltam 30 s (não 21 horas).
        self.assertEqual(assinaturas.segundos_ate_meia_noite(datetime(2026, 11, 15, 2, 59, 30, tzinfo=timezone.utc)), 30)


class TestTeste(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.conn = novo_banco_memoria()
        self.addCleanup(self.conn.close)

    def situacao(self, agora, user_id=UID):
        return assinaturas.situacao_acesso(self.conn, user_id, agora)

    def test_teste_das_14h_vale_ate_as_14h_do_dia_21_e_vence_na_hora_exata(self):
        teste = assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11, 14))
        self.assertEqual((teste.inicio, teste.fim, teste.repetido), (br(2026, 11, 11, 14), br(2026, 11, 21, 14), False))
        self.assertEqual(teste.fim - teste.inicio, timedelta(hours=240))
        casos = ((br(2026, 11, 11, 14), 'teste', True),              # dia 0
                 (br(2026, 11, 20, 14), 'teste', True),              # dia 9
                 (br(2026, 11, 21, 13, 59, 59), 'teste', True),
                 (br(2026, 11, 21, 14), 'teste_vencido', False),     # dia 10, hora exata
                 (br(2026, 11, 22, 14), 'teste_vencido', False))     # dia 11
        for agora, estado, acesso in casos:
            with self.subTest(agora=agora):
                situacao = self.situacao(agora)
                self.assertEqual((situacao.estado, situacao.tem_acesso), (estado, acesso))
                self.assertEqual(assinaturas.tem_acesso(self.conn, UID, agora), acesso)
        self.assertEqual(self.situacao(br(2026, 11, 22)).fim, br(2026, 11, 21, 14))

    def test_relogio_em_utc_vira_brasilia(self):
        teste = assinaturas.iniciar_teste(self.conn, UID, datetime(2026, 11, 11, 17, 0, tzinfo=timezone.utc))
        self.assertEqual(teste.inicio, br(2026, 11, 11, 14))
        linha = self.conn.execute("SELECT inicio, fim, dias, origem, plano, estado, criado_por FROM assinaturas").fetchone()
        self.assertEqual(linha, ('2026-11-11 14:00:00', '2026-11-21 14:00:00', 10, 'teste', 'teste', 'ativa', None))

    def test_teste_unico(self):
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11, 14))
        repetido = assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 12))
        self.assertEqual((repetido.inicio, repetido.repetido), (br(2026, 11, 11, 14), True))
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 21, 14))
        self.assertEqual(ctx.exception.codigo, 'teste_ja_usado')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assinaturas").fetchone()[0], 1)
        # Outra pessoa tem o próprio teste.
        self.assertFalse(assinaturas.iniciar_teste(self.conn, OUTRO_UID, br(2026, 11, 21, 14)).repetido)

    def test_sem_cadastro_pode_comecar_o_teste(self):
        teste = assinaturas.iniciar_teste(self.conn, SEM_CADASTRO, br(2026, 11, 11))
        self.assertFalse(teste.repetido)
        self.assertEqual(self.situacao(br(2026, 11, 12), SEM_CADASTRO).estado, 'teste')

    def test_admin_e_pro_ativo_nao_comecam_teste(self):
        self.ambiente(ADMIN_IDS=str(UID))
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11))
        self.assertEqual(ctx.exception.codigo, 'plano_ativo')
        assinaturas.dar_pro_manual(self.conn, OUTRO_UID, 30, UID, br(2026, 11, 1))
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.iniciar_teste(self.conn, OUTRO_UID, br(2026, 11, 11))
        self.assertEqual(ctx.exception.codigo, 'plano_ativo')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste'").fetchone()[0], 0)

    def test_dois_pedidos_juntos_criam_um_teste_so(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            caminho = os.path.join(pasta, 'corrida.db')
            conn = sqlite3.connect(caminho)
            conn.executescript(ESQUEMA)
            conn.executescript(assinaturas.SQL_CRIAR_TABELAS)
            conn.close()
            barreira = threading.Barrier(2, timeout=10)
            original = assinaturas.ler_linhas
            contagem = [0]
            trava = threading.Lock()

            def ler_com_barreira(conn, user_id):
                linhas = original(conn, user_id)
                with trava:
                    contagem[0] += 1
                    numero = contagem[0]
                if numero <= 2:
                    barreira.wait()   # os dois leem "sem teste" antes de qualquer um gravar
                return linhas

            resultados = []

            def pedir():
                c = sqlite3.connect(caminho, timeout=10, check_same_thread=False)
                try:
                    resultados.append(assinaturas.iniciar_teste(c, UID, br(2026, 11, 11, 14)))
                except Exception as erro:  # pragma: no cover - aparece na asserção
                    resultados.append(erro)
                finally:
                    c.close()

            with mock.patch.object(assinaturas, 'ler_linhas', ler_com_barreira), \
                    mock.patch.object(registro, '_esperar', lambda s: None):
                threads = [threading.Thread(target=pedir) for _ in range(2)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(30)
            conn = sqlite3.connect(caminho)
            total = conn.execute("SELECT COUNT(*) FROM assinaturas").fetchone()[0]
            conn.close()
        self.assertEqual(total, 1)
        self.assertEqual(sorted(r.repetido for r in resultados), [False, True])
        self.assertEqual({(r.inicio, r.fim) for r in resultados}, {(br(2026, 11, 11, 14), br(2026, 11, 21, 14))})


# =============================================================================
# REGRA ÚNICA DE SOMA (D17): OS 8 CENÁRIOS, COM AS DATAS EXATAS
# =============================================================================
class TesteSomaBase(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.conn = novo_banco_memoria()
        self.addCleanup(self.conn.close)

    def assertSemSobreposicao(self, user_id=UID):
        """Nenhum par de linhas Pro válidas com períodos que se cruzam
        ([início, fim), as de tamanho zero não contam)."""
        validas = [l for l in assinaturas.ler_linhas(self.conn, user_id) if l.plano == 'pro' and l.valida]
        for i, a in enumerate(validas):
            self.assertLessEqual(a.inicio, a.fim)
            for b in validas[i + 1:]:
                if a.fim > a.inicio and b.fim > b.inicio:
                    self.assertFalse(a.inicio < b.fim and b.inicio < a.fim, f"sobreposição: {a} x {b}")

    def bloco(self, agora, admin=False):
        return assinaturas.montar_bloco_assinatura(self.conn, UID, agora, admin=admin, ligada=True)

    def folha(self, agora):
        bloco = self.bloco(agora)
        return (bloco['vale_ate'], bloco['proxima_renovacao'], bloco['valor_stars'], bloco['renovacao_cancelada'])

    def pro_dado(self, agora, dias=30):
        return assinaturas.dar_pro_manual(self.conn, UID, dias, 7803589807, agora)


class TestSomaD17(TesteSomaBase):
    def test_1_assina_em_11_11(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'ativa', '11/11', '11/12')])
        self.assertEqual(self.folha(br(2026, 11, 11)), ('2026-12-11', '2026-12-11', 150, False))
        self.assertSemSobreposicao()

    def test_2_renova_em_11_12(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        pagar(self.conn, br(2026, 12, 11), 'c2')
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'ativa', '11/11', '11/12'),
                                                        ('stars', 'ativa', '11/12', '10/01')])
        self.assertEqual(self.folha(br(2026, 12, 11)), ('2027-01-10', '2027-01-10', 150, False))
        self.assertSemSobreposicao()

    def test_3_pro_dado_em_01_11_e_assina_em_11_11(self):
        self.pro_dado(br(2026, 11, 1))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        self.assertEqual(linhas_resumidas(self.conn), [('manual', 'ativa', '01/11', '01/12'),
                                                        ('stars', 'ativa', '01/12', '31/12')])
        # A próxima cobrança (11/12) fica ANTES do "vale até" (31/12).
        self.assertEqual(self.folha(br(2026, 11, 11)), ('2026-12-31', '2026-12-11', 150, False))
        self.assertSemSobreposicao()

    def test_4_o_mesmo_renova_em_11_12(self):
        self.pro_dado(br(2026, 11, 1))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        pagar(self.conn, br(2026, 12, 11), 'c2')
        self.assertEqual(linhas_resumidas(self.conn)[-1], ('stars', 'ativa', '31/12', '30/01'))
        self.assertEqual(self.folha(br(2026, 12, 11)), ('2027-01-30', '2027-01-10', 150, False))
        self.assertSemSobreposicao()

    def test_5_reembolso_da_cobranca_de_11_11_em_15_11_no_3o_cenario(self):
        self.pro_dado(br(2026, 11, 1))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        antes = assinaturas.fim_do_pro(self.conn, UID)
        retirado = assinaturas.retirar_pro(self.conn, UID, assinaturas.MODO_REEMBOLSO, br(2026, 11, 15),
                                           pagamento_id='c1')
        self.assertTrue(retirado.alterou)
        self.assertEqual(antes - retirado.fim_do_pro, timedelta(days=30))   # o fim único cai exatos 30 dias
        self.assertEqual([l[:2] for l in linhas_resumidas(self.conn)], [('manual', 'ativa'), ('stars', 'reembolsada')])
        self.assertEqual(linhas_resumidas(self.conn)[0], ('manual', 'ativa', '01/11', '01/12'))
        bloco = self.bloco(br(2026, 11, 15))
        self.assertEqual((bloco['estado'], bloco['selo']), ('pro', 'pro'))
        self.assertEqual(self.folha(br(2026, 11, 15)), ('2026-12-01', None, None, False))   # folha PRO DADO
        self.assertSemSobreposicao()

    def test_6_assina_em_11_11_e_e_reembolsado_em_20_11(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        assinaturas.retirar_pro(self.conn, UID, assinaturas.MODO_REEMBOLSO, br(2026, 11, 20), pagamento_id='c1')
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'reembolsada', '11/11', '20/11')])
        situacao = assinaturas.situacao_acesso(self.conn, UID, br(2026, 11, 20))
        self.assertEqual((situacao.estado, situacao.tem_acesso), ('pro_vencido', False))
        self.assertEqual(situacao.fim, br(2026, 11, 20))
        self.assertIsNone(assinaturas.fim_do_pro(self.conn, UID))

    def test_7_pro_dado_assina_e_tirar_pro_em_15_11(self):
        self.pro_dado(br(2026, 11, 1))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        retirado = assinaturas.tirar_pro_manual(self.conn, UID, 7803589807, br(2026, 11, 15))
        self.assertTrue(retirado.alterou)
        self.assertTrue(retirado.tem_pro_pago)
        self.assertEqual(linhas_resumidas(self.conn), [('manual', 'encerrada', '01/11', '15/11'),
                                                        ('stars', 'ativa', '15/11', '15/12')])
        self.assertEqual(self.folha(br(2026, 11, 15)), ('2026-12-15', '2026-12-11', 150, False))
        self.assertSemSobreposicao()

    def test_8_assina_em_11_11_e_ganha_pro_dado_em_15_11(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        self.pro_dado(br(2026, 11, 15))
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'ativa', '11/11', '11/12'),
                                                        ('manual', 'ativa', '11/12', '10/01')])
        self.assertEqual(self.folha(br(2026, 11, 15)), ('2027-01-10', '2026-12-11', 150, False))
        self.assertSemSobreposicao()


class TestSomaOutrosCasos(TesteSomaBase):
    def test_reembolso_que_cai_antes_de_agora_acaba_o_pro_agora(self):
        # Cobrança de 11/11 e renovação de 11/12; em 20/12, a de 11/11 é
        # reembolsada: a renovação recua 30 dias (11/11 a 11/12), antes de
        # agora, então o Pro acaba agora (20/12).
        pagar(self.conn, br(2026, 11, 11), 'c1')
        pagar(self.conn, br(2026, 12, 11), 'c2')
        retirado = assinaturas.retirar_pro(self.conn, UID, assinaturas.MODO_REEMBOLSO, br(2026, 12, 20),
                                           pagamento_id='c1')
        self.assertEqual(retirado.fim_do_pro, br(2026, 12, 20))
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'reembolsada', '11/11', '11/12'),
                                                        ('stars', 'ativa', '11/11', '20/12')])
        # A cobrança seguinte (10/01) fica depois do fim: sem a tolerância de 24 horas.
        situacao = assinaturas.situacao_acesso(self.conn, UID, br(2026, 12, 20))
        self.assertEqual((situacao.estado, situacao.tem_acesso), ('pro_vencido', False))
        self.assertTrue(assinaturas.tem_acesso(self.conn, UID, br(2026, 12, 20, 11, 59, 59)))
        self.assertSemSobreposicao()

    def test_reembolso_repetido_nao_muda_nada(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        assinaturas.retirar_pro(self.conn, UID, assinaturas.MODO_REEMBOLSO, br(2026, 11, 20), pagamento_id='c1')
        antes = linhas_resumidas(self.conn)
        segundo = assinaturas.retirar_pro(self.conn, UID, assinaturas.MODO_REEMBOLSO, br(2026, 11, 21),
                                          pagamento_id='c1')
        self.assertFalse(segundo.alterou)
        self.assertEqual(linhas_resumidas(self.conn), antes)

    def test_vencimento_sem_renovacao_com_a_tolerancia_de_24_horas(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')   # renova_em 11/12 12:00
        for agora, acesso in ((br(2026, 12, 11, 11, 59), True), (br(2026, 12, 11, 13), True),
                              (br(2026, 12, 12, 11, 59, 59), True), (br(2026, 12, 12, 12), False)):
            with self.subTest(agora=agora):
                self.assertEqual(assinaturas.tem_acesso(self.conn, UID, agora), acesso)
        self.assertEqual(assinaturas.situacao_acesso(self.conn, UID, br(2026, 12, 12, 12)).estado, 'pro_vencido')

    def test_vencimento_sem_tolerancia_com_a_renovacao_cancelada_e_no_pro_dado(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        self.conn.execute("UPDATE assinaturas SET estado = 'cancelada', renovacao = 0 WHERE origem = 'stars'")
        self.conn.commit()
        self.assertTrue(assinaturas.tem_acesso(self.conn, UID, br(2026, 12, 11, 11, 59, 59)))
        self.assertFalse(assinaturas.tem_acesso(self.conn, UID, br(2026, 12, 11, 12)))
        conn = novo_banco_memoria()
        assinaturas.dar_pro_manual(conn, UID, 30, 1, br(2026, 11, 1))
        self.assertTrue(assinaturas.tem_acesso(conn, UID, br(2026, 12, 1, 11, 59, 59)))
        self.assertFalse(assinaturas.tem_acesso(conn, UID, br(2026, 12, 1, 12)))
        conn.close()

    def test_pro_vencido_e_novo_lancamento_comeca_agora(self):
        self.pro_dado(br(2026, 11, 1), dias=10)
        self.pro_dado(br(2026, 11, 20), dias=5)   # o anterior venceu em 11/11: começa agora
        self.assertEqual(linhas_resumidas(self.conn), [('manual', 'ativa', '01/11', '11/11'),
                                                        ('manual', 'ativa', '20/11', '25/11')])
        self.assertSemSobreposicao()

    def test_dar_pro_sem_numero_30_e_de_novo_estende_sem_sobrepor(self):
        primeiro = self.pro_dado(br(2026, 11, 1), dias=None)
        self.assertEqual(primeiro.fim_do_pro, br(2026, 12, 1))
        segundo = self.pro_dado(br(2026, 11, 3), dias='5')
        self.assertEqual(segundo.fim_do_pro, br(2026, 12, 6))
        self.assertEqual(linhas_resumidas(self.conn), [('manual', 'ativa', '01/11', '01/12'),
                                                        ('manual', 'ativa', '01/12', '06/12')])
        linhas = self.conn.execute("SELECT origem, plano, dias, criado_por, valor, moeda, pagamento_id, renova_em, "
                                   "renovacao, criado_em FROM assinaturas ORDER BY id").fetchall()
        self.assertEqual(linhas, [('manual', 'pro', 30, 7803589807, None, None, None, None, 0, '2026-11-01 12:00:00'),
                                  ('manual', 'pro', 5, 7803589807, None, None, None, None, 0, '2026-11-03 12:00:00')])
        self.assertSemSobreposicao()

    def test_dar_pro_dias_invalidos_e_usuario_inexistente(self):
        for dias in (0, 367, -1, '0', '367', 'abc', '5.5', '', True, 1.5, [30]):
            with self.subTest(dias=dias):
                with self.assertRaises(ErroRegistro) as ctx:
                    self.pro_dado(br(2026, 11, 1), dias=dias)
                self.assertEqual(ctx.exception.codigo, 'dias_invalidos')
        for dias in (1, 366, '366', ' 7 '):
            self.pro_dado(br(2026, 11, 1), dias=dias)
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.dar_pro_manual(self.conn, 999, 30, 1, br(2026, 11, 1))
        self.assertEqual(ctx.exception.codigo, 'usuario_nao_encontrado')
        self.assertSemSobreposicao()

    def test_mesmo_pagamento_nunca_vale_duas_vezes(self):
        primeiro = pagar(self.conn, br(2026, 11, 11), 'c1')
        segundo = pagar(self.conn, br(2026, 11, 12), 'c1')
        self.assertTrue(segundo.repetido)
        self.assertEqual(segundo.linha.id, primeiro.linha.id)
        self.assertEqual(len(linhas_resumidas(self.conn)), 1)

    def test_dois_lancamentos_juntos_nao_se_sobrepoem(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            caminho = os.path.join(pasta, 'soma.db')
            conn = sqlite3.connect(caminho)
            conn.executescript(ESQUEMA)
            conn.executescript(assinaturas.SQL_CRIAR_TABELAS)
            conn.execute("INSERT INTO usuarios (user_id, nome) VALUES (?, 'Y')", (UID,))
            conn.commit()
            conn.close()
            barreira = threading.Barrier(4, timeout=10)

            def lancar(dias):
                c = sqlite3.connect(caminho, timeout=10, check_same_thread=False)
                try:
                    barreira.wait()
                    assinaturas.adicionar_pro(c, UID, dias, 'manual', br(2026, 11, 1), criado_por=1)
                finally:
                    c.close()

            with mock.patch.object(registro, '_esperar', lambda s: None):
                threads = [threading.Thread(target=lancar, args=(d,)) for d in (10, 20, 30, 40)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(30)
            self.conn = sqlite3.connect(caminho)
            self.assertSemSobreposicao()
            self.assertEqual(assinaturas.fim_do_pro(self.conn, UID), br(2026, 11, 1) + timedelta(days=100))
            self.conn.close()
            self.conn = novo_banco_memoria()

    def test_tirar_pro_sem_pro_dado_e_com_pro_pago(self):
        retirado = assinaturas.tirar_pro_manual(self.conn, UID, 1, br(2026, 11, 15))
        self.assertEqual((retirado.alterou, retirado.tem_pro_pago), (False, False))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        retirado = assinaturas.tirar_pro_manual(self.conn, UID, 1, br(2026, 11, 15))
        self.assertEqual((retirado.alterou, retirado.tem_pro_pago), (False, True))
        self.assertEqual(linhas_resumidas(self.conn), [('stars', 'ativa', '11/11', '11/12')])
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.tirar_pro_manual(self.conn, 999, 1, br(2026, 11, 15))
        self.assertEqual(ctx.exception.codigo, 'usuario_nao_encontrado')

    def test_tirar_pro_com_dado_futuro_e_dado_ja_usado(self):
        self.pro_dado(br(2026, 10, 1), dias=5)                 # 01/10 a 06/10, já usado
        pagar(self.conn, br(2026, 11, 11), 'c1')                # 11/11 a 11/12
        self.pro_dado(br(2026, 11, 12), dias=10)               # 11/12 a 21/12 (futuro)
        retirado = assinaturas.tirar_pro_manual(self.conn, UID, 1, br(2026, 11, 15))
        self.assertEqual(linhas_resumidas(self.conn), [('manual', 'ativa', '01/10', '06/10'),
                                                        ('stars', 'ativa', '11/11', '11/12'),
                                                        ('manual', 'encerrada', '11/12', '11/12')])
        self.assertEqual(retirado.fim_do_pro, br(2026, 12, 11))
        self.assertSemSobreposicao()

    def test_tirar_pro_so_dado_acaba_agora(self):
        self.pro_dado(br(2026, 11, 1))
        retirado = assinaturas.tirar_pro_manual(self.conn, UID, 1, br(2026, 11, 15))
        self.assertEqual((retirado.alterou, retirado.tem_pro_pago), (True, False))
        situacao = assinaturas.situacao_acesso(self.conn, UID, br(2026, 11, 15))
        self.assertEqual((situacao.estado, situacao.fim), ('pro_vencido', br(2026, 11, 15)))


# =============================================================================
# SELO E FOLHA MINHA ASSINATURA (AJUSTE DE 2026-11-12)
# =============================================================================
CAMPOS = ('ligada', 'estado', 'inicio', 'fim', 'renovacao', 'teste_disponivel', 'teste_previsto', 'preco',
          'limite_ia_dia', 'uso_ia_hoje', 'selo', 'selo_tocavel', 'dias_restantes', 'valor_stars',
          'proxima_renovacao', 'renovacao_cancelada', 'vale_ate', 'modo_leitura')


class TestSeloEFolha(TesteSomaBase):
    def sete(self, bloco):
        return {chave: bloco[chave] for chave in ('selo', 'selo_tocavel', 'dias_restantes', 'valor_stars',
                                                   'proxima_renovacao', 'renovacao_cancelada', 'vale_ate')}

    def assertSemAdmin(self, bloco):
        self.assertNotIn('admin', json.dumps(bloco).lower())

    def test_campos_e_ordem_do_contrato(self):
        bloco = self.bloco(br(2026, 11, 14))
        self.assertEqual(tuple(bloco), CAMPOS)
        self.assertEqual(bloco['preco'], {'stars': 150, 'reais_aproximado': 19})
        self.assertEqual(bloco['limite_ia_dia'], 17)

    def test_teste_ativo_hoje_14_11(self):
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11, 14))
        bloco = self.bloco(br(2026, 11, 14))
        self.assertEqual(self.sete(bloco), {'selo': 'gratis', 'selo_tocavel': True, 'dias_restantes': 7,
                                            'valor_stars': None, 'proxima_renovacao': None,
                                            'renovacao_cancelada': False, 'vale_ate': None})
        self.assertEqual((bloco['estado'], bloco['inicio'], bloco['fim'], bloco['renovacao'],
                          bloco['teste_disponivel'], bloco['teste_previsto']),
                         ('teste', '2026-11-11', '2026-11-21', False, False, None))
        self.assertSemAdmin(bloco)

    def test_assinou_em_11_11(self):
        pagar(self.conn, br(2026, 11, 11), 'c1')
        bloco = self.bloco(br(2026, 11, 11))
        self.assertEqual(self.sete(bloco), {'selo': 'pro', 'selo_tocavel': True, 'dias_restantes': 30,
                                            'valor_stars': 150, 'proxima_renovacao': '2026-12-11',
                                            'renovacao_cancelada': False, 'vale_ate': '2026-12-11'})
        self.assertEqual((bloco['estado'], bloco['renovacao'], bloco['inicio'], bloco['fim']),
                         ('pro', True, '2026-11-11', '2026-12-11'))
        self.assertSemAdmin(bloco)

    def test_pro_dado_e_assinou_renovando_e_cancelado(self):
        self.pro_dado(br(2026, 11, 1))
        pagar(self.conn, br(2026, 11, 11), 'c1')
        self.assertEqual(self.sete(self.bloco(br(2026, 11, 11))),
                         {'selo': 'pro', 'selo_tocavel': True, 'dias_restantes': 50, 'valor_stars': 150,
                          'proxima_renovacao': '2026-12-11', 'renovacao_cancelada': False, 'vale_ate': '2026-12-31'})
        self.conn.execute("UPDATE assinaturas SET estado = 'cancelada', renovacao = 0 WHERE origem = 'stars'")
        self.conn.commit()
        bloco = self.bloco(br(2026, 11, 11))
        self.assertEqual(self.sete(bloco),
                         {'selo': 'pro', 'selo_tocavel': True, 'dias_restantes': 50, 'valor_stars': 150,
                          'proxima_renovacao': None, 'renovacao_cancelada': True, 'vale_ate': '2026-12-31'})
        self.assertEqual(bloco['renovacao'], False)
        # Fora da tolerância, sem pagamento novo: renova_em vencido há mais de 24 h conta como cancelada.
        self.conn.execute("UPDATE assinaturas SET estado = 'ativa', renovacao = 1 WHERE origem = 'stars'")
        self.conn.commit()
        bloco = self.bloco(br(2026, 12, 12, 12))
        self.assertEqual((bloco['proxima_renovacao'], bloco['renovacao_cancelada'], bloco['vale_ate']),
                         (None, True, '2026-12-31'))

    def test_so_pro_dado(self):
        self.pro_dado(br(2026, 11, 1))
        bloco = self.bloco(br(2026, 11, 1))
        self.assertEqual(self.sete(bloco), {'selo': 'pro', 'selo_tocavel': True, 'dias_restantes': 30,
                                            'valor_stars': None, 'proxima_renovacao': None,
                                            'renovacao_cancelada': False, 'vale_ate': '2026-12-01'})
        self.assertEqual(bloco['renovacao'], False)

    def test_assinatura_cancelada_e_usada_antes_de_um_pro_dado_nao_conta(self):
        pagar(self.conn, br(2026, 9, 1), 'c1')     # 01/09 a 01/10, já usada
        self.conn.execute("UPDATE assinaturas SET estado = 'cancelada', renovacao = 0")
        self.conn.commit()
        self.pro_dado(br(2026, 11, 1))
        bloco = self.bloco(br(2026, 11, 5))
        self.assertEqual((bloco['valor_stars'], bloco['proxima_renovacao'], bloco['renovacao_cancelada'],
                          bloco['vale_ate']), (None, None, False, '2026-12-01'))

    def test_admin_ve_pro_sem_toque_e_sem_datas(self):
        self.ambiente(ADMIN_IDS=str(UID))
        bloco = assinaturas.montar_bloco_assinatura(self.conn, UID, br(2026, 11, 14), ligada=True)
        self.assertEqual(self.sete(bloco), {'selo': 'pro', 'selo_tocavel': False, 'dias_restantes': None,
                                            'valor_stars': None, 'proxima_renovacao': None,
                                            'renovacao_cancelada': False, 'vale_ate': None})
        self.assertEqual((bloco['estado'], bloco['inicio'], bloco['fim'], bloco['renovacao'], bloco['limite_ia_dia']),
                         ('pro', None, None, None, None))
        self.assertSemAdmin(bloco)
        # Mesmo com linhas (um Pro dado a si mesmo), o admin continua sem datas.
        self.pro_dado(br(2026, 11, 1))
        self.assertEqual(assinaturas.montar_bloco_assinatura(self.conn, UID, br(2026, 11, 14), ligada=True)['vale_ate'],
                         None)

    def test_sem_plano_sem_selo_e_vencidos_com_o_selo_assinar(self):
        bloco = self.bloco(br(2026, 11, 14))
        self.assertEqual(self.sete(bloco), {'selo': None, 'selo_tocavel': False, 'dias_restantes': None,
                                            'valor_stars': None, 'proxima_renovacao': None,
                                            'renovacao_cancelada': False, 'vale_ate': None})
        self.assertEqual((bloco['estado'], bloco['inicio'], bloco['fim'], bloco['teste_disponivel'],
                          bloco['teste_previsto']),
                         ('sem_plano', None, None, True, {'inicio': '2026-11-14', 'fim': '2026-11-24'}))
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 1))
        bloco = self.bloco(br(2026, 11, 14))
        self.assertEqual((bloco['estado'], bloco['inicio'], bloco['fim'], bloco['selo'], bloco['dias_restantes'],
                          bloco['teste_disponivel'], bloco['teste_previsto']),
                         ('teste_vencido', '2026-11-01', '2026-11-11', 'assinar', None, False, None))
        self.assertEqual((bloco['selo_tocavel'], bloco['modo_leitura']), (True, True))
        self.pro_dado(br(2026, 11, 12), dias=1)
        bloco = self.bloco(br(2026, 11, 14))
        self.assertEqual((bloco['estado'], bloco['inicio'], bloco['fim'], bloco['selo'], bloco['vale_ate']),
                         ('pro_vencido', '2026-11-12', '2026-11-13', 'assinar', None))
        self.assertEqual((bloco['selo_tocavel'], bloco['modo_leitura']), (True, True))

    def test_modo_leitura_e_selo_assinar_em_cada_caso(self):
        def caso(user_id, agora, admin=False, ligada=True):
            bloco = assinaturas.montar_bloco_assinatura(self.conn, user_id, agora, admin=admin, ligada=ligada)
            self.assertNotIn('admin', json.dumps(bloco).lower())
            self.assertIn(bloco['selo'], ('gratis', 'pro', 'assinar', None))
            return bloco['estado'], bloco['selo'], bloco['selo_tocavel'], bloco['modo_leitura']

        agora = br(2026, 11, 14)
        self.assertEqual(caso(UID, agora), ('sem_plano', None, False, False))
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11))
        self.assertEqual(caso(UID, agora), ('teste', 'gratis', True, False))
        self.assertEqual(caso(UID, br(2026, 11, 21, 12)), ('teste_vencido', 'assinar', True, True))
        self.assertEqual(caso(UID, br(2026, 11, 21, 12), ligada=False), ('teste_vencido', None, False, False))
        self.assertEqual(caso(UID, br(2026, 11, 21, 12), admin=True), ('pro', 'pro', False, False))
        pagar(self.conn, br(2026, 11, 22), 'c1')
        self.assertEqual(caso(UID, br(2026, 11, 22)), ('pro', 'pro', True, False))
        self.assertEqual(caso(UID, br(2026, 12, 23, 12)), ('pro_vencido', 'assinar', True, True))
        self.assertEqual(caso(UID, br(2026, 12, 23, 12), ligada=False), ('pro_vencido', None, False, False))
        assinaturas.dar_pro_manual(self.conn, OUTRO_UID, 1, 1, br(2026, 11, 1))
        self.assertEqual(caso(OUTRO_UID, br(2026, 11, 1)), ('pro', 'pro', True, False))
        self.assertEqual(caso(OUTRO_UID, agora), ('pro_vencido', 'assinar', True, True))

    def test_chave_desligada_sem_selo(self):
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11, 14))
        bloco = assinaturas.montar_bloco_assinatura(self.conn, UID, br(2026, 11, 14), admin=False, ligada=False)
        self.assertEqual((bloco['ligada'], bloco['selo'], bloco['selo_tocavel'], bloco['estado']),
                         (False, None, False, 'teste'))
        self.ambiente(ADMIN_IDS=str(UID))
        bloco = assinaturas.montar_bloco_assinatura(self.conn, UID, br(2026, 11, 14), ligada=False)
        self.assertEqual((bloco['selo'], bloco['selo_tocavel']), (None, False))

    def test_selo_tocavel_em_todo_selo_de_quem_nao_e_admin(self):
        cenarios = []
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 11))
        cenarios.append(self.bloco(br(2026, 11, 12)))
        pagar(self.conn, br(2026, 11, 12), 'c1')
        cenarios.append(self.bloco(br(2026, 11, 12)))
        self.pro_dado(br(2026, 11, 12))
        cenarios.append(self.bloco(br(2026, 12, 20)))
        for bloco in cenarios:
            self.assertIsNotNone(bloco['selo'])
            self.assertIn(bloco['selo'], ('gratis', 'pro'))
            self.assertTrue(bloco['selo_tocavel'])
            self.assertSemAdmin(bloco)

    def test_valor_stars_e_o_da_linha_mesmo_com_outro_preco(self):
        pagar(self.conn, br(2026, 11, 11), 'c1', valor=150)
        self.ambiente(PRECO_PRO_STARS='200')
        bloco = self.bloco(br(2026, 11, 12))
        self.assertEqual((bloco['valor_stars'], bloco['preco']['stars']), (150, 200))


# =============================================================================
# COMANDOS DE ADMIN (FUNÇÕES) E LISTA DE USUÁRIOS
# =============================================================================
class TestComandosDeAdminFuncoes(TesteSomaBase):
    def test_estender_teste(self):
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 11, 1, 14))
        self.assertFalse(assinaturas.tem_acesso(self.conn, UID, br(2026, 11, 15)))    # vencido
        fim = assinaturas.estender_teste(self.conn, UID, '7', 1, br(2026, 11, 15))
        self.assertEqual(fim, br(2026, 11, 18, 14))
        self.assertTrue(assinaturas.tem_acesso(self.conn, UID, br(2026, 11, 15)))
        self.assertEqual(self.conn.execute("SELECT dias FROM assinaturas").fetchone()[0], 17)
        for dias in (0, 61, 'x', None, -1):
            with self.subTest(dias=dias):
                with self.assertRaises(ErroRegistro) as ctx:
                    assinaturas.estender_teste(self.conn, UID, dias, 1, br(2026, 11, 15))
                self.assertEqual(ctx.exception.codigo, 'dias_invalidos')
        self.assertEqual(assinaturas.estender_teste(self.conn, UID, 60, 1, br(2026, 11, 15)), br(2027, 1, 17, 14))
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.estender_teste(self.conn, OUTRO_UID, 5, 1, br(2026, 11, 15))
        self.assertEqual(ctx.exception.codigo, 'sem_teste')
        with self.assertRaises(ErroRegistro) as ctx:
            assinaturas.estender_teste(self.conn, 999, 5, 1, br(2026, 11, 15))
        self.assertEqual(ctx.exception.codigo, 'usuario_nao_encontrado')

    def test_reiniciar_teste_na_mesma_linha_ou_cria(self):
        assinaturas.iniciar_teste(self.conn, UID, br(2026, 10, 1))
        teste = assinaturas.reiniciar_teste(self.conn, UID, 77, br(2026, 11, 15, 9))
        self.assertEqual((teste.inicio, teste.fim), (br(2026, 11, 15, 9), br(2026, 11, 25, 9)))
        self.assertEqual(self.conn.execute("SELECT COUNT(*), MIN(criado_por) FROM assinaturas WHERE user_id = ?",
                                           (UID,)).fetchone(), (1, None))
        assinaturas.reiniciar_teste(self.conn, OUTRO_UID, 77, br(2026, 11, 15, 9))
        self.assertEqual(self.conn.execute("SELECT origem, criado_por, inicio FROM assinaturas WHERE user_id = ?",
                                           (OUTRO_UID,)).fetchone(), ('teste', 77, '2026-11-15 09:00:00'))
        self.assertEqual(assinaturas.situacao_acesso(self.conn, OUTRO_UID, br(2026, 11, 16)).estado, 'teste')
        with self.assertRaises(ErroRegistro):
            assinaturas.reiniciar_teste(self.conn, 999, 77, br(2026, 11, 15))

    def test_listar_usuarios(self):
        self.ambiente(ADMIN_IDS='7803589807')
        conn = novo_banco_memoria(((1, 'Ana'), (2, 'Bia'), (3, 'Caio'), (4, 'Duda'), (7803589807, 'Ygor')))
        agora = br(2026, 11, 15)
        assinaturas.iniciar_teste(conn, 1, br(2026, 11, 10))             # teste até 20/11
        assinaturas.dar_pro_manual(conn, 2, 3, 7803589807, br(2026, 11, 14))   # pro até 17/11
        assinaturas.iniciar_teste(conn, 3, br(2026, 10, 1))              # teste vencido em 11/10
        assinaturas.iniciar_teste(conn, 5, br(2026, 11, 1))              # sem cadastro, vencido em 11/11
        itens = assinaturas.listar_usuarios_com_plano(conn, agora)
        self.assertEqual([(i.user_id, i.nome, i.estado, assinaturas.data_texto(i.fim), i.origem) for i in itens], [
            (2, 'Bia', 'pro', '2026-11-17', 'manual'),
            (1, 'Ana', 'teste', '2026-11-20', 'teste'),
            (5, None, 'teste_vencido', '2026-11-11', 'teste'),
            (3, 'Caio', 'teste_vencido', '2026-10-11', 'teste'),
            (4, 'Duda', 'sem_plano', None, None),
            (7803589807, 'Ygor', 'admin', None, None),
        ])
        self.assertEqual(len(assinaturas.listar_usuarios_com_plano(conn, agora, limite=2)), 2)
        conn.close()

    def test_listar_no_maximo_50(self):
        conn = novo_banco_memoria(tuple((i, f'U{i}') for i in range(1, 61)))
        self.assertEqual(len(assinaturas.listar_usuarios_com_plano(conn, br(2026, 11, 15))), 50)
        conn.close()


# =============================================================================
# LIMITE DIÁRIO DE IA (FUNÇÕES)
# =============================================================================
class TestUsoIaFuncoes(TesteSomaBase):
    def test_reserva_ate_o_limite_e_devolve(self):
        agora = br(2026, 11, 14, 10)
        dias = [assinaturas.reservar_uso_ia(self.conn, UID, agora, limite=3, admin=False) for _ in range(4)]
        self.assertEqual(dias, ['2026-11-14'] * 3 + [None])
        self.assertEqual(assinaturas.uso_ia_hoje(self.conn, UID, agora), 3)
        assinaturas.devolver_uso_ia(self.conn, UID, '2026-11-14')
        self.assertEqual(assinaturas.uso_ia_hoje(self.conn, UID, agora), 2)
        for _ in range(5):
            assinaturas.devolver_uso_ia(self.conn, UID, '2026-11-14')
        self.assertEqual(assinaturas.uso_ia_hoje(self.conn, UID, agora), 0)    # nunca abaixo de 0
        assinaturas.devolver_uso_ia(self.conn, OUTRO_UID, '2026-11-14')         # sem linha: nada

    def test_dia_de_brasilia_e_virada(self):
        self.ambiente(LIMITE_IA_DIA='1')
        self.assertEqual(assinaturas.reservar_uso_ia(self.conn, UID, br(2026, 11, 14, 23, 59, 59)), '2026-11-14')
        self.assertIsNone(assinaturas.reservar_uso_ia(self.conn, UID, br(2026, 11, 14, 23, 59, 59)))
        # 02:30 UTC do dia 15 ainda é dia 14 em Brasília.
        self.assertIsNone(assinaturas.reservar_uso_ia(self.conn, UID, datetime(2026, 11, 15, 2, 30, tzinfo=timezone.utc)))
        self.assertEqual(assinaturas.reservar_uso_ia(self.conn, UID, br(2026, 11, 15, 0, 0)), '2026-11-15')

    def test_admin_nunca_recusado_mas_contado(self):
        self.ambiente(ADMIN_IDS=str(UID), LIMITE_IA_DIA='2')
        agora = br(2026, 11, 14)
        self.assertTrue(all(assinaturas.reservar_uso_ia(self.conn, UID, agora) for _ in range(5)))
        self.assertEqual(assinaturas.uso_ia_hoje(self.conn, UID, agora), 5)

    def test_dois_pedidos_juntos_na_ultima_vaga_so_um_passa(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            caminho = os.path.join(pasta, 'uso.db')
            conn = sqlite3.connect(caminho)
            conn.executescript(assinaturas.SQL_CRIAR_TABELAS)
            conn.execute("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 16)", (UID,))
            conn.commit()
            conn.close()
            for _ in range(5):
                barreira = threading.Barrier(2, timeout=10)
                resultados = []

                def reservar():
                    c = sqlite3.connect(caminho, timeout=10, check_same_thread=False)
                    try:
                        barreira.wait()
                        resultados.append(assinaturas.reservar_uso_ia(c, UID, br(2026, 11, 14), limite=17, admin=False))
                    finally:
                        c.close()

                with mock.patch.object(registro, '_esperar', lambda s: None):
                    threads = [threading.Thread(target=reservar) for _ in range(2)]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join(30)
                self.assertEqual(sorted(resultados, key=str), ['2026-11-14', None])
                conn = sqlite3.connect(caminho)
                self.assertEqual(conn.execute("SELECT chamadas FROM uso_ia").fetchone()[0], 17)
                conn.execute("UPDATE uso_ia SET chamadas = 16")
                conn.commit()
                conn.close()


# =============================================================================
# ROTAS DA API
# =============================================================================
class TesteApiBase(TesteRegistroBase):
    """A base do testes_rotas_registro.py (UID 4242 Ygor, fechamento 28, com
    Alimentação, Casa e Roupas; OUTRO_UID 5151; INCOMPLETO; SEM_CADASTRO),
    com as variáveis da assinatura limpas, o "agora" trocável e o limite por
    minuto folgado (para chegar a 17 por dia)."""

    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        for nome in VARIAVEIS:
            os.environ.pop(nome, None)
        self.agora = br(2026, 11, 14, 12)
        app.dependency_overrides[obter_agora] = lambda: self.agora
        self.limite = LimiteDeChamadas(maximo=1000, janela=60.0)

    def ambiente(self, **valores):
        AmbienteLimpo.ambiente(self, **valores)

    def get(self, rota, user_id=UID, client=None):
        return (client or self.client).get(rota, headers=self.cabecalho(user_id))

    def executar(self, funcao, *args, **kwargs):
        conn = sqlite3.connect(self.caminho)
        try:
            return funcao(conn, *args, **kwargs)
        finally:
            conn.close()

    def escrever(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def uso(self, user_id=UID, dia='2026-11-14'):
        linha = self.ler("SELECT chamadas FROM uso_ia WHERE user_id = ? AND dia = ?", (user_id, dia))
        return linha[0][0] if linha else 0

    def interpretar(self, texto="pão 20", user_id=UID, tipo="gasto", client=None):
        return self.post("/registro/interpretar", {"tipo": tipo, "texto": texto}, user_id, client)


ROTAS_DE_LEITURA = (
    ('get', '/resumo', None), ('get', '/historico', None), ('get', '/gastos/recentes', None),
    ('get', '/movimentacoes/recentes', None), ('get', '/gastos/1', None), ('get', '/categorias', None),
    ('get', '/recorrentes', None), ('get', '/receitas-recorrentes', None), ('get', '/categorias/ranking', None),
)
ROTAS_DE_ESCRITA = (
    ('post', '/registro/interpretar', {"tipo": "gasto", "texto": "pão 20"}),
    ('post', '/gastos', corpo_gasto()), ('post', '/receitas', corpo_receita()),
    ('post', '/recorrentes', {"chave": CHAVE, "dia_cobranca": 5, "valor": 10, "descricao": "X", "categoria": "Casa"}),
    ('put', '/recorrentes/1', {"dia_cobranca": 5, "valor": 10, "descricao": "X", "categoria": "Casa"}),
    ('delete', '/recorrentes/1', None),
    ('post', '/receitas-recorrentes', {"chave": CHAVE, "dia_recebimento": 5, "valor": 10, "descricao": "X"}),
    ('put', '/receitas-recorrentes/1', {"dia_recebimento": 5, "valor": 10, "descricao": "X"}),
    ('delete', '/receitas-recorrentes/1', None),
    ('post', '/categorias', {"chave": CHAVE, "nome": "Pets"}), ('put', '/categorias/1', {"nome": "Pets"}),
    ('delete', '/categorias/1', None),
)
ROTAS_COM_DADOS = ROTAS_DE_LEITURA + ROTAS_DE_ESCRITA
# (a base do testes_rotas_registro.py não tem receitas_recorrentes: a rota dá
# 500 lá com a chave desligada, e com ela ligada o 403 vem antes da consulta)
TABELAS_DE_DADOS = ('transacoes', 'parcelamentos', 'receitas', 'gastos_recorrentes', 'categorias',
                    'chaves_registro', 'uso_ia', 'assinaturas')


class TestRotasAcesso(TesteApiBase):
    def pedir(self, metodo, rota, corpo, user_id=UID):
        kwargs = {"headers": self.cabecalho(user_id)}
        if corpo is not None:
            kwargs["json"] = corpo
        return getattr(self.client, metodo)(rota, **kwargs)

    def contagens(self):
        return {tabela: self.contar(tabela) for tabela in TABELAS_DE_DADOS}

    def test_toda_escrita_usa_acesso_liberado_e_toda_leitura_usa_leitura_liberada(self):
        # Percorre TODAS as rotas: uma rota nova não escapa da regra (PRO-106,
        # modo somente leitura). Fora as livres: método diferente de GET usa a
        # acesso_liberado; GET com dados usa a leitura_liberada (nunca as duas).
        livres = {'/saude', '/me', '/assinatura', '/assinaturas/teste', '/cadastro'}

        def chamadas(dependente):
            for dep in dependente.dependencies:
                yield dep.call
                yield from chamadas(dep)

        vistas, escritas, leituras = set(), set(), set()
        for rota in app.routes:
            if not isinstance(rota, APIRoute):
                continue
            usadas = set(chamadas(rota.dependant))
            escrita = api_main.acesso_liberado in usadas
            leitura = api_main.leitura_liberada in usadas
            vistas.add(rota.path)
            with self.subTest(rota=rota.path, metodos=rota.methods):
                if rota.path in livres:
                    self.assertFalse(escrita or leitura)
                elif rota.methods == {'GET'}:
                    self.assertEqual((leitura, escrita), (True, False))
                    leituras.add(rota.path)
                else:
                    self.assertNotIn('GET', rota.methods)
                    self.assertEqual((escrita, leitura), (True, False))
                    escritas.update((metodo, rota.path) for metodo in rota.methods)
        self.assertTrue(livres <= vistas)
        self.assertEqual(len(leituras), 9)
        self.assertEqual(len(escritas), 12)
        self.assertEqual((len(ROTAS_DE_LEITURA), len(ROTAS_DE_ESCRITA)), (9, 12))

    def test_chave_desligada_nenhuma_rota_responde_403_nem_consulta_a_assinatura(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 10, 1))   # teste vencido
        self.instalar(resposta_gasto())
        with mock.patch.object(assinaturas, 'situacao_acesso', side_effect=AssertionError("consultou")):
            for metodo, rota, corpo in ROTAS_COM_DADOS:
                with self.subTest(rota=rota, metodo=metodo):
                    resposta = self.pedir(metodo, rota, corpo)
                    self.assertNotEqual(resposta.status_code, 403, resposta.text)

    def preparar_leituras(self):
        # A base não tem receitas_recorrentes: cria com o texto do bot, para o GET responder 200.
        from testes_receitas_recorrentes import criar_tabela
        criar_tabela(self.caminho)
        self.escrever("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, descricao) "
                      "VALUES (?, 'Casa', 50, '2026-11-01 10:00:00', 'Luz')", (UID,))

    def conferir_somente_leitura(self, codigo):
        ia = self.instalar()
        antes = self.contagens()
        for metodo, rota, corpo in ROTAS_DE_LEITURA:
            with self.subTest(rota=rota, metodo=metodo):
                self.assertEqual(self.pedir(metodo, rota, corpo).status_code, 200)
        for metodo, rota, corpo in ROTAS_DE_ESCRITA:
            with self.subTest(rota=rota, metodo=metodo):
                self.assertErro(self.pedir(metodo, rota, corpo), 403, codigo)
        self.assertEqual(ia.chamadas, [])
        self.assertEqual(self.contagens(), antes)

    def test_teste_vencido_le_tudo_e_recebe_403_nas_escritas_sem_mexer_em_nada_e_sem_ia(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.preparar_leituras()
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 1, 14))   # venceu em 11/11 14:00
        with mock.patch.object(registro, 'listar_categorias', side_effect=AssertionError("consultou dados")):
            self.assertErro(self.interpretar(), 403, "assinatura_vencida")
        self.conferir_somente_leitura("assinatura_vencida")
        self.assertEqual(self.get('/gastos/1').json()['descricao'], 'Luz')

    def test_pro_vencido_le_tudo_e_recebe_403_nas_escritas(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.preparar_leituras()
        self.executar(assinaturas.dar_pro_manual, UID, 1, 1, br(2026, 11, 1))
        self.conferir_somente_leitura("assinatura_vencida")

    def test_sem_plano_com_cadastro_le_tudo_e_recebe_403_ausente_nas_escritas(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.preparar_leituras()
        self.conferir_somente_leitura("assinatura_ausente")
        # Sem cadastro: a leitura segue a regra de sempre (404); a escrita é recusada antes.
        self.assertErro(self.get('/resumo', SEM_CADASTRO), 404, "usuario_nao_cadastrado")
        self.assertErro(self.post('/gastos', corpo_gasto(), SEM_CADASTRO), 403, "assinatura_ausente")

    def test_leitura_nunca_consulta_a_assinatura(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.preparar_leituras()
        with mock.patch.object(assinaturas, 'situacao_acesso', side_effect=AssertionError("consultou")):
            for metodo, rota, corpo in ROTAS_DE_LEITURA:
                with self.subTest(rota=rota):
                    self.assertEqual(self.pedir(metodo, rota, corpo).status_code, 200)

    def test_com_teste_ou_pro_valendo_tudo_como_antes(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11, 14))
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, 30, 1, br(2026, 11, 1))
        self.instalar(resposta_gasto())
        self.assertEqual(self.get('/resumo').status_code, 200)
        self.assertEqual(self.post("/gastos", corpo_gasto()).status_code, 201)
        self.assertEqual(self.interpretar().status_code, 200)
        self.assertEqual(self.get('/resumo', OUTRO_UID).status_code, 200)
        # O mesmo teste, no fim exato (21/11 14:00), já recusa a escrita (a leitura continua).
        self.agora = br(2026, 11, 21, 14)
        chave_receita = "40000000-0000-4000-8000-000000000001"
        self.assertErro(self.post("/receitas", corpo_receita(chave=chave_receita)), 403, "assinatura_vencida")
        self.assertEqual(self.get('/resumo').status_code, 200)
        self.agora = br(2026, 11, 21, 13, 59, 59)
        self.assertEqual(self.post("/receitas", corpo_receita(chave=chave_receita)).status_code, 201)

    def test_admin_nunca_bloqueado(self):
        self.ambiente(ASSINATURAS_LIGADAS='1', ADMIN_IDS=f'{UID}, abc')
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.instalar(resposta_gasto())
        with mock.patch.object(assinaturas, 'situacao_acesso', side_effect=AssertionError("admin não consulta")):
            self.assertEqual(self.get('/resumo').status_code, 200)
            self.assertEqual(self.interpretar().status_code, 200)
            self.assertEqual(self.post("/gastos", corpo_gasto()).status_code, 201)
        self.assertErro(self.post("/gastos", corpo_gasto(chave="00000000-0000-4000-8000-000000000009",
                                                          categoria="Lazer"), OUTRO_UID), 403, "assinatura_vencida")

    def test_dois_usuarios_independentes(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11, 14))
        self.assertEqual(self.post("/receitas", corpo_receita()).status_code, 201)
        self.assertErro(self.post("/receitas", corpo_receita(), OUTRO_UID), 403, "assinatura_ausente")
        resposta = self.post("/assinaturas/teste", {}, OUTRO_UID)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(self.post("/receitas", corpo_receita(), OUTRO_UID).status_code, 201)
        self.assertEqual(self.get('/assinatura').json()['inicio'], '2026-11-11')
        self.assertEqual(self.get('/assinatura', OUTRO_UID).json()['inicio'], '2026-11-14')


class TestRotasAssinatura(TesteApiBase):
    def test_get_assinatura_sem_cadastro_e_sem_plano(self):
        resposta = self.get('/assinatura', SEM_CADASTRO)
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(tuple(corpo), CAMPOS)
        self.assertEqual(corpo, {
            'ligada': False, 'estado': 'sem_plano', 'inicio': None, 'fim': None, 'renovacao': False,
            'teste_disponivel': True, 'teste_previsto': {'inicio': '2026-11-14', 'fim': '2026-11-24'},
            'preco': {'stars': 150, 'reais_aproximado': 19}, 'limite_ia_dia': 17, 'uso_ia_hoje': 0,
            'selo': None, 'selo_tocavel': False, 'dias_restantes': None, 'valor_stars': None,
            'proxima_renovacao': None, 'renovacao_cancelada': False, 'vale_ate': None, 'modo_leitura': False,
        })

    def test_get_assinatura_teste_ligada(self):
        self.ambiente(ASSINATURAS_LIGADAS='1', LIMITE_IA_DIA='20', PRECO_PRO_STARS='149')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11, 14))
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 4)", (UID,))
        corpo = self.get('/assinatura').json()
        self.assertEqual(corpo, {
            'ligada': True, 'estado': 'teste', 'inicio': '2026-11-11', 'fim': '2026-11-21', 'renovacao': False,
            'teste_disponivel': False, 'teste_previsto': None, 'preco': {'stars': 149, 'reais_aproximado': 19},
            'limite_ia_dia': 20, 'uso_ia_hoje': 4, 'selo': 'gratis', 'selo_tocavel': True, 'dias_restantes': 7,
            'valor_stars': None, 'proxima_renovacao': None, 'renovacao_cancelada': False, 'vale_ate': None,
            'modo_leitura': False,
        })

    def test_nenhuma_resposta_tem_a_palavra_admin(self):
        self.ambiente(ASSINATURAS_LIGADAS='1', ADMIN_IDS=str(UID))
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11))
        respostas = [self.get('/assinatura'), self.get('/me'), self.get('/assinatura', OUTRO_UID),
                     self.get('/me', OUTRO_UID), self.post('/assinaturas/teste', {}),
                     self.post('/assinaturas/teste', {}, OUTRO_UID), self.get('/assinatura', SEM_CADASTRO)]
        for resposta in respostas:
            with self.subTest(resposta=resposta.text):
                self.assertNotIn('admin', resposta.text.lower())
        corpo = respostas[0].json()
        self.assertEqual((corpo['estado'], corpo['selo'], corpo['selo_tocavel'], corpo['inicio'], corpo['fim'],
                          corpo['renovacao'], corpo['limite_ia_dia']), ('pro', 'pro', False, None, None, None, None))
        self.assertEqual(respostas[4].status_code, 409)
        self.assertEqual(respostas[4].json(), {"detail": "plano_ativo"})

    def test_post_teste_201_200_e_409(self):
        resposta = self.post('/assinaturas/teste', {}, SEM_CADASTRO)   # antes do POST /cadastro
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json(), {"inicio": "2026-11-14", "fim": "2026-11-24", "repetido": False})
        repetido = self.post('/assinaturas/teste', {}, SEM_CADASTRO)
        self.assertEqual(repetido.status_code, 200)
        self.assertEqual(repetido.json(), {"inicio": "2026-11-14", "fim": "2026-11-24", "repetido": True})
        self.agora = br(2026, 11, 24, 12)   # venceu na hora exata
        self.assertErro(self.post('/assinaturas/teste', {}, SEM_CADASTRO), 409, "teste_ja_usado")
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, 30, 1, br(2026, 11, 20))
        self.assertErro(self.post('/assinaturas/teste', {}, OUTRO_UID), 409, "plano_ativo")
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste'"), [(1,)])

    def test_campos_a_mais_sao_ignorados_e_nenhum_corpo_da_pro(self):
        corpo = {"plano": "pro", "estado": "pro", "origem": "manual", "dias": 999, "fim": "2099-01-01",
                 "user_id": OUTRO_UID, "admin": True, "inicio": "2020-01-01"}
        resposta = self.post('/assinaturas/teste', corpo)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json(), {"inicio": "2026-11-14", "fim": "2026-11-24", "repetido": False})
        self.assertEqual(self.ler("SELECT user_id, plano, origem, dias, fim FROM assinaturas"),
                         [(UID, 'teste', 'teste', 10, '2026-11-24 12:00:00')])
        # Nem o cadastro nem as outras rotas criam linha de assinatura.
        resposta = self.post('/cadastro', {"nome": "Novo", "dia_fechamento": 5, "categorias": ["Casa"],
                                           "plano": "pro", "assinatura": {"estado": "pro"}}, SEM_CADASTRO)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas WHERE user_id = ?", (SEM_CADASTRO,)), [(0,)])
        self.assertEqual(self.get('/assinatura', SEM_CADASTRO).json()['estado'], 'sem_plano')

    def test_post_teste_toque_duplo_em_duas_threads(self):
        original = assinaturas.ler_linhas
        barreira = threading.Barrier(2, timeout=10)
        contagem = [0]
        trava = threading.Lock()

        def ler_com_barreira(conn, user_id):
            linhas = original(conn, user_id)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= 2:
                barreira.wait()
            return linhas

        respostas = []

        def pedir():
            respostas.append(self.post('/assinaturas/teste', {}, client=TestClient(app)))

        with mock.patch.object(assinaturas, 'ler_linhas', ler_com_barreira):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 201])
        self.assertEqual(self.contar('assinaturas'), 1)

    def test_post_teste_banco_ocupado_e_erro_interno(self):
        erro_busy = sqlite3.OperationalError('database is locked')
        client = TestClient(app, raise_server_exceptions=False)
        with mock.patch.object(assinaturas, 'ler_linhas', side_effect=erro_busy):
            self.assertErro(self.post('/assinaturas/teste', {}, client=client), 503, "banco_ocupado")
        # Erro que não é passageiro (e a consulta depois dele funciona): 500, sem nova tentativa.
        original = assinaturas.ler_linhas
        chamadas = [0]

        def falha_uma_vez(conn, user_id):
            chamadas[0] += 1
            if chamadas[0] == 1:
                raise RuntimeError('SQL secreto')
            return original(conn, user_id)

        with mock.patch.object(assinaturas, 'ler_linhas', falha_uma_vez):
            resposta = self.post('/assinaturas/teste', {}, client=client)
            self.assertErro(resposta, 500, "erro_interno")
            self.assertNotIn('secreto', resposta.text)
        self.assertEqual(chamadas[0], 2)
        with mock.patch.object(assinaturas, 'ler_linhas', side_effect=RuntimeError('SQL secreto')):
            self.assertErro(self.get('/assinatura', client=client), 500, "erro_interno")

    def test_me_identico_com_a_chave_desligada(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))
        resposta = self.get('/me')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"user_id": UID, "nome": "Ygor"})
        self.assertErro(self.get('/me', SEM_CADASTRO), 404, "usuario_nao_cadastrado")

    def test_me_com_o_bloco_com_a_chave_ligada(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11, 14))
        corpo = self.get('/me').json()
        self.assertEqual(set(corpo), {"user_id", "nome", "assinatura"})
        self.assertEqual((corpo["user_id"], corpo["nome"]), (UID, "Ygor"))
        self.assertEqual(corpo["assinatura"], self.get('/assinatura').json())
        self.assertEqual(corpo["assinatura"]["selo"], "gratis")
        # Vencido: o /me continua 200 (o bloqueio vem nas outras rotas).
        self.agora = br(2026, 11, 30)
        corpo = self.get('/me').json()
        self.assertEqual(corpo["assinatura"]["estado"], "teste_vencido")
        self.assertErro(self.get('/me', SEM_CADASTRO), 404, "usuario_nao_cadastrado")

    def test_me_200_com_assinatura_null_quando_o_bloco_falha(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        with mock.patch.object(assinaturas, 'montar_bloco_assinatura', side_effect=sqlite3.OperationalError('x')), \
                self.assertLogs('api', 'ERROR') as logs:
            resposta = self.get('/me')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"user_id": UID, "nome": "Ygor", "assinatura": None})
        self.assertIn('OperationalError', '\n'.join(logs.output))


# =============================================================================
# LIMITE DIÁRIO NO /registro/interpretar
# =============================================================================
class TestLimiteDiarioApi(TesteApiBase):
    def test_a_17a_passa_e_a_18a_429_sem_chamar_a_ia(self):
        ia = self.instalar(*[resposta_gasto()] * 18)
        for _ in range(17):
            self.assertEqual(self.interpretar().status_code, 200)
        resposta = self.interpretar()
        self.assertErro(resposta, 429, "limite_diario_ia")
        self.assertEqual(resposta.headers["Retry-After"], str(12 * 3600))
        self.assertEqual(len(ia.chamadas), 17)
        self.assertEqual(self.uso(), 17)
        self.assertEqual(self.get('/assinatura').json()['uso_ia_hoje'], 17)

    def test_retry_after_as_23_59_30_e_virada_as_00_00(self):
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 17)", (UID,))
        ia = self.instalar(resposta_gasto())
        self.agora = br(2026, 11, 14, 23, 59, 30)
        resposta = self.interpretar()
        self.assertErro(resposta, 429, "limite_diario_ia")
        self.assertEqual(resposta.headers["Retry-After"], "30")
        # Em UTC, 02:59:30 do dia 15 = 23:59:30 do dia 14 em Brasília.
        self.agora = datetime(2026, 11, 15, 2, 59, 30, tzinfo=timezone.utc)
        self.assertEqual(self.interpretar().headers["Retry-After"], "30")
        self.agora = br(2026, 11, 15, 0, 0, 0)
        self.assertEqual(self.interpretar().status_code, 200)
        self.assertEqual((self.uso(dia='2026-11-14'), self.uso(dia='2026-11-15')), (17, 1))
        self.assertEqual(len(ia.chamadas), 1)

    def test_ia_fora_do_ar_devolve_a_vaga(self):
        self.instalar(ErroFalsoDaIA("500 erro interno da IA"), ErroFalsoDaIA("503 UNAVAILABLE"),
                      ErroFalsoDaIA("503 UNAVAILABLE"), ErroFalsoDaIA("503 UNAVAILABLE"))
        self.assertErro(self.interpretar(), 503, "ia_indisponivel")
        self.assertErro(self.interpretar(), 503, "ia_indisponivel")   # 3 tentativas, todas 503
        self.assertEqual(self.uso(), 0)

    def test_resposta_que_nao_serve_conta(self):
        self.instalar(json.dumps({"tipo": "despesa", "categoria": "Casa"}), "isso não é json",
                      resposta_gasto(categoria="Inexistente"), resposta_gasto(valor=0))
        self.assertErro(self.interpretar(), 502, "resposta_ia_invalida")    # resposta sem valor
        self.assertErro(self.interpretar(), 502, "resposta_ia_invalida")    # nem é JSON
        self.assertErro(self.interpretar(), 422, "categoria_nao_encontrada")
        self.assertErro(self.interpretar(), 422, "valor_nao_entendido")
        self.assertEqual(self.uso(), 4)

    def test_o_que_nao_conta(self):
        ia = self.instalar()
        # Confirmar (sem IA), texto inválido, sem cadastro e o limite por minuto.
        for indice in range(20):
            chave = f"00000000-0000-4000-8000-{indice:012d}"
            self.assertEqual(self.post("/gastos", corpo_gasto(chave=chave)).status_code, 201)
            self.assertEqual(self.post("/receitas", corpo_receita(chave=f"10000000-0000-4000-8000-{indice:012d}"))
                             .status_code, 201)
        self.assertErro(self.interpretar(texto=""), 422, "texto_invalido")
        self.assertErro(self.interpretar(user_id=SEM_CADASTRO), 404, "usuario_nao_cadastrado")
        self.assertErro(self.interpretar(user_id=INCOMPLETO), 409, "cadastro_incompleto")
        self.post("/recorrentes", {"chave": "20000000-0000-4000-8000-000000000001", "dia_cobranca": 5, "valor": 10,
                                   "descricao": "Luz", "categoria": "Casa"})
        self.post("/categorias", {"chave": "30000000-0000-4000-8000-000000000001", "nome": "Pets"})
        self.limite = LimiteDeChamadas(maximo=1, janela=60.0)
        self.limite.registrar(UID)
        self.assertErro(self.interpretar(), 429, "limite_excedido")
        self.assertEqual(ia.chamadas, [])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM uso_ia"), [(0,)])

    def test_limite_por_minuto_continua_10_e_nao_gasta_a_vaga_do_dia(self):
        self.limite = LimiteDeChamadas(maximo=10, janela=60.0, relogio=lambda: 1000.0)
        self.instalar(*[resposta_gasto()] * 11)
        for _ in range(10):
            self.assertEqual(self.interpretar().status_code, 200)
        self.assertErro(self.interpretar(), 429, "limite_excedido")
        self.assertEqual(self.uso(), 10)

    def test_admin_passa_da_17a(self):
        self.ambiente(ADMIN_IDS=str(UID))
        ia = self.instalar(*[resposta_gasto()] * 20)
        for _ in range(20):
            self.assertEqual(self.interpretar().status_code, 200)
        self.assertEqual(len(ia.chamadas), 20)
        self.assertEqual(self.uso(), 20)

    def test_vale_com_a_assinatura_desligada_e_limite_invalido_vale_17(self):
        self.ambiente(LIMITE_IA_DIA='abc')
        self.assertFalse(assinaturas.assinaturas_ligadas())
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 16)", (UID,))
        self.instalar(resposta_gasto(), resposta_gasto())
        self.assertEqual(self.interpretar().status_code, 200)
        self.assertErro(self.interpretar(), 429, "limite_diario_ia")
        self.ambiente(LIMITE_IA_DIA='18')
        self.assertEqual(self.interpretar().status_code, 200)

    def test_limite_por_pessoa(self):
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 17)", (UID,))
        self.instalar(resposta_gasto(categoria="Lazer"))
        self.assertErro(self.interpretar(), 429, "limite_diario_ia")
        self.assertEqual(self.interpretar(user_id=OUTRO_UID).status_code, 200)

    def test_dois_pedidos_juntos_na_17a_vaga_so_um_passa(self):
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 16)", (UID,))
        ia = self.instalar(resposta_gasto(), resposta_gasto())
        original = assinaturas.reservar_uso_ia
        barreira = threading.Barrier(2, timeout=10)

        def reservar_junto(*args, **kwargs):
            barreira.wait()
            return original(*args, **kwargs)

        respostas = []

        def pedir():
            respostas.append(self.interpretar(client=TestClient(app)))

        with mock.patch.object(assinaturas, 'reservar_uso_ia', reservar_junto):
            threads = [threading.Thread(target=pedir) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        self.assertEqual(sorted(r.status_code for r in respostas), [200, 429])
        self.assertEqual(len(ia.chamadas), 1)
        self.assertEqual(self.uso(), 17)

    def test_com_a_assinatura_ligada_o_acesso_vem_antes_do_limite(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.escrever("INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, '2026-11-14', 17)", (UID,))
        self.assertErro(self.interpretar(), 403, "assinatura_ausente")
        self.assertErro(self.interpretar(texto=""), 403, "assinatura_ausente")


# =============================================================================
# BOT
# =============================================================================
class RelogioBot(datetime):
    """datetime do bot com o now() fixo (o resto da classe é o de verdade)."""
    AGORA = br(2026, 11, 14, 12)

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.AGORA.replace(tzinfo=None)
        return cls.AGORA.astimezone(tz)


class BotFalso:
    def __init__(self):
        self.enviadas = []
        self.proximo_id = 100

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        self.enviadas.append((chat_id, text))
        self.proximo_id += 1
        return SimpleNamespace(message_id=self.proximo_id)

    async def delete_message(self, chat_id, message_id):
        return True

    async def edit_message_text(self, **kwargs):
        raise RuntimeError("sem mensagem para editar")


class FilaFalsa:
    def __init__(self):
        self.agendados = []

    def run_once(self, callback, quando, **kwargs):
        self.agendados.append(kwargs.get('chat_id'))

    def get_jobs_by_name(self, nome):
        return []


class TesteBotBase(AmbienteLimpo):
    """SQLite em arquivo criado pelo iniciar_banco REAL do bot, com UID (Ygor,
    categorias Alimentação, Casa e Roupas) e OUTRO_UID (Maria, Lazer)."""

    def setUp(self):
        super().setUp()
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.pasta.cleanup)
        self.caminho = os.path.join(self.pasta.name, 'bot.db')
        for alvo, valor in (('obter_conexao_db', lambda: sqlite3.connect(self.caminho)),
                            ('datetime', RelogioBot)):
            patcher = mock.patch.object(bot, alvo, valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            bot.iniciar_banco()
        conn = sqlite3.connect(self.caminho)
        for user_id, nome in ((UID, 'Ygor'), (OUTRO_UID, 'Maria')):
            conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
                         "VALUES (?, ?, 1000, 14, '20:00')", (user_id, nome))
        for user_id, nome in ((UID, 'Alimentação'), (UID, 'Casa'), (UID, 'Roupas'), (OUTRO_UID, 'Lazer')):
            conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                         "VALUES (?, ?, 0, 0, '2026-01-01 10:00:00')", (user_id, nome))
        conn.commit()
        conn.close()
        self.telas = []

        async def enviar_fluxo(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
            self.telas.append((chat_id, texto, self.botoes(reply_markup)))

        async def atualizar_callback(query, context, texto, reply_markup=None, parse_mode='HTML'):
            self.telas.append((query.from_user.id, texto, self.botoes(reply_markup)))

        async def responder_callback(query, texto=None):
            return None

        for alvo, valor in (('enviar_fluxo', enviar_fluxo), ('atualizar_callback', atualizar_callback),
                            ('responder_callback', responder_callback)):
            patcher = mock.patch.object(bot, alvo, valor)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.cliente_anterior = registro._cliente_ia
        self.addCleanup(lambda: registro.definir_cliente_ia(self.cliente_anterior))
        self.saida = io.StringIO()

    @staticmethod
    def botoes(teclado):
        if teclado is None:
            return None
        return [[b.text for b in linha] for linha in teclado.inline_keyboard]

    def executar(self, funcao, *args, **kwargs):
        conn = sqlite3.connect(self.caminho)
        try:
            return funcao(conn, *args, **kwargs)
        finally:
            conn.close()

    def ler(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def contexto(self, **extra):
        return SimpleNamespace(user_data={}, application=SimpleNamespace(handlers={}), bot=BotFalso(),
                               job_queue=FilaFalsa(), args=extra.pop('args', None), **extra)

    def update_texto(self, texto, user_id=UID):
        async def apagar():
            return None
        mensagem = SimpleNamespace(text=texto, date=datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc), delete=apagar,
                                   successful_payment=None, from_user=SimpleNamespace(id=user_id))
        return SimpleNamespace(message=mensagem, effective_message=mensagem, callback_query=None,
                               pre_checkout_query=None, effective_user=SimpleNamespace(id=user_id),
                               effective_chat=SimpleNamespace(id=user_id))

    def update_botao(self, dados, user_id=UID):
        query = SimpleNamespace(data=dados, from_user=SimpleNamespace(id=user_id),
                                message=SimpleNamespace(chat_id=user_id, message_id=1))
        return SimpleNamespace(message=None, effective_message=query.message, callback_query=query,
                               pre_checkout_query=None, effective_user=SimpleNamespace(id=user_id),
                               effective_chat=SimpleNamespace(id=user_id))

    def rodar(self, corotina):
        with contextlib.redirect_stdout(self.saida):
            return asyncio.run(corotina)


class TestPorteiro(TesteBotBase):
    def porteiro(self, update, contexto=None):
        """True se o porteiro parou o update (ApplicationHandlerStop)."""
        try:
            self.rodar(bot.porteiro(update, contexto or self.contexto()))
        except bot.ApplicationHandlerStop:
            return True
        return False

    def test_desligado_nao_consulta_nada(self):
        with mock.patch.object(bot, 'obter_conexao_db', side_effect=AssertionError("consultou")):
            self.assertFalse(self.porteiro(self.update_texto("oi")))
        self.assertEqual(self.telas, [])

    def test_sem_plano_com_cadastro_recebe_a_escolha(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        contexto = self.contexto()
        contexto.user_data['gasto_pendente'] = {'valor': 1}
        self.assertTrue(self.porteiro(self.update_texto("oi", OUTRO_UID), contexto))
        self.assertEqual(self.telas, [(OUTRO_UID, bot.TEXTO_ESCOLHA_PLANO, [["Testar Grátis por 10 Dias"]])])
        self.assertNotIn('gasto_pendente', contexto.user_data)

    def test_vencido_recebe_o_aviso_com_o_botao_do_mini_app(self):
        self.ambiente(ASSINATURAS_LIGADAS='1', URL_MINI_APP='https://exemplo.ngrok.app/app/')
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.assertTrue(self.porteiro(self.update_botao('menu_resumo', OUTRO_UID)))
        texto, botoes = self.telas[-1][1:]
        self.assertEqual(texto, "<b>Sua assinatura venceu.</b> Seus dados continuam guardados, e você ainda "
                                "pode vê-los no L1d3r. Assine o Plano Pro para voltar a registrar.")
        self.assertEqual(botoes, [["Abrir o L1d3r"]])
        teclado, = [t for t in [bot.montar_aviso_assinatura(SimpleNamespace(estado='pro_vencido'))[1]]]
        self.assertEqual(teclado.inline_keyboard[0][0].web_app.url, 'https://exemplo.ngrok.app/app/?tela=assinatura')

    def test_quem_tem_acesso_admin_e_os_livres_passam(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))
        self.assertFalse(self.porteiro(self.update_texto("almoço 25")))           # teste ativo
        for texto in ("/start", "/assinatura", "/terms", "/paysupport@MeuBot oi"):
            with self.subTest(texto=texto):
                self.assertFalse(self.porteiro(self.update_texto(texto, OUTRO_UID)))
        self.assertFalse(self.porteiro(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, OUTRO_UID)))
        self.assertTrue(self.porteiro(self.update_texto("/usuarios", OUTRO_UID)))   # não é livre para quem não é admin
        self.ambiente(ADMIN_IDS=str(OUTRO_UID))
        self.assertFalse(self.porteiro(self.update_texto("oi", OUTRO_UID)))
        pagamento = self.update_texto("", OUTRO_UID)
        self.ambiente(ADMIN_IDS=None)
        pagamento.message.successful_payment = SimpleNamespace(total_amount=150)
        self.assertFalse(self.porteiro(pagamento))
        checkout = SimpleNamespace(message=None, effective_message=None, callback_query=None,
                                   pre_checkout_query=SimpleNamespace(id='1'), effective_user=SimpleNamespace(id=OUTRO_UID),
                                   effective_chat=None)
        self.assertFalse(self.porteiro(checkout))

    def test_sem_cadastro_e_sem_plano_passa_para_o_cadastro_pelo_chat(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.assertFalse(self.porteiro(self.update_texto("Ygor", SEM_CADASTRO)))
        self.executar(assinaturas.iniciar_teste, SEM_CADASTRO, br(2026, 10, 1))   # testou e venceu sem cadastro
        self.assertTrue(self.porteiro(self.update_texto("Ygor", SEM_CADASTRO)))

    def test_handlers_registrados(self):
        registrados = []
        aplicacao = SimpleNamespace(add_handler=lambda handler, group=0: registrados.append((handler, group)))
        bot.registrar_handlers_assinatura(aplicacao)
        porteiro, grupo = registrados[0]
        self.assertIsInstance(porteiro, bot.TypeHandler)
        self.assertIs(porteiro.callback, bot.porteiro)
        self.assertEqual(grupo, -1)
        comandos = {}
        for handler, grupo in registrados[1:]:
            self.assertEqual(grupo, 0)
            if isinstance(handler, bot.CommandHandler):
                comandos[next(iter(handler.commands))] = handler
        self.assertEqual(set(comandos), {'admin', 'usuarios', 'dar_pro', 'tirar_pro', 'estender_teste',
                                         'reiniciar_teste'})
        for handler in comandos.values():
            self.assertIs(handler.filters, bot.FILTRO_ADMIN)
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertIn('app.run_polling(bootstrap_retries=5, allowed_updates=Update.ALL_TYPES)', fonte)
        self.assertIn('registrar_handlers_assinatura(app)', fonte)

    def test_filtro_admin_lido_na_hora(self):
        mensagem = SimpleNamespace(from_user=SimpleNamespace(id=UID))
        self.assertFalse(bot.FILTRO_ADMIN.filter(mensagem))
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertTrue(bot.FILTRO_ADMIN.filter(mensagem))
        self.assertFalse(bot.FILTRO_ADMIN.filter(SimpleNamespace(from_user=SimpleNamespace(id=OUTRO_UID))))
        self.assertFalse(bot.FILTRO_ADMIN.filter(SimpleNamespace(from_user=None)))

    def test_start_de_quem_tem_cadastro_e_esta_sem_acesso(self):
        self.ambiente(ASSINATURAS_LIGADAS='1')
        with mock.patch.object(bot, 'iniciar_banco', lambda: None):
            estado = self.rodar(bot.start(self.update_texto("/start", OUTRO_UID), self.contexto()))
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertEqual(self.telas[-1][1], bot.TEXTO_ESCOLHA_PLANO)
        self.ambiente(ASSINATURAS_LIGADAS=None)
        with mock.patch.object(bot, 'iniciar_banco', lambda: None):
            self.rodar(bot.start(self.update_texto("/start", OUTRO_UID), self.contexto()))
        self.assertEqual(self.telas[-1][1], "Menu Principal")


class TestBotaoDoTeste(TesteBotBase):
    def test_comeca_o_teste_e_mostra_o_menu(self):
        self.rodar(bot.testar_gratis_botao(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, OUTRO_UID), self.contexto()))
        self.assertEqual(self.telas[-1][1], "Seu teste começou! Vale até <b>24/11/2026</b>.")
        self.assertEqual(self.telas[-1][2][0], ["Registrar Gasto"])
        self.assertEqual(self.ler("SELECT origem, inicio, fim FROM assinaturas WHERE user_id = ?", (OUTRO_UID,)),
                         [('teste', '2026-11-14 12:00:00', '2026-11-24 12:00:00')])
        # Toque de novo: o mesmo teste.
        self.rodar(bot.testar_gratis_botao(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, OUTRO_UID), self.contexto()))
        self.assertEqual(self.telas[-1][1], "Seu teste começou! Vale até <b>24/11/2026</b>.")
        self.assertEqual(len(self.ler("SELECT * FROM assinaturas")), 1)

    def test_teste_ja_usado_e_banco_ocupado(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.rodar(bot.testar_gratis_botao(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, OUTRO_UID), self.contexto()))
        self.assertEqual(self.telas[-1][1], bot.TEXTO_ASSINATURA_VENCIDA)
        with mock.patch.object(bot, 'iniciar_teste', side_effect=ErroRegistro('banco_ocupado')):
            self.rodar(bot.testar_gratis_botao(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, UID), self.contexto()))
        self.assertEqual(self.telas[-1][1], bot.TEXTO_ERRO_TESTE)


class TestComandosDeAdminBot(TesteBotBase):
    def comando(self, funcao, args, admin_id=UID):
        update = self.update_texto('/x', admin_id)
        self.rodar(funcao(update, self.contexto(args=args)))
        return self.telas[-1][1]

    def test_dar_pro_sem_numero_e_com_numero(self):
        self.assertEqual(self.comando(bot.comando_dar_pro, [str(OUTRO_UID)]), "Pro de Maria vale até <b>14/12/2026</b>.")
        self.assertEqual(self.comando(bot.comando_dar_pro, [str(OUTRO_UID), '5']),
                         "Pro de Maria vale até <b>19/12/2026</b>.")
        self.assertEqual(self.ler("SELECT origem, dias, criado_por, inicio, fim FROM assinaturas ORDER BY id"), [
            ('manual', 30, UID, '2026-11-14 12:00:00', '2026-12-14 12:00:00'),
            ('manual', 5, UID, '2026-12-14 12:00:00', '2026-12-19 12:00:00')])
        self.assertIn('Admin 4242: /dar_pro 5151', self.saida.getvalue())

    def test_dar_pro_invalidos(self):
        for args in ([], ['abc'], [str(OUTRO_UID), '0'], [str(OUTRO_UID), '367'], [str(OUTRO_UID), 'x'],
                     ['-5', '3'], [str(OUTRO_UID), '1', '2']):
            with self.subTest(args=args):
                self.assertEqual(self.comando(bot.comando_dar_pro, args), bot.USO_DAR_PRO)
        self.assertEqual(self.comando(bot.comando_dar_pro, ['999']), "Usuário não encontrado.")
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas"), [(0,)])

    def test_tirar_pro(self):
        self.assertEqual(self.comando(bot.comando_tirar_pro, [str(OUTRO_UID)]), "Esta pessoa não tem Pro dado.")
        self.comando(bot.comando_dar_pro, [str(OUTRO_UID)])
        self.assertEqual(self.comando(bot.comando_tirar_pro, [str(OUTRO_UID)]),
                         "Pro dado de Maria retirado. Sem Pro valendo agora.")
        self.executar(pagar, br(2026, 11, 14), 'c1', user_id=OUTRO_UID)
        self.assertEqual(self.comando(bot.comando_tirar_pro, [str(OUTRO_UID)]),
                         "Esta pessoa não tem Pro dado. O Pro pago só sai com /reembolsar.")
        self.assertEqual(self.comando(bot.comando_tirar_pro, []), bot.USO_TIRAR_PRO)
        self.assertEqual(self.comando(bot.comando_tirar_pro, ['999']), "Usuário não encontrado.")

    def test_estender_e_reiniciar_teste(self):
        self.assertEqual(self.comando(bot.comando_estender_teste, [str(OUTRO_UID), '5']),
                         "Esta pessoa ainda não começou o teste.")
        self.assertEqual(self.comando(bot.comando_reiniciar_teste, [str(OUTRO_UID)]),
                         "Teste de Maria reiniciado: vale até <b>24/11/2026</b>.")
        self.assertEqual(self.comando(bot.comando_estender_teste, [str(OUTRO_UID), '5']),
                         "Teste de Maria vale até <b>29/11/2026</b>.")
        self.assertEqual(self.comando(bot.comando_estender_teste, [str(OUTRO_UID), '61']), bot.USO_ESTENDER_TESTE)
        self.assertEqual(self.comando(bot.comando_estender_teste, [str(OUTRO_UID)]), bot.USO_ESTENDER_TESTE)

    def test_usuarios_e_admin(self):
        self.ambiente(ADMIN_IDS=str(UID))
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11))
        texto = self.comando(bot.comando_usuarios, [])
        self.assertEqual(texto, "<b>Usuários</b>\n\n<code>5151</code> · Maria · teste · 21/11/2026 · teste\n"
                                "<code>4242</code> · Ygor · admin · — · —")
        self.assertIn("/dar_pro", self.comando(bot.comando_admin, []))


class TestLimiteDiarioBot(TesteBotBase):
    def gasto_livre(self, texto="almoço 25", user_id=UID):
        contexto = self.contexto()
        return self.rodar(bot.salvar_gasto_livre(self.update_texto(texto, user_id), contexto)), contexto

    def uso(self, user_id=UID):
        linha = self.ler("SELECT chamadas FROM uso_ia WHERE user_id = ? AND dia = '2026-11-14'", (user_id,))
        return linha[0][0] if linha else 0

    def test_limite_no_gasto_livre_sem_chamar_a_ia(self):
        ia = IAFalsa(resposta_gasto(categoria='Alimentação', valor=25))
        registro.definir_cliente_ia(ia)
        self.executar(lambda c: (c.execute("INSERT INTO uso_ia VALUES (?, '2026-11-14', 17)", (UID,)), c.commit()))
        estado, contexto = self.gasto_livre()
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertEqual(self.telas[-1][1], "Você chegou ao limite de <b>17 registros com IA</b> por hoje. "
                                            "A partir da meia-noite (horário de Brasília), o limite volta a zero.")
        self.assertEqual(self.telas[-1][2], [["← Voltar ao Menu"]])
        self.assertEqual(ia.chamadas, [])
        self.assertNotIn('gasto_pendente', contexto.user_data)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM transacoes"), [(0,)])

    def test_gasto_livre_conta_e_ia_fora_do_ar_devolve(self):
        registro.definir_cliente_ia(IAFalsa(resposta_gasto(categoria='Alimentação', valor=25), "não é json",
                                            ErroFalsoDaIA("erro qualquer")))
        estado, contexto = self.gasto_livre()
        self.assertEqual(estado, bot.METODO_PAGAMENTO)
        self.assertEqual(self.uso(), 1)
        self.gasto_livre()            # resposta que não serve: conta
        self.assertEqual(self.uso(), 2)
        self.gasto_livre()            # IA fora do ar: devolve
        self.assertEqual(self.uso(), 2)
        self.assertIn("Erro: erro qualquer", self.telas[-1][1])

    def test_bot_e_mini_app_somam_e_admin_passa(self):
        self.ambiente(LIMITE_IA_DIA='3')
        conn = sqlite3.connect(self.caminho)
        for _ in range(2):   # duas prévias pelo Mini App (mesma função de reserva da API)
            assinaturas.reservar_uso_ia(conn, UID, RelogioBot.AGORA)
        conn.close()
        registro.definir_cliente_ia(IAFalsa(*[resposta_gasto(categoria='Alimentação')] * 5))
        self.gasto_livre()
        self.assertEqual(self.uso(), 3)
        estado, _ = self.gasto_livre()
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertIn("limite de <b>3 registros com IA</b>", self.telas[-1][1])
        self.ambiente(ADMIN_IDS=str(UID))
        estado, _ = self.gasto_livre()
        self.assertEqual(estado, bot.METODO_PAGAMENTO)
        self.assertEqual(self.uso(), 4)

    def test_recorrente_por_texto_conta_e_respeita_o_limite(self):
        ia = IAFalsa(json.dumps({"descricao": "Spotify", "valor": 12.9, "dia_cobranca": 14}))
        registro.definir_cliente_ia(ia)
        contexto = self.contexto()
        contexto.user_data.update({'recorrente_cat_id': 1, 'recorrente_cat_nome': 'Casa'})
        with mock.patch.object(bot, '_perguntar_proximo_campo_recorrente', side_effect=lambda c, u: asyncio.sleep(0)):
            self.rodar(bot.receber_texto_recorrente(self.update_texto("spotify 12,90 dia 14"), contexto))
        self.assertEqual((len(ia.chamadas), self.uso()), (1, 1))
        self.executar(lambda c: (c.execute("UPDATE uso_ia SET chamadas = 17"), c.commit()))
        contexto.user_data.update({'recorrente_cat_id': 1, 'recorrente_cat_nome': 'Casa'})
        estado = self.rodar(bot.receber_texto_recorrente(self.update_texto("netflix 40 dia 2"), contexto))
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertIn("limite de <b>17 registros com IA</b>", self.telas[-1][1])
        self.assertEqual(len(ia.chamadas), 1)
        self.assertNotIn('recorrente_cat_id', contexto.user_data)


class TestJobsBot(TesteBotBase):
    def setUp(self):
        super().setUp()
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))       # UID com acesso
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))  # OUTRO_UID vencido

    def test_recorrentes_pulam_quem_esta_vencido(self):
        conn = sqlite3.connect(self.caminho)
        for user_id, categoria in ((UID, 'Casa'), (OUTRO_UID, 'Lazer')):
            conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                         "criado_em) VALUES (?, ?, 'Luz', 50, 14, '2026-01-01 10:00:00')", (user_id, categoria))
        conn.commit()
        conn.close()
        fila = FilaFalsa()
        self.rodar(bot.verificar_gastos_recorrentes(SimpleNamespace(job_queue=fila)))
        self.assertEqual(fila.agendados, [UID])
        self.ambiente(ASSINATURAS_LIGADAS=None)
        fila = FilaFalsa()
        self.rodar(bot.verificar_gastos_recorrentes(SimpleNamespace(job_queue=fila)))
        self.assertEqual(sorted(fila.agendados), sorted([UID, OUTRO_UID]))

    def test_alerta_diario_pula_quem_esta_vencido(self):
        for user_id, enviadas in ((OUTRO_UID, 0), (UID, 1)):
            contexto = self.contexto(job=SimpleNamespace(chat_id=user_id))
            self.rodar(bot.enviar_resumo_diario(contexto))
            self.assertEqual(len(contexto.bot.enviadas), enviadas)

    def test_resumo_do_fechamento_marca_o_reset_mas_nao_manda_para_o_vencido(self):
        bot_falso = BotFalso()
        user_data = {UID: {}, OUTRO_UID: {}}
        self.rodar(bot._executar_verificacao_resets(bot_falso, user_data))
        self.assertEqual([chat for chat, _ in bot_falso.enviadas], [UID])
        self.assertEqual(self.ler("SELECT user_id, ultimo_reset FROM usuarios ORDER BY user_id"),
                         [(UID, '2026-11-14'), (OUTRO_UID, '2026-11-14')])


# =============================================================================
# LOG DOS TOKENS (registro.py)
# =============================================================================
class TestLogDeTokens(unittest.TestCase):
    def setUp(self):
        self.anterior = registro._cliente_ia
        self.addCleanup(lambda: registro.definir_cliente_ia(self.anterior))

    def test_so_os_numeros_sem_texto_nem_id(self):
        uso = SimpleNamespace(prompt_token_count=650, candidates_token_count=100, thoughts_token_count=470,
                              total_token_count=1220)
        resposta = SimpleNamespace(text='{"segredo": "resposta-da-ia"}', usage_metadata=uso)
        registro.definir_cliente_ia(IAFalsa(resposta))
        with self.assertLogs('registro', 'INFO') as logs:
            obtida = asyncio.run(registro.gerar_conteudo_com_retry(f"texto-do-usuario {UID}"))
        self.assertIs(obtida, resposta)
        self.assertEqual(logs.output, ['INFO:registro:Tokens da IA: entrada=650 saida=100 pensamento=470 total=1220'])
        for proibido in ('texto-do-usuario', str(UID), 'segredo', 'resposta-da-ia'):
            self.assertNotIn(proibido, logs.output[0])

    def test_o_log_sai_de_verdade_no_terminal_da_api_e_do_bot(self):
        """Sem handler, o Python só mostraria WARNING ou acima: a API (no
        import) e o bot (no __main__) põem um StreamHandler INFO só nos
        loggers 'registro' e 'assinaturas', sem propagar e sem duplicar."""
        bot.configurar_logs_do_dominio()     # de novo: não duplica o da API
        api_main.configurar_logs_do_dominio()
        for nome in ('registro', 'assinaturas'):
            alvo = logging.getLogger(nome)
            marcados = [h for h in alvo.handlers if getattr(h, '_log_do_dominio', False)]
            self.assertEqual(len(marcados), 1, nome)
            self.assertIsInstance(marcados[0], logging.StreamHandler)
            self.assertEqual((alvo.level, alvo.propagate), (logging.INFO, False))
        self.assertGreater(logging.getLogger().getEffectiveLevel(), logging.INFO)   # o root não mudou
        handler = next(h for h in logging.getLogger('registro').handlers if getattr(h, '_log_do_dominio', False))
        saida = io.StringIO()
        anterior = handler.setStream(saida)
        try:
            uso = SimpleNamespace(prompt_token_count=650, candidates_token_count=100, thoughts_token_count=470,
                                  total_token_count=1220)
            registro.definir_cliente_ia(IAFalsa(SimpleNamespace(text='{"x": "resposta-da-ia"}', usage_metadata=uso)))
            asyncio.run(registro.gerar_conteudo_com_retry(f"texto-do-usuario {UID}"))
        finally:
            handler.setStream(anterior)
        linhas = saida.getvalue().splitlines()
        self.assertEqual(len(linhas), 1)
        self.assertRegex(linhas[0], r'^\d{4}-\d{2}-\d{2} [\d:,]+ registro INFO '
                                    r'Tokens da IA: entrada=650 saida=100 pensamento=470 total=1220$')
        for proibido in ('texto-do-usuario', str(UID), 'resposta-da-ia'):
            self.assertNotIn(proibido, linhas[0])

    def test_sem_usage_metadata_nao_escreve_e_campo_faltando_vira_none(self):
        registro.definir_cliente_ia(IAFalsa('{"tipo": "despesa"}'))
        with self.assertNoLogs('registro', 'INFO'):
            asyncio.run(registro.gerar_conteudo_com_retry("prompt"))
        registro.definir_cliente_ia(IAFalsa(SimpleNamespace(text='x', usage_metadata=SimpleNamespace(
            prompt_token_count=10, total_token_count=12))))
        with self.assertLogs('registro', 'INFO') as logs:
            asyncio.run(registro.gerar_conteudo_com_retry("prompt"))
        self.assertEqual(logs.output, ['INFO:registro:Tokens da IA: entrada=10 saida=None pensamento=None total=12'])


if __name__ == "__main__":
    unittest.main(verbosity=2)
