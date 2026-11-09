"""Testes dos gastos recorrentes pela API (PRO-87): o módulo recorrentes.py, a
coluna nova em gastos_recorrentes (migração do bot.py) e as rotas
GET /categorias, GET /recorrentes, POST /recorrentes, PUT /recorrentes/{id} e
DELETE /recorrentes/{id}.

Roda com `python testes_recorrentes.py`. Não usa .env, rede nem banco real:
reaproveita a base do testes_rotas_registro.py (initData assinado com o token
falso, SQLite em ARQUIVO temporário com uma conexão nova por requisição, as
esperas das novas tentativas sem dormir e o "agora" da API fixo em 06/11/2026
01:30 UTC = 05/11/2026 22:30 em Brasília). A migração do bot
(COLUNAS_ESPERADAS e migrar_colunas_faltantes, lidas do bot.py com `ast`, sem
importá-lo) roda sobre o banco de cada teste: o ESQUEMA compartilhado do
testes_rotas_leitura.py já tem a coluna nova, e um teste confere que a
migração não tem nada a criar nele (o esquema do teste não diverge do real).

Usuários da base: UID (fechamento 28; categorias Alimentação, Casa e Roupas),
OUTRO_UID (categoria Lazer), SEM_CADASTRO e INCOMPLETO (sem fechamento).
"""
import ast
import contextlib
import io
import logging
import os
import sqlite3
import sys
import threading
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

from fastapi.testclient import TestClient  # noqa: E402

import recorrentes  # noqa: E402
import registro  # noqa: E402
from api.main import app  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402
from testes_rotas_registro import (  # noqa: E402
    CHAVE, CHAVE_2, ERRO_BUSY, INCOMPLETO, MOMENTO_BRASILIA, OUTRO_UID, SEM_CADASTRO, UID,
    ConexaoEmbrulhada, PlanoDeFalhas, TesteRegistroBase, corpo_gasto, corpo_receita,
)

logging.getLogger("api").addHandler(logging.NullHandler())

CHAVE_3 = '0c9e7a52-1b3d-4f6a-a8e2-5d4c3b2a1f00'
# O banco como era antes da PRO-87: gastos_recorrentes sem o estabelecimento.
ESQUEMA_ANTES_DA_PRO87 = ESQUEMA.replace('ultimo_ciclo_cobrado TEXT,\n        estabelecimento TEXT)',
                                         'ultimo_ciclo_cobrado TEXT)')
assert ESQUEMA_ANTES_DA_PRO87 != ESQUEMA, "ESQUEMA do testes_rotas_leitura.py mudou: ajuste a troca acima"
CRIADO_PELO_CHAT = '2026-10-01 08:00:00'


def carregar_do_bot():
    """COLUNAS_ESPERADAS, TAMANHO_MAX_NOME_RECORRENTE e migrar_colunas_faltantes
    do bot.py, lidos com ast (importar o bot abriria conexão com o Turso)."""
    with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
        arvore = ast.parse(f.read())
    nomes = {'COLUNAS_ESPERADAS', 'TAMANHO_MAX_NOME_RECORRENTE', 'migrar_colunas_faltantes'}
    selecionados = [
        no for no in arvore.body
        if (isinstance(no, ast.FunctionDef) and no.name in nomes)
        or (isinstance(no, ast.Assign) and any(isinstance(alvo, ast.Name) and alvo.id in nomes
                                               for alvo in no.targets))
    ]
    ns = {}
    exec(compile(ast.Module(body=selecionados, type_ignores=[]), 'bot.py', 'exec'), ns)
    faltando = nomes - set(ns)
    if faltando:
        sys.exit(f"Não encontrei no bot.py: {sorted(faltando)}")
    return ns


BOT = carregar_do_bot()


def migrar(conn):
    """Roda a migração do bot (o aviso com emoji vai para um buffer: o console
    do Windows não o imprime) e faz o commit. Devolve o que foi impresso."""
    with contextlib.redirect_stdout(io.StringIO()) as saida:
        BOT['migrar_colunas_faltantes'](conn.cursor())
    conn.commit()
    return saida.getvalue()


def corpo_recorrente(**mudancas):
    corpo = {"chave": CHAVE, "dia_cobranca": 12, "valor": 117.8, "descricao": "Assinatura Spotify",
             "estabelecimento": "Spotify", "categoria": "Casa"}
    corpo.update(mudancas)
    return corpo


def corpo_edicao(**mudancas):
    corpo = corpo_recorrente(**mudancas)
    corpo.pop("chave", None)
    return corpo


