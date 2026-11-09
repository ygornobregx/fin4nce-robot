"""Testes das rotas de leitura da API: GET /resumo e GET /historico (PRO-76) e
GET /gastos/recentes (PRO-79; campo estabelecimento na PRO-81), de
GET /gastos/{id} com o cálculo das parcelas pagas (PRO-80) e de
GET /movimentacoes/recentes (PRO-92, gastos e receitas numa lista só).

Roda com `python testes_rotas_leitura.py`. Não usa .env nem rede: o token é
falso, o banco é um SQLite em memória e o "agora" da API é fixo (17/10/2026
12:00 BRT), por isso a fatura vigente é 2026-10 (28/09 a 27/10, fechamento
dia 28). A data de corte da Sobra (PRO-83) é fixada em 28/09/2026, para a
fatura 2026-10 ter Sobra, salvo nos testes que dizem outra data. Os valores
esperados estão escritos à mão, com a conta ao lado.
"""
import sqlite3
import time
import unittest
from datetime import date, datetime
from unittest import mock

from fastapi.testclient import TestClient

import calculos
from api.main import Config, app, obter_agora, obter_config, obter_conexao_db
from calculos import FUSO_BRASILIA, _meses_entre, calcular_parcelas_pagas, calcular_resumo_fatura
from testes_initdata import TOKEN_FALSO, USER_ID, montar_init_data

AGORA_FIXO = datetime(2026, 10, 17, 12, 0, tzinfo=FUSO_BRASILIA)
DIA_FECHAMENTO = 28
RENDA = 5000.0
# Início da fatura 2026-10 (fechamento 28): a fatura vigente tem Sobra.
DATA_CORTE_TESTE = date(2026, 9, 28)

