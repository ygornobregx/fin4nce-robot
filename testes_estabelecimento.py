"""Testes do estabelecimento do gasto (PRO-81): normalizar_estabelecimento,
formatar_estabelecimento e a gravação em debitar_categoria. Desde a PRO-83,
também o lançamento de um gasto recorrente (_lancar_item_recorrente), que só
dispara às 05:00 e por isso é coberto aqui, e não no teste manual, e a etapa
dos nomes do cadastro (receber_nomes_categorias), sem nomes repetidos.

Roda com `python testes_estabelecimento.py`. Não usa .env nem rede: desde a
PRO-85, normalizar_estabelecimento e debitar_categoria vêm do registro.py
(importável sem .env, sem chave e sem rede); as funções que ficaram no bot.py
são lidas com `ast` (sem importá-lo, para não abrir conexão com o Turso) e o
banco é um SQLite em memória.
"""
import ast
import asyncio
import contextlib
import io
import json
import os
import sqlite3
import sys
import unicodedata
import unittest
from datetime import datetime
from html import escape
from types import SimpleNamespace
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import registro  # noqa: E402

NOMES_DO_BOT = {'formatar_estabelecimento', 'formatar_real', '_lancar_item_recorrente'}


def carregar_funcoes():
    """Executa só as funções de NOMES_DO_BOT, extraídas do bot.py, com a
    debitar_categoria do registro.py no lugar do import do bot."""
    with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
        arvore = ast.parse(f.read())
    selecionados = [no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name in NOMES_DO_BOT]
    ns = {'escape': escape, 'datetime': datetime, 'FUSO_BRASILIA': ZoneInfo("America/Sao_Paulo"),
          'debitar_categoria': registro.debitar_categoria}
    exec(compile(ast.Module(body=selecionados, type_ignores=[]), 'bot.py', 'exec'), ns)
    faltando = NOMES_DO_BOT - set(ns)
    if faltando:
        sys.exit(f"Não encontrei no bot.py: {sorted(faltando)}")
    return ns


F = carregar_funcoes()
normalizar_estabelecimento = registro.normalizar_estabelecimento
formatar_estabelecimento = F['formatar_estabelecimento']
debitar_categoria = registro.debitar_categoria
lancar_item_recorrente = F['_lancar_item_recorrente']

UID = 1
MOMENTO = '2026-10-17 12:00:00'


