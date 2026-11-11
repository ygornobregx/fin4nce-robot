"""Testes das categorias (PRO-104): o módulo categorias.py (nome, nome
repetido, limite, ranking do mês, criar com chave, renomear com a propagação do
nome e excluir), as rotas GET /categorias/ranking, POST /categorias,
PUT /categorias/{id} e DELETE /categorias/{id}, e os handlers do bot que
passaram a usar o módulo ("Nova Categoria", "Editar Nome" e "Excluir
Categoria" do chat).

Roda com `python testes_categorias.py`. Não usa .env, rede nem banco real:
  - antes do `import bot`, o load_dotenv vira no-op, o ambiente recebe valores
    vazios e o libsql.connect passa a levantar erro (como no
    testes_receitas_recorrentes.py): qualquer conexão real derruba o teste;
  - as rotas reaproveitam a base do testes_rotas_registro.py (initData
    assinado com o token falso, SQLite em ARQUIVO temporário com uma conexão
    nova por requisição, esperas sem dormir e o "agora" da API fixo em
    05/11/2026 22:30 em Brasília);
  - os handlers do bot rodam as funções REAIS do bot.py, com o
    obter_conexao_db trocado pelo mesmo SQLite em arquivo e as funções de
    mensagem do Telegram trocadas por versões que só guardam o texto.

Usuários da base (criar_banco do testes_rotas_registro.py): UID (renda
5.000,00, fechamento 28, fatura vigente 2026-11; categorias Alimentação, Casa
e Roupas), OUTRO_UID (renda 1.000,00, fechamento 10; categoria Lazer),
SEM_CADASTRO e INCOMPLETO.
"""
import ast
import asyncio
import logging
import os
import sqlite3
import sys
import unittest
from datetime import datetime
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

import bot  # noqa: E402
import cadastro  # noqa: E402
import categorias  # noqa: E402
import recorrentes  # noqa: E402
import registro  # noqa: E402
from api.main import STATUS_ERRO_REGISTRO, app, obter_agora  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402
from testes_rotas_registro import (  # noqa: E402
    CHAVE, CHAVE_2, ERRO_BUSY, INCOMPLETO, MOMENTO_BRASILIA, OUTRO_UID, SEM_CADASTRO, UID,
    ConexaoEmbrulhada, PlanoDeFalhas, TesteRegistroBase, comando_chaves_registro, corpo_gasto,
)

logging.getLogger("api").addHandler(logging.NullHandler())

CHAVE_3 = '0c9e7a52-1b3d-4f6a-a8e2-5d4c3b2a1f00'
CHAVE_4 = '7d1e2f3a-4b5c-4d6e-8f70-81a2b3c4d5e6'
CRIADA_ANTES = '2026-01-01 10:00:00'
TABELAS = ('usuarios', 'categorias', 'transacoes', 'parcelamentos', 'gastos_recorrentes', 'transferencias',
           'receitas', 'chaves_registro')


def novo_banco_memoria():
    """SQLite em memória com o esquema compartilhado e a chaves_registro do
    bot.py, e dois usuários cadastrados (UID e OUTRO_UID)."""
    conn = sqlite3.connect(':memory:')
    conn.executescript(ESQUEMA)
    conn.execute(comando_chaves_registro())
    for user_id, dia in ((UID, 28), (OUTRO_UID, 10)):
        conn.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento) VALUES (?, 'X', 0, ?)",
                     (user_id, dia))
    conn.commit()
    return conn


def inserir_categoria(conn, nome, user_id=UID, data_criacao=CRIADA_ANTES):
    id_novo = conn.execute(
        "INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) VALUES (?, ?, 0, 0, ?)",
        (user_id, nome, data_criacao)).lastrowid
    conn.commit()
    return id_novo