# --- MIGRAÇÃO E CONSTANTES ---
class TestMigracaoEConstantes(unittest.TestCase):
    def test_colunas_esperadas_tem_o_estabelecimento_do_recorrente(self):
        self.assertIn(('estabelecimento', 'TEXT'), BOT['COLUNAS_ESPERADAS']['gastos_recorrentes'])

    def test_esquema_compartilhado_dos_testes_ja_tem_as_colunas_da_migracao(self):
        # O ESQUEMA do testes_rotas_leitura.py é o banco DEPOIS da migração
        # (testes_cadastro.py roda o iniciar_banco do bot sobre ele): a
        # migração não tem nada a criar nele.
        conn = sqlite3.connect(':memory:')
        conn.executescript(ESQUEMA)
        self.assertEqual(migrar(conn), '')
        conn.close()

    def test_migracao_cria_a_coluna_sem_mudar_as_linhas_antigas(self):
        conn = sqlite3.connect(':memory:')
        conn.executescript(ESQUEMA_ANTES_DA_PRO87)
        conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                     "criado_em, ultimo_ciclo_cobrado) VALUES (?, 'Casa', 'Internet', 99.9, 10, ?, '2026-10')",
                     (UID, CRIADO_PELO_CHAT))
        conn.commit()
        antes = conn.execute("SELECT * FROM gastos_recorrentes").fetchall()
        aviso = migrar(conn)
        self.assertIn("'estabelecimento' adicionada em 'gastos_recorrentes'", aviso)
        colunas = [linha[1] for linha in conn.execute("PRAGMA table_info(gastos_recorrentes)")]
        self.assertEqual(colunas[-1], 'estabelecimento')
        self.assertEqual(conn.execute("SELECT * FROM gastos_recorrentes").fetchall(),
                         [antes[0] + (None,)])
        # Rodar de novo (toda subida do bot) não faz nada.
        self.assertNotIn('gastos_recorrentes', migrar(conn))
        conn.close()

    def test_descricao_maxima_igual_a_do_bot(self):
        self.assertEqual(recorrentes.DESCRICAO_MAXIMA_RECORRENTE, 30)
        self.assertEqual(recorrentes.DESCRICAO_MAXIMA_RECORRENTE, BOT['TAMANHO_MAX_NOME_RECORRENTE'])

    def test_modulo_nao_importa_telegram_bot_dotenv_nem_libsql(self):
        with open(os.path.join(RAIZ, 'recorrentes.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split('.')[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(no.module.split('.')[0])
        self.assertEqual(importados, {'typing', 'cadastro', 'registro'})


# --- MÓDULO: VALIDAÇÃO ---
class TestValidarRecorrente(unittest.TestCase):
    CATEGORIAS = ['Alimentação', 'Casa']

    def validar(self, dia=12, valor=117.8, descricao="Assinatura Spotify", estabelecimento="Spotify",
                categoria="Casa"):
        return recorrentes.validar_recorrente(dia, valor, descricao, estabelecimento, categoria, self.CATEGORIAS)

    def assertCodigo(self, codigo, **campos):
        with self.assertRaises(ErroRegistro) as ctx:
            self.validar(**campos)
        self.assertEqual(ctx.exception.codigo, codigo)

    def test_pedido_valido_devolve_os_campos_limpos(self):
        self.assertEqual(self.validar(descricao="  Assinatura Spotify  ", estabelecimento="  Spotify "),
                         (12, 117.8, "Assinatura Spotify", "Spotify", "Casa"))
        self.assertEqual(self.validar(valor=10, estabelecimento=None), (12, 10.0, "Assinatura Spotify", None, "Casa"))

    def test_dia_invalido(self):
        for dia in (0, 32, -1, 12.0, 12.5, "12", True, False, None, [12]):
            with self.subTest(dia=dia):
                self.assertCodigo('dia_invalido', dia=dia)

    def test_dia_nas_pontas_passa(self):
        for dia in (1, 31):
            with self.subTest(dia=dia):
                self.assertEqual(self.validar(dia=dia).dia_cobranca, dia)

    def test_valor_invalido(self):
        for valor in (0, -5, 0.001, 1_000_000.01, float('nan'), float('inf'), "10", True, None):
            with self.subTest(valor=valor):
                self.assertCodigo('valor_invalido', valor=valor)

    def test_valor_arredondado_e_teto_passa(self):
        self.assertEqual(self.validar(valor=10.456).valor, 10.46)
        self.assertEqual(self.validar(valor=1_000_000).valor, 1_000_000.0)

    def test_descricao_invalida(self):
        for descricao in (None, "", "   ", "\t\n", 5, ["Spotify"]):
            with self.subTest(descricao=descricao):
                self.assertCodigo('descricao_invalida', descricao=descricao)

    def test_descricao_longa_conta_depois_de_aparar(self):
        self.assertCodigo('descricao_longa', descricao="x" * 31)
        self.assertEqual(self.validar(descricao="  " + "x" * 30 + "  ").descricao, "x" * 30)

    def test_estabelecimento(self):
        self.assertCodigo('estabelecimento_longo', estabelecimento="E" * 61)
        self.assertEqual(self.validar(estabelecimento="  " + "E" * 60 + " ").estabelecimento, "E" * 60)
        for vazio in ("", "   ", None):
            with self.subTest(estabelecimento=vazio):
                self.assertIsNone(self.validar(estabelecimento=vazio).estabelecimento)

    def test_categoria_nao_encontrada(self):
        for categoria in ("casa", "Casa ", "Lazer", "", None, 1):
            with self.subTest(categoria=categoria):
                self.assertCodigo('categoria_nao_encontrada', categoria=categoria)

    def test_ordem_da_validacao(self):
        tudo_errado = dict(dia=0, valor=0, descricao="", estabelecimento="E" * 61, categoria="Nenhuma")
        ordem = [('dia_invalido', 'dia', 5), ('valor_invalido', 'valor', 10.0),
                 ('descricao_invalida', 'descricao', "x" * 31), ('descricao_longa', 'descricao', "Ok"),
                 ('estabelecimento_longo', 'estabelecimento', None), ('categoria_nao_encontrada', 'categoria', "Casa")]
        for codigo, campo, valor_certo in ordem:
            with self.subTest(codigo=codigo):
                self.assertCodigo(codigo, **tudo_errado)
            tudo_errado[campo] = valor_certo
        self.validar(**tudo_errado)  # tudo certo: não levanta


# --- MÓDULO: NOME REPETIDO ---
class TestDescricaoRepetida(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.executescript(ESQUEMA)
        migrar(self.conn)
        for user_id, categoria, descricao in ((UID, 'Casa', 'Água'), (UID, 'Casa', '  Internet  '),
                                              (UID, 'Lazer', 'Netflix'), (OUTRO_UID, 'Casa', 'Luz')):
            self.conn.execute("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, "
                              "criado_em) VALUES (?, ?, ?, 10, 5, ?)", (user_id, categoria, descricao, CRIADO_PELO_CHAT))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def repetida(self, descricao, categoria='Casa', ignorar_id=None, user_id=UID):
        return recorrentes.tem_descricao_repetida(self.conn, user_id, categoria, descricao, ignorar_id)

    def test_maiusculas_e_minusculas_nao_diferenciam(self):
        self.assertTrue(self.repetida('água'))
        self.assertTrue(self.repetida('ÁGUA'))   # casefold ignora a caixa também fora do ASCII
        self.assertTrue(self.repetida('INTERNET'))

    def test_espacos_nas_pontas_da_gravada_nao_contam(self):
        self.assertTrue(self.repetida('Internet'))

    def test_acento_conta(self):
        self.assertFalse(self.repetida('Agua'))

    def test_outra_categoria_nao_conta(self):
        self.assertFalse(self.repetida('Netflix', categoria='Casa'))
        self.assertTrue(self.repetida('netflix', categoria='Lazer'))

    def test_outro_usuario_nao_conta(self):
        self.assertFalse(self.repetida('Luz'))
        self.assertFalse(self.repetida('Água', user_id=OUTRO_UID))

    def test_ignorar_o_proprio_item(self):
        self.assertFalse(self.repetida('Água', ignorar_id=1))
        self.assertTrue(self.repetida('Internet', ignorar_id=1))

    def test_descricao_nula_gravada_nao_quebra(self):
        self.conn.execute("UPDATE gastos_recorrentes SET descricao = NULL WHERE id = 1")
        self.assertFalse(self.repetida('Água'))


# --- ROTAS ---
class TesteRecorrentesBase(TesteRegistroBase):
    """A base das rotas de registro, com a migração do bot aplicada ao banco."""

    def setUp(self):
        super().setUp()
        conn = sqlite3.connect(self.caminho)
        migrar(conn)
        conn.close()

    def get(self, rota, user_id=UID, client=None):
        return (client or self.client).get(rota, headers=self.cabecalho(user_id))

    def put(self, rota, corpo, user_id=UID, client=None):
        return (client or self.client).put(rota, json=corpo, headers=self.cabecalho(user_id))

    def delete(self, rota, user_id=UID, client=None):
        return (client or self.client).delete(rota, headers=self.cabecalho(user_id))

    def criar(self, user_id=UID, client=None, **mudancas):
        return self.post("/recorrentes", corpo_recorrente(**mudancas), user_id=user_id, client=client)

    def inserir(self, descricao, categoria='Casa', user_id=UID, valor=50.0, dia=5, estabelecimento=None,
                criado_em=CRIADO_PELO_CHAT, ultimo_ciclo=None):
        """Recorrente gravado direto no banco (como o chat do bot grava)."""
        conn = sqlite3.connect(self.caminho)
        try:
            id_novo = conn.execute(
                "INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em, "
                "ultimo_ciclo_cobrado, estabelecimento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (user_id, categoria, descricao, valor, dia, criado_em, ultimo_ciclo, estabelecimento)).lastrowid
            conn.commit()
            return id_novo
        finally:
            conn.close()

    def linha(self, recorrente_id):
        linhas = self.ler("SELECT user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em, "
                          "ultimo_ciclo_cobrado, estabelecimento FROM gastos_recorrentes WHERE id = ?",
                          (recorrente_id,))
        return linhas[0] if linhas else None

    def tabela(self):
        return self.ler("SELECT * FROM gastos_recorrentes ORDER BY id")


ROTAS = (("GET", "/categorias", None), ("GET", "/recorrentes", None),
         ("POST", "/recorrentes", corpo_recorrente()), ("PUT", "/recorrentes/1", corpo_edicao()),
         ("DELETE", "/recorrentes/1", None))


class TestAutenticacaoECadastro(TesteRecorrentesBase):
    def pedir(self, metodo, rota, corpo, headers):
        if corpo is None:
            return self.client.request(metodo, rota, headers=headers)
        return self.client.request(metodo, rota, json=corpo, headers=headers)

    def test_sem_cabecalho_e_initdata_invalido_401(self):
        self.inserir('Internet')
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, {}), 401, "nao_autorizado")
                self.assertErro(self.pedir(metodo, rota, corpo, {"Authorization": "tma invalido"}),
                                401, "nao_autorizado")
        self.assertEqual(len(self.tabela()), 1)
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_usuario_nao_cadastrado_404_e_cadastro_incompleto_409(self):
        self.inserir('Internet')
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(SEM_CADASTRO)),
                                404, "usuario_nao_cadastrado")
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(INCOMPLETO)),
                                409, "cadastro_incompleto")
        self.assertEqual(len(self.tabela()), 1)

    def test_corpo_que_nao_e_json_422_padrao(self):
        rota_por_metodo = {"POST": "/recorrentes", "PUT": "/recorrentes/1"}
        self.inserir('Internet')
        for metodo, rota in rota_por_metodo.items():
            with self.subTest(metodo=metodo):
                resposta = self.client.request(metodo, rota, content=b"isto nao e json",
                                               headers={**self.cabecalho(), "Content-Type": "application/json"})
                self.assertEqual(resposta.status_code, 422)
                self.assertIsInstance(resposta.json()["detail"], list)

    def test_id_na_url_que_nao_e_inteiro_422_padrao(self):
        for resposta in (self.put("/recorrentes/abc", corpo_edicao()), self.delete("/recorrentes/1.5")):
            self.assertEqual(resposta.status_code, 422)
            self.assertIsInstance(resposta.json()["detail"], list)


