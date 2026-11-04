"""Testes da ferramenta do banco de teste (PRO-90).

Roda com `python testes_banco_teste.py`. Não usa rede nem o .env real: os
arquivos de ambiente são falsos, numa pasta temporária, e o "banco" é um
SQLite em memória injetado no lugar da conexão com o Turso.
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ferramentas"))
import banco_teste as bt  # noqa: E402

URL_TESTE = "libsql://fin4ncetest-l1d3r.aws-us-east-1.turso.io"
URL_PRODUCAO = "libsql://fin4nce-l1d3r.aws-us-east-1.turso.io"
TOKEN_FALSO = "tokenfalso.abc123.segredo"


def criar_env(pasta, url=URL_TESTE, token=TOKEN_FALSO, nome=".env"):
    caminho = os.path.join(pasta, nome)
    with open(caminho, "w", encoding="utf-8") as f:
        if url is not None:
            f.write(f"TURSO_DATABASE_URL={url}\n")
        if token is not None:
            f.write(f"TURSO_AUTH_TOKEN={token}\n")
        f.write("GEMINI_API_KEY=outrosegredo\n")
    return caminho


_BANCOS_ABERTOS = []


def tearDownModule():
    for conn in _BANCOS_ABERTOS:
        conn.close()


class BancoFalso:
    """SQLite em memória com uma tabela, contando as conexões abertas."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        _BANCOS_ABERTOS.append(self.conn)
        self.conn.execute("CREATE TABLE categorias (id INTEGER PRIMARY KEY, user_id INTEGER, nome TEXT)")
        self.conn.execute("INSERT INTO categorias (user_id, nome) VALUES (1, 'Mercado'), (1, 'Lazer'), (2, 'Outro')")
        self.conn.commit()
        self.conexoes = 0

    def conectar(self, url, token):
        self.conexoes += 1
        banco = self

        class SemFechar:
            def __getattr__(self, nome):
                return getattr(banco.conn, nome)

            def close(self):
                pass

        return SemFechar()


def rodar(argv, banco=None):
    banco = banco or BancoFalso()
    saida, erro = io.StringIO(), io.StringIO()
    codigo = bt.main(argv, conectar=banco.conectar, saida=saida, erro=erro)
    return codigo, saida.getvalue(), erro.getvalue(), banco


class TestTravaDeProducao(unittest.TestCase):
    def test_nome_do_banco_de_teste_numa_constante(self):
        self.assertEqual(bt.NOME_BANCO_TESTE, "fin4ncetest")
        self.assertEqual(bt.HOST_BANCO_TESTE, "fin4ncetest-l1d3r")

    def test_aceita_o_banco_de_teste(self):
        self.assertTrue(bt.e_banco_de_teste(URL_TESTE))
        self.assertTrue(bt.e_banco_de_teste("https://fin4ncetest-l1d3r.turso.io"))
        self.assertTrue(bt.e_banco_de_teste("LIBSQL://FIN4NCETEST-L1D3R.aws-us-east-1.turso.io"))

    def test_recusa_qualquer_outro_banco(self):
        for url in (URL_PRODUCAO, "", "libsql://", "fin4ncetest", "fin4ncetest-l1d3r",
                    # nome antigo, que nunca existiu no Turso
                    "libsql://fin4nce-teste-l1d3r.aws-us-east-1.turso.io",
                    # parecidos: prefixo, sufixo, outra organização
                    "libsql://fin4ncetest-prod-l1d3r.aws-us-east-1.turso.io",
                    "libsql://fin4ncetest2-l1d3r.aws-us-east-1.turso.io",
                    "libsql://xfin4ncetest-l1d3r.aws-us-east-1.turso.io",
                    "libsql://fin4ncetest-outraorg.aws-us-east-1.turso.io",
                    "libsql://fin4ncetest.aws-us-east-1.turso.io",
                    # o nome certo fora do primeiro pedaço do endereço
                    "libsql://fin4nce-l1d3r.turso.io/?db=fin4ncetest-l1d3r",
                    "libsql://fin4nce-l1d3r.turso.io/fin4ncetest-l1d3r",
                    "libsql://outro.fin4ncetest-l1d3r.turso.io",
                    "libsql://fin4ncetest-l1d3r@fin4nce-l1d3r.turso.io"):
            with self.subTest(url=url):
                self.assertFalse(bt.e_banco_de_teste(url))


