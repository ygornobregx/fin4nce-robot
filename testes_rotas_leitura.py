"""Testes das rotas de leitura da API (PRO-76): GET /resumo e GET /historico.

Roda com `python testes_rotas_leitura.py`. Não usa .env nem rede: o token é
falso, o banco é um SQLite em memória e o "agora" da API é fixo (17/10/2026
12:00 BRT), por isso a fatura vigente é 2026-10 (28/09 a 27/10, fechamento
dia 28). Os valores esperados estão escritos à mão, com a conta ao lado.
"""
import sqlite3
import time
import unittest
from datetime import datetime

from fastapi.testclient import TestClient

from api.main import Config, app, obter_agora, obter_config, obter_conexao_db
from calculos import FUSO_BRASILIA, calcular_totais_mes, montar_categorias_fatura
from testes_initdata import TOKEN_FALSO, USER_ID, montar_init_data

AGORA_FIXO = datetime(2026, 10, 17, 12, 0, tzinfo=FUSO_BRASILIA)
DIA_FECHAMENTO = 28
RENDA = 5000.0

ESQUEMA = '''
    CREATE TABLE usuarios (user_id INTEGER PRIMARY KEY, nome TEXT, renda_mensal REAL,
        dia_fechamento INTEGER, horario_alerta TEXT, ultimo_reset TEXT);
    CREATE TABLE categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT,
        orcamento_mensal REAL, saldo_atual REAL, data_criacao TEXT);
    CREATE TABLE transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metodo_pagamento TEXT,
        descricao TEXT, parcelamento_id INTEGER);
    CREATE TABLE parcelamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        descricao TEXT, valor_total REAL, valor_parcela REAL, total_parcelas INTEGER,
        parcelas_pagas INTEGER DEFAULT 1, data_criacao TEXT, ativo INTEGER DEFAULT 1, ciclo_referencia TEXT);
    CREATE TABLE gastos_recorrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        descricao TEXT, valor REAL, dia_cobranca INTEGER, criado_em TEXT, ultimo_ciclo_cobrado TEXT);
    CREATE TABLE transferencias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_origem TEXT,
        categoria_destino TEXT, valor REAL, data_transferencia TEXT);
'''


def novo_banco(nome="Ygor", renda=RENDA, dia_fechamento=DIA_FECHAMENTO, cadastrar=True):
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.executescript(ESQUEMA)
    if cadastrar:
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, ?, ?, ?)",
                     (USER_ID, nome, renda, dia_fechamento))
    return conn


def categoria(conn, nome, limite, data_criacao="2026-01-01 10:00:00"):
    conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                 "VALUES (?, ?, ?, ?, ?)", (USER_ID, nome, limite, limite, data_criacao))


def transacao(conn, cat, valor, momento):
    conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) VALUES (?, ?, ?, ?)",
                 (USER_ID, cat, valor, momento))


def parcelamento(conn, cat, total, n, momento, ciclo_referencia):
    """Como o bot: a parcela 1 também vira transação, na mesma data."""
    parcela = round(total / n, 2)
    transacao(conn, cat, parcela, momento)
    conn.execute("INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, valor_parcela, "
                 "total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo) "
                 "VALUES (?, ?, 'compra', ?, ?, ?, 1, ?, ?, 1)",
                 (USER_ID, cat, total, parcela, n, momento, ciclo_referencia))


def transferencia(conn, origem, destino, valor, momento):
    conn.execute("INSERT INTO transferencias (user_id, categoria_origem, categoria_destino, valor, "
                 "data_transferencia) VALUES (?, ?, ?, ?, ?)", (USER_ID, origem, destino, valor, momento))


def banco_completo():
    """Cenário principal (renda 5.000, fechamento dia 28, fatura vigente 2026-10).

    Fatura 2026-10 = de 28/09 a 27/10:
      Mercado (limite 1.000): avulsos 100 e 250, receita -30, compra de 300 em 3x
        (parcela 1 = 100, que já existe como transação e não conta em dobro).
      Lazer (limite 500): avulso 80, mais 20 em 28/09 (primeiro dia da fatura 2026-10).
      Transferência de 200 de Lazer para Mercado.
      Antiga: categoria já excluída (fora de 'categorias') com gasto avulso 60.
    Fora da fatura: Lazer 70 em 27/09 e Mercado 40 em 15/09 (fatura 2026-09) e
    Mercado 25,50 em 10/06 (fatura 2026-06).
    """
    conn = novo_banco()
    categoria(conn, "Mercado", 1000.0)
    categoria(conn, "Lazer", 500.0)
    transacao(conn, "Mercado", 100.0, "2026-10-05 10:00:00")
    transacao(conn, "Mercado", -30.0, "2026-10-06 10:00:00")
    transacao(conn, "Mercado", 250.0, "2026-10-10 09:00:00")
    parcelamento(conn, "Mercado", 300.0, 3, "2026-10-10 12:00:00", "2026-10")
    transacao(conn, "Lazer", 80.0, "2026-10-08 20:00:00")
    transacao(conn, "Lazer", 20.0, "2026-09-28 10:00:00")
    transacao(conn, "Antiga", 60.0, "2026-10-09 08:00:00")
    transferencia(conn, "Lazer", "Mercado", 200.0, "2026-10-12 10:00:00")
    transacao(conn, "Lazer", 70.0, "2026-09-27 23:00:00")
    transacao(conn, "Mercado", 40.0, "2026-09-15 10:00:00")
    transacao(conn, "Mercado", 25.5, "2026-06-10 10:00:00")
    conn.commit()
    return conn