class TestCategorias(TesteRecorrentesBase):
    def test_lista_do_usuario_na_ordem_da_listar_categorias(self):
        resposta = self.get("/categorias")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"categorias": ["Alimentação", "Casa", "Roupas"]})
        self.assertEqual(self.get("/categorias", user_id=OUTRO_UID).json(), {"categorias": ["Lazer"]})

    def test_erro_inesperado_500_sem_detalhes(self):
        client = TestClient(app, raise_server_exceptions=False)
        with mock.patch.object(registro, "listar_categorias", side_effect=ValueError("boom detalhe-do-banco")), \
                self.assertLogs("api", level="ERROR") as logs:
            resposta = self.get("/categorias", client=client)
        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta.json(), {"detail": "erro_interno"})
        self.assertIn("Recorrentes falhou: erro_interno (ValueError)", "\n".join(logs.output))
        self.assertNotIn("boom", "\n".join(logs.output))


class TestListar(TesteRecorrentesBase):
    def test_sem_nenhum(self):
        resposta = self.get("/recorrentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"recorrentes": [], "total_mensal": 0.0})
        self.assertIsInstance(resposta.json()["total_mensal"], float)

    def test_contrato_ordem_por_dia_e_id_total_e_so_do_usuario(self):
        self.inserir('Internet', dia=20, valor=99.9)
        self.inserir('Aluguel', dia=5, valor=1500.0, estabelecimento='Imobiliária')
        self.inserir('Luz', dia=20, valor=100.1)
        self.inserir('Cinema', categoria='Lazer', user_id=OUTRO_UID, dia=1, valor=40.0)
        resposta = self.get("/recorrentes")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {
            "recorrentes": [
                {"id": 2, "descricao": "Aluguel", "estabelecimento": "Imobiliária", "categoria": "Casa",
                 "valor": 1500.0, "dia_cobranca": 5},
                {"id": 1, "descricao": "Internet", "estabelecimento": None, "categoria": "Casa",
                 "valor": 99.9, "dia_cobranca": 20},
                {"id": 3, "descricao": "Luz", "estabelecimento": None, "categoria": "Casa",
                 "valor": 100.1, "dia_cobranca": 20},
            ],
            "total_mensal": 1700.0,  # 1500 + 99.9 + 100.1 (a soma em float é arredondada só na resposta)
        })
        self.assertEqual(self.get("/recorrentes", user_id=OUTRO_UID).json()["recorrentes"][0]["descricao"], "Cinema")

    def test_recorrente_de_categoria_apagada_aparece_com_o_nome_antigo(self):
        self.inserir('Revista', categoria='Apagada')
        self.assertEqual(self.get("/recorrentes").json()["recorrentes"][0]["categoria"], "Apagada")

    def test_erro_inesperado_500_sem_detalhes(self):
        client = TestClient(app, raise_server_exceptions=False)
        with mock.patch.object(recorrentes, "listar_recorrentes", side_effect=ValueError("boom")):
            resposta = self.get("/recorrentes", client=client)
        self.assertEqual(resposta.status_code, 500)
        self.assertEqual(resposta.json(), {"detail": "erro_interno"})