# =============================================================================
# MÓDULO, CONSTANTES E O QUE SAIU DO BOT.PY
# =============================================================================
class TestConstantesEModulo(unittest.TestCase):
    def test_constantes(self):
        self.assertEqual(categorias.LIMITE_CATEGORIAS, 20)
        self.assertEqual(categorias.LIMITE_CATEGORIAS, cadastro.CATEGORIAS_MAXIMAS)
        self.assertEqual(categorias.NOME_MAXIMO, 30)
        self.assertEqual(categorias.NOME_MAXIMO, cadastro.CATEGORIA_MAXIMA)
        self.assertEqual(categorias.TIPO_CHAVE, 'categoria')

    def test_status_dos_codigos(self):
        self.assertEqual(STATUS_ERRO_REGISTRO['categoria_inexistente'], 404)
        # O código do registro do gasto continua 422 (não entra no dicionário).
        self.assertNotIn('categoria_nao_encontrada', STATUS_ERRO_REGISTRO)
        for codigo in ('nome_invalido', 'nome_repetido', 'limite_categorias', 'chave_invalida',
                       'categoria_com_recorrentes', 'ultima_categoria'):
            self.assertNotIn(codigo, STATUS_ERRO_REGISTRO)

    def test_modulo_sem_telegram_bot_dotenv_nem_libsql(self):
        with open(os.path.join(RAIZ, 'categorias.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split('.')[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(no.module.split('.')[0])
        self.assertEqual(importados, {'typing', 'cadastro', 'recorrentes', 'registro', 'calculos'})

    def test_referencias_com_as_5_colunas(self):
        self.assertEqual(categorias._REFERENCIAS_NOME_CATEGORIA, (
            ('transacoes', 'categoria_nome'),
            ('parcelamentos', 'categoria_nome'),
            ('gastos_recorrentes', 'categoria_nome'),
            ('transferencias', 'categoria_origem'),
            ('transferencias', 'categoria_destino'),
        ))

    def test_bot_usa_as_funcoes_do_categorias(self):
        self.assertIs(bot.renomear_categoria, categorias.renomear_categoria)
        self.assertIs(bot._REFERENCIAS_NOME_CATEGORIA, categorias._REFERENCIAS_NOME_CATEGORIA)
        self.assertIs(bot.criar_categoria, categorias.criar_categoria)
        self.assertIs(bot.excluir_categoria, categorias.excluir_categoria)

    def test_bot_nao_define_mais_o_renomear_nem_grava_categoria_direto(self):
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            fonte = f.read()
        arvore = ast.parse(fonte)
        funcoes = {no.name for no in ast.walk(arvore) if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertNotIn('renomear_categoria', funcoes)
        atribuidos = {alvo.id for no in arvore.body if isinstance(no, ast.Assign)
                      for alvo in no.targets if isinstance(alvo, ast.Name)}
        self.assertNotIn('_REFERENCIAS_NOME_CATEGORIA', atribuidos)
        # O DELETE sem user_id saiu, e o INSERT da Nova Categoria também.
        self.assertNotIn('DELETE FROM categorias', fonte)
        self.assertNotIn('INSERT INTO categorias', fonte)


# =============================================================================
# REGRAS DO NOME (D6)
# =============================================================================
class TestValidarNome(unittest.TestCase):
    def test_strip_e_capitalize(self):
        for digitado, gravado in (('pets', 'Pets'), ('  pets  ', 'Pets'), ('PETS', 'Pets'), ('pETS', 'Pets'),
                                  ('água', 'Água'), ('vale refeição', 'Vale refeição'), ('\tLazer\n', 'Lazer')):
            with self.subTest(digitado=digitado):
                self.assertEqual(categorias.validar_nome(digitado), gravado)

    def test_ate_30_caracteres_depois_do_strip(self):
        self.assertEqual(categorias.validar_nome('a' * 30), 'A' + 'a' * 29)
        self.assertEqual(categorias.validar_nome('   ' + 'a' * 30 + '   '), 'A' + 'a' * 29)
        with self.assertRaises(ErroRegistro) as ctx:
            categorias.validar_nome('a' * 31)
        self.assertEqual(ctx.exception.codigo, 'nome_invalido')

    def test_vazio_so_espacos_ou_nao_texto(self):
        for nome in ('', '   ', '\n', None, 12, 1.5, True, ['Pets'], {'nome': 'Pets'}, '́'):
            with self.subTest(nome=nome):
                with self.assertRaises(ErroRegistro) as ctx:
                    categorias.validar_nome(nome)
                self.assertEqual(ctx.exception.codigo, 'nome_invalido')


class TestNomeRepetido(unittest.TestCase):
    def setUp(self):
        self.conn = novo_banco_memoria()
        self.saude = inserir_categoria(self.conn, 'Saude')
        self.vale = inserir_categoria(self.conn, 'Vale refeição')
        inserir_categoria(self.conn, 'Mercado', user_id=OUTRO_UID)

    def tearDown(self):
        self.conn.close()

    def test_sem_caixa_sem_acento_e_espacos_juntos(self):
        for nome in ('Saude', 'saude', 'SAUDE', 'Saúde', 'SAÚDE', '  saúde ', 'Vale refeicao', 'VALE   REFEIÇÃO'):
            with self.subTest(nome=nome):
                self.assertTrue(categorias.tem_nome_repetido(self.conn, UID, nome))
        self.assertEqual(categorias.buscar_nome_repetido(self.conn, UID, 'Saúde'), 'Saude')
        for nome in ('Saudes', 'Sa ude', 'Vale', 'Mercado'):
            with self.subTest(nome=nome):
                self.assertFalse(categorias.tem_nome_repetido(self.conn, UID, nome))

    def test_ignorar_a_propria(self):
        self.assertFalse(categorias.tem_nome_repetido(self.conn, UID, 'Saúde', ignorar_id=self.saude))
        self.assertTrue(categorias.tem_nome_repetido(self.conn, UID, 'Saúde', ignorar_id=self.vale))

    def test_so_as_do_proprio_usuario(self):
        self.assertTrue(categorias.tem_nome_repetido(self.conn, OUTRO_UID, 'mercado'))
        self.assertFalse(categorias.tem_nome_repetido(self.conn, OUTRO_UID, 'Saude'))

    def test_regra_do_recorrente_nao_muda_o_acento_conta(self):
        # D6: "Saúde" é recusada como CATEGORIA quando "Saude" existe...
        with self.assertRaises(ErroRegistro) as ctx:
            categorias.criar_categoria(self.conn, UID, 'Saúde', MOMENTO_BRASILIA)
        self.assertEqual(ctx.exception.codigo, 'nome_repetido')
        # ...e os dois nomes continuam aceitos como RECORRENTES (recorrentes.py).
        recorrentes.criar_recorrente_api(self.conn, UID, CHAVE, 5, 10.0, 'Saude', None, 'Saude', MOMENTO_BRASILIA)
        self.assertFalse(recorrentes.tem_descricao_repetida(self.conn, UID, 'Saude', 'Saúde'))
        recorrentes.criar_recorrente_api(self.conn, UID, CHAVE_2, 5, 10.0, 'Saúde', None, 'Saude', MOMENTO_BRASILIA)
        self.assertEqual(
            self.conn.execute("SELECT descricao FROM gastos_recorrentes ORDER BY id").fetchall(),
            [('Saude',), ('Saúde',)])
        # A caixa continua sem contar no recorrente.
        self.assertTrue(recorrentes.tem_descricao_repetida(self.conn, UID, 'Saude', 'SAUDE'))


# =============================================================================
# MÓDULO: CRIAR, CHAVE E BANCO EM MEMÓRIA
# =============================================================================
class TestCriarNoModulo(unittest.TestCase):
    def setUp(self):
        self.conn = novo_banco_memoria()
        inserir_categoria(self.conn, 'Casa')

    def tearDown(self):
        self.conn.close()

    def test_criar_sem_chave_para_o_bot(self):
        categoria = categorias.criar_categoria(self.conn, UID, '  pets ', MOMENTO_BRASILIA)
        self.assertEqual(categoria.nome, 'Pets')
        self.assertEqual(
            self.conn.execute("SELECT user_id, nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                              "WHERE id = ?", (categoria.id,)).fetchall(),
            [(UID, 'Pets', 0.0, 0.0, MOMENTO_BRASILIA)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM chaves_registro").fetchone(), (0,))

    def test_toque_duplo_que_bate_no_nome_repetido_devolve_a_original(self):
        # O primeiro pedido gravou (categoria e chave) DEPOIS da pré-checagem
        # da chave do segundo: o segundo bate no nome repetido dentro do
        # 'gravar', e a gravar_com_chave acha a chave e devolve a original.
        primeira = categorias.criar_categoria_api(self.conn, UID, CHAVE, 'Pets', MOMENTO_BRASILIA)
        real = registro.buscar_chave
        chamadas = []

        def buscar_chave(conn, user_id, chave):
            chamadas.append(chave)
            return None if len(chamadas) == 1 else real(conn, user_id, chave)

        with mock.patch.object(registro, 'buscar_chave', buscar_chave):
            segunda = categorias.criar_categoria_api(self.conn, UID, CHAVE, 'Pets', MOMENTO_BRASILIA)
        self.assertEqual(segunda, categorias.Categoria(primeira.id, 'Pets', True))
        self.assertEqual(len(chamadas), 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM categorias WHERE nome = 'Pets'").fetchone(), (1,))

    def test_nome_repetido_com_outra_chave_nao_grava_a_chave(self):
        categorias.criar_categoria_api(self.conn, UID, CHAVE, 'Pets', MOMENTO_BRASILIA)
        with self.assertRaises(ErroRegistro) as ctx:
            categorias.criar_categoria_api(self.conn, UID, CHAVE_2, 'pets', MOMENTO_BRASILIA)
        self.assertEqual(ctx.exception.codigo, 'nome_repetido')
        self.assertEqual(self.conn.execute("SELECT chave FROM chaves_registro").fetchall(), [(CHAVE,)])


# =============================================================================
# ROTAS
# =============================================================================
class TesteCategoriasBase(TesteRegistroBase):
    """A base das rotas de registro, com atalhos para as rotas de categoria."""

    def get(self, rota, user_id=UID):
        return self.client.get(rota, headers=self.cabecalho(user_id))

    def put(self, rota, corpo, user_id=UID):
        return self.client.put(rota, json=corpo, headers=self.cabecalho(user_id))

    def delete(self, rota, user_id=UID):
        return self.client.delete(rota, headers=self.cabecalho(user_id))

    def criar(self, nome, chave=CHAVE, user_id=UID, **extra):
        return self.post("/categorias", dict({"nome": nome, "chave": chave}, **extra), user_id=user_id)

    def executar(self, sql, params=()):
        conn = sqlite3.connect(self.caminho)
        try:
            id_novo = conn.execute(sql, params).lastrowid
            conn.commit()
            return id_novo
        finally:
            conn.close()

    def inserir_categoria(self, nome, user_id=UID, data_criacao=CRIADA_ANTES):
        return self.executar(
            "INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
            "VALUES (?, ?, 0, 0, ?)", (user_id, nome, data_criacao))

    def id_de(self, nome, user_id=UID):
        return self.ler("SELECT id FROM categorias WHERE user_id = ? AND nome = ?", (user_id, nome))[0][0]

    def gasto(self, categoria, valor, data, user_id=UID):
        return self.executar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
                             "VALUES (?, ?, ?, ?)", (user_id, categoria, valor, data))

    def recorrente(self, categoria, descricao='Internet', user_id=UID):
        return self.executar(
            "INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em) "
            "VALUES (?, ?, ?, 99.9, 5, ?)", (user_id, categoria, descricao, CRIADA_ANTES))

    def foto(self):
        """O banco inteiro, tabela por tabela (para provar que nada mudou)."""
        return {tabela: self.ler(f"SELECT * FROM {tabela} ORDER BY rowid") for tabela in TABELAS}

    def foto_do_usuario(self, user_id):
        return {tabela: self.ler(f"SELECT * FROM {tabela} WHERE user_id = ? ORDER BY rowid", (user_id,))
                for tabela in TABELAS}

    def nomes(self, user_id=UID):
        return [nome for (nome,) in self.ler("SELECT nome FROM categorias WHERE user_id = ? ORDER BY id",
                                             (user_id,))]


ROTAS = (("GET", "/categorias/ranking", None), ("POST", "/categorias", {"nome": "Pets", "chave": CHAVE}),
         ("PUT", "/categorias/1", {"nome": "Lar"}), ("DELETE", "/categorias/1", None))


class TestAutenticacaoECadastro(TesteCategoriasBase):
    def pedir(self, metodo, rota, corpo, headers):
        if corpo is None:
            return self.client.request(metodo, rota, headers=headers)
        return self.client.request(metodo, rota, json=corpo, headers=headers)

    def test_sem_cabecalho_e_initdata_invalido_401(self):
        antes = self.foto()
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, {}), 401, "nao_autorizado")
                self.assertErro(self.pedir(metodo, rota, corpo, {"Authorization": "tma invalido"}),
                                401, "nao_autorizado")
        self.assertEqual(self.foto(), antes)

    def test_usuario_nao_cadastrado_404_e_cadastro_incompleto_409(self):
        antes = self.foto()
        for metodo, rota, corpo in ROTAS:
            with self.subTest(metodo=metodo, rota=rota):
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(SEM_CADASTRO)),
                                404, "usuario_nao_cadastrado")
                self.assertErro(self.pedir(metodo, rota, corpo, self.cabecalho(INCOMPLETO)),
                                409, "cadastro_incompleto")
        self.assertEqual(self.foto(), antes)

    def test_id_na_url_que_nao_e_inteiro_422_padrao(self):
        for resposta in (self.put("/categorias/abc", {"nome": "Lar"}), self.delete("/categorias/1.5")):
            self.assertEqual(resposta.status_code, 422)
            self.assertIsInstance(resposta.json()["detail"], list)

    def test_get_categorias_antigo_nao_muda(self):
        resposta = self.get("/categorias")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"categorias": ["Alimentação", "Casa", "Roupas"]})


