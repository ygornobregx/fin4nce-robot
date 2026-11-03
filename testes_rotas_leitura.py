"""Testes das rotas de leitura da API: GET /resumo e GET /historico (PRO-76) e
GET /gastos/recentes (PRO-79; campo estabelecimento na PRO-81).

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
        descricao TEXT, parcelamento_id INTEGER, estabelecimento TEXT);
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


class TestPaginasEstaticas(TesteRotasBase):
    def test_app_serve_a_home(self):
        self.iniciar(banco_completo())
        resposta = self.client.get("/app/")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Últimos gastos", resposta.text)

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