class TestCriar(TesteRecorrentesBase):
    def test_contrato_201_e_o_que_fica_gravado(self):
        resposta = self.criar()
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta.json(), {"id": 1, "descricao": "Assinatura Spotify", "estabelecimento": "Spotify",
                                           "categoria": "Casa", "valor": 117.8, "dia_cobranca": 12,
                                           "repetido": False})
        # criado_em do servidor, em Brasília; nada cobrado ainda (D7: não lança na hora).
        self.assertEqual(self.linha(1), (UID, 'Casa', 'Assinatura Spotify', 117.8, 12, MOMENTO_BRASILIA,
                                         None, 'Spotify'))
        self.assertEqual(self.ler("SELECT user_id, chave, tipo, lancamento_id, criado_em FROM chaves_registro"),
                         [(UID, CHAVE, 'recorrente', 1, MOMENTO_BRASILIA)])
        self.assertEqual(self.contar("transacoes"), 0)

    def test_textos_aparados_e_estabelecimento_vazio_vira_null(self):
        resposta = self.criar(descricao="  Academia  ", estabelecimento="   ", chave=CHAVE.upper())
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual((resposta.json()["descricao"], resposta.json()["estabelecimento"]), ("Academia", None))
        self.assertEqual(self.linha(1)[7], None)
        self.assertEqual(self.ler("SELECT chave FROM chaves_registro"), [(CHAVE,)])  # minúsculas

    def test_campos_a_mais_sao_ignorados(self):
        corpo = corpo_recorrente(user_id=OUTRO_UID, criado_em="2000-01-01 00:00:00",
                                 ultimo_ciclo_cobrado="2026-11", id=99)
        resposta = self.post("/recorrentes", corpo)
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json()["id"], 1)
        self.assertEqual(self.linha(1)[0], UID)
        self.assertEqual(self.linha(1)[5:7], (MOMENTO_BRASILIA, None))

    def test_codigos_de_validacao_sem_gravar(self):
        casos = [
            ('chave_invalida', dict(chave="nao-e-uuid")), ('chave_invalida', dict(chave=None)),
            ('dia_invalido', dict(dia_cobranca=0)), ('dia_invalido', dict(dia_cobranca=32)),
            ('dia_invalido', dict(dia_cobranca=12.0)), ('dia_invalido', dict(dia_cobranca="12")),
            ('dia_invalido', dict(dia_cobranca=True)), ('dia_invalido', dict(dia_cobranca=None)),
            ('valor_invalido', dict(valor=0)), ('valor_invalido', dict(valor=-10)),
            ('valor_invalido', dict(valor=1_000_000.01)), ('valor_invalido', dict(valor="117.8")),
            ('descricao_invalida', dict(descricao="   ")), ('descricao_invalida', dict(descricao=None)),
            ('descricao_invalida', dict(descricao=30)),
            ('descricao_longa', dict(descricao="x" * 31)),
            ('estabelecimento_longo', dict(estabelecimento="E" * 61)),
            ('categoria_nao_encontrada', dict(categoria="casa")),
            ('categoria_nao_encontrada', dict(categoria="Lazer")),  # categoria de outro usuário
            ('categoria_nao_encontrada', dict(categoria=None)),
        ]
        for codigo, mudancas in casos:
            with self.subTest(codigo=codigo, mudancas=mudancas):
                self.assertErro(self.criar(**mudancas), 422, codigo)
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (0, 0))

    def test_ordem_dos_422(self):
        corpo = dict(chave="x", dia_cobranca=0, valor=0, descricao="", estabelecimento="E" * 61, categoria="Nada")
        self.inserir("Repetida")
        ordem = [('chave_invalida', 'chave', CHAVE), ('dia_invalido', 'dia_cobranca', 1),
                 ('valor_invalido', 'valor', 10.0), ('descricao_invalida', 'descricao', "x" * 31),
                 ('descricao_longa', 'descricao', "repetida"), ('estabelecimento_longo', 'estabelecimento', None),
                 ('categoria_nao_encontrada', 'categoria', "Casa"), ('descricao_repetida', 'descricao', "Nova")]
        for codigo, campo, valor_certo in ordem:
            with self.subTest(codigo=codigo):
                self.assertErro(self.post("/recorrentes", corpo), 422, codigo)
            corpo[campo] = valor_certo
        self.assertEqual(self.post("/recorrentes", corpo).status_code, 201)

    def test_limites_aceitos(self):
        resposta = self.criar(dia_cobranca=31, valor=1_000_000, descricao="x" * 30, estabelecimento="E" * 60)
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.criar(chave=CHAVE_2, dia_cobranca=1, valor=0.01, descricao="y").status_code, 201)

    def test_toque_duplo_em_sequencia(self):
        primeira = self.criar()
        segunda = self.criar()
        self.assertEqual((primeira.status_code, segunda.status_code), (201, 200))
        self.assertEqual(segunda.json(), dict(primeira.json(), repetido=True))
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))

    def test_mesma_chave_com_outro_corpo_devolve_o_original_como_esta_agora(self):
        self.criar()
        self.assertEqual(self.put("/recorrentes/1", corpo_edicao(valor=130.0)).status_code, 200)
        resposta = self.criar(valor=999.0, categoria="Inexistente", dia_cobranca=0)
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual((resposta.json()["valor"], resposta.json()["categoria"], resposta.json()["repetido"]),
                         (130.0, "Casa", True))
        self.assertEqual(len(self.tabela()), 1)

    def test_reenvio_que_passa_da_consulta_bate_no_nome_e_devolve_repetido(self):
        """O reenvio do mesmo pedido depois de gravar, quando a consulta da
        chave antes de gravar não a acha (como no pedido que chega junto com o
        primeiro): a checagem do nome, DENTRO da gravação, levanta
        descricao_repetida; a gravar_com_chave desfaz, acha a chave e devolve
        200 repetido, nunca 422 descricao_repetida."""
        primeira = self.criar()
        original_buscar = registro.buscar_chave
        chamadas = [0]

        def buscar(conn, user_id, chave):
            chamadas[0] += 1
            return None if chamadas[0] == 1 else original_buscar(conn, user_id, chave)

        resultados = []
        original_repetida = recorrentes.tem_descricao_repetida

        def repetida(*args, **kwargs):
            resultados.append(original_repetida(*args, **kwargs))
            return resultados[-1]

        with mock.patch.object(registro, "buscar_chave", buscar), \
                mock.patch.object(recorrentes, "tem_descricao_repetida", repetida):
            segunda = self.criar()
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(segunda.json(), dict(primeira.json(), repetido=True))
        self.assertEqual(resultados, [True])  # passou mesmo pela checagem do nome
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (1, 1))

    def test_toque_duplo_em_duas_threads(self):
        """Os dois pedidos passam pela consulta da chave (nenhum acha nada)
        antes de qualquer um gravar: só a chave primária (ou o nome repetido,
        dentro da gravação) decide, e o perdedor devolve o original."""
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
        self.assertGreaterEqual(contagem[0], 3)  # o perdedor consultou a chave de novo depois do erro

    def test_chave_de_gasto_ou_de_receita_da_chave_invalida(self):
        self.assertEqual(self.post("/gastos", corpo_gasto(parcelas=1)).status_code, 201)
        self.assertEqual(self.post("/receitas", corpo_receita(chave=CHAVE_2)).status_code, 201)
        self.assertErro(self.criar(chave=CHAVE), 422, "chave_invalida")
        self.assertErro(self.criar(chave=CHAVE_2), 422, "chave_invalida")
        self.assertEqual(len(self.tabela()), 0)

    def test_chave_de_recorrente_nao_serve_para_gasto(self):
        self.criar()
        self.assertErro(self.post("/gastos", corpo_gasto(parcelas=1)), 422, "chave_invalida")
        self.assertEqual(self.contar("transacoes"), 0)

    def test_chave_de_recorrente_apagado_da_chave_invalida(self):
        self.criar()
        self.assertEqual(self.delete("/recorrentes/1").status_code, 200)
        self.assertErro(self.criar(), 422, "chave_invalida")
        self.assertEqual(len(self.tabela()), 0)

    def test_mesma_chave_de_outro_usuario_grava_separado(self):
        primeira = self.criar()
        outra = self.criar(user_id=OUTRO_UID, categoria="Lazer", descricao="Cinema")
        self.assertEqual(outra.status_code, 201)
        self.assertNotEqual(outra.json()["id"], primeira.json()["id"])
        self.assertEqual(self.ler("SELECT user_id, chave FROM chaves_registro ORDER BY user_id"),
                         [(UID, CHAVE), (OUTRO_UID, CHAVE)])

    def test_categoria_apagada_no_meio_da_gravacao(self):
        # A lista lida na validação ainda tem a categoria; dentro da gravação ela já não existe.
        with mock.patch.object(registro, "listar_categorias", return_value=["Fantasma"]):
            self.assertErro(self.criar(categoria="Fantasma"), 422, "categoria_nao_encontrada")
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (0, 0))


