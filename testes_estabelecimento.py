"""Testes do estabelecimento do gasto (PRO-81): normalizar_estabelecimento,
formatar_estabelecimento e a gravação em debitar_categoria.

Roda com `python testes_estabelecimento.py`. Não usa .env nem rede: as funções
são lidas do bot.py com `ast` (sem importá-lo, para não abrir conexão com o
Turso nem criar o cliente da IA) e o banco é um SQLite em memória.
"""
import ast
import os
import sqlite3
import sys
import unittest
from html import escape

NOMES_DO_BOT = {'normalizar_estabelecimento', 'formatar_estabelecimento', 'debitar_categoria'}


def carregar_funcoes():
    """Executa só as funções de NOMES_DO_BOT, extraídas do bot.py."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(raiz, 'bot.py'), encoding='utf-8') as f:
        arvore = ast.parse(f.read())
    selecionados = [no for no in arvore.body if isinstance(no, ast.FunctionDef) and no.name in NOMES_DO_BOT]
    ns = {'escape': escape}
    exec(compile(ast.Module(body=selecionados, type_ignores=[]), 'bot.py', 'exec'), ns)
    faltando = NOMES_DO_BOT - set(ns)
    if faltando:
        sys.exit(f"Não encontrei no bot.py: {sorted(faltando)}")
    return ns


F = carregar_funcoes()
normalizar_estabelecimento = F['normalizar_estabelecimento']
formatar_estabelecimento = F['formatar_estabelecimento']
debitar_categoria = F['debitar_categoria']

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

    def test_continua_debitando_o_saldo_da_categoria(self):
        debitar_categoria(self.cursor, UID, 'Roupas', 300.0, MOMENTO, descricao="Tênis", estabelecimento="Nike")
        self.assertEqual(self.conn.execute("SELECT saldo_atual FROM categorias").fetchone()[0], 700.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