class TestCriar(TesteCategoriasBase):
    def test_cria_com_strip_capitalize_e_grava_a_chave(self):
        resposta = self.criar("  pets ")
        self.assertEqual(resposta.status_code, 201, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo, {"id": corpo["id"], "nome": "Pets", "repetido": False})
        self.assertEqual(
            self.ler("SELECT user_id, nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias WHERE id = ?",
                     (corpo["id"],)),
            [(UID, 'Pets', 0.0, 0.0, MOMENTO_BRASILIA)])  # data_criacao em Brasília, não em UTC
        self.assertEqual(self.ler("SELECT user_id, chave, tipo, lancamento_id, criado_em FROM chaves_registro"),
                         [(UID, CHAVE, 'categoria', corpo["id"], MOMENTO_BRASILIA)])
        self.assertEqual(self.nomes(), ['Alimentação', 'Casa', 'Roupas', 'Pets'])

    def test_repetido_sem_caixa_sem_acento_e_sem_espacos(self):
        self.assertEqual(self.criar("pets").status_code, 201)
        for nome, chave in (("PETS", CHAVE_2), ("Pets ", CHAVE_3), ("Pêts", CHAVE_4), ("alimentacao", CHAVE_2),
                            ("CASA", CHAVE_3)):
            with self.subTest(nome=nome):
                self.assertErro(self.criar(nome, chave=chave), 422, "nome_repetido")
        self.assertEqual(self.nomes(), ['Alimentação', 'Casa', 'Roupas', 'Pets'])
        self.assertEqual(self.contar("chaves_registro"), 1)

    def test_saude_recusada_quando_saude_sem_acento_existe(self):
        self.inserir_categoria('Saude')
        self.assertErro(self.criar("Saúde"), 422, "nome_repetido")
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_nome_invalido(self):
        antes = self.foto()
        for nome in ("", "   ", None, 12, ["Pets"], "a" * 31):
            with self.subTest(nome=nome):
                self.assertErro(self.criar(nome), 422, "nome_invalido")
        self.assertErro(self.post("/categorias", {"chave": CHAVE}), 422, "nome_invalido")
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.criar("a" * 30, chave=CHAVE_2).status_code, 201)
        self.assertEqual(self.nomes(), ['Alimentação', 'Casa', 'Roupas', 'A' + 'a' * 29])

    def test_chave_invalida(self):
        antes = self.foto()
        for chave in (None, "", "abc", 123, CHAVE.replace('-4', '-1', 1)):
            with self.subTest(chave=chave):
                self.assertErro(self.criar("Pets", chave=chave), 422, "chave_invalida")
        self.assertEqual(self.foto(), antes)

    def test_mesma_chave_duas_vezes_cria_uma_so(self):
        primeira = self.criar("Pets")
        self.assertEqual(primeira.status_code, 201)
        segunda = self.criar("Pets")
        self.assertEqual(segunda.status_code, 200, segunda.text)
        self.assertEqual(segunda.json(), {"id": primeira.json()["id"], "nome": "Pets", "repetido": True})
        # Mesmo com outro nome no corpo: a chave manda (não valida nem grava).
        terceira = self.criar("Outra Coisa")
        self.assertEqual(terceira.status_code, 200)
        self.assertEqual(terceira.json(), {"id": primeira.json()["id"], "nome": "Pets", "repetido": True})
        self.assertEqual(self.nomes().count('Pets'), 1)
        self.assertNotIn('Outra coisa', self.nomes())
        self.assertEqual(self.contar("chaves_registro"), 1)

    def test_chave_repetida_devolve_a_categoria_como_esta_agora(self):
        criada = self.criar("Pets").json()
        self.assertEqual(self.put(f"/categorias/{criada['id']}", {"nome": "Bichos"}).status_code, 200)
        resposta = self.criar("Pets")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"id": criada["id"], "nome": "Bichos", "repetido": True})

    def test_chave_de_categoria_excluida_ou_de_outro_tipo(self):
        criada = self.criar("Pets").json()
        self.assertEqual(self.delete(f"/categorias/{criada['id']}").status_code, 200)
        self.assertErro(self.criar("Pets"), 422, "chave_invalida")
        # Chave já usada num gasto.
        self.assertEqual(self.post("/gastos", corpo_gasto(chave=CHAVE_2)).status_code, 201)
        self.assertErro(self.criar("Viagens", chave=CHAVE_2), 422, "chave_invalida")
        self.assertNotIn('Viagens', self.nomes())

    def test_chave_vale_por_usuario(self):
        minha = self.criar("Pets").json()
        do_outro = self.criar("Pets", user_id=OUTRO_UID)
        self.assertEqual(do_outro.status_code, 201)
        self.assertNotEqual(do_outro.json()["id"], minha["id"])
        self.assertEqual(self.nomes(OUTRO_UID), ['Lazer', 'Pets'])

    def test_campos_a_mais_sao_ignorados_e_nunca_gravados(self):
        outro_antes = self.foto_do_usuario(OUTRO_UID)
        colunas_antes = self.ler("PRAGMA table_info(categorias)")
        resposta = self.criar("Pets", descricao="Ração e banho", tipo="temporaria", temporaria=True,
                              user_id_extra=1, orcamento_mensal=500, saldo_atual=500,
                              data_criacao="2020-01-01 00:00:00", id=999)
        # O user_id no corpo também é ignorado (o dono vem só do initData).
        resposta_2 = self.post("/categorias", {"nome": "Viagens", "chave": CHAVE_2, "user_id": OUTRO_UID})
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(resposta_2.status_code, 201, resposta_2.text)
        self.assertEqual(set(resposta.json()), {"id", "nome", "repetido"})
        self.assertNotEqual(resposta.json()["id"], 999)
        self.assertEqual(
            self.ler("SELECT * FROM categorias WHERE nome IN ('Pets', 'Viagens') ORDER BY id"),
            [(resposta.json()["id"], UID, 'Pets', 0.0, 0.0, MOMENTO_BRASILIA),
             (resposta_2.json()["id"], UID, 'Viagens', 0.0, 0.0, MOMENTO_BRASILIA)])
        self.assertEqual(self.ler("PRAGMA table_info(categorias)"), colunas_antes)
        self.assertEqual(self.foto_do_usuario(OUTRO_UID), outro_antes)

    def test_limite_de_20(self):
        for i in range(16):
            self.inserir_categoria(f'Extra {i}')
        self.assertEqual(len(self.nomes()), 19)
        resposta = self.criar("Vigésima")
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(len(self.nomes()), 20)
        # A mesma chave de novo, já no limite: devolve a original, não o limite.
        self.assertEqual(self.criar("Vigésima").json(), dict(resposta.json(), repetido=True))
        antes = self.foto()
        self.assertErro(self.criar("Vigésima primeira", chave=CHAVE_2), 422, "limite_categorias")
        # No limite, o limite vem antes do nome repetido.
        self.assertErro(self.criar("Casa", chave=CHAVE_3), 422, "limite_categorias")
        self.assertEqual(self.foto(), antes)
        # O limite é por usuário.
        self.assertEqual(self.criar("Pets", chave=CHAVE_2, user_id=OUTRO_UID).status_code, 201)

    def test_commit_que_gravou_mas_chegou_como_erro(self):
        def gravou_e_deu_erro(conexao):
            conexao.real.commit()
            return ValueError('Hrana: stream error: conexão encerrada')

        plano = PlanoDeFalhas(falhas_commit=[gravou_e_deu_erro])
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=plano)
        resposta = self.criar("Pets")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["repetido"], True)
        # A tela reenvia o mesmo corpo: continua uma categoria só.
        self.assertEqual(self.criar("Pets").json(), resposta.json())
        self.assertEqual(self.nomes().count('Pets'), 1)

    def test_banco_ocupado(self):
        plano = PlanoDeFalhas(falhas_commit=[ValueError(ERRO_BUSY)])
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=plano)
        self.assertEqual(self.criar("Pets").status_code, 201)
        self.assertEqual(self.esperas_banco, [0.25])
        self.assertEqual(self.nomes().count('Pets'), 1)

        plano.falhas_commit = [ValueError(ERRO_BUSY)] * 4
        antes = self.foto()
        self.assertErro(self.criar("Viagens", chave=CHAVE_2), 503, "banco_ocupado")
        self.assertEqual(self.foto(), antes)

    def test_erro_inesperado_500_sem_detalhes(self):
        with mock.patch.object(categorias, '_inserir', side_effect=RuntimeError("SQL secreto")), \
                self.assertLogs("api", level="ERROR") as logs:
            resposta = self.criar("Pets")
        self.assertErro(resposta, 500, "erro_interno")
        self.assertNotIn("SQL secreto", "\n".join(logs.output))
        self.assertIn("Categorias falhou", "\n".join(logs.output))

    def test_log_leva_so_o_codigo(self):
        with self.assertLogs("api", level="WARNING") as logs:
            self.criar("Nome Secreto Muito Longo Com Mais De Trinta Letras")
        self.assertEqual(logs.output, ["WARNING:api:Registro recusado: nome_invalido"])