ESQUEMA = '''
    CREATE TABLE usuarios (user_id INTEGER PRIMARY KEY, nome TEXT, renda_mensal REAL,
        dia_fechamento INTEGER, horario_alerta TEXT, ultimo_reset TEXT);
    CREATE TABLE categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT,
        orcamento_mensal REAL, saldo_atual REAL, data_criacao TEXT);
    CREATE TABLE transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metodo_pagamento TEXT,
        descricao TEXT, parcelamento_id INTEGER, estabelecimento TEXT);
    CREATE TABLE parcelamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        descricao TEXT, valor_total REAL, valor_parcela REAL, total_parcelas INTEGER,
        parcelas_pagas INTEGER DEFAULT 1, data_criacao TEXT, ativo INTEGER DEFAULT 1, ciclo_referencia TEXT);
    CREATE TABLE gastos_recorrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
        descricao TEXT, valor REAL, dia_cobranca INTEGER, criado_em TEXT, ultimo_ciclo_cobrado TEXT,
        estabelecimento TEXT);
    CREATE TABLE transferencias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_origem TEXT,
        categoria_destino TEXT, valor REAL, data_transferencia TEXT);
    CREATE TABLE receitas (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        valor REAL NOT NULL CHECK (valor > 0), descricao TEXT, data_receita TEXT NOT NULL,
        origem TEXT NOT NULL DEFAULT 'avulsa', receita_recorrente_id INTEGER);
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


def transacao(conn, cat, valor, momento, descricao=None, user_id=USER_ID, parcelamento_id=None,
              estabelecimento=None):
    conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, descricao, "
                 "parcelamento_id, estabelecimento) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (user_id, cat, valor, momento, descricao, parcelamento_id, estabelecimento))


def parcelamento(conn, cat, total, n, momento, ciclo_referencia, descricao=None, user_id=USER_ID, ativo=1,
                 estabelecimento=None):
    """Como o bot (desde a PRO-78): a parcela 1 também vira transação, na mesma
    data, e guarda o id da compra em parcelamento_id. A descrição é gravada nas
    duas linhas; sem ela, a compra fica como 'compra' e a transação sem nome.
    O estabelecimento (PRO-81) fica só na parcela 1, em transacoes."""
    parcela = round(total / n, 2)
    compra = conn.execute("INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, "
                          "valor_parcela, total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo) "
                          "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                          (user_id, cat, descricao or "compra", total, parcela, n, momento, ciclo_referencia, ativo))
    transacao(conn, cat, parcela, momento, descricao, user_id, compra.lastrowid, estabelecimento)


def receita(conn, valor, momento, descricao=None, user_id=USER_ID):
    """Receita avulsa (PRO-86): tabela 'receitas', valor sempre positivo."""
    conn.execute("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem) "
                 "VALUES (?, ?, ?, ?, 'avulsa')", (user_id, valor, descricao, momento))


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
    corte = None

    def iniciar(self, conn, data_corte=DATA_CORTE_TESTE):
        self.conn = conn
        app.dependency_overrides[obter_config] = lambda: Config(TOKEN_FALSO, "libsql://falso", "falso")
        app.dependency_overrides[obter_conexao_db] = lambda: self.conn
        app.dependency_overrides[obter_agora] = lambda: AGORA_FIXO
        # A data de corte é uma constante do calculos.py: fixada aqui, como o "agora".
        if self.corte is not None:
            self.corte.stop()
        self.corte = mock.patch.object(calculos, "DATA_CORTE_SOBRA", data_corte)
        self.corte.start()
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        if self.corte is not None:
            self.corte.stop()
            self.corte = None
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
        # Mercado: gasto 100 + 250 avulsos + 100 da parcela 1 (a transação dela
        #   não conta de novo) = 450. A receita de -30 não entra.
        # Lazer: gasto 80 + 20 (28/09) = 100. O gasto de 70 em 27/09 é da fatura 2026-09.
        # Antiga (excluída, com gasto 60): entra com o gasto.
        # A transferência antiga de 200 não muda nada.
        # Total gasto 450 + 100 + 60 = 610; sobra do mês = 5.000 - 610 = 4.390.
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "total_gasto": 610.0,
            "sobra_mes": 4390.0,
            "total_receitas": 0.0,
            "categorias": [
                {"nome": "Mercado", "gasto": 450.0},
                {"nome": "Lazer", "gasto": 100.0},
                {"nome": "Antiga", "gasto": 60.0},
            ],
        })

    def test_resumo_sobra_desconta_o_gasto_real(self):
        conn = novo_banco(renda=3000.0)
        categoria(conn, "Mercado", 500.0)
        categoria(conn, "Lazer", 200.0)
        transacao(conn, "Mercado", 100.0, "2026-10-05 10:00:00")
        transacao(conn, "Lazer", 350.0, "2026-10-06 10:00:00")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Os limites antigos gravados (500 e 200) não contam: total gasto
        # 100 + 350 = 450; sobra do mês = 3.000 - 450 = 2.550.
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 3000.0,
            "total_gasto": 450.0,
            "sobra_mes": 2550.0,
            "total_receitas": 0.0,
            "categorias": [
                {"nome": "Mercado", "gasto": 100.0},
                {"nome": "Lazer", "gasto": 350.0},
            ],
        })

    def test_resumo_gasto_maior_que_a_renda_da_sobra_negativa(self):
        conn = novo_banco(renda=300.0)
        categoria(conn, "Mercado", 0.0)
        transacao(conn, "Mercado", 450.0, "2026-10-05 10:00:00")
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        # 300 - 450 = -150.
        self.assertEqual(corpo["total_gasto"], 450.0)
        self.assertEqual(corpo["sobra_mes"], -150.0)
        self.assertIsInstance(corpo["sobra_mes"], float)

    def test_reembolso_antigo_nao_muda_a_sobra(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 1000.0)
        transacao(conn, "Mercado", 200.0, "2026-10-05 10:00:00")
        transacao(conn, "Mercado", -80.0, "2026-10-06 10:00:00")
        transacao(conn, "Removida", -40.0, "2026-10-07 10:00:00")
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        # As transações negativas antigas (reembolsos) não entram em nada:
        # gasto 200, sobra 5.000 - 200 = 4.800, e "Removida" (só reembolso)
        # não aparece nas categorias.
        self.assertEqual(corpo["total_gasto"], 200.0)
        self.assertEqual(corpo["sobra_mes"], 4800.0)
        self.assertEqual(corpo["categorias"], [{"nome": "Mercado", "gasto": 200.0}])

    def test_resumo_antes_do_corte_sobra_nula(self):
        # Corte em 10/11/2026: com fechamento 28, a primeira fatura com Sobra é
        # 2026-11; a vigente (2026-10) é anterior, então sobra_mes vem null.
        self.iniciar(banco_completo(), data_corte=date(2026, 11, 10))
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "total_gasto": 610.0,
            "sobra_mes": None,
            "total_receitas": 0.0,
            "categorias": [
                {"nome": "Mercado", "gasto": 450.0},
                {"nome": "Lazer", "gasto": 100.0},
                {"nome": "Antiga", "gasto": 60.0},
            ],
        })

    def test_resumo_no_dia_do_corte_tem_sobra(self):
        # Corte no próprio dia 17/10/2026 ("agora" do teste): a fatura vigente tem Sobra.
        self.iniciar(banco_completo(), data_corte=date(2026, 10, 17))
        self.assertEqual(self.obter("/resumo").json()["sobra_mes"], 4390.0)

    def test_resumo_sem_gastos_tem_categorias_zeradas(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 1000.0)
        categoria(conn, "Lazer", 500.0)
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Sem gasto: gasto 0 em cada categoria; sobra do mês = 5.000 - 0.
        corpo = resposta.json()
        self.assertEqual(corpo, {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "total_gasto": 0.0,
            "sobra_mes": 5000.0,
            "total_receitas": 0.0,
            "categorias": [
                {"nome": "Mercado", "gasto": 0.0},
                {"nome": "Lazer", "gasto": 0.0},
            ],
        })
        for campo in ("renda", "total_gasto", "sobra_mes"):
            self.assertIsInstance(corpo[campo], float, campo)
        for cat in corpo["categorias"]:
            self.assertIsInstance(cat["gasto"], float, cat["nome"])

    def test_resumo_renda_nula_vira_zero_e_sem_categorias(self):
        self.iniciar(novo_banco(renda=None))
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo, {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 0.0,
            "total_gasto": 0.0,
            "sobra_mes": 0.0,
            "total_receitas": 0.0,
            "categorias": [],
        })
        for campo in ("renda", "total_gasto", "sobra_mes"):
            self.assertIsInstance(corpo[campo], float, campo)

    def test_resumo_arredonda_a_duas_casas(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 100.0)
        transacao(conn, "Mercado", 10.005, "2026-10-05 10:00:00")
        transacao(conn, "Mercado", 0.114, "2026-10-06 10:00:00")
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        # Gasto bruto 10,119 -> 10,12; sobra do mês bruta 5.000 - 10,119 = 4.989,881 -> 4.989,88.
        self.assertEqual(corpo["categorias"][0]["gasto"], 10.12)
        self.assertEqual(corpo["total_gasto"], 10.12)
        self.assertEqual(corpo["sobra_mes"], 4989.88)


class TestResumoComReceitas(TesteRotasBase):
    """PRO-86: a Sobra soma as receitas da fatura (mesmo intervalo do gasto) e
    o /resumo traz total_receitas."""

    def test_resumo_com_receita_soma_na_sobra(self):
        conn = banco_completo()
        receita(conn, 500.0, "2026-10-11 10:00:00", "Venda da Bicicleta")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/resumo")
        self.assertEqual(resposta.status_code, 200)
        # Sobra = 5.000 + 500 - 610 = 4.890. A receita não muda gasto nem categorias.
        self.assertEqual(resposta.json(), {
            "ano_mes": "2026-10",
            "dia_fechamento": 28,
            "renda": 5000.0,
            "total_gasto": 610.0,
            "sobra_mes": 4890.0,
            "total_receitas": 500.0,
            "categorias": [
                {"nome": "Mercado", "gasto": 450.0},
                {"nome": "Lazer", "gasto": 100.0},
                {"nome": "Antiga", "gasto": 60.0},
            ],
        })

    def test_so_as_receitas_da_fatura_e_do_usuario(self):
        conn = banco_completo()
        receita(conn, 70.0, "2026-09-27 23:59:00")            # fatura 2026-09: fora
        receita(conn, 20.0, "2026-09-28 00:00:00")            # primeiro dia da 2026-10: entra
        receita(conn, 30.0, "2026-10-27 23:59:00")            # último dia da 2026-10: entra
        receita(conn, 90.0, "2026-10-28 00:00:00")            # fatura 2026-11: fora
        receita(conn, 999.0, "2026-10-11 10:00:00", user_id=USER_ID + 1)  # outro usuário: fora
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        self.assertEqual(corpo["total_receitas"], 50.0)
        self.assertEqual(corpo["sobra_mes"], 4440.0)  # 5.000 + 50 - 610

    def test_antes_do_corte_sobra_nula_e_receitas_preenchidas(self):
        conn = banco_completo()
        receita(conn, 500.0, "2026-10-11 10:00:00")
        conn.commit()
        self.iniciar(conn, data_corte=date(2026, 11, 10))
        corpo = self.obter("/resumo").json()
        self.assertIsNone(corpo["sobra_mes"])
        self.assertEqual(corpo["total_receitas"], 500.0)
        self.assertEqual(corpo["total_gasto"], 610.0)

    def test_total_receitas_float_arredondado_a_duas_casas(self):
        conn = banco_completo()
        receita(conn, 10.005, "2026-10-05 10:00:00")
        receita(conn, 0.114, "2026-10-06 10:00:00")
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        # Bruto: receitas 10,119 -> 10,12; Sobra 5.000 + 10,119 - 610 = 4.400,119 -> 4.400,12.
        self.assertEqual(corpo["total_receitas"], 10.12)
        self.assertIsInstance(corpo["total_receitas"], float)
        self.assertEqual(corpo["sobra_mes"], 4400.12)

    def test_reembolso_antigo_continua_fora_com_receita(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 1000.0)
        transacao(conn, "Mercado", 200.0, "2026-10-05 10:00:00")
        transacao(conn, "Mercado", -80.0, "2026-10-06 10:00:00")
        receita(conn, 100.0, "2026-10-07 10:00:00")
        conn.commit()
        self.iniciar(conn)
        corpo = self.obter("/resumo").json()
        # 5.000 + 100 - 200 = 4.900 (o -80 em transacoes não entra).
        self.assertEqual(corpo["total_receitas"], 100.0)
        self.assertEqual(corpo["sobra_mes"], 4900.0)

    def test_outras_rotas_iguais_com_receita_no_banco(self):
        """/historico, /gastos/recentes e /gastos/{id} não mudam com uma receita
        no banco (D13: a receita fica fora do extrato nesta tarefa)."""
        def montar(com_receita):
            conn = banco_completo()
            transacao(conn, "Alimentação", 18.5, "2026-10-14 14:30:00", descricao="Padaria")
            if com_receita:
                receita(conn, 500.0, "2026-10-14 15:00:00", "Venda da Bicicleta")
            conn.commit()
            return conn

        respostas = {}
        for com_receita in (False, True):
            self.iniciar(montar(com_receita))
            respostas[com_receita] = [self.obter(rota).json()
                                      for rota in ("/historico", "/gastos/recentes", "/gastos/11", "/gastos/1")]
            self.tearDown()
        self.assertEqual(respostas[True], respostas[False])
        self.assertEqual(len(respostas[True][1]["gastos"]), 1)
        self.assertEqual(respostas[True][2]["descricao"], "Padaria")


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
        """O total por mês (uma consulta por mês) bate com o total_gasto da
        função única de resumo (calcular_resumo_fatura), usada pelo painel."""
        conn = banco_completo()
        self.iniciar(conn)
        meses = self.obter("/historico").json()["meses"]
        self.assertEqual(len(meses), 5)
        for mes in meses:
            total_painel = calcular_resumo_fatura(conn, USER_ID, mes["ano_mes"], DIA_FECHAMENTO).total_gasto
            self.assertEqual(mes["total_gasto"], round(total_painel, 2), mes["ano_mes"])

    def test_sobra_de_cada_mes_pela_data_de_corte(self):
        """banco_completo com o corte do teste (28/09/2026): as faturas antes de
        2026-10 ficam sem Sobra (só o gasto); 2026-10 e as previstas têm Sobra."""
        conn = banco_completo()
        self.iniciar(conn)
        esperado = {"2026-06": (25.5, None), "2026-09": (110.0, None), "2026-10": (610.0, 4390.0),
                    "2026-11": (100.0, 4900.0), "2026-12": (100.0, 4900.0)}
        for ano_mes, (gasto, sobra) in esperado.items():
            resumo = calcular_resumo_fatura(conn, USER_ID, ano_mes, DIA_FECHAMENTO)
            self.assertEqual(round(resumo.total_gasto, 2), gasto, ano_mes)
            self.assertEqual(None if resumo.sobra_mes is None else round(resumo.sobra_mes, 2), sobra, ano_mes)

    def test_total_do_mes_atual_igual_ao_do_resumo(self):
        self.iniciar(banco_completo())
        total_resumo = self.obter("/resumo").json()["total_gasto"]
        atual = [m for m in self.obter("/historico").json()["meses"] if m["situacao"] == "atual"]
        self.assertEqual(len(atual), 1)
        self.assertEqual(atual[0]["total_gasto"], total_resumo)


class TestGastosRecentes(TesteRotasBase):
    def test_sem_cabecalho_401(self):
        self.iniciar(banco_completo())
        resposta = self.client.get("/gastos/recentes")
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": "nao_autorizado"})

    def test_usuario_nao_cadastrado_404(self):
        self.iniciar(banco_completo())
        resposta = self.obter("/gastos/recentes", user_id=777)
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json(), {"detail": "usuario_nao_cadastrado"})

    def test_sem_dia_fechamento_409(self):
        for dia in (None, 0):
            with self.subTest(dia_fechamento=dia):
                self.iniciar(novo_banco(dia_fechamento=dia))
                resposta = self.obter("/gastos/recentes")
                self.assertEqual(resposta.status_code, 409)
                self.assertEqual(resposta.json(), {"detail": "cadastro_incompleto"})
                self.tearDown()

    def test_sem_gasto_com_descricao_devolve_lista_vazia(self):
        # banco_completo só tem gastos sem descrição (anteriores à PRO-78) e uma
        # receita: nada entra no extrato, mesmo com os totais do /resumo cheios.
        self.iniciar(banco_completo())
        resposta = self.obter("/gastos/recentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"gastos": []})

    def test_gasto_a_vista(self):
        conn = novo_banco()
        transacao(conn, "Alimentação", 18.5, "2026-10-29 14:30:00", descricao="Padaria Pão Quente")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/gastos/recentes")
        self.assertEqual(resposta.status_code, 200)
        # A hora some: "data" é só o dia. Sem parcelamento_id: à vista, campos nulos.
        self.assertEqual(resposta.json(), {"gastos": [{
            "id": 1, "descricao": "Padaria Pão Quente", "estabelecimento": None,
            "categoria": "Alimentação", "valor": 18.5,
            "data": "2026-10-29", "parcelado": False, "total_parcelas": None, "valor_total": None,
        }]})

    def test_gasto_com_estabelecimento_traz_o_nome_e_a_descricao_intacta(self):
        conn = novo_banco()
        transacao(conn, "Roupas", 300.0, "2026-10-10 10:00:00", descricao="Tênis na Nike",
                  estabelecimento="Nike")
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/recentes").json()["gastos"][0]
        self.assertEqual(gasto["estabelecimento"], "Nike")
        self.assertEqual(gasto["descricao"], "Tênis na Nike")

    def test_gasto_sem_estabelecimento_vem_null_e_continua_na_lista(self):
        conn = novo_banco()
        transacao(conn, "Alimentação", 12.0, "2026-10-10 10:00:00", descricao="Pão")  # estabelecimento NULL
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        self.assertEqual(len(gastos), 1)
        self.assertEqual(gastos[0]["descricao"], "Pão")
        # A API manda null puro: "Sem Informação" é decidido pelo bot e pela tela.
        self.assertIn("estabelecimento", gastos[0])
        self.assertIsNone(gastos[0]["estabelecimento"])

    def test_estabelecimento_so_com_espacos_ou_vazio_vem_null(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="A", estabelecimento="   ")  # id 1
        transacao(conn, "Lazer", 11.0, "2026-10-11 10:00:00", descricao="B", estabelecimento="")     # id 2
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        self.assertEqual([(g["descricao"], g["estabelecimento"]) for g in gastos], [("B", None), ("A", None)])

    def test_estabelecimento_vem_sem_espacos_nas_pontas(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="Lanche", estabelecimento="  iFood  ")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/recentes").json()["gastos"][0]["estabelecimento"], "iFood")

    def test_compra_parcelada_com_estabelecimento_na_parcela_1(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 3000.0, 10, "2026-10-28 11:00:00", "2026-10",
                     descricao="Geladeira nas Casas Bahia", estabelecimento="Casas Bahia")
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/recentes").json()["gastos"][0]
        self.assertEqual(gasto["estabelecimento"], "Casas Bahia")
        self.assertEqual((gasto["parcelado"], gasto["total_parcelas"], gasto["valor_total"]), (True, 10, 3000.0))

    def test_descricao_vem_sem_espacos_nas_pontas(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 30.0, "2026-10-10 10:00:00", descricao="  Cinema  ")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/recentes").json()["gastos"][0]["descricao"], "Cinema")

    def test_compra_parcelada_aparece_uma_vez_pela_parcela_1(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 389.7, 3, "2026-10-28 11:00:00", "2026-10", descricao="Magazine Luiza")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/gastos/recentes")
        self.assertEqual(resposta.status_code, 200)
        # 389,70 em 3x: parcela 1 = 129,90. "valor" é a parcela; "valor_total", a compra.
        self.assertEqual(resposta.json(), {"gastos": [{
            "id": 1, "descricao": "Magazine Luiza", "estabelecimento": None,
            "categoria": "Casa", "valor": 129.9,
            "data": "2026-10-28", "parcelado": True, "total_parcelas": 3, "valor_total": 389.7,
        }]})

    def test_compra_parcelada_cancelada_continua_no_extrato(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 300.0, 3, "2026-10-28 11:00:00", "2026-10", descricao="Sofá", ativo=0)
        conn.commit()
        self.iniciar(conn)
        # A compra aconteceu: não se filtra por parcelamentos.ativo.
        gasto = self.obter("/gastos/recentes").json()["gastos"][0]
        self.assertEqual((gasto["valor"], gasto["parcelado"], gasto["total_parcelas"], gasto["valor_total"]),
                         (100.0, True, 3, 300.0))

    def test_gasto_recorrente_lancado_aparece_como_a_vista(self):
        conn = novo_banco()
        # O recorrente grava descrição e nenhum parcelamento_id.
        transacao(conn, "Assinaturas", 39.9, "2026-10-15 08:00:00", descricao="Netflix")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/recentes").json()["gastos"], [{
            "id": 1, "descricao": "Netflix", "estabelecimento": None,
            "categoria": "Assinaturas", "valor": 39.9,
            "data": "2026-10-15", "parcelado": False, "total_parcelas": None, "valor_total": None,
        }])

    def test_ficam_de_fora_gasto_antigo_descricao_vazia_so_espacos_e_receita(self):
        conn = novo_banco()
        transacao(conn, "Mercado", 50.0, "2026-10-01 10:00:00")                         # antigo, descrição NULL
        transacao(conn, "Mercado", 51.0, "2026-10-02 10:00:00", descricao="")           # vazia
        transacao(conn, "Mercado", 52.0, "2026-10-03 10:00:00", descricao="   ")        # só espaços
        transacao(conn, "Mercado", -500.0, "2026-10-04 10:00:00", descricao="Salário")  # receita
        transacao(conn, "Mercado", 10.0, "2026-10-05 10:00:00", descricao="Padaria")    # único válido (id 5)
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/gastos/recentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual([(g["id"], g["descricao"]) for g in resposta.json()["gastos"]], [(5, "Padaria")])

    def test_ordem_do_mais_recente_para_o_mais_antigo_com_desempate_por_id(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 1.0, "2026-10-10 09:00:00", descricao="A")  # id 1
        transacao(conn, "Lazer", 2.0, "2026-10-12 09:00:00", descricao="B")  # id 2
        transacao(conn, "Lazer", 3.0, "2026-10-12 09:00:00", descricao="C")  # id 3, mesmo horário de B
        transacao(conn, "Lazer", 4.0, "2026-10-11 09:00:00", descricao="D")  # id 4
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        # 12/10: C (id 3) antes de B (id 2); depois D (11/10) e A (10/10).
        self.assertEqual([(g["descricao"], g["id"]) for g in gastos], [("C", 3), ("B", 2), ("D", 4), ("A", 1)])

    def test_limite_de_10_devolve_os_mais_recentes(self):
        conn = novo_banco()
        for dia in range(1, 13):  # 12 gastos válidos, um por dia (01 a 12/10)
            transacao(conn, "Lazer", float(dia), f"2026-10-{dia:02d} 10:00:00", descricao=f"Gasto {dia}")
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        # Voltam os dias 12 a 3; os dias 2 e 1 (os mais antigos) ficam de fora.
        self.assertEqual(len(gastos), 10)
        self.assertEqual([g["descricao"] for g in gastos], [f"Gasto {dia}" for dia in range(12, 2, -1)])

    def test_gastos_de_outro_usuario_nao_aparecem(self):
        outro = 999
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="Meu gasto")  # id 1
        transacao(conn, "Lazer", 99.0, "2026-10-11 10:00:00", descricao="Gasto alheio", user_id=outro)
        # Compra parcelada do outro usuário: parcelamento id 1 e transação id 3.
        parcelamento(conn, "Lazer", 600.0, 6, "2026-10-12 10:00:00", "2026-10", descricao="Compra alheia",
                     user_id=outro)
        # Vínculo cruzado: gasto meu (id 4) apontando para a compra do outro usuário.
        transacao(conn, "Lazer", 20.0, "2026-10-09 10:00:00", descricao="Meu com vínculo errado",
                  parcelamento_id=1)
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        # Só os dois meus. O vínculo cruzado não traz dados do outro: sai como à vista.
        self.assertEqual([(g["descricao"], g["parcelado"], g["total_parcelas"], g["valor_total"]) for g in gastos],
                         [("Meu gasto", False, None, None), ("Meu com vínculo errado", False, None, None)])

    def test_transferencia_entre_categorias_nao_aparece(self):
        conn = novo_banco()
        categoria(conn, "Mercado", 1000.0)
        categoria(conn, "Lazer", 500.0)
        transacao(conn, "Mercado", 40.0, "2026-10-05 10:00:00", descricao="Feira")
        # O bot grava a transferência só em transferencias, nunca em transacoes.
        transferencia(conn, "Lazer", "Mercado", 200.0, "2026-10-12 10:00:00")
        conn.commit()
        self.iniciar(conn)
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        self.assertEqual([g["descricao"] for g in gastos], ["Feira"])

    def test_valores_vem_como_float_arredondado(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 100.0, 3, "2026-10-28 11:00:00", "2026-10", descricao="Teste")
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/recentes").json()["gastos"][0]
        # 100 / 3 = 33,33 (parcela arredondada pelo bot); total 100,0.
        self.assertEqual((gasto["valor"], gasto["valor_total"]), (33.33, 100.0))
        self.assertIsInstance(gasto["valor"], float)
        self.assertIsInstance(gasto["valor_total"], float)
        self.assertIsInstance(gasto["total_parcelas"], int)


class TestMovimentacoesRecentes(TesteRotasBase):
    """PRO-92: GET /movimentacoes/recentes junta gastos (transacoes) e receitas
    (receitas) numa lista só, com o campo "tipo" e os mesmos campos nos dois."""

    ROTA = "/movimentacoes/recentes"

    def resumo(self, movimentacoes):
        return [(m["tipo"], m["id"], m["descricao"]) for m in movimentacoes]

    def test_sem_cabecalho_401(self):
        self.iniciar(banco_completo())
        resposta = self.client.get(self.ROTA)
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": "nao_autorizado"})

    def test_esquema_errado_e_initdata_invalido_401(self):
        self.iniciar(banco_completo())
        self.assertEqual(self.client.get(self.ROTA, headers={"Authorization": "Bearer abc"}).status_code, 401)
        adulterado = montar_init_data(auth_date=int(time.time()), hash_override="0" * 64)
        self.assertEqual(self.client.get(self.ROTA, headers={"Authorization": "tma " + adulterado}).status_code, 401)

    def test_usuario_nao_cadastrado_404(self):
        self.iniciar(banco_completo())
        resposta = self.obter(self.ROTA, user_id=777)
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json(), {"detail": "usuario_nao_cadastrado"})

    def test_sem_dia_fechamento_409(self):
        for dia in (None, 0):
            with self.subTest(dia_fechamento=dia):
                self.iniciar(novo_banco(dia_fechamento=dia))
                resposta = self.obter(self.ROTA)
                self.assertEqual(resposta.status_code, 409)
                self.assertEqual(resposta.json(), {"detail": "cadastro_incompleto"})
                self.tearDown()

    def test_lista_vazia(self):
        self.iniciar(novo_banco())
        resposta = self.obter(self.ROTA)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"movimentacoes": []})

    def test_banco_completo_sem_descricao_e_sem_receita_da_lista_vazia(self):
        # banco_completo só tem gastos sem descrição e um negativo antigo.
        self.iniciar(banco_completo())
        self.assertEqual(self.obter(self.ROTA).json(), {"movimentacoes": []})

    def test_contrato_completo_de_receita_e_gasto_parcelado(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 389.7, 3, "2026-10-28 11:00:00", "2026-10", descricao="Magazine Luiza",
                     estabelecimento="Magazine Luiza")                                    # gasto id 1
        receita(conn, 53.9, "2026-10-29 09:00:00", descricao="Reembolso")                  # receita id 1
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter(self.ROTA)
        self.assertEqual(resposta.status_code, 200)
        # Os dois tipos com os mesmos campos; o gasto e a receita têm o mesmo id 1
        # (tabelas independentes): só o par (tipo, id) identifica a linha.
        self.assertEqual(resposta.json(), {"movimentacoes": [
            {"tipo": "receita", "id": 1, "descricao": "Reembolso", "estabelecimento": None,
             "categoria": None, "valor": 53.9, "data": "2026-10-29", "parcelado": False,
             "total_parcelas": None, "valor_total": None},
            {"tipo": "gasto", "id": 1, "descricao": "Magazine Luiza", "estabelecimento": "Magazine Luiza",
             "categoria": "Casa", "valor": 129.9, "data": "2026-10-28", "parcelado": True,
             "total_parcelas": 3, "valor_total": 389.7},
        ]})

    def test_gasto_parcelado_igual_ao_da_gastos_recentes(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 100.0, 3, "2026-10-28 11:00:00", "2026-10", descricao="Teste")
        conn.commit()
        self.iniciar(conn)
        movimentacoes = self.obter(self.ROTA).json()["movimentacoes"]
        gastos = self.obter("/gastos/recentes").json()["gastos"]
        # Aparece uma vez (parcela 1), com os mesmos campos da /gastos/recentes mais o "tipo".
        self.assertEqual(len(movimentacoes), 1)
        self.assertEqual(movimentacoes[0], {"tipo": "gasto", **gastos[0]})
        self.assertEqual((movimentacoes[0]["valor"], movimentacoes[0]["valor_total"]), (33.33, 100.0))
        self.assertIsInstance(movimentacoes[0]["total_parcelas"], int)

    def test_campos_da_receita_nulos_e_valor_positivo_com_duas_casas(self):
        conn = novo_banco()
        receita(conn, 10.119, "2026-10-10 10:00:00", descricao="Pix")
        conn.commit()
        self.iniciar(conn)
        item = self.obter(self.ROTA).json()["movimentacoes"][0]
        self.assertEqual(item["tipo"], "receita")
        for campo in ("estabelecimento", "categoria", "total_parcelas", "valor_total"):
            self.assertIn(campo, item)
            self.assertIsNone(item[campo], campo)
        self.assertIs(item["parcelado"], False)
        # 10,119 -> 10,12: positivo, float, 2 casas (o "+" é da tela).
        self.assertEqual(item["valor"], 10.12)
        self.assertIsInstance(item["valor"], float)

    def test_ordem_mista_pelo_momento_completo(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 08:00:00", descricao="Café")         # gasto 1
        receita(conn, 100.0, "2026-10-10 12:00:00", descricao="Freela")                  # receita 1
        transacao(conn, "Lazer", 20.0, "2026-10-10 18:30:00", descricao="Jantar")       # gasto 2
        receita(conn, 30.0, "2026-10-09 23:59:59", descricao="Reembolso")                # receita 2
        transacao(conn, "Lazer", 5.0, "2026-10-11 00:00:00", descricao="Bala")          # gasto 3
        conn.commit()
        self.iniciar(conn)
        # Mesmo dia (10/10) em horas diferentes: vale a hora, não só o dia.
        self.assertEqual(self.resumo(self.obter(self.ROTA).json()["movimentacoes"]), [
            ("gasto", 3, "Bala"),
            ("gasto", 2, "Jantar"),
            ("receita", 1, "Freela"),
            ("gasto", 1, "Café"),
            ("receita", 2, "Reembolso"),
        ])

    def test_empate_no_mesmo_segundo_gasto_antes_e_maior_id_primeiro(self):
        conn = novo_banco()
        momento = "2026-10-15 10:00:00"
        receita(conn, 1.0, momento, descricao="R1")                # receita 1
        transacao(conn, "Lazer", 1.0, momento, descricao="G1")    # gasto 1
        receita(conn, 2.0, momento, descricao="R2")                # receita 2
        transacao(conn, "Lazer", 2.0, momento, descricao="G2")    # gasto 2
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.resumo(self.obter(self.ROTA).json()["movimentacoes"]), [
            ("gasto", 2, "G2"), ("gasto", 1, "G1"), ("receita", 2, "R2"), ("receita", 1, "R1"),
        ])

    def test_limite_de_10_no_total_contando_os_dois_tipos(self):
        conn = novo_banco()
        for dia in range(1, 8):  # 7 gastos, dias 01 a 07, às 10h
            transacao(conn, "Lazer", float(dia), f"2026-10-{dia:02d} 10:00:00", descricao=f"Gasto {dia}")
        for dia in range(1, 8):  # 7 receitas, dias 01 a 07, às 11h
            receita(conn, float(dia), f"2026-10-{dia:02d} 11:00:00", descricao=f"Receita {dia}")
        conn.commit()
        self.iniciar(conn)
        movimentacoes = self.obter(self.ROTA).json()["movimentacoes"]
        # 14 no banco, 10 na resposta: dias 07 a 03 (receita das 11h antes do gasto das 10h).
        self.assertEqual(len(movimentacoes), 10)
        esperado = []
        for dia in range(7, 2, -1):
            esperado += [f"Receita {dia}", f"Gasto {dia}"]
        self.assertEqual([m["descricao"] for m in movimentacoes], esperado)

    def test_outro_usuario_nunca_aparece_nos_dois_tipos(self):
        outro = 999
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="Meu gasto")
        receita(conn, 20.0, "2026-10-10 11:00:00", descricao="Minha receita")
        transacao(conn, "Lazer", 99.0, "2026-10-12 10:00:00", descricao="Gasto alheio", user_id=outro)
        receita(conn, 999.0, "2026-10-12 11:00:00", descricao="Receita alheia", user_id=outro)
        parcelamento(conn, "Lazer", 600.0, 6, "2026-10-13 10:00:00", "2026-10", descricao="Compra alheia",
                     user_id=outro)
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, ?, ?, ?)",
                     (outro, "Outro", RENDA, DIA_FECHAMENTO))
        conn.commit()
        self.iniciar(conn)
        self.assertEqual([m["descricao"] for m in self.obter(self.ROTA).json()["movimentacoes"]],
                         ["Minha receita", "Meu gasto"])
        # O user_id vem só do initData: o outro usuário, autenticado, vê só os dados dele.
        self.assertEqual([m["descricao"] for m in self.obter(self.ROTA, user_id=outro).json()["movimentacoes"]],
                         ["Compra alheia", "Receita alheia", "Gasto alheio"])

    def test_ficam_fora_negativo_antigo_e_gasto_sem_descricao_e_receita_sem_descricao_entra(self):
        conn = novo_banco()
        transacao(conn, "Mercado", 50.0, "2026-10-01 10:00:00")                          # gasto antigo, NULL
        transacao(conn, "Mercado", 51.0, "2026-10-02 10:00:00", descricao="")            # vazia
        transacao(conn, "Mercado", 52.0, "2026-10-03 10:00:00", descricao="   ")         # só espaços
        transacao(conn, "Mercado", -500.0, "2026-10-04 10:00:00", descricao="Salário")   # negativo antigo
        transacao(conn, "Mercado", 10.0, "2026-10-05 10:00:00", descricao="Padaria")     # gasto id 5
        receita(conn, 40.0, "2026-10-06 10:00:00")                                        # receita 1, NULL
        receita(conn, 41.0, "2026-10-07 10:00:00", descricao="   ")                       # receita 2, espaços
        receita(conn, 42.0, "2026-10-08 10:00:00", descricao="  Pix da Ana  ")            # receita 3
        conn.commit()
        self.iniciar(conn)
        movimentacoes = self.obter(self.ROTA).json()["movimentacoes"]
        # Receita sem descrição entra com null (o "Receita" é escrito pela tela).
        self.assertEqual(self.resumo(movimentacoes), [
            ("receita", 3, "Pix da Ana"),
            ("receita", 2, None),
            ("receita", 1, None),
            ("gasto", 5, "Padaria"),
        ])
        self.assertTrue(all(m["valor"] > 0 for m in movimentacoes))

    def test_transferencia_entre_categorias_nao_aparece(self):
        conn = novo_banco()
        transacao(conn, "Mercado", 40.0, "2026-10-05 10:00:00", descricao="Feira")
        transferencia(conn, "Lazer", "Mercado", 200.0, "2026-10-12 10:00:00")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual([m["descricao"] for m in self.obter(self.ROTA).json()["movimentacoes"]], ["Feira"])

    def test_gastos_recentes_e_detalhe_continuam_iguais_com_receitas_no_banco(self):
        conn = novo_banco()
        transacao(conn, "Alimentação", 18.5, "2026-10-14 14:30:00", descricao="Padaria")  # gasto 1
        receita(conn, 500.0, "2026-10-15 10:00:00", descricao="Freela")                     # receita 1
        receita(conn, 60.0, "2026-10-16 10:00:00", descricao="Pix")                         # receita 2
        conn.commit()
        self.iniciar(conn)
        # /gastos/recentes: só o gasto, no contrato de antes (sem "tipo").
        self.assertEqual(self.obter("/gastos/recentes").json(), {"gastos": [{
            "id": 1, "descricao": "Padaria", "estabelecimento": None,
            "categoria": "Alimentação", "valor": 18.5,
            "data": "2026-10-14", "parcelado": False, "total_parcelas": None, "valor_total": None,
        }]})
        # /gastos/{id}: o gasto 1 como antes; o id 2 só existe como receita e dá 404.
        detalhe = self.obter("/gastos/1")
        self.assertEqual(detalhe.status_code, 200)
        self.assertEqual(detalhe.json(), {
            "id": 1, "descricao": "Padaria", "estabelecimento": None,
            "categoria": "Alimentação", "valor": 18.5,
            "data": "2026-10-14", "parcelado": False, "total_parcelas": None, "valor_total": None,
            "parcelas_pagas": None,
        })
        resposta = self.obter("/gastos/2")
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json(), {"detail": "gasto_nao_encontrado"})

    def test_funcao_do_calculos_devolve_tuplas_documentadas(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="Cinema", estabelecimento="  Cinemark ")
        receita(conn, 20.0, "2026-10-11 10:00:00", descricao="Freela")
        conn.commit()
        self.assertEqual(calculos.buscar_movimentacoes_recentes(conn, USER_ID, 10), [
            ("receita", 1, "Freela", None, 20.0, "2026-10-11", None, None, None),
            ("gasto", 1, "Cinema", "Lazer", 10.0, "2026-10-10", None, None, "Cinemark"),
        ])
        self.assertEqual(calculos.buscar_movimentacoes_recentes(conn, USER_ID, 1),
                         [("receita", 1, "Freela", None, 20.0, "2026-10-11", None, None, None)])
        conn.close()


class TestParcelasPagas(unittest.TestCase):
    """calcular_parcelas_pagas (PRO-80): função pura, com o "agora" por parâmetro.
    Fechamento dia 28: até o dia 27 a fatura vigente é a do próprio mês."""

    @staticmethod
    def pagas(ciclo_referencia, data_criacao, total, ano, mes, dia=10):
        agora = datetime(ano, mes, dia, 12, 0, tzinfo=FUSO_BRASILIA)
        return calcular_parcelas_pagas(ciclo_referencia, data_criacao, total, DIA_FECHAMENTO, agora)

    def test_compra_no_mes_corrente_tem_1_de_3(self):
        # Primeira fatura 2026-11, vigente 2026-11: 0 meses de distância + 1.
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 3, 2026, 11), 1)

    def test_um_mes_depois_tem_2_de_3(self):
        # Vigente 2026-12: 1 mês de distância + 1.
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 3, 2026, 12), 2)

    def test_todas_as_faturas_passadas_limita_no_total(self):
        # Vigente 2027-04: 5 meses + 1 = 6, limitado a 3.
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 3, 2027, 4), 3)

    def test_banco_adiou_pode_dar_zero(self):
        # Primeira parcela empurrada para 2026-12, vigente 2026-11: -1 + 1 = 0.
        self.assertEqual(self.pagas("2026-12", "2026-11-05 10:00:00", 3, 2026, 11), 0)
        # Adiada por 3 meses, o resultado nunca fica negativo.
        self.assertEqual(self.pagas("2027-02", "2026-11-05 10:00:00", 3, 2026, 11), 0)

    def test_ciclo_referencia_nulo_usa_o_mes_de_data_criacao(self):
        # Mesmo reserva da expansão em SQL: 7 primeiros caracteres de data_criacao.
        self.assertEqual(self.pagas(None, "2026-10-10 12:00:00", 3, 2026, 10), 1)
        self.assertEqual(self.pagas(None, "2026-10-10 12:00:00", 3, 2026, 11), 2)

    def test_ciclo_referencia_vazio_usa_o_mes_de_data_criacao(self):
        self.assertEqual(self.pagas("", "2026-10-10 12:00:00", 3, 2026, 10), 1)
        self.assertEqual(self.pagas("", "2026-10-10 12:00:00", 3, 2026, 11), 2)

    def test_virada_de_ano(self):
        # 2026-11 até 2027-01 são 2 meses: 2 + 1 = 3 (compra em 5x).
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 5, 2027, 1), 3)
        # E no sentido contrário: primeira fatura em 2027-01, vigente em 2026-11.
        self.assertEqual(self.pagas("2027-01", "2026-11-05 10:00:00", 5, 2026, 11), 0)

    def test_compra_em_1x(self):
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 1, 2026, 11), 1)
        self.assertEqual(self.pagas("2026-11", "2026-11-05 10:00:00", 1, 2027, 3), 1)
        self.assertEqual(self.pagas("2026-12", "2026-11-05 10:00:00", 1, 2026, 11), 0)

    def test_dia_de_fechamento_vira_a_fatura_vigente(self):
        # Em 28/10 (dia de fechamento) a fatura vigente já é a 2026-11.
        self.assertEqual(self.pagas("2026-10", "2026-10-05 10:00:00", 3, 2026, 10, dia=27), 1)
        self.assertEqual(self.pagas("2026-10", "2026-10-05 10:00:00", 3, 2026, 10, dia=28), 2)

    def test_meses_entre(self):
        self.assertEqual(_meses_entre("2026-11", "2026-11"), 0)
        self.assertEqual(_meses_entre("2026-11", "2027-01"), 2)
        self.assertEqual(_meses_entre("2027-01", "2026-11"), -2)
        self.assertEqual(_meses_entre("2025-12", "2027-01"), 13)

    def test_sem_agora_usa_o_agora_de_brasilia(self):
        # Sem 'agora', usa a data real: um ciclo muito antigo está todo pago e um
        # muito futuro não tem nenhuma parcela paga, em qualquer dia que o teste rode.
        self.assertEqual(calcular_parcelas_pagas("2000-01", None, 3, DIA_FECHAMENTO), 3)
        self.assertEqual(calcular_parcelas_pagas("2999-01", None, 3, DIA_FECHAMENTO), 0)


class TestDetalheGasto(TesteRotasBase):
    def test_sem_cabecalho_401(self):
        self.iniciar(banco_completo())
        resposta = self.client.get("/gastos/1")
        self.assertEqual(resposta.status_code, 401)
        self.assertEqual(resposta.json(), {"detail": "nao_autorizado"})

    def test_usuario_nao_cadastrado_404(self):
        self.iniciar(banco_completo())
        resposta = self.obter("/gastos/1", user_id=777)
        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(resposta.json(), {"detail": "usuario_nao_cadastrado"})

    def test_sem_dia_fechamento_409(self):
        for dia in (None, 0):
            with self.subTest(dia_fechamento=dia):
                self.iniciar(novo_banco(dia_fechamento=dia))
                resposta = self.obter("/gastos/1")
                self.assertEqual(resposta.status_code, 409)
                self.assertEqual(resposta.json(), {"detail": "cadastro_incompleto"})
                self.tearDown()

    def test_gasto_a_vista(self):
        conn = novo_banco()
        transacao(conn, "Alimentação", 18.5, "2026-10-14 14:30:00", descricao="Padaria Pão Quente")
        conn.commit()
        self.iniciar(conn)
        resposta = self.obter("/gastos/1")
        self.assertEqual(resposta.status_code, 200)
        # À vista: parcelado false e os quatro campos de parcela nulos.
        self.assertEqual(resposta.json(), {
            "id": 1, "descricao": "Padaria Pão Quente", "estabelecimento": None,
            "categoria": "Alimentação", "valor": 18.5, "data": "2026-10-14",
            "parcelado": False, "total_parcelas": None, "valor_total": None, "parcelas_pagas": None,
        })

    def test_compra_parcelada_conta_as_parcelas_pagas_pela_fatura(self):
        conn = novo_banco()
        # Compra de 600 em 3x com a primeira parcela na fatura 2026-09; o "agora" fixo
        # (17/10) está na fatura 2026-10: 1 mês depois + 1 = 2 parcelas pagas. A coluna
        # parcelas_pagas é gravada como 1 (abandonada) e não pode ser lida.
        parcelamento(conn, "Roupas", 600.0, 3, "2026-09-10 12:00:00", "2026-09",
                     descricao="Tênis na Nike", estabelecimento="Nike")
        conn.commit()
        self.assertEqual(conn.execute("SELECT parcelas_pagas FROM parcelamentos").fetchone()[0], 1)
        self.iniciar(conn)
        resposta = self.obter("/gastos/1")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {
            "id": 1, "descricao": "Tênis na Nike", "estabelecimento": "Nike",
            "categoria": "Roupas", "valor": 200.0, "data": "2026-09-10",
            "parcelado": True, "total_parcelas": 3, "valor_total": 600.0, "parcelas_pagas": 2,
        })

    def test_compra_de_hoje_tem_1_parcela_paga_e_valores_arredondados(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 100.0, 3, "2026-10-10 12:00:00", "2026-10", descricao="Teste")
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/1").json()
        # 100 / 3 = 33,33: a tela mostra o que o bot gravou, sem refazer a divisão.
        self.assertEqual((gasto["valor"], gasto["valor_total"], gasto["parcelas_pagas"]), (33.33, 100.0, 1))
        self.assertIsInstance(gasto["valor"], float)
        self.assertIsInstance(gasto["valor_total"], float)
        self.assertIsInstance(gasto["total_parcelas"], int)
        self.assertIsInstance(gasto["parcelas_pagas"], int)

    def test_banco_adiou_da_zero_parcelas_pagas(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 300.0, 3, "2026-10-10 12:00:00", "2026-11", descricao="Sofá")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/1").json()["parcelas_pagas"], 0)

    def test_ciclo_referencia_nulo_cai_no_mes_de_data_criacao(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 300.0, 3, "2026-08-10 12:00:00", None, descricao="Sofá")
        conn.commit()
        self.iniciar(conn)
        # Primeira fatura 2026-08, vigente 2026-10: 2 meses + 1 = 3.
        self.assertEqual(self.obter("/gastos/1").json()["parcelas_pagas"], 3)

    def test_compra_cancelada_continua_com_detalhe(self):
        conn = novo_banco()
        parcelamento(conn, "Casa", 300.0, 3, "2026-10-10 12:00:00", "2026-10", descricao="Sofá", ativo=0)
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/1").json()
        self.assertEqual((gasto["parcelado"], gasto["total_parcelas"], gasto["valor_total"]), (True, 3, 300.0))

    def test_estabelecimento_vazio_ou_so_espacos_vem_null(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="A", estabelecimento="   ")  # id 1
        transacao(conn, "Lazer", 11.0, "2026-10-11 10:00:00", descricao="B", estabelecimento="")     # id 2
        transacao(conn, "Lazer", 12.0, "2026-10-12 10:00:00", descricao="C")                         # id 3, NULL
        transacao(conn, "Lazer", 13.0, "2026-10-13 10:00:00", descricao="D", estabelecimento="  iFood  ")  # id 4
        conn.commit()
        self.iniciar(conn)
        for id_gasto in (1, 2, 3):
            self.assertIsNone(self.obter(f"/gastos/{id_gasto}").json()["estabelecimento"], id_gasto)
        # Com texto, vem sem os espaços das pontas.
        self.assertEqual(self.obter("/gastos/4").json()["estabelecimento"], "iFood")

    def test_descricao_vem_sem_espacos_nas_pontas(self):
        conn = novo_banco()
        transacao(conn, "Lazer", 30.0, "2026-10-10 10:00:00", descricao="  Cinema  ")
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/1").json()["descricao"], "Cinema")

    def test_gasto_nao_encontrado_404_nos_quatro_casos(self):
        outro = 999
        conn = novo_banco()
        transacao(conn, "Lazer", 10.0, "2026-10-10 10:00:00", descricao="Meu gasto")            # id 1 (existe)
        transacao(conn, "Lazer", 99.0, "2026-10-11 10:00:00", descricao="Gasto alheio", user_id=outro)  # id 2
        transacao(conn, "Lazer", -500.0, "2026-10-12 10:00:00", descricao="Salário")            # id 3, receita
        transacao(conn, "Lazer", 50.0, "2026-10-01 10:00:00")                                   # id 4, descrição NULL
        transacao(conn, "Lazer", 51.0, "2026-10-02 10:00:00", descricao="")                     # id 5, vazia
        transacao(conn, "Lazer", 52.0, "2026-10-03 10:00:00", descricao="   ")                  # id 6, só espaços
        conn.commit()
        self.iniciar(conn)
        self.assertEqual(self.obter("/gastos/1").status_code, 200)
        casos = {"inexistente": 12345, "de outro usuário": 2, "receita": 3,
                 "sem descrição (NULL)": 4, "sem descrição (vazia)": 5, "sem descrição (só espaços)": 6}
        for nome, id_gasto in casos.items():
            with self.subTest(caso=nome):
                resposta = self.obter(f"/gastos/{id_gasto}")
                self.assertEqual(resposta.status_code, 404)
                # O mesmo detail em todos: a resposta não diz qual foi o caso.
                self.assertEqual(resposta.json(), {"detail": "gasto_nao_encontrado"})

    def test_vinculo_cruzado_com_compra_de_outro_usuario_sai_como_a_vista(self):
        outro = 999
        conn = novo_banco()
        # Compra parcelada do outro usuário: parcelamento id 1 e transação id 1.
        parcelamento(conn, "Lazer", 600.0, 6, "2026-10-12 10:00:00", "2026-10", descricao="Compra alheia",
                     user_id=outro)
        # Gasto meu (id 2) apontando para a compra do outro usuário.
        transacao(conn, "Lazer", 20.0, "2026-10-09 10:00:00", descricao="Meu com vínculo errado",
                  parcelamento_id=1)
        conn.commit()
        self.iniciar(conn)
        gasto = self.obter("/gastos/2").json()
        self.assertEqual((gasto["parcelado"], gasto["total_parcelas"], gasto["valor_total"], gasto["parcelas_pagas"]),
                         (False, None, None, None))

    def test_id_que_nao_e_numero_da_422(self):
        self.iniciar(novo_banco())
        self.assertEqual(self.obter("/gastos/abc").status_code, 422)

    def test_gastos_recentes_continua_respondendo_igual(self):
        conn = novo_banco()
        transacao(conn, "Alimentação", 18.5, "2026-10-14 14:30:00", descricao="Padaria")
        conn.commit()
        self.iniciar(conn)
        # A rota nova ("/gastos/{id_gasto}") não captura "/gastos/recentes" (nem dá 422 de id).
        resposta = self.obter("/gastos/recentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"gastos": [{
            "id": 1, "descricao": "Padaria", "estabelecimento": None,
            "categoria": "Alimentação", "valor": 18.5,
            "data": "2026-10-14", "parcelado": False, "total_parcelas": None, "valor_total": None,
        }]})


class TestPaginasEstaticas(TesteRotasBase):
    def test_app_serve_a_home(self):
        self.iniciar(banco_completo())
        resposta = self.client.get("/app/")
        self.assertEqual(resposta.status_code, 200)
        # Título da lista desde a PRO-92 (antes: "Últimos gastos").
        self.assertIn("Últimas Movimentações", resposta.text)

    def test_teste_serve_a_pagina_de_diagnostico(self):
        self.iniciar(banco_completo())
        resposta = self.client.get("/teste/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Teste do initData", resposta.text)

    def test_paginas_pedem_revalidacao_ao_navegador(self):
        self.iniciar(banco_completo())
        for caminho in ("/app/", "/teste/"):
            resposta = self.client.get(caminho)
            self.assertEqual(resposta.headers.get("cache-control"), "no-cache", caminho)

    def test_pagina_sem_mudanca_responde_304_sem_cache(self):
        self.iniciar(banco_completo())
        primeira = self.client.get("/app/")
        etag = primeira.headers.get("etag")
        self.assertTrue(etag)
        segunda = self.client.get("/app/", headers={"If-None-Match": etag})
        self.assertEqual(segunda.status_code, 304)
        self.assertEqual(segunda.headers.get("cache-control"), "no-cache")


if __name__ == "__main__":
    unittest.main(verbosity=2)