class TestValidarLeitura(unittest.TestCase):
    def aceita(self, sql):
        return bt.validar_sql(sql)

    def recusa(self, sql):
        with self.assertRaises(bt.ErroFerramenta) as ctx:
            bt.validar_sql(sql)
        self.assertEqual(ctx.exception.codigo, 2)

    def test_select_e_with(self):
        self.assertEqual(self.aceita("SELECT * FROM categorias;"), "SELECT * FROM categorias")
        self.aceita("  select nome from categorias  ")
        self.aceita("WITH x AS (SELECT 1 AS a) SELECT a FROM x")
        self.aceita("SELECT * FROM pragma_table_info('categorias')")

    def test_palavra_proibida_dentro_de_texto_ou_comentario_passa(self):
        self.aceita("SELECT 'delete; drop table x' AS t")
        self.aceita("SELECT 1 -- delete from x")
        self.aceita("SELECT 1 /* update x */")
        self.aceita("SELECT replace(nome, 'a', 'b') FROM categorias")

    def test_recusa_escrita_e_estrutura(self):
        for sql in ("INSERT INTO categorias (nome) VALUES ('x')",
                    "UPDATE categorias SET nome = 'x' WHERE id = 1",
                    "DELETE FROM categorias",
                    "DROP TABLE categorias",
                    "PRAGMA table_info(categorias)",
                    "WITH x AS (SELECT 1) DELETE FROM categorias",
                    "WITH x AS (SELECT 1) INSERT INTO categorias (nome) SELECT 'a'",
                    "WITH x AS (SELECT 1) REPLACE INTO categorias (nome) VALUES ('a')",
                    "SELECT 1; DROP TABLE categorias",
                    "SELECT 1; SELECT 2",
                    "ATTACH DATABASE 'x.db' AS x",
                    "", "   ;  "):
            with self.subTest(sql=sql):
                self.recusa(sql)


class TestValidarEscrita(unittest.TestCase):
    def test_aceita_insert_e_update_com_where(self):
        bt.validar_sql("INSERT INTO categorias (user_id, nome) VALUES (?, ?)", escrita=True)
        bt.validar_sql("UPDATE categorias SET nome = ? WHERE id = ?", escrita=True)

    def test_recusa_o_resto(self):
        for sql in ("UPDATE categorias SET nome = 'x'",
                    "INSERT OR REPLACE INTO categorias (id, nome) VALUES (1, 'x')",
                    "REPLACE INTO categorias (id, nome) VALUES (1, 'x')",
                    "DELETE FROM categorias WHERE id = 1",
                    "SELECT * FROM categorias",
                    "INSERT INTO categorias (nome) VALUES ('a'); DELETE FROM categorias",
                    "UPDATE categorias SET nome = 'x' WHERE id IN (SELECT id FROM categorias); DROP TABLE x"):
            with self.subTest(sql=sql):
                with self.assertRaises(bt.ErroFerramenta):
                    bt.validar_sql(sql, escrita=True)


class TestSegredos(unittest.TestCase):
    def test_oculta_token_url_e_endereco(self):
        texto = f"falha em {URL_TESTE} com {TOKEN_FALSO}; host fin4ncetest-l1d3r.aws-us-east-1.turso.io"
        limpo = bt.ocultar_segredos(texto, [URL_TESTE, TOKEN_FALSO])
        self.assertNotIn(TOKEN_FALSO, limpo)
        self.assertNotIn("turso.io", limpo)
        self.assertIn("***", limpo)

    def test_parametros(self):
        self.assertEqual(bt.converter_parametro("12"), 12)
        self.assertEqual(bt.converter_parametro("12.5"), 12.5)
        self.assertIsNone(bt.converter_parametro("null"))
        self.assertEqual(bt.converter_parametro('"12"'), "12")
        self.assertEqual(bt.converter_parametro("Mercado"), "Mercado")
        self.assertEqual(bt.converter_parametro("true"), 1)
        self.assertEqual(bt.converter_parametro("[1,2]"), "[1,2]")


class TestArquivoDeAmbiente(unittest.TestCase):
    def test_prefere_env_teste(self):
        with tempfile.TemporaryDirectory() as pasta:
            self.assertEqual(bt.escolher_arquivo_env(raiz=pasta), os.path.join(pasta, ".env"))
            criar_env(pasta, nome=".env.teste")
            self.assertEqual(bt.escolher_arquivo_env(raiz=pasta), os.path.join(pasta, ".env.teste"))
            self.assertEqual(bt.escolher_arquivo_env("outro.env", raiz=pasta), "outro.env")