class TestRanking(TesteCategoriasBase):
    def resumo(self, user_id=UID):
        resposta = self.get("/resumo", user_id=user_id)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()

    def ranking(self, user_id=UID):
        resposta = self.get("/categorias/ranking", user_id=user_id)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()

    def conferir_com_o_resumo(self, user_id=UID):
        """ano_mes e total_gasto iguais aos do /resumo, e cada nome com o
        mesmo gasto do /resumo (as barras somam o total)."""
        ranking, resumo = self.ranking(user_id), self.resumo(user_id)
        self.assertEqual(ranking["ano_mes"], resumo["ano_mes"])
        self.assertEqual(ranking["total_gasto"], resumo["total_gasto"])
        self.assertEqual(sorted((c["nome"], c["gasto"]) for c in ranking["categorias"]),
                         sorted((c["nome"], c["gasto"]) for c in resumo["categorias"]))
        self.assertAlmostEqual(sum(c["gasto"] for c in ranking["categorias"]), ranking["total_gasto"], places=2)
        self.assertEqual(ranking["limite"], 20)
        return ranking

    def montar_cenario(self):
        # Fatura vigente de UID: 2026-11 (28/10 a 27/11).
        self.gasto('Casa', 120.0, '2026-11-02 10:00:00')
        self.gasto('Alimentação', 30.0, '2026-11-01 12:00:00')
        self.gasto('Alimentação', 20.0, '2026-10-28 00:00:00')  # primeiro dia da fatura
        self.inserir_categoria('Água')
        self.gasto('Água', 50.0, '2026-11-03 08:00:00')
        self.gasto('Pets', 20.0, '2026-11-04 09:00:00')         # categoria já excluída
        self.inserir_categoria('Bolsa', data_criacao=MOMENTO_BRASILIA)
        self.gasto('Casa', 999.0, '2026-10-27 23:59:00')       # fatura 2026-10: fora
        self.gasto('Roupas', -40.0, '2026-11-02 11:00:00')      # reembolso antigo: não conta
        self.gasto('Casa', 77.0, '2026-11-02 10:00:00', user_id=OUTRO_UID)

    def test_ordem_empate_sem_acento_e_excluida(self):
        self.montar_cenario()
        ranking = self.conferir_com_o_resumo()
        self.assertEqual(ranking, {
            "ano_mes": "2026-11",
            "total_gasto": 240.0,
            "categorias": [
                {"id": self.id_de('Casa'), "nome": "Casa", "gasto": 120.0, "ativa": True},
                # Empate em 50: "Água" fica no "A" e vem antes de "Alimentação".
                {"id": self.id_de('Água'), "nome": "Água", "gasto": 50.0, "ativa": True},
                {"id": self.id_de('Alimentação'), "nome": "Alimentação", "gasto": 50.0, "ativa": True},
                {"id": None, "nome": "Pets", "gasto": 20.0, "ativa": False},
                {"id": self.id_de('Bolsa'), "nome": "Bolsa", "gasto": 0.0, "ativa": True},
                {"id": self.id_de('Roupas'), "nome": "Roupas", "gasto": 0.0, "ativa": True},
            ],
            "limite": 20,
        })

    def test_sem_gasto_nenhum(self):
        ranking = self.conferir_com_o_resumo()
        self.assertEqual(ranking["total_gasto"], 0.0)
        self.assertEqual([c["nome"] for c in ranking["categorias"]], ['Alimentação', 'Casa', 'Roupas'])

    def test_com_parcelas_e_centavos(self):
        compra = self.executar(
            "INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, valor_parcela, "
            "total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo) "
            "VALUES (?, 'Roupas', 'Tênis', 300.0, 100.0, 3, 1, '2026-09-10 12:00:00', '2026-09', 1)", (UID,))
        self.executar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, parcelamento_id) "
                      "VALUES (?, 'Roupas', 100.0, '2026-09-10 12:00:00', ?)", (UID, compra))
        self.gasto('Casa', 0.1, '2026-11-01 10:00:00')
        self.gasto('Casa', 0.2, '2026-11-01 11:00:00')
        self.gasto('Alimentação', 0.3, '2026-11-01 12:00:00')
        ranking = self.conferir_com_o_resumo()
        # A 3ª parcela da compra de setembro cai em 2026-11.
        self.assertEqual(ranking["categorias"][0], {"id": self.id_de('Roupas'), "nome": "Roupas", "gasto": 100.0,
                                                    "ativa": True})
        # 0,1 + 0,2 e 0,3 empatam em centavos: vale a ordem alfabética.
        self.assertEqual([c["nome"] for c in ranking["categorias"][1:]], ['Alimentação', 'Casa'])

    def test_outro_usuario_e_outro_fechamento(self):
        self.montar_cenario()
        self.gasto('Lazer', 15.0, '2026-11-01 10:00:00', user_id=OUTRO_UID)
        ranking = self.conferir_com_o_resumo(OUTRO_UID)
        self.assertEqual(ranking["ano_mes"], "2026-11")
        self.assertEqual(ranking["categorias"], [
            {"id": None, "nome": "Casa", "gasto": 77.0, "ativa": False},  # só o Casa DELE (que não existe)
            {"id": self.id_de('Lazer', OUTRO_UID), "nome": "Lazer", "gasto": 15.0, "ativa": True},
        ])

    def test_fechamento_dia_5_no_dia_11_de_novembro_e_dezembro(self):
        # D4: com fechamento no dia 5, no dia 11/11 a fatura vigente é a de dezembro.
        self.executar("UPDATE usuarios SET dia_fechamento = 5 WHERE user_id = ?", (UID,))
        app.dependency_overrides[obter_agora] = lambda: datetime(2026, 11, 11, 12, 0, tzinfo=FUSO_BRASILIA)
        self.gasto('Casa', 10.0, '2026-11-04 10:00:00')   # fatura 2026-11: fora
        self.gasto('Casa', 25.0, '2026-11-05 10:00:00')   # fatura 2026-12
        ranking = self.conferir_com_o_resumo()
        self.assertEqual(ranking["ano_mes"], "2026-12")
        self.assertEqual(ranking["total_gasto"], 25.0)

    def test_nomes_repetidos_antigos_recebem_um_id_cada(self):
        segunda_casa = self.inserir_categoria('Casa')
        self.gasto('Casa', 10.0, '2026-11-01 10:00:00')
        ranking = self.conferir_com_o_resumo()
        casas = [c for c in ranking["categorias"] if c["nome"] == 'Casa']
        self.assertEqual([c["id"] for c in casas], [self.id_de('Casa'), segunda_casa])
        self.assertTrue(all(c["ativa"] for c in casas))

    def test_categoria_criada_agora_aparece_com_zero(self):
        criada = self.criar("Pets").json()
        self.gasto('Casa', 10.0, '2026-11-01 10:00:00')
        ranking = self.conferir_com_o_resumo()
        self.assertIn({"id": criada["id"], "nome": "Pets", "gasto": 0.0, "ativa": True}, ranking["categorias"])
        self.assertEqual([c["nome"] for c in ranking["categorias"]], ['Casa', 'Alimentação', 'Pets', 'Roupas'])

    def test_modulo_nao_faz_conta_de_gasto(self):
        # O ranking usa a calcular_resumo_fatura do calculos.py (nenhuma soma própria).
        with open(os.path.join(RAIZ, 'categorias.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertIn('calcular_resumo_fatura(conn, user_id, ano_mes, dia_fechamento)', fonte)
        self.assertNotIn('SUM(', fonte.upper())
        self.assertNotIn('FROM transacoes', fonte)


class TestRenomear(TesteCategoriasBase):
    def preencher_referencias(self, nome, user_id):
        """Uma linha em cada uma das 5 colunas de referência, com o nome."""
        self.gasto(nome, 10.0, '2026-11-01 10:00:00', user_id=user_id)
        self.executar("INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, valor_parcela, "
                      "total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo) "
                      "VALUES (?, ?, 'Sofá', 300.0, 100.0, 3, 1, '2026-11-01 10:00:00', '2026-11', 1)",
                      (user_id, nome))
        self.recorrente(nome, user_id=user_id)
        self.executar("INSERT INTO transferencias (user_id, categoria_origem, categoria_destino, valor, "
                      "data_transferencia) VALUES (?, ?, 'Roupas', 5.0, '2026-10-01')", (user_id, nome))
        self.executar("INSERT INTO transferencias (user_id, categoria_origem, categoria_destino, valor, "
                      "data_transferencia) VALUES (?, 'Roupas', ?, 5.0, '2026-10-01')", (user_id, nome))

    def referencias(self, user_id):
        return [self.ler(f"SELECT {coluna} FROM {tabela} WHERE user_id = ? ORDER BY id", (user_id,))
                for tabela, coluna in categorias._REFERENCIAS_NOME_CATEGORIA]

    def test_propaga_nas_5_colunas_so_do_usuario(self):
        self.preencher_referencias('Casa', UID)
        self.inserir_categoria('Casa', user_id=OUTRO_UID)
        self.preencher_referencias('Casa', OUTRO_UID)
        outro_antes = self.foto_do_usuario(OUTRO_UID)
        casa = self.id_de('Casa')
        resumo_antes = self.get("/resumo").json()
        gasto_antes = dict((c["nome"], c["gasto"]) for c in resumo_antes["categorias"])["Casa"]
        self.assertGreater(gasto_antes, 0)

        resposta = self.put(f"/categorias/{casa}", {"nome": "  lar "})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"id": casa, "nome": "Lar"})
        self.assertEqual(self.nomes(), ['Alimentação', 'Lar', 'Roupas'])
        self.assertEqual(self.referencias(UID), [
            [('Lar',)], [('Lar',)], [('Lar',)], [('Lar',), ('Roupas',)], [('Roupas',), ('Lar',)]])
        self.assertEqual(self.foto_do_usuario(OUTRO_UID), outro_antes)
        # O gasto vai junto: o total e o valor da categoria no ranking ficam iguais.
        ranking = self.get("/categorias/ranking").json()
        self.assertEqual(ranking["total_gasto"], resumo_antes["total_gasto"])
        self.assertIn({"id": casa, "nome": "Lar", "gasto": gasto_antes, "ativa": True}, ranking["categorias"])
        self.assertNotIn("Casa", [c["nome"] for c in ranking["categorias"]])

    def test_mesmo_nome_de_agora_nao_muda_nada(self):
        self.preencher_referencias('Casa', UID)
        antes = self.foto()
        casa = self.id_de('Casa')
        for nome in ("Casa", "casa", "  CASA  "):
            with self.subTest(nome=nome):
                resposta = self.put(f"/categorias/{casa}", {"nome": nome})
                self.assertEqual(resposta.status_code, 200)
                self.assertEqual(resposta.json(), {"id": casa, "nome": "Casa"})
        self.assertEqual(self.foto(), antes)

    def test_corrigir_so_caixa_ou_acento_da_propria(self):
        self.executar("UPDATE categorias SET nome = 'alimentacao' WHERE user_id = ? AND nome = 'Alimentação'",
                      (UID,))
        self.preencher_referencias('alimentacao', UID)
        alimentacao = self.id_de('alimentacao')
        resposta = self.put(f"/categorias/{alimentacao}", {"nome": "alimentação"})
        self.assertEqual(resposta.json(), {"id": alimentacao, "nome": "Alimentação"})
        self.assertEqual(self.referencias(UID)[0], [('Alimentação',)])

    def test_nome_de_outra_categoria(self):
        self.preencher_referencias('Casa', UID)
        antes = self.foto()
        casa = self.id_de('Casa')
        for nome in ("roupas", "Roupás", "ALIMENTACAO", "  alimentação "):
            with self.subTest(nome=nome):
                self.assertErro(self.put(f"/categorias/{casa}", {"nome": nome}), 422, "nome_repetido")
        self.assertEqual(self.foto(), antes)
        # O nome de uma categoria de OUTRO usuário não conta.
        self.assertEqual(self.put(f"/categorias/{casa}", {"nome": "Lazer"}).status_code, 200)

    def test_nome_invalido_antes_da_existencia(self):
        antes = self.foto()
        for nome in ("", "  ", None, 7, "a" * 31):
            with self.subTest(nome=nome):
                self.assertErro(self.put(f"/categorias/{self.id_de('Casa')}", {"nome": nome}), 422, "nome_invalido")
                self.assertErro(self.put("/categorias/99999", {"nome": nome}), 422, "nome_invalido")
        self.assertEqual(self.foto(), antes)

    def test_inexistente_ou_de_outro_usuario_404(self):
        self.preencher_referencias('Lazer', OUTRO_UID)
        antes = self.foto()
        lazer = self.id_de('Lazer', OUTRO_UID)
        for categoria_id in (lazer, 99999, 0, -1, 2 ** 63, 2 ** 70):
            with self.subTest(categoria_id=categoria_id):
                self.assertErro(self.put(f"/categorias/{categoria_id}", {"nome": "Meu"}), 404,
                                "categoria_inexistente")
        self.assertEqual(self.foto(), antes)

    def test_campos_a_mais_ignorados(self):
        casa = self.id_de('Casa')
        resposta = self.put(f"/categorias/{casa}", {"nome": "Lar", "user_id": OUTRO_UID, "id": 1, "so_daqui": True})
        self.assertEqual(resposta.json(), {"id": casa, "nome": "Lar"})

    def test_banco_ocupado(self):
        self.preencher_referencias('Casa', UID)
        casa = self.id_de('Casa')
        plano = PlanoDeFalhas(falhas_commit=[ValueError(ERRO_BUSY)] * 4)
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=plano)
        antes = self.foto()
        self.assertErro(self.put(f"/categorias/{casa}", {"nome": "Lar"}), 503, "banco_ocupado")
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.esperas_banco, [0.25, 0.5, 1.0])

        plano.falhas_commit = [ValueError(ERRO_BUSY)]
        self.assertEqual(self.put(f"/categorias/{casa}", {"nome": "Lar"}).json(), {"id": casa, "nome": "Lar"})
        self.assertEqual(self.referencias(UID)[0], [('Lar',)])