class TestNomeRepetido(TesteRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.agua = self.inserir('Água', categoria='Casa')
        self.internet = self.inserir('  Internet  ', categoria='Casa')  # como o chat pode ter gravado
        self.netflix = self.inserir('Netflix', categoria='Roupas')

    def test_post_recusa_o_mesmo_nome_na_mesma_categoria(self):
        for descricao in ('Água', 'água', 'ÁGUA', '  água  ', 'internet', 'INTERNET '):
            with self.subTest(descricao=descricao):
                self.assertErro(self.criar(descricao=descricao), 422, "descricao_repetida")
        self.assertEqual((len(self.tabela()), self.contar("chaves_registro")), (3, 0))

    def test_post_aceita_com_acento_diferente_e_em_outra_categoria(self):
        self.assertEqual(self.criar(descricao='Agua').status_code, 201)
        self.assertEqual(self.criar(chave=CHAVE_2, descricao='Netflix', categoria='Casa').status_code, 201)
        self.assertEqual(self.criar(chave=CHAVE_3, descricao='Água', categoria='Alimentação').status_code, 201)

    def test_post_de_outro_usuario_nao_conta(self):
        self.assertEqual(self.criar(user_id=OUTRO_UID, categoria='Lazer', descricao='Água').status_code, 201)

    def test_put_que_mantem_o_proprio_nome_passa(self):
        resposta = self.put(f"/recorrentes/{self.agua}", corpo_edicao(descricao='ÁGUA', valor=80.0))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["descricao"], 'ÁGUA')

    def test_put_com_o_nome_de_outro_da_mesma_categoria_recusa(self):
        self.assertErro(self.put(f"/recorrentes/{self.agua}", corpo_edicao(descricao='internet')),
                        422, "descricao_repetida")
        self.assertEqual(self.linha(self.agua)[2], 'Água')

    def test_put_que_troca_de_categoria_compara_com_a_nova(self):
        # Netflix (Roupas) indo para Casa com o nome "Água": repetido na nova categoria.
        self.assertErro(self.put(f"/recorrentes/{self.netflix}", corpo_edicao(descricao='Água', categoria='Casa')),
                        422, "descricao_repetida")
        # Água (Casa) indo para Roupas com o próprio nome: Roupas não tem "Água".
        resposta = self.put(f"/recorrentes/{self.agua}", corpo_edicao(descricao='Água', categoria='Roupas'))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        # Agora Netflix (Roupas) não pode virar "água" em Roupas.
        self.assertErro(self.put(f"/recorrentes/{self.netflix}", corpo_edicao(descricao='água', categoria='Roupas')),
                        422, "descricao_repetida")

    def test_put_recusado_por_nome_repetido_nao_altera_nada(self):
        # Pedido do Ygor no teste humano (passo 7): depois da recusa, a linha
        # inteira fica igual, inclusive criado_em e ultimo_ciclo_cobrado, mesmo
        # com os outros 4 campos mudando no pedido recusado.
        cobrado = self.inserir('Luz', categoria='Casa', valor=120.0, dia=10, estabelecimento='Enel',
                               ultimo_ciclo='2026-11')
        antes_linha = self.linha(cobrado)
        antes_tabela = self.tabela()
        resposta = self.put(f"/recorrentes/{cobrado}", corpo_edicao(
            descricao='ÁGUA', categoria='Casa', valor=999.99, dia_cobranca=28, estabelecimento='Outra'))
        self.assertErro(resposta, 422, "descricao_repetida")
        self.assertEqual(self.linha(cobrado), antes_linha)
        self.assertEqual(self.linha(cobrado)[5:7], (CRIADO_PELO_CHAT, '2026-11'))  # criado_em, ultimo_ciclo_cobrado
        self.assertEqual(self.tabela(), antes_tabela)
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_dois_iguais_criados_pelo_chat_put_sem_trocar_o_nome_recusa(self):
        outra_agua = self.inserir('água', categoria='Casa')
        self.assertErro(self.put(f"/recorrentes/{outra_agua}", corpo_edicao(descricao='água')),
                        422, "descricao_repetida")
        self.assertEqual(self.put(f"/recorrentes/{outra_agua}", corpo_edicao(descricao='Água 2')).status_code, 200)