class TestMain(unittest.TestCase):
    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)

    def env(self, **kw):
        return criar_env(self.pasta.name, **kw)

    def sem_segredo(self, *textos):
        for texto in textos:
            self.assertNotIn(TOKEN_FALSO, texto)
            self.assertNotIn("turso.io", texto)
            self.assertNotIn("outrosegredo", texto)

    def test_select_imprime_so_o_resultado(self):
        codigo, saida, erro, _ = rodar(["--env", self.env(), "SELECT nome FROM categorias WHERE user_id = ? ORDER BY id", "-p", "1"])
        self.assertEqual(codigo, 0)
        self.assertEqual(saida, "nome\n-------\nMercado\nLazer\n")
        self.assertEqual(erro, "(2 linhas)\n")
        self.sem_segredo(saida, erro)

    def test_json(self):
        codigo, saida, _, _ = rodar(["--env", self.env(), "--json", "SELECT id, nome FROM categorias WHERE id = 1"])
        self.assertEqual(codigo, 0)
        self.assertIn('"nome": "Mercado"', saida)

    def test_producao_aborta_sem_conectar(self):
        codigo, saida, erro, banco = rodar(["--env", self.env(url=URL_PRODUCAO), "SELECT 1"])
        self.assertEqual(codigo, 3)
        self.assertEqual(banco.conexoes, 0)
        self.assertEqual(saida, "")
        self.assertIn("ABORTADO", erro)
        self.sem_segredo(erro)
        self.assertNotIn("fin4nce-l1d3r", erro)

    def test_variavel_ausente_cita_so_o_nome(self):
        codigo, _, erro, banco = rodar(["--env", self.env(token=None), "SELECT 1"])
        self.assertEqual(codigo, 3)
        self.assertEqual(banco.conexoes, 0)
        self.assertIn("TURSO_AUTH_TOKEN", erro)
        self.sem_segredo(erro)

    def test_env_inexistente(self):
        codigo, _, erro, banco = rodar(["--env", os.path.join(self.pasta.name, "nao_existe.env"), "SELECT 1"])
        self.assertEqual(codigo, 3)
        self.assertEqual(banco.conexoes, 0)

    def test_escrita_sem_modo_e_bloqueada_e_nao_conecta(self):
        codigo, _, erro, banco = rodar(["--env", self.env(), "INSERT INTO categorias (nome) VALUES ('x')"])
        self.assertEqual(codigo, 2)
        self.assertEqual(banco.conexoes, 0)
        self.assertEqual(banco.conn.execute("SELECT COUNT(*) FROM categorias").fetchone()[0], 3)

    def test_escrita_com_modo_grava(self):
        banco = BancoFalso()
        codigo, saida, _, _ = rodar(["--env", self.env(), "--escrita",
                                     "INSERT INTO categorias (user_id, nome) VALUES (?, ?)", "-p", "1", "-p", "Viagem"], banco)
        self.assertEqual(codigo, 0)
        self.assertEqual(saida, "linhas afetadas: 1\nid gerado: 4\n")
        codigo, saida, _, _ = rodar(["--env", self.env(), "--escrita",
                                     "UPDATE categorias SET nome = ? WHERE id = ?", "-p", "Viagens", "-p", "4"], banco)
        self.assertEqual(codigo, 0)
        self.assertEqual(saida, "linhas afetadas: 1\n")
        self.assertEqual(banco.conn.execute("SELECT nome FROM categorias WHERE id = 4").fetchone()[0], "Viagens")

    def test_escrita_em_producao_aborta(self):
        codigo, _, _, banco = rodar(["--env", self.env(url=URL_PRODUCAO), "--escrita",
                                     "INSERT INTO categorias (nome) VALUES ('x')"])
        self.assertEqual(codigo, 3)
        self.assertEqual(banco.conexoes, 0)

    def test_erro_do_banco_nao_vaza_segredo(self):
        class Quebra(BancoFalso):
            def conectar(self, url, token):
                raise RuntimeError(f"falha ao conectar em {url} usando {token}")
        codigo, saida, erro, _ = rodar(["--env", self.env(), "SELECT 1"], Quebra())
        self.assertEqual(codigo, 1)
        self.assertEqual(saida, "")
        self.assertIn("***", erro)
        self.sem_segredo(erro)

    def test_arquivo_sql(self):
        caminho = os.path.join(self.pasta.name, "consulta.sql")
        with open(caminho, "w", encoding="utf-8") as f:
            f.write("SELECT COUNT(*) AS total FROM categorias;\n")
        codigo, saida, _, _ = rodar(["--env", self.env(), "--arquivo", caminho])
        self.assertEqual(codigo, 0)
        self.assertEqual(saida, "total\n-----\n3\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