class TestExcluir(TesteCategoriasBase):
    def test_exclui_so_a_linha_e_os_gastos_ficam_no_total(self):
        self.gasto('Casa', 120.0, '2026-11-02 10:00:00')
        self.gasto('Roupas', 30.0, '2026-11-02 10:00:00')
        casa = self.id_de('Casa')
        resumo_antes = self.get("/resumo").json()
        antes = self.foto()

        resposta = self.delete(f"/categorias/{casa}")
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"id": casa, "excluida": True})
        self.assertEqual(self.nomes(), ['Alimentação', 'Roupas'])
        depois = self.foto()
        for tabela in TABELAS:
            if tabela != 'categorias':
                self.assertEqual(depois[tabela], antes[tabela], tabela)
        resumo_depois = self.get("/resumo").json()
        self.assertEqual(resumo_depois["total_gasto"], resumo_antes["total_gasto"])
        self.assertEqual(resumo_depois["total_gasto"], 150.0)
        ranking = self.get("/categorias/ranking").json()
        self.assertEqual(ranking["total_gasto"], 150.0)
        self.assertEqual(ranking["categorias"][0], {"id": None, "nome": "Casa", "gasto": 120.0, "ativa": False})

        # De novo: já excluída.
        self.assertErro(self.delete(f"/categorias/{casa}"), 404, "categoria_inexistente")

    def test_com_gasto_recorrente_e_recusado(self):
        self.recorrente('Casa')
        antes = self.foto()
        self.assertErro(self.delete(f"/categorias/{self.id_de('Casa')}"), 422, "categoria_com_recorrentes")
        self.assertEqual(self.foto(), antes)

    def test_recorrente_de_outro_usuario_nao_bloqueia(self):
        self.recorrente('Casa', user_id=OUTRO_UID)
        self.assertEqual(self.delete(f"/categorias/{self.id_de('Casa')}").status_code, 200)

    def test_ultima_categoria_e_recusada(self):
        antes = self.foto()
        self.assertErro(self.delete(f"/categorias/{self.id_de('Lazer', OUTRO_UID)}", user_id=OUTRO_UID),
                        422, "ultima_categoria")
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.delete(f"/categorias/{self.id_de('Casa')}").status_code, 200)
        self.assertEqual(self.delete(f"/categorias/{self.id_de('Roupas')}").status_code, 200)
        self.assertErro(self.delete(f"/categorias/{self.id_de('Alimentação')}"), 422, "ultima_categoria")
        self.assertEqual(self.nomes(), ['Alimentação'])

    def test_de_outro_usuario_ou_inexistente_404(self):
        self.inserir_categoria('Mercado', user_id=OUTRO_UID)
        antes = self.foto()
        for categoria_id in (self.id_de('Lazer', OUTRO_UID), self.id_de('Mercado', OUTRO_UID), 99999, 0,
                             2 ** 63, 2 ** 70):
            with self.subTest(categoria_id=categoria_id):
                self.assertErro(self.delete(f"/categorias/{categoria_id}"), 404, "categoria_inexistente")
        self.assertEqual(self.foto(), antes)

    def test_banco_ocupado(self):
        casa = self.id_de('Casa')
        plano = PlanoDeFalhas(falhas_commit=[ValueError(ERRO_BUSY)] * 4)
        self.embrulho = lambda conn: ConexaoEmbrulhada(conn, plano=plano)
        antes = self.foto()
        self.assertErro(self.delete(f"/categorias/{casa}"), 503, "banco_ocupado")
        self.assertEqual(self.foto(), antes)
        plano.falhas_commit = [ValueError(ERRO_BUSY)]
        self.assertEqual(self.delete(f"/categorias/{casa}").json(), {"id": casa, "excluida": True})