class TestEditar(TesteRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.id = self.inserir('Internet', valor=99.9, dia=10, ultimo_ciclo='2026-11', estabelecimento='Vivo')
        self.alheio = self.inserir('Cinema', categoria='Lazer', user_id=OUTRO_UID)

    def test_contrato_e_os_5_campos_sem_mexer_no_ciclo_nem_no_criado_em(self):
        resposta = self.put(f"/recorrentes/{self.id}", corpo_edicao(
            dia_cobranca=15, valor=120.555, descricao="  Fibra  ", estabelecimento=" Claro ", categoria="Roupas",
            user_id=OUTRO_UID, criado_em="2000-01-01 00:00:00", ultimo_ciclo_cobrado=None, chave=CHAVE))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"id": self.id, "descricao": "Fibra", "estabelecimento": "Claro",
                                           "categoria": "Roupas", "valor": 120.56, "dia_cobranca": 15})
        self.assertEqual(self.linha(self.id), (UID, 'Roupas', 'Fibra', 120.56, 15, CRIADO_PELO_CHAT, '2026-11',
                                               'Claro'))
        self.assertEqual(self.contar("chaves_registro"), 0)  # o PUT não usa chave

    def test_estabelecimento_vazio_vira_null(self):
        resposta = self.put(f"/recorrentes/{self.id}", corpo_edicao(estabelecimento=""))
        self.assertIsNone(resposta.json()["estabelecimento"])
        self.assertIsNone(self.linha(self.id)[7])

    def test_mesmo_put_repetido_da_o_mesmo_resultado(self):
        primeira = self.put(f"/recorrentes/{self.id}", corpo_edicao())
        segunda = self.put(f"/recorrentes/{self.id}", corpo_edicao())
        self.assertEqual((primeira.status_code, segunda.status_code), (200, 200))
        self.assertEqual(primeira.json(), segunda.json())

    def test_nao_mexe_nas_transacoes_ja_lancadas(self):
        conn = sqlite3.connect(self.caminho)
        conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, descricao) "
                     "VALUES (?, 'Casa', 99.9, '2026-11-10 05:00:00', 'Internet')", (UID,))
        conn.commit()
        conn.close()
        self.put(f"/recorrentes/{self.id}", corpo_edicao(valor=150.0, categoria="Roupas", descricao="Fibra"))
        self.assertEqual(self.ler("SELECT categoria_nome, valor, descricao FROM transacoes"),
                         [('Casa', 99.9, 'Internet')])

    def test_codigos_de_validacao_sem_gravar(self):
        antes = self.tabela()
        casos = [('dia_invalido', dict(dia_cobranca=0)), ('dia_invalido', dict(dia_cobranca=12.0)),
                 ('valor_invalido', dict(valor=0)), ('valor_invalido', dict(valor=1_000_000.01)),
                 ('descricao_invalida', dict(descricao="")), ('descricao_longa', dict(descricao="x" * 31)),
                 ('estabelecimento_longo', dict(estabelecimento="E" * 61)),
                 ('categoria_nao_encontrada', dict(categoria="Lazer"))]
        for codigo, mudancas in casos:
            with self.subTest(codigo=codigo):
                self.assertErro(self.put(f"/recorrentes/{self.id}", corpo_edicao(**mudancas)), 422, codigo)
        self.assertEqual(self.tabela(), antes)

    def test_ordem_campos_depois_existencia_depois_nome_repetido(self):
        self.inserir('Luz')
        # Campo inválido num id que não existe: o 422 do campo vem antes do 404.
        self.assertErro(self.put("/recorrentes/999", corpo_edicao(dia_cobranca=0)), 422, "dia_invalido")
        self.assertErro(self.put("/recorrentes/999", corpo_edicao(categoria="Nada")), 422, "categoria_nao_encontrada")
        # Campos certos num id que não existe: 404, mesmo com um nome que seria repetido.
        self.assertErro(self.put("/recorrentes/999", corpo_edicao(descricao="Luz")), 404, "recorrente_nao_encontrado")
        # Existe, mas o nome repete na categoria: 422 descricao_repetida.
        self.assertErro(self.put(f"/recorrentes/{self.id}", corpo_edicao(descricao="Luz")), 422, "descricao_repetida")

    def test_id_de_outro_usuario_404_sem_mexer_em_nada(self):
        antes = self.tabela()
        self.assertErro(self.put(f"/recorrentes/{self.alheio}", corpo_edicao(categoria="Casa")),
                        404, "recorrente_nao_encontrado")
        # Nem com a categoria do dono o id alheio é encontrado (não diz que existe).
        self.assertErro(self.put(f"/recorrentes/{self.alheio}", corpo_edicao(categoria="Lazer")),
                        422, "categoria_nao_encontrada")
        self.assertEqual(self.tabela(), antes)

    def test_id_enorme_404(self):
        self.assertErro(self.put("/recorrentes/99999999999999999999", corpo_edicao()), 404,
                        "recorrente_nao_encontrado")
        self.assertErro(self.delete("/recorrentes/99999999999999999999"), 404, "recorrente_nao_encontrado")