def novo_banco():
    """Só as tabelas que o debitar_categoria toca, com as colunas de COLUNAS_ESPERADAS."""
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT,
            orcamento_mensal REAL, saldo_atual REAL, data_criacao TEXT);
        CREATE TABLE transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metodo_pagamento TEXT,
            descricao TEXT, parcelamento_id INTEGER, estabelecimento TEXT);
        CREATE TABLE gastos_recorrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            descricao TEXT, valor REAL, dia_cobranca INTEGER, criado_em TEXT, ultimo_ciclo_cobrado TEXT);
    ''')
    conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual) VALUES (?, 'Roupas', 1000, 1000)",
                 (UID,))
    conn.commit()
    return conn


class TestNormalizar(unittest.TestCase):
    def test_tira_espacos_das_pontas(self):
        self.assertEqual(normalizar_estabelecimento(" Nike "), "Nike")

    def test_vazio_so_espacos_none_e_nao_texto_viram_none(self):
        for valor in ("", "   ", "\t\n", None, 123, 4.5, ["Nike"], True):
            with self.subTest(valor=valor):
                self.assertIsNone(normalizar_estabelecimento(valor))

    def test_nao_muda_maiusculas_nem_minusculas(self):
        self.assertEqual(normalizar_estabelecimento("iFood"), "iFood")
        self.assertEqual(normalizar_estabelecimento("Casas Bahia"), "Casas Bahia")
        self.assertEqual(normalizar_estabelecimento("  nike  "), "nike")


class TestFormatar(unittest.TestCase):
    def test_nome_simples(self):
        self.assertEqual(formatar_estabelecimento("Nike"), "Nike")

    def test_none_vira_sem_informacao(self):
        self.assertEqual(formatar_estabelecimento(None), "Sem Informação")

    def test_escapa_html(self):
        self.assertEqual(formatar_estabelecimento("A&B"), "A&amp;B")
        self.assertEqual(formatar_estabelecimento("<b>Loja</b>"), "&lt;b&gt;Loja&lt;/b&gt;")


class TestDebitarCategoria(unittest.TestCase):
    def setUp(self):
        self.conn = novo_banco()
        self.cursor = self.conn.cursor()

    def tearDown(self):
        self.conn.close()

    def ler(self, transacao_id):
        return self.conn.execute(
            "SELECT descricao, estabelecimento, valor, metodo_pagamento, parcelamento_id "
            "FROM transacoes WHERE id = ?", (transacao_id,)).fetchone()

    def test_grava_o_estabelecimento_e_a_descricao(self):
        _, transacao_id = debitar_categoria(self.cursor, UID, 'Roupas', 300.0, MOMENTO, 'Débito/Pix',
                                            descricao="Tênis na Nike", estabelecimento="Nike")
        self.assertEqual(self.ler(transacao_id), ("Tênis na Nike", "Nike", 300.0, 'Débito/Pix', None))

    def test_sem_o_parametro_grava_null_e_a_descricao_como_antes(self):
        _, transacao_id = debitar_categoria(self.cursor, UID, 'Roupas', 50.0, MOMENTO, descricao="  Camisa  ")
        self.assertEqual(self.ler(transacao_id), ("Camisa", None, 50.0, None, None))

    def test_estabelecimento_com_espacos_e_normalizado(self):
        _, transacao_id = debitar_categoria(self.cursor, UID, 'Roupas', 10.0, MOMENTO, descricao="Lanche",
                                            estabelecimento="  iFood ")
        self.assertEqual(self.ler(transacao_id)[1], "iFood")

    def test_estabelecimento_vazio_ou_nao_texto_grava_null(self):
        for valor in ("", "   ", 42):
            with self.subTest(valor=valor):
                _, transacao_id = debitar_categoria(self.cursor, UID, 'Roupas', 10.0, MOMENTO,
                                                    descricao="Lanche", estabelecimento=valor)
                self.assertIsNone(self.ler(transacao_id)[1])

    def test_parcela_1_grava_estabelecimento_com_parcelamento_id(self):
        _, transacao_id = debitar_categoria(self.cursor, UID, 'Roupas', 300.0, MOMENTO, 'Crédito',
                                            descricao="Geladeira nas Casas Bahia", parcelamento_id=7,
                                            estabelecimento="Casas Bahia")
        self.assertEqual(self.ler(transacao_id),
                         ("Geladeira nas Casas Bahia", "Casas Bahia", 300.0, 'Crédito', 7))

    def test_categoria_inexistente_devolve_none_e_nao_grava(self):
        self.assertIsNone(debitar_categoria(self.cursor, UID, 'Inexistente', 10.0, MOMENTO,
                                            descricao="X", estabelecimento="Nike"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM transacoes").fetchone()[0], 0)

    def test_nao_mexe_mais_no_saldo_da_categoria(self):
        # PRO-83: saldo_atual fica sem uso; o gasto é calculado na hora pelo calculos.py.
        lancamento = debitar_categoria(self.cursor, UID, 'Roupas', 300.0, MOMENTO, descricao="Tênis",
                                       estabelecimento="Nike")
        self.assertEqual(lancamento, (1, 1))
        self.assertEqual(self.conn.execute("SELECT saldo_atual, orcamento_mensal FROM categorias").fetchone(),
                         (1000.0, 1000.0))
        self.assertEqual(self.ler(1), ("Tênis", "Nike", 300.0, None, None))


class TestLancarItemRecorrente(unittest.TestCase):
    CICLO = '2026-11'

    def setUp(self):
        self.conn = novo_banco()
        self.conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                          "criado_em, ultimo_ciclo_cobrado) VALUES (?, 'Roupas', 'Academia & Cia', 99.9, 5, ?, NULL)",
                          (UID, MOMENTO))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def transacoes(self):
        return self.conn.execute(
            "SELECT categoria_nome, valor, descricao, metodo_pagamento, parcelamento_id, estabelecimento "
            "FROM transacoes").fetchall()

    def ciclo_cobrado(self):
        return self.conn.execute("SELECT ultimo_ciclo_cobrado FROM gastos_recorrentes WHERE id = 1").fetchone()[0]

    def test_grava_a_transacao_e_marca_o_ciclo(self):
        corpo = lancar_item_recorrente(self.conn, UID, 1, self.CICLO)
        self.assertIsNotNone(corpo)
        self.assertEqual(self.transacoes(), [('Roupas', 99.9, 'Academia & Cia', None, None, None)])
        self.assertEqual(self.ciclo_cobrado(), self.CICLO)

    def test_nao_lanca_duas_vezes_no_mesmo_ciclo(self):
        self.assertIsNotNone(lancar_item_recorrente(self.conn, UID, 1, self.CICLO))
        self.assertIsNone(lancar_item_recorrente(self.conn, UID, 1, self.CICLO))
        self.assertEqual(len(self.transacoes()), 1)

    def test_mensagem_sem_resta_na_categoria(self):
        corpo = lancar_item_recorrente(self.conn, UID, 1, self.CICLO)
        self.assertEqual(corpo,
                         "Descrição: <b>Academia &amp; Cia</b>\n"
                         "Categoria: <b>Roupas</b>\n"
                         "Valor: <b>R$ 99,90</b>")
        self.assertNotIn("Resta", corpo)

    def test_nao_mexe_no_saldo_da_categoria(self):
        lancar_item_recorrente(self.conn, UID, 1, self.CICLO)
        self.assertEqual(self.conn.execute("SELECT saldo_atual FROM categorias").fetchone()[0], 1000.0)

    def test_categoria_inexistente_nao_lanca_nem_marca_o_ciclo(self):
        self.conn.execute("UPDATE gastos_recorrentes SET categoria_nome = 'Apagada' WHERE id = 1")
        self.conn.commit()
        # O aviso do print vai para um buffer: o console do Windows (cp1252) não imprime o emoji.
        with contextlib.redirect_stdout(io.StringIO()) as aviso:
            self.assertIsNone(lancar_item_recorrente(self.conn, UID, 1, self.CICLO))
        self.assertIn("não lançado", aviso.getvalue())
        self.assertEqual(self.transacoes(), [])
        self.assertIsNone(self.ciclo_cobrado())

    def test_recorrente_inexistente_devolve_none(self):
        self.assertIsNone(lancar_item_recorrente(self.conn, UID, 99, self.CICLO))
        self.assertEqual(self.transacoes(), [])


def carregar_cadastro():
    """receber_nomes_categorias do bot.py (AST), com as constantes de estado
    reais (NOME_USUARIO... e DIA_FECHAMENTO) e um enviar_fluxo falso que só
    guarda o texto enviado. Desde a PRO-96, a regra dos nomes
    (montar_lista_categorias) e a _normalizar_texto ficam no cadastro.py, que o
    bot importa: vêm de lá, como no bot."""
    import cadastro
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, 'bot.py'), encoding='utf-8') as f:
        arvore = ast.parse(f.read())
    funcoes = {'receber_nomes_categorias'}
    constantes = {'NOMES_CATEGORIAS', 'DIA_FECHAMENTO'}
    selecionados = [
        no for no in arvore.body
        if (isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)) and no.name in funcoes)
        or (isinstance(no, ast.Assign) and any(
            isinstance(sub, ast.Name) and sub.id in constantes
            for alvo in no.targets for sub in ast.walk(alvo)))
    ]
    enviados = []

    async def enviar_fluxo_falso(context, chat_id, texto, reply_markup=None):
        enviados.append(texto)

    ns = {'unicodedata': unicodedata, 'enviar_fluxo': enviar_fluxo_falso,
          'Update': object, 'ContextTypes': SimpleNamespace(DEFAULT_TYPE=object),
          '_normalizar_texto': cadastro.normalizar_texto,
          'montar_lista_categorias': cadastro.montar_lista_categorias}
    exec(compile(ast.Module(body=selecionados, type_ignores=[]), 'bot.py', 'exec'), ns)
    faltando = (funcoes | constantes) - set(ns)
    if faltando:
        sys.exit(f"Não encontrei no bot.py: {sorted(faltando)}")
    return ns, enviados


class TestCadastroSemNomesRepetidos(unittest.TestCase):
    """Cadastro (/start), etapa dos nomes das categorias (PRO-83): sem limite,
    sem nomes repetidos, direto ao dia de vencimento."""

    def setUp(self):
        self.ns, self.enviados = carregar_cadastro()

    def enviar(self, texto):
        async def apagar():
            pass
        update = SimpleNamespace(message=SimpleNamespace(text=texto, delete=apagar),
                                 effective_chat=SimpleNamespace(id=UID))
        context = SimpleNamespace(user_data={'nome_temp': 'Ygor'})
        estado = asyncio.run(self.ns['receber_nomes_categorias'](update, context))
        return estado, context.user_data

    def test_constantes_de_estado_mantem_os_numeros(self):
        self.assertEqual(self.ns['NOMES_CATEGORIAS'], 2)
        self.assertEqual(self.ns['DIA_FECHAMENTO'], 4)

    def test_repetido_com_caixa_diferente_vira_uma_categoria(self):
        _, user_data = self.enviar("Lazer, lazer, Mercado")
        self.assertEqual(user_data['categorias_temp'], ['Lazer', 'Mercado'])

    def test_repetido_com_acento_e_espacos_mantem_a_primeira_grafia(self):
        _, user_data = self.enviar("Alimentação, alimentacao,  ALIMENTAÇÃO ")
        self.assertEqual(user_data['categorias_temp'], ['Alimentação'])

    def test_so_virgulas_e_espacos_continua_pedindo_os_nomes(self):
        estado, user_data = self.enviar(" , , ")
        self.assertEqual(estado, self.ns['NOMES_CATEGORIAS'])
        self.assertNotIn('categorias_temp', user_data)
        self.assertEqual(self.enviados, [])

    def test_nomes_validos_vao_direto_ao_dia_de_vencimento(self):
        estado, user_data = self.enviar("Mercado, Lazer, Casa")
        self.assertEqual(estado, self.ns['DIA_FECHAMENTO'])
        self.assertEqual(user_data['categorias_temp'], ['Mercado', 'Lazer', 'Casa'])
        self.assertEqual(len(self.enviados), 1)
        texto = self.enviados[0]
        self.assertIn("data de vencimento da fatura", texto)
        self.assertNotIn("limite", texto.lower())
        self.assertNotIn("orçamento", texto.lower())
        self.assertNotIn("orcamento", texto.lower())
        self.assertEqual(set(user_data), {'nome_temp', 'categorias_temp'})


class TestRecusaDaReceita(unittest.TestCase):
    def test_recusa_da_receita_vem_antes_da_checagem_de_categoria(self):
        """A receita é identificada antes de ler o valor e de checar a
        categoria (PRO-83): com categoria fora da lista e valor inválido,
        interpretar_gasto (PRO-85) devolve o tipo 'receita' sem levantar, e o
        bot mostra o aviso da receita. O fluxo inteiro está no G07 do
        testes_registro_caracterizacao.py."""
        class IAFalsa:
            def __init__(self):
                self.models = self

            def generate_content(self, model, contents):
                return SimpleNamespace(text=json.dumps(
                    {"tipo": "Receita ", "valor": "abc", "categoria": "Salário"}))

        cliente_anterior = registro._cliente_ia
        registro.definir_cliente_ia(IAFalsa())
        try:
            gasto = asyncio.run(registro.interpretar_gasto("recebi 5000 de salário", ['Roupas']))
        finally:
            registro.definir_cliente_ia(cliente_anterior)
        self.assertEqual(gasto.tipo, 'receita')
        self.assertIsNone(gasto.valor)
        self.assertFalse(gasto.categoria_valida)


if __name__ == "__main__":
    unittest.main(verbosity=2)
