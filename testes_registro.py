"""Testes de unidade do registro.py (PRO-85): a interpretação do gasto pela IA
e a gravação do gasto, fora do bot. Desde a PRO-86, também a leitura, a
validação e a gravação da receita avulsa.

Roda com `python testes_registro.py`. Não usa .env, rede nem banco real: a IA
é falsa (registro.definir_cliente_ia), o asyncio.sleep não dorme e o banco é
um SQLite em arquivo temporário (para conferir o commit por uma segunda
conexão, como o bot e a API, que abrem conexões separadas).
"""
import ast
import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import registro  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402

UID = 1
OUTRO_UID = 2
MOMENTO = '2026-11-05 12:30:00'

# SHA-256 do prompt do G01 do testes_registro_caracterizacao.py (categorias
# Alimentação, Roupas, Lazer e Mercado; texto "pão na padaria cisne branco 12"),
# medido no bot.py antes da PRO-85. Se mudar, a IA passa a receber outro prompt.
SHA_PROMPT_G01 = '8553d7f93d30d64d0ac678ae0ac5b75256ac0eb6e73755c4e076e882a1d0642d'


class ErroFalsoDaIA(Exception):
    pass


class IAFalsa:
    """Grava o modelo e o prompt de cada chamada e devolve as respostas
    roteirizadas (texto) ou levanta as exceções da lista."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.models = self

    def generate_content(self, model, contents):
        self.chamadas.append((model, contents))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return SimpleNamespace(text=resposta)


class ComIAFalsa(unittest.TestCase):
    """Base: instala a IA falsa e um asyncio.sleep que só grava a espera."""

    def setUp(self):
        self.cliente_anterior = registro._cliente_ia
        self.esperas = []

        async def sleep_falso(segundos, result=None):
            self.esperas.append(segundos)
            return result

        patcher = mock.patch.object(registro.asyncio, 'sleep', sleep_falso)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        registro.definir_cliente_ia(self.cliente_anterior)

    def instalar(self, *respostas):
        ia = IAFalsa(*respostas)
        registro.definir_cliente_ia(ia)
        return ia


def criar_banco(caminho, com_transacoes=True):
    conn = sqlite3.connect(caminho)
    conn.executescript('''
        CREATE TABLE usuarios (user_id INTEGER PRIMARY KEY, nome TEXT, renda_mensal REAL,
            dia_fechamento INTEGER, horario_alerta TEXT, ultimo_reset TEXT);
        CREATE TABLE categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT,
            orcamento_mensal REAL, saldo_atual REAL, data_criacao TEXT);
        CREATE TABLE parcelamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            descricao TEXT, valor_total REAL, valor_parcela REAL, total_parcelas INTEGER,
            parcelas_pagas INTEGER DEFAULT 1, data_criacao TEXT, ativo INTEGER DEFAULT 1, ciclo_referencia TEXT);
        CREATE TABLE receitas (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            valor REAL NOT NULL CHECK (valor > 0), descricao TEXT, data_receita TEXT NOT NULL,
            origem TEXT NOT NULL DEFAULT 'avulsa', receita_recorrente_id INTEGER);
    ''')
    if com_transacoes:
        conn.execute('''CREATE TABLE transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
            categoria_nome TEXT, valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            metodo_pagamento TEXT, descricao TEXT, parcelamento_id INTEGER, estabelecimento TEXT)''')
    conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, 'Ygor', 5000, 29)",
                 (UID,))
    conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, 'Outro', 1000, 10)",
                 (OUTRO_UID,))
    for uid, nome in ((UID, 'Alimentação'), (UID, 'Roupas'), (OUTRO_UID, 'Lazer')):
        conn.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual) VALUES (?, ?, 0, 0)",
                     (uid, nome))
    conn.commit()
    return conn


class ComBanco(unittest.TestCase):
    """Base: banco SQLite em arquivo temporário; 'self.conn' grava e
    'self.ler()' consulta por outra conexão (só enxerga o que teve commit)."""
    COM_TRANSACOES = True

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.caminho = os.path.join(self.pasta.name, 'teste.db')
        self.conn = criar_banco(self.caminho, self.COM_TRANSACOES)

    def tearDown(self):
        self.conn.close()
        self.pasta.cleanup()

    def ler(self, sql, params=()):
        outra = sqlite3.connect(self.caminho)
        try:
            return outra.execute(sql, params).fetchall()
        finally:
            outra.close()


# --- IMPORT E DEPENDÊNCIAS ---
class TestImportLimpo(unittest.TestCase):
    def test_import_sem_env_sem_chave_nao_carrega_genai_nem_telegram(self):
        # Ambiente mínimo: só PATH e SYSTEMROOT (sem chave, sem .env). Não usar
        # `python -I`: ele tira a pasta atual do sys.path e o import falharia
        # por outro motivo.
        ambiente = {chave: os.environ[chave] for chave in ('PATH', 'SYSTEMROOT') if chave in os.environ}
        codigo = ("import os, registro, sys; "
                  "print('google.genai' in sys.modules, 'telegram' in sys.modules, "
                  "'GEMINI_API_KEY' in os.environ, 'dotenv' in sys.modules, 'libsql' in sys.modules)")
        resultado = subprocess.run([sys.executable, '-c', codigo], cwd=RAIZ, env=ambiente,
                                   capture_output=True, text=True, timeout=60)
        self.assertEqual(resultado.returncode, 0, resultado.stderr)
        self.assertEqual(resultado.stdout.strip(), "False False False False False")

    def test_imports_do_modulo(self):
        """Por AST: no topo, só biblioteca padrão e calculos; o google.genai
        só dentro de obter_cliente_ia; nada de telegram, bot, dotenv, libsql."""
        with open(os.path.join(RAIZ, 'registro.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())

        def modulos(nos):
            encontrados = []
            for no in nos:
                if isinstance(no, ast.Import):
                    encontrados += [a.name for a in no.names]
                elif isinstance(no, ast.ImportFrom):
                    encontrados.append(no.module)
            return encontrados

        topo = modulos(arvore.body)
        self.assertEqual(set(topo), {'asyncio', 'json', 're', 'datetime', 'typing', 'calculos'})

        todos = modulos(ast.walk(arvore))
        proibidos = [m for m in todos if m.split('.')[0] in ('telegram', 'bot', 'dotenv', 'libsql')]
        self.assertEqual(proibidos, [])

        funcao = next(no for no in arvore.body
                      if isinstance(no, ast.FunctionDef) and no.name == 'obter_cliente_ia')
        dentro = modulos(ast.walk(funcao))
        self.assertEqual(dentro, ['google'])
        self.assertEqual([m for m in todos if m.startswith('google')], ['google'])

    def test_modelo(self):
        self.assertEqual(registro.MODELO_IA, 'gemini-3.6-flash')


class TestObterClienteIA(unittest.TestCase):
    def setUp(self):
        self.cliente_anterior = registro._cliente_ia
        registro.definir_cliente_ia(None)

    def tearDown(self):
        registro.definir_cliente_ia(self.cliente_anterior)

    def test_cria_uma_vez_e_reaproveita(self):
        criados = []

        class ClienteFalso:
            def __init__(self):
                criados.append(self)

        genai_falso = types.ModuleType('google.genai')
        genai_falso.Client = ClienteFalso
        google_falso = types.ModuleType('google')
        google_falso.genai = genai_falso
        with mock.patch.dict(sys.modules, {'google': google_falso, 'google.genai': genai_falso}):
            primeiro = registro.obter_cliente_ia()
            segundo = registro.obter_cliente_ia()
        self.assertIs(primeiro, segundo)
        self.assertEqual(len(criados), 1)

    def test_definir_cliente_troca_o_cliente(self):
        falso = object()
        registro.definir_cliente_ia(falso)
        self.assertIs(registro.obter_cliente_ia(), falso)


# --- IA ---
class TestGerarConteudoComRetry(ComIAFalsa):
    def gerar(self):
        return asyncio.run(registro.gerar_conteudo_com_retry("prompt de teste"))

    def test_503_503_e_resposta(self):
        ia = self.instalar(ErroFalsoDaIA("503 UNAVAILABLE"), ErroFalsoDaIA("503 UNAVAILABLE"), "ok")
        self.assertEqual(self.gerar().text, "ok")
        self.assertEqual(len(ia.chamadas), 3)
        self.assertEqual(self.esperas, [2, 4])
        self.assertEqual({modelo for modelo, _ in ia.chamadas}, {'gemini-3.6-flash'})
        self.assertEqual({prompt for _, prompt in ia.chamadas}, {"prompt de teste"})

    def test_tres_429_a_excecao_sobe_igual(self):
        ultimo = ErroFalsoDaIA("429 RESOURCE_EXHAUSTED")
        ia = self.instalar(ErroFalsoDaIA("429"), ErroFalsoDaIA("429"), ultimo)
        with self.assertRaises(ErroFalsoDaIA) as erro:
            self.gerar()
        self.assertIs(erro.exception, ultimo)
        self.assertEqual(len(ia.chamadas), 3)
        self.assertEqual(self.esperas, [2, 4])

    def test_erro_comum_uma_chamada_sem_espera(self):
        ia = self.instalar(RuntimeError("Falha qualquer"))
        with self.assertRaises(RuntimeError) as erro:
            self.gerar()
        self.assertEqual(str(erro.exception), "Falha qualquer")
        self.assertEqual(len(ia.chamadas), 1)
        self.assertEqual(self.esperas, [])


class TestLimparJsonIA(unittest.TestCase):
    def test_cercas_de_codigo(self):
        self.assertEqual(registro.limpar_json_ia('```json\n{"a": 1}\n```'), {"a": 1})

    def test_texto_antes_e_depois(self):
        self.assertEqual(registro.limpar_json_ia('Aqui está: {"a": {"b": 2}} pronto.'), {"a": {"b": 2}})

    def test_lista(self):
        self.assertEqual(registro.limpar_json_ia('[{"a": 1}]'), [{"a": 1}])

    def test_json_invalido_levanta(self):
        with self.assertRaises(json.JSONDecodeError):
            registro.limpar_json_ia("Não entendi.")


class TestNormalizarEstabelecimento(unittest.TestCase):
    def test_apara_e_vazio_vira_none(self):
        self.assertEqual(registro.normalizar_estabelecimento("  iFood "), "iFood")
        for valor in ("", "  ", None, 12):
            with self.subTest(valor=valor):
                self.assertIsNone(registro.normalizar_estabelecimento(valor))


# --- INTERPRETAÇÃO ---
class TestMontarPrompt(unittest.TestCase):
    def test_lista_de_categorias_no_prompt(self):
        prompt = registro.montar_prompt_gasto(['Alimentação', 'Roupas'], "almoço 30")
        self.assertIn("[Alimentação, Roupas]", prompt)
        self.assertIn('Mensagem do usuário: "almoço 30"', prompt)

    def test_sem_categorias(self):
        prompt = registro.montar_prompt_gasto([], "almoço 30")
        self.assertIn("[nenhuma categoria cadastrada]", prompt)

    def test_prompt_byte_a_byte_igual_ao_de_antes(self):
        prompt = registro.montar_prompt_gasto(['Alimentação', 'Roupas', 'Lazer', 'Mercado'],
                                              "pão na padaria cisne branco 12")
        self.assertEqual(hashlib.sha256(prompt.encode('utf-8')).hexdigest(), SHA_PROMPT_G01)
        self.assertTrue(prompt.startswith("\n        Analise a mensagem de gasto/receita"))
        self.assertTrue(prompt.endswith("Retorne APENAS o JSON puro.\n        "))

    def test_formatar_lista(self):
        self.assertEqual(registro.formatar_lista_categorias(['A', 'B']), "A, B")
        self.assertEqual(registro.formatar_lista_categorias([]), "nenhuma categoria cadastrada")


class TestInterpretarGasto(ComIAFalsa):
    CATEGORIAS = ['Alimentação', 'Roupas']

    def interpretar(self, resposta, texto="texto do usuário"):
        ia = self.instalar(resposta)
        gasto = asyncio.run(registro.interpretar_gasto(texto, self.CATEGORIAS))
        return gasto, ia

    def test_despesa(self):
        gasto, ia = self.interpretar(json.dumps({
            "tipo": " Despesa", "valor": "12.5", "categoria": "Roupas",
            "descricao": "  Camisa ", "estabelecimento": " Renner "}))
        self.assertEqual(gasto, registro.GastoInterpretado('despesa', 12.5, 'Roupas', "  Camisa ", 'Renner', True))
        self.assertEqual(ia.chamadas[0][1], registro.montar_prompt_gasto(self.CATEGORIAS, "texto do usuário"))

    def test_receita_valor_invalido_vira_none_sem_erro(self):
        # Mudou de propósito na PRO-86: a receita passa a ler valor e descrição,
        # mas um valor que não é número vira None, sem levantar erro.
        gasto, _ = self.interpretar(json.dumps({"tipo": "Receita ", "valor": "abc"}))
        self.assertEqual(gasto, registro.GastoInterpretado('receita', None, None, None, None, False))

    def test_receita_le_valor_e_descricao_e_ignora_categoria(self):
        gasto, ia = self.interpretar(json.dumps({
            "tipo": "Receita", "valor": 500, "categoria": "Salário",
            "descricao": "  Venda da Bicicleta ", "estabelecimento": "OLX"}))
        self.assertEqual(gasto, registro.GastoInterpretado('receita', 500.0, None, 'Venda da Bicicleta', None, False))
        # O prompt é o mesmo do gasto (não muda com a PRO-86).
        self.assertEqual(ia.chamadas[0][1], registro.montar_prompt_gasto(self.CATEGORIAS, "texto do usuário"))

    def test_receita_sem_categoria_nem_descricao_nao_levanta(self):
        gasto, _ = self.interpretar(json.dumps({"tipo": "receita", "valor": "200.5"}))
        self.assertEqual(gasto, registro.GastoInterpretado('receita', 200.5, None, None, None, False))

    def test_receita_valores_lidos_sem_validar(self):
        # interpretar_gasto só lê: zero e negativo passam (quem valida é
        # validar_valor_receita); o que não é número vira None.
        casos = [(0, 0.0), (-200, -200.0), ("-200", -200.0), (None, None), (True, None),
                 ([500], None), ({"v": 1}, None), ("doze", None), (10 ** 400, None)]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                gasto, _ = self.interpretar(json.dumps({"tipo": "receita", "valor": valor}))
                self.assertEqual(gasto.valor, esperado)

    def test_receita_sem_valor_vira_none(self):
        gasto, _ = self.interpretar(json.dumps({"tipo": "receita", "descricao": "Reembolso"}))
        self.assertEqual(gasto, registro.GastoInterpretado('receita', None, None, 'Reembolso', None, False))

    def test_receita_descricao_vazia_ou_nao_texto_vira_none(self):
        for descricao in ("   ", "", 123, None):
            with self.subTest(descricao=descricao):
                gasto, _ = self.interpretar(json.dumps({"tipo": "receita", "valor": 10, "descricao": descricao}))
                self.assertIsNone(gasto.descricao)

    def test_outro_tipo_devolve_cedo_sem_ler_nada(self):
        gasto, _ = self.interpretar(json.dumps({"tipo": "Transferência", "valor": "abc"}))
        self.assertEqual(gasto, registro.GastoInterpretado('transferência', None, None, None, None, False))

    def test_categoria_minuscula_e_capitalizada(self):
        gasto, _ = self.interpretar(json.dumps({"tipo": "despesa", "valor": 10, "categoria": "alimentação"}))
        self.assertEqual(gasto.categoria, 'Alimentação')
        self.assertTrue(gasto.categoria_valida)
        self.assertEqual(gasto.descricao, '')
        self.assertIsNone(gasto.estabelecimento)

    def test_categoria_fora_da_lista(self):
        gasto, _ = self.interpretar(json.dumps({"tipo": "despesa", "valor": 10, "categoria": "transporte"}))
        self.assertEqual(gasto.categoria, 'Transporte')
        self.assertFalse(gasto.categoria_valida)

    def test_keyerror_sobe_sem_embrulho(self):
        with self.assertRaises(KeyError) as erro:
            self.interpretar(json.dumps({"tipo": "despesa", "categoria": "Roupas"}))
        self.assertEqual(str(erro.exception), "'valor'")

    def test_valor_invalido_sobe_antes_da_categoria(self):
        with self.assertRaises(ValueError) as erro:
            self.interpretar(json.dumps({"tipo": "despesa", "valor": "doze", "categoria": "Inexistente"}))
        self.assertEqual(str(erro.exception), "could not convert string to float: 'doze'")


# --- GRAVAÇÃO ---
COLUNAS_TRANSACAO = ('user_id, categoria_nome, valor, data_transacao, metodo_pagamento, '
                     'descricao, parcelamento_id, estabelecimento')


class TestListarCategorias(ComBanco):
    def test_so_do_usuario_na_ordem_do_banco(self):
        self.assertEqual(registro.listar_categorias(self.conn, UID), ['Alimentação', 'Roupas'])
        self.assertEqual(registro.listar_categorias(self.conn, 99), [])


class TestRegistrarGastoAvulso(ComBanco):
    def test_grava_e_faz_commit(self):
        lancamento = registro.registrar_gasto_avulso(self.conn, UID, 'Roupas', 59.9, MOMENTO, 'Débito/Pix',
                                                     descricao="  Camisa  ", estabelecimento=" Renner ")
        self.assertEqual(lancamento, (2, 1))
        self.assertEqual(self.ler(f"SELECT {COLUNAS_TRANSACAO} FROM transacoes"),
                         [(UID, 'Roupas', 59.9, MOMENTO, 'Débito/Pix', 'Camisa', None, 'Renner')])

    def test_categoria_inexistente_devolve_none_sem_gravar(self):
        self.assertIsNone(registro.registrar_gasto_avulso(self.conn, UID, 'Lazer', 10.0, MOMENTO, 'Crédito'))
        self.assertEqual(self.ler("SELECT COUNT(*) FROM transacoes"), [(0,)])


class TestRegistrarGastoParcelado(ComBanco):
    def test_grava_parcelamento_e_parcela_1_com_commit(self):
        resultado = registro.registrar_gasto_parcelado(self.conn, UID, 'Roupas', 100.0, 7, MOMENTO, 'Crédito',
                                                       descricao="  Calça  ", estabelecimento="Renner")
        self.assertEqual(resultado, registro.ParceladoRegistrado(1, 1, 14.29, '2026-11'))
        self.assertEqual(
            self.ler("SELECT user_id, categoria_nome, descricao, valor_total, valor_parcela, total_parcelas, "
                     "parcelas_pagas, data_criacao, ativo, ciclo_referencia FROM parcelamentos"),
            [(UID, 'Roupas', "  Calça  ", 100.0, 14.29, 7, 1, MOMENTO, 1, '2026-11')])
        self.assertEqual(self.ler(f"SELECT {COLUNAS_TRANSACAO} FROM transacoes"),
                         [(UID, 'Roupas', 14.29, MOMENTO, 'Crédito', 'Calça', 1, 'Renner')])

    def test_no_dia_do_fechamento_vai_para_a_fatura_seguinte(self):
        resultado = registro.registrar_gasto_parcelado(self.conn, UID, 'Roupas', 80.0, 2, '2026-11-29 10:00:00')
        self.assertEqual(resultado.ciclo_referencia, '2026-12')
        self.assertEqual(resultado.valor_parcela, 40.0)

    def test_categoria_inexistente_rollback_e_none(self):
        self.assertIsNone(registro.registrar_gasto_parcelado(self.conn, UID, 'Lazer', 300.0, 3, MOMENTO))
        self.assertEqual(self.ler("SELECT COUNT(*) FROM parcelamentos"), [(0,)])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM transacoes"), [(0,)])
        self.assertFalse(self.conn.in_transaction)


class TestRegistrarGastoParceladoComErro(ComBanco):
    COM_TRANSACOES = False  # o INSERT da parcela 1 falha depois do INSERT do parcelamento

    def test_erro_no_meio_desfaz_tudo_e_a_excecao_sobe(self):
        with self.assertRaises(sqlite3.OperationalError):
            registro.registrar_gasto_parcelado(self.conn, UID, 'Roupas', 300.0, 3, MOMENTO)
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM parcelamentos"), [(0,)])


# --- RECEITA AVULSA (PRO-86) ---
class TestValidarValorReceita(unittest.TestCase):
    def test_aceitos_arredondados_a_duas_casas(self):
        # round() do Python sobre o valor binário do float: 10.005 é guardado
        # como 10.00500000000000078... e vira 10.01; 2.675 é guardado como
        # 2.67499999999999982... e vira 2.67.
        casos = [(500, 500.0), (500.0, 500.0), (0.01, 0.01), (1234.567, 1234.57),
                 (10.005, 10.01), (2.675, 2.67), (10.006, 10.01), (1e12, 1e12)]
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.assertEqual(registro.validar_valor_receita(valor), esperado)
                self.assertIsInstance(registro.validar_valor_receita(valor), float)

    def test_recusados(self):
        casos = [0, 0.0, -0.0, -5, -200.0, 0.004, "500", "abc", None, True, False,
                 float('nan'), float('inf'), float('-inf'), 10 ** 400, [500]]
        for valor in casos:
            with self.subTest(valor=valor):
                self.assertIsNone(registro.validar_valor_receita(valor))


class TestRegistrarReceita(ComBanco):
    COLUNAS = 'user_id, valor, descricao, data_receita, origem, receita_recorrente_id'

    def receitas(self):
        return self.ler(f"SELECT {self.COLUNAS} FROM receitas ORDER BY id")

    def test_grava_faz_commit_e_nao_toca_em_transacoes(self):
        receita_id = registro.registrar_receita(self.conn, UID, 500, MOMENTO, "  Venda da Bicicleta ")
        self.assertEqual(receita_id, 1)
        self.assertEqual(self.receitas(), [(UID, 500.0, 'Venda da Bicicleta', MOMENTO, 'avulsa', None)])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM transacoes"), [(0,)])
        self.assertFalse(self.conn.in_transaction)

    def test_descricao_vazia_ou_ausente_vira_null(self):
        registro.registrar_receita(self.conn, UID, 10.0, MOMENTO, "   ")
        registro.registrar_receita(self.conn, UID, 20.0, MOMENTO)
        self.assertEqual([linha[2] for linha in self.receitas()], [None, None])

    def test_valor_arredondado_e_origem(self):
        receita_id = registro.registrar_receita(self.conn, OUTRO_UID, 10.006, MOMENTO, "Salário", origem='recorrente')
        self.assertEqual(receita_id, 1)
        self.assertEqual(self.receitas(), [(OUTRO_UID, 10.01, 'Salário', MOMENTO, 'recorrente', None)])

    def test_valor_invalido_levanta_value_error_sem_gravar(self):
        for valor in (0, -200, "500", "abc", None, True, float('nan'), float('inf')):
            with self.subTest(valor=valor):
                with self.assertRaises(ValueError):
                    registro.registrar_receita(self.conn, UID, valor, MOMENTO, "X")
                self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM receitas"), [(0,)])

    def test_banco_recusa_sem_dono_ou_sem_data_desfaz_e_a_excecao_sobe(self):
        for user_id, momento in ((None, MOMENTO), (UID, None)):
            with self.subTest(user_id=user_id, momento=momento):
                with self.assertRaises(sqlite3.IntegrityError):
                    registro.registrar_receita(self.conn, user_id, 10.0, momento)
                self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.ler("SELECT COUNT(*) FROM receitas"), [(0,)])

    def test_nao_fecha_a_conexao(self):
        registro.registrar_receita(self.conn, UID, 1.0, MOMENTO)
        registro.registrar_receita(self.conn, UID, 2.0, MOMENTO)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM receitas").fetchone(), (2,))


# --- EDIÇÃO DO ÚLTIMO GASTO ---
class TestEdicao(ComBanco):
    def setUp(self):
        super().setUp()
        self.conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
                          "VALUES (?, 'Roupas', 50.0, ?)", (UID, MOMENTO))
        self.conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
                          "VALUES (?, 'Lazer', 70.0, ?)", (OUTRO_UID, MOMENTO))
        self.conn.commit()

    def transacoes(self):
        return self.ler("SELECT id, user_id, categoria_nome, valor FROM transacoes ORDER BY id")

    def test_buscar_lancamento(self):
        self.assertEqual(registro.buscar_lancamento(self.conn, UID, 1), (50.0, 'Roupas'))
        self.assertIsNone(registro.buscar_lancamento(self.conn, UID, 2))
        self.assertIsNone(registro.buscar_lancamento(self.conn, OUTRO_UID, 1))
        self.assertIsNone(registro.buscar_lancamento(self.conn, UID, 99))

    def test_alterar_valor_so_do_proprio_usuario(self):
        self.assertTrue(registro.alterar_valor_lancamento(self.conn, UID, 1, 13.5))
        self.assertFalse(registro.alterar_valor_lancamento(self.conn, UID, 2, 1.0))
        self.assertEqual(self.transacoes(), [(1, UID, 'Roupas', 13.5), (2, OUTRO_UID, 'Lazer', 70.0)])

    def test_mover_so_do_proprio_usuario(self):
        registro.mover_lancamento_de_categoria(self.conn, UID, 1, 'Alimentação')
        registro.mover_lancamento_de_categoria(self.conn, UID, 2, 'Alimentação')
        self.assertEqual(self.transacoes(), [(1, UID, 'Alimentação', 50.0), (2, OUTRO_UID, 'Lazer', 70.0)])


class TestMomentoAgora(unittest.TestCase):
    def test_converte_para_brasilia(self):
        self.assertEqual(registro.momento_agora(datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)),
                         '2026-11-05 12:30:00')
        self.assertEqual(registro.momento_agora(datetime(2026, 11, 29, 2, 0, 0, tzinfo=timezone.utc)),
                         '2026-11-28 23:00:00')

    def test_sem_fuso_e_tomado_como_brasilia(self):
        self.assertEqual(registro.momento_agora(datetime(2026, 11, 5, 12, 30, 0)), '2026-11-05 12:30:00')

    def test_padrao_e_agora_em_brasilia(self):
        antes = datetime.now(FUSO_BRASILIA).replace(tzinfo=None, microsecond=0)
        momento = datetime.strptime(registro.momento_agora(), '%Y-%m-%d %H:%M:%S')
        depois = datetime.now(FUSO_BRASILIA).replace(tzinfo=None)
        self.assertTrue(antes <= momento <= depois + timedelta(seconds=1), (antes, momento, depois))


if __name__ == "__main__":
    unittest.main(verbosity=2)