class TestApagar(TesteRecorrentesBase):
    def setUp(self):
        super().setUp()
        self.id = self.inserir('Internet')
        self.alheio = self.inserir('Cinema', categoria='Lazer', user_id=OUTRO_UID)

    def test_contrato_e_404_no_repetido(self):
        resposta = self.delete(f"/recorrentes/{self.id}")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"id": self.id, "apagado": True})
        self.assertIsNone(self.linha(self.id))
        self.assertErro(self.delete(f"/recorrentes/{self.id}"), 404, "recorrente_nao_encontrado")
        self.assertErro(self.delete("/recorrentes/999"), 404, "recorrente_nao_encontrado")

    def test_id_de_outro_usuario_404_sem_apagar(self):
        self.assertErro(self.delete(f"/recorrentes/{self.alheio}"), 404, "recorrente_nao_encontrado")
        self.assertIsNotNone(self.linha(self.alheio))
        self.assertEqual(self.get("/recorrentes", user_id=OUTRO_UID).json()["recorrentes"][0]["id"], self.alheio)

    def test_transacoes_ja_lancadas_ficam(self):
        conn = sqlite3.connect(self.caminho)
        conn.execute("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, descricao) "
                     "VALUES (?, 'Casa', 50.0, '2026-11-05 05:00:00', 'Internet')", (UID,))
        conn.commit()
        conn.close()
        self.assertEqual(self.delete(f"/recorrentes/{self.id}").status_code, 200)
        self.assertEqual(self.contar("transacoes"), 1)