class TestDoisUsuarios(TesteCategoriasBase):
    def test_o_outro_nunca_mexe_nas_minhas(self):
        self.gasto('Casa', 50.0, '2026-11-01 10:00:00')
        self.recorrente('Alimentação')
        meus = self.foto_do_usuario(UID)
        casa = self.id_de('Casa')
        ranking_do_outro = self.get("/categorias/ranking", user_id=OUTRO_UID).json()
        self.assertNotIn(casa, [c["id"] for c in ranking_do_outro["categorias"]])
        self.assertErro(self.put(f"/categorias/{casa}", {"nome": "Do Outro"}, user_id=OUTRO_UID),
                        404, "categoria_inexistente")
        self.assertErro(self.delete(f"/categorias/{casa}", user_id=OUTRO_UID), 404, "categoria_inexistente")
        self.assertEqual(self.criar("Casa", user_id=OUTRO_UID).status_code, 201)  # o nome é por usuário
        self.assertEqual(self.foto_do_usuario(UID), meus)


# =============================================================================
# BOT (HANDLERS REAIS, TELEGRAM FALSO)
# =============================================================================
class Relogio(datetime):
    """datetime do bot congelado em 05/11/2026 22:30 de Brasília."""
    @classmethod
    def now(cls, tz=None):
        fixo = datetime(2026, 11, 5, 22, 30, tzinfo=FUSO_BRASILIA)
        return fixo.astimezone(tz) if tz is not None else fixo.replace(tzinfo=None)