class TesteRotasBase(unittest.TestCase):
    conn = None

    def iniciar(self, conn):
        self.conn = conn
        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://falso", "falso")
        app.dependency_overrides[obter_conexao_db] = lambda: self.conn
        app.dependency_overrides[obter_agora] = lambda: AGORA_FIXO
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        if self.conn is not None:
            self.conn.close()

    def cabecalho(self, user_id=USER_ID):
        init_data = montar_init_data(auth_date=int(time.time()), user={"id": user_id, "first_name": "Teste"})
        return {"Authorization": "tma " + init_data}

    def obter(self, rota, user_id=USER_ID):
        return self.client.get(rota, headers=self.cabecalho(user_id))


class TestAutenticacaoECadastro(TesteRotasBase):
    def test_sem_cabecalho_401_nas_duas_rotas(self):
        self.iniciar(banco_completo())
        for rota in ("/resumo", "/historico"):
            resposta = self.client.get(rota)
            self.assertEqual(resposta.status_code, 401, rota)
            self.assertEqual(resposta.json(), {"detail": "nao_autorizado"}, rota)

    def test_esquema_errado_e_initdata_invalido_401(self):
        self.iniciar(banco_completo())
        for rota in ("/resumo", "/historico"):
            self.assertEqual(self.client.get(rota, headers={"Authorization": "Bearer abc"}).status_code, 401)
            adulterado = montar_init_data(auth_date=int(time.time()), hash_override="0" * 64)
            self.assertEqual(self.client.get(rota, headers={"Authorization": "tma " + adulterado}).status_code, 401)
            vencido = montar_init_data(auth_date=int(time.time()) - 7200)
            self.assertEqual(self.client.get(rota, headers={"Authorization": "tma " + vencido}).status_code, 401)

    def test_usuario_nao_cadastrado_404(self):
        self.iniciar(banco_completo())
        for rota in ("/resumo", "/historico"):
            resposta = self.obter(rota, user_id=777)
            self.assertEqual(resposta.status_code, 404, rota)
            self.assertEqual(resposta.json(), {"detail": "usuario_nao_cadastrado"}, rota)

    def test_sem_dia_fechamento_409(self):
        for dia in (None, 0):
            with self.subTest(dia_fechamento=dia):
                self.iniciar(novo_banco(dia_fechamento=dia))
                for rota in ("/resumo", "/historico"):
                    resposta = self.obter(rota)
                    self.assertEqual(resposta.status_code, 409, rota)
                    self.assertEqual(resposta.json(), {"detail": "cadastro_incompleto"}, rota)
                self.tearDown()