# --- BANCO OCUPADO E ERRO INESPERADO ---
class TestBancoOcupado(TesteRecorrentesBase):
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
        id_rec = self.inserir('Internet')
        self.planejar([ValueError(ERRO_BUSY)])
        resposta = self.put(f"/recorrentes/{id_rec}", corpo_edicao(valor=80.0))
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(self.linha(id_rec)[3], 80.0)
        self.planejar([sqlite3.OperationalError("database is locked")])
        self.assertEqual(self.delete(f"/recorrentes/{id_rec}").status_code, 200)
        self.assertIsNone(self.linha(id_rec))
        self.assertEqual(self.esperas_banco, [0.25, 0.25])

    def test_busy_em_todas_as_tentativas_503_banco_ocupado(self):
        id_rec = self.inserir('Internet')
        pedidos = (("POST", lambda: self.criar()),
                   ("PUT", lambda: self.put(f"/recorrentes/{id_rec}", corpo_edicao(valor=80.0))),
                   ("DELETE", lambda: self.delete(f"/recorrentes/{id_rec}")))
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
        id_rec = self.inserir('Internet')
        client = TestClient(app, raise_server_exceptions=False)
        pedidos = (("POST", lambda: self.criar(client=client)),
                   ("PUT", lambda: self.put(f"/recorrentes/{id_rec}", corpo_edicao(valor=80.0), client=client)),
                   ("DELETE", lambda: self.delete(f"/recorrentes/{id_rec}", client=client)))
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
                self.assertIn("Recorrentes falhou: erro_interno (ValueError)", saida_log)
                self.assertNotIn("detalhe-do-banco", saida_log)
                self.assertEqual(plano.commits, 1)
                self.assertEqual(self.esperas_banco, [])
        self.assertEqual(self.tabela(), antes)


# --- DA API AO JOB: O ESTABELECIMENTO CHEGA AO GASTO LANÇADO (D4) ---
class TestDaApiAoJob(TesteRecorrentesBase):
    def test_recorrente_criado_pela_api_e_lancado_com_o_estabelecimento(self):
        import testes_estabelecimento  # _lancar_item_recorrente real do bot.py (lido com ast)
        self.assertEqual(self.criar().status_code, 201)
        conn = sqlite3.connect(self.caminho)
        try:
            corpo = testes_estabelecimento.lancar_item_recorrente(conn, UID, 1, '2026-12')
        finally:
            conn.close()
        self.assertIn("Assinatura Spotify", corpo)
        self.assertEqual(self.ler("SELECT user_id, categoria_nome, valor, descricao, estabelecimento, metodo_pagamento "
                                  "FROM transacoes"), [(UID, 'Casa', 117.8, 'Assinatura Spotify', 'Spotify', None)])
        self.assertEqual(self.linha(1)[6], '2026-12')


if __name__ == "__main__":
    unittest.main(verbosity=2)