class TesteBotBase(TesteCategoriasBase):
    """O banco da base, com o obter_conexao_db do bot apontando para ele e as
    mensagens do Telegram só guardadas (enviar_fluxo, atualizar_callback,
    mostrar_resumo_temporario e responder_callback)."""

    def setUp(self):
        super().setUp()
        self.fluxo, self.callbacks, self.resumos = [], [], []

        async def enviar_fluxo(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
            self.fluxo.append((chat_id, texto, reply_markup))

        async def atualizar_callback(query, context, texto, reply_markup=None, parse_mode='HTML'):
            self.callbacks.append((texto, reply_markup))

        async def mostrar_resumo_temporario(context, chat_id, texto, segundos=10, parse_mode='HTML'):
            self.resumos.append((chat_id, texto))

        async def responder_callback(query, texto=None):
            return None

        for nome, falso in (('obter_conexao_db', lambda: sqlite3.connect(self.caminho)),
                            ('enviar_fluxo', enviar_fluxo), ('atualizar_callback', atualizar_callback),
                            ('mostrar_resumo_temporario', mostrar_resumo_temporario),
                            ('responder_callback', responder_callback), ('datetime', Relogio)):
            patcher = mock.patch.object(bot, nome, falso)
            patcher.start()
            self.addCleanup(patcher.stop)

    def mensagem(self, texto, user_id=UID):
        async def apagar():
            return None
        return SimpleNamespace(message=SimpleNamespace(text=texto, delete=apagar),
                               effective_user=SimpleNamespace(id=user_id))

    def nova_categoria(self, texto, user_id=UID):
        return asyncio.run(bot.receber_nome_categoria(self.mensagem(texto, user_id), SimpleNamespace(user_data={})))

    def clicar_excluir(self, categoria_id, user_id=UID):
        query = SimpleNamespace(data=f'acao_excluir_{categoria_id}', from_user=SimpleNamespace(id=user_id),
                                message=SimpleNamespace(chat_id=user_id))
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id))
        return asyncio.run(bot.excluir_categoria_botao(update, SimpleNamespace(user_data={})))