class TestResumo(TesteRotasBase):
    def test_resumo_completo(self):
        self.iniciar(banco_completo())
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Mercado: orçamento 1.000 + 200 recebidos = 1.200; gasto 100 + 250 avulsos
        #   + 100 da parcela 1 (a transação dela não conta de novo) = 450;
        #   sobra 1.200 - 450 + 30 de receita = 780.
        # Lazer: orçamento 500 - 200 enviados = 300; gasto 80 + 20 (28/09) = 100;
        #   sobra 300 - 100 = 200. O gasto de 70 em 27/09 é da fatura 2026-09.
        # Antiga (excluída, com gasto 60): só o gasto, orçamento e sobra nulos.
        # Total gasto 450 + 100 + 60 = 610; comprometido 1.200 + 300 = 1.500;
        # sem estouro, sobra do mês = 5.000 - 1.500 = 3.500.
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "comprometido": 1500.0,
            "total_gasto": 610.0,
            "sobra_mes": 3500.0,
            "categorias": [
                {"nome": "Mercado", "orcamento": 1200.0, "gasto": 450.0, "sobra": 780.0},
                {"nome": "Lazer", "orcamento": 300.0, "gasto": 100.0, "sobra": 200.0},
                {"nome": "Antiga", "orcamento": None, "gasto": 60.0, "sobra": None},
            ],
        })

    def test_resumo_com_estouro_abate_a_sobra_do_mes(self):
        conn = novo_banco(renda=3000.0)
        categoria(conn, "Mercado", 500.0)
        categoria(conn, "Lazer", 200.0)
        transacao(conn, "Mercado", 100.0, "2026-10-05 10:00:00")
        transacao(conn, "Lazer", 350.0, "2026-10-06 10:00:00")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Lazer estourou: 350 - 200 = 150 de excedente. Comprometido 500 + 200 = 700;
        # total gasto 100 + 350 = 450; sobra do mês = (3.000 - 700) - 150 = 2.150.
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 3000.0,
            "comprometido": 700.0,
            "total_gasto": 450.0,
            "sobra_mes": 2150.0,
            "categorias": [
                {"nome": "Mercado", "orcamento": 500.0, "gasto": 100.0, "sobra": 400.0},
                {"nome": "Lazer", "orcamento": 200.0, "gasto": 350.0, "sobra": -150.0},
            ],
        })

    def test_resumo_sem_gastos_tem_categorias_zeradas(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 1000.0)
        categoria(conn, "Lazer", 500.0)
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Sem gasto: gasto 0, sobra = orçamento; sobra do mês = 5.000 - 1.500.
        corpo = resposta.json()
        self.assertEqual(corpo, {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "comprometido": 1500.0,
            "total_gasto": 0.0,
            "sobra_mes": 3500.0,
            "categorias": [
                {"nome": "Mercado", "orcamento": 1000.0, "gasto": 0.0, "sobra": 1000.0},
                {"nome": "Lazer", "orcamento": 500.0, "gasto": 0.0, "sobra": 500.0},
            ],
        })
        for campo in ("renda", "comprometido", "total_gasto", "sobra_mes"):
            self.assertIsInstance(corpo[campo], float, campo)

    def test_resumo_renda_nula_vira_zero_e_sem_categorias(self):
        self.iniciar(novo_banco(renda=None))
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo, {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 0.0,
            "comprometido": 0.0,
            "total_gasto": 0.0,
            "sobra_mes": 0.0,
            "categorias": [],
        })
        for campo in ("renda", "comprometido", "total_gasto", "sobra_mes"):
            self.assertIsInstance(corpo[campo], float, campo)

    def test_resumo_arredonda_a_duas_casas(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 100.0)
        transacao(conn, "Mercado", 10.005, "2026-10-05 10:00:00")
        transacao(conn, "Mercado", 0.114, "2026-10-06 10:00:00")
        conn.commit()
        self.iniciar(conn)
        categoria_mercado = self.obter("/resumo").json()["categorias"][0]
        # Gasto bruto 10,119 -> 10,12; sobra bruta 89,881 -> 89,88.
        self.assertEqual(categoria_mercado["gasto"], 10.12)
        self.assertEqual(categoria_mercado["sobra"], 89.88)


class TestHistorico(TesteRotasBase):
    def test_historico_com_fechado_atual_e_previstos(self):
        self.iniciar(banco_completo())
        resposta = self.obter("/historico")
        self.assertEqual(resposta.status_code, 200)
        # Só os meses com movimento: julho e agosto, sem nada, não aparecem.
        #   2026-06: 25,50 (avulso de 10/06)                       -> fechado
        #   2026-09: 40 (15/09) + 70 (27/09)                       -> fechado
        #   2026-10: 610 (a mesma conta do /resumo)                -> atual
        #   2026-11: parcela 2 da compra do Mercado (100)          -> previsto
        #   2026-12: parcela 3 da compra do Mercado (100)          -> previsto
        self.assertEqual(resposta.json(), {
            "ano_mes_atual": "2026-10",
            "meses": [
                {"ano_mes": "2026-06", "total_gasto": 25.5, "situacao": "fechado"},
                {"ano_mes": "2026-09", "total_gasto": 110.0, "situacao": "fechado"},
                {"ano_mes": "2026-10", "total_gasto": 610.0, "situacao": "atual"},
                {"ano_mes": "2026-11", "total_gasto": 100.0, "situacao": "previsto"},
                {"ano_mes": "2026-12", "total_gasto": 100.0, "situacao": "previsto"},
            ],
        })

    def test_historico_sem_gastos_so_tem_o_mes_atual(self):
        self.iniciar(novo_banco())
        resposta = self.obter("/historico")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo, {
            "ano_mes_atual": "2026-10",
            "meses": [{"ano_mes": "2026-10", "total_gasto": 0.0, "situacao": "atual"}],
        })
        self.assertIsInstance(corpo["meses"][0]["total_gasto"], float)

    def test_total_de_cada_mes_igual_ao_do_painel(self):
        """O total por mês (uma consulta por mês) bate com o de
        calcular_totais_mes(renda, montar_categorias_fatura(...))[1]."""
        conn = banco_completo()
        self.iniciar(conn)
        meses = self.obter("/historico").json()["meses"]
        self.assertEqual(len(meses), 5)
        for mes in meses:
            categorias = montar_categorias_fatura(conn, USER_ID, mes["ano_mes"], DIA_FECHAMENTO)
            total_painel = calcular_totais_mes(RENDA, categorias)[1]
            self.assertEqual(mes["total_gasto"], round(total_painel, 2), mes["ano_mes"])

    def test_total_do_mes_atual_igual_ao_do_resumo(self):
        self.iniciar(banco_completo())
        total_resumo = self.obter("/resumo").json()["total_gasto"]
        atual = [m for m in self.obter("/historico").json()["meses"] if m["situacao"] == "atual"]
        self.assertEqual(len(atual), 1)
        self.assertEqual(atual[0]["total_gasto"], total_resumo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