def callbacks_do_teclado(teclado):
    return [[botao.callback_data for botao in linha] for linha in teclado.inline_keyboard]


class TestBotNovaCategoria(TesteBotBase):
    def test_cria_como_antes(self):
        estado = self.nova_categoria('  pets ')
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertEqual(self.resumos, [(UID, "Categoria criada com sucesso!")])
        self.assertEqual(self.ler("SELECT user_id, nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias "
                                  "WHERE nome = 'Pets'"), [(UID, 'Pets', 0.0, 0.0, MOMENTO_BRASILIA)])
        self.assertEqual(self.contar("chaves_registro"), 0)

    def test_repetido_avisa_e_continua_pedindo_o_nome(self):
        self.nova_categoria('pets')
        antes = self.foto()
        for digitado in ('PETS', 'Pêts ', 'pets'):
            with self.subTest(digitado=digitado):
                self.fluxo.clear()
                self.assertEqual(self.nova_categoria(digitado), bot.AGUARDANDO_NOME_CAT)
                self.assertEqual(self.fluxo, [(UID, "Já existe a categoria <b>Pets</b>. Envie outro nome:",
                                               bot.TECLADO_VOLTAR)])
        self.assertEqual(self.foto(), antes)
        self.assertEqual(len(self.resumos), 1)

    def test_repetido_mostra_o_nome_gravado_com_escape(self):
        self.inserir_categoria('Saude')
        self.inserir_categoria('A<b>')
        self.assertEqual(self.nova_categoria('saúde'), bot.AGUARDANDO_NOME_CAT)
        self.assertEqual(self.fluxo[-1][1], "Já existe a categoria <b>Saude</b>. Envie outro nome:")
        self.assertEqual(self.nova_categoria('a<B>'), bot.AGUARDANDO_NOME_CAT)
        self.assertEqual(self.fluxo[-1][1], "Já existe a categoria <b>A&lt;b&gt;</b>. Envie outro nome:")

    def test_nome_vazio_ou_longo(self):
        antes = self.foto()
        for digitado in ('   ', 'a' * 31):
            with self.subTest(digitado=digitado):
                self.assertEqual(self.nova_categoria(digitado), bot.AGUARDANDO_NOME_CAT)
                self.assertEqual(self.fluxo[-1], (UID, "O nome precisa ter de 1 a 30 caracteres. Envie outro nome:",
                                                  bot.TECLADO_VOLTAR))
        self.assertEqual(self.foto(), antes)

    def test_limite_volta_ao_menu(self):
        for i in range(17):
            self.inserir_categoria(f'Extra {i}')
        antes = self.foto()
        self.assertEqual(self.nova_categoria('Pets'), bot.ConversationHandler.END)
        self.assertEqual(self.resumos, [(UID, "Você já tem 20 categorias. Exclua uma para criar outra.")])
        self.assertEqual(self.foto(), antes)

    def test_so_olha_as_categorias_do_proprio_usuario(self):
        self.assertEqual(self.nova_categoria('lazer'), bot.ConversationHandler.END)  # Lazer é do OUTRO_UID
        self.assertIn('Lazer', self.nomes(UID))


class TestBotExcluirCategoria(TesteBotBase):
    def test_exclui_a_propria_como_antes(self):
        self.gasto('Casa', 10.0, '2026-11-01 10:00:00')
        casa = self.id_de('Casa')
        self.clicar_excluir(casa)
        self.assertEqual(self.resumos, [(UID, "Categoria excluída com sucesso!")])
        self.assertEqual(self.nomes(), ['Alimentação', 'Roupas'])
        self.assertEqual(self.ler("SELECT categoria_nome, valor FROM transacoes"), [('Casa', 10.0)])

    def test_nao_exclui_a_de_outro_usuario(self):
        lazer = self.id_de('Lazer', OUTRO_UID)
        antes = self.foto()
        self.clicar_excluir(lazer, user_id=UID)
        self.assertEqual(self.foto(), antes)
        self.assertEqual(self.resumos, [])
        texto, teclado = self.callbacks[-1]
        self.assertEqual(texto, "Categoria não encontrada.")
        self.assertEqual(callbacks_do_teclado(teclado), callbacks_do_teclado(bot.obter_teclado_menu()))

    def test_com_recorrente_explica_e_nao_apaga(self):
        self.recorrente('Casa')
        casa = self.id_de('Casa')
        antes = self.foto()
        self.clicar_excluir(casa)
        self.assertEqual(self.foto(), antes)
        texto, teclado = self.callbacks[-1]
        self.assertEqual(texto, "Essa categoria tem gastos recorrentes. Mude a categoria deles em Recorrentes "
                                "antes de excluir.")
        self.assertEqual(callbacks_do_teclado(teclado), [[f'edit_cat_{casa}', 'voltar_menu']])

    def test_ultima_categoria(self):
        lazer = self.id_de('Lazer', OUTRO_UID)
        antes = self.foto()
        self.clicar_excluir(lazer, user_id=OUTRO_UID)
        self.assertEqual(self.foto(), antes)
        texto, teclado = self.callbacks[-1]
        self.assertEqual(texto, "Você precisa de pelo menos uma categoria.")
        self.assertEqual(callbacks_do_teclado(teclado), [[f'edit_cat_{lazer}', 'voltar_menu']])


class TestBotEditarNome(TesteBotBase):
    def test_renomear_pelo_chat_continua_propagando(self):
        self.gasto('Casa', 10.0, '2026-11-01 10:00:00')
        self.recorrente('Casa')
        casa = self.id_de('Casa')
        contexto = SimpleNamespace(user_data={'cat_em_edicao': casa})
        estado = asyncio.run(bot.salvar_novo_nome(self.mensagem('lar'), contexto))
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertEqual(self.resumos, [(UID, "Categoria renomeada para <b>Lar</b> com sucesso!")])
        self.assertEqual(self.ler("SELECT categoria_nome FROM transacoes"), [('Lar',)])
        self.assertEqual(self.ler("SELECT categoria_nome FROM gastos_recorrentes"), [('Lar',)])

    def test_renomear_para_nome_de_outra_continua_recusado(self):
        casa = self.id_de('Casa')
        antes = self.foto()
        estado = asyncio.run(bot.salvar_novo_nome(self.mensagem('roupás'), SimpleNamespace(user_data={'cat_em_edicao': casa})))
        self.assertEqual(estado, bot.EDITAR_NOME_CAT)
        self.assertEqual(self.fluxo[-1][1], "Já existe a categoria <b>Roupas</b>. Envie outro nome:")
        self.assertEqual(self.foto(), antes)


if __name__ == "__main__":
    unittest.main(verbosity=2)
