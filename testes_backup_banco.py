"""Testes da ferramenta de backup e restauração (PRO-94, D13:
ferramentas/backup_banco.py).

Roda com `python testes_backup_banco.py`. Sem .env e sem internet:
  - antes de qualquer import do projeto, o load_dotenv vira no-op e as
    variáveis do bot ficam vazias;
  - o libsql.connect só aceita arquivo LOCAL ou ":memory:" (qualquer endereço
    com "://" ou sem ser um caminho de arquivo é recusado);
  - os bancos são arquivos temporários: SQLite (sqlite3) e libsql local, com
    todas as tabelas do bot.iniciar_banco();
  - a conexão, as credenciais e a confirmação "sim" são injetadas no main().
"""
import contextlib
import hashlib
import io
import math
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
for _caminho in (RAIZ, os.path.join(RAIZ, 'ferramentas')):
    if _caminho not in sys.path:
        sys.path.insert(0, _caminho)

# --- COSTURA: nada de .env nem conexão remota (antes de qualquer import do projeto) ---
import dotenv  # noqa: E402

_LOAD_DOTENV_ORIGINAL = dotenv.load_dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ['TOKEN'] = ''
os.environ['TURSO_DATABASE_URL'] = ''
os.environ['TURSO_AUTH_TOKEN'] = ''

import libsql  # noqa: E402

_LIBSQL_CONNECT_ORIGINAL = libsql.connect


def _libsql_so_local(database=None, *args, **kwargs):
    if database == ':memory:':
        return _LIBSQL_CONNECT_ORIGINAL(database)
    if not isinstance(database, str) or '://' in database or database.startswith(('libsql:', 'http')) \
            or not os.path.isabs(database):
        raise RuntimeError("Só banco libsql LOCAL (arquivo temporário) nos testes.")
    return _LIBSQL_CONNECT_ORIGINAL(database)


libsql.connect = _libsql_so_local

import backup_banco  # noqa: E402
import bot  # noqa: E402

URL_TESTE = 'libsql://fin4ncetest-l1d3r.aws-us-east-1.turso.io'
URL_PRODUCAO = 'libsql://fin4nce-l1d3r.aws-us-east-1.turso.io'
TOKEN_FALSO = 'token-do-banco-falso-xyz'

TABELAS_DO_BOT = {
    'usuarios', 'categorias', 'transacoes', 'parcelamentos', 'gastos_recorrentes', 'transferencias', 'receitas',
    'chaves_registro', 'receitas_recorrentes', 'assinaturas', 'uso_ia', 'pagamentos_recebidos',
}

TEXTOS = (
    "O'Brien; \n-- não é comentário\n;",
    'emoji 😀, acentos ção, aspas "duplas"',
    "antes\x00depois",
    "linha1\r\nlinha2\ttab \\ barra",
)
REAIS = (0.1 + 0.2, 1e-300, 123456789.123456789, 10.0, 1e16, 2.5e-7)


def sha256(caminho):
    with open(caminho, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def criar_banco_do_bot(caminho):
    """Todas as tabelas e índices do bot.iniciar_banco(), num SQLite em arquivo."""
    with mock.patch.object(bot, 'obter_conexao_db', lambda: sqlite3.connect(caminho)), \
            contextlib.redirect_stdout(io.StringIO()):
        bot.iniciar_banco()


def popular(caminho, linhas_por_tabela=4):
    """Linhas com valores difíceis em TODAS as tabelas: aspas, ';', "\\r\\n" e
    quebra de linha no texto, emoji, caractere nulo, reais que perdem
    precisão com str() ou que o SQLite não lê de volta pelo repr (1e-300),
    inteiros grandes, NULL e um blob (na 3ª linha), e um texto com UTF-8
    inválido. Depois apaga a última linha de cada tabela com AUTOINCREMENT
    (a sqlite_sequence fica maior que o maior id)."""
    conn = sqlite3.connect(caminho)
    tabelas = [n for (n,) in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t_indice, tabela in enumerate(tabelas):
        colunas = conn.execute(f'PRAGMA table_info("{tabela}")').fetchall()
        for i in range(1, linhas_por_tabela + 1):
            valores = []
            blob_posto = False
            for cid, nome, tipo, notnull, _padrao, pk in colunas:
                tipo = (tipo or '').upper()
                if pk == 1 and tipo == 'INTEGER' and sum(1 for c in colunas if c[5]) == 1:
                    valores.append(i * 10 + t_indice)                      # id explícito, com buracos
                elif i == 3 and not notnull and not pk:
                    if not blob_posto and 'TEXT' in tipo:
                        valores.append(bytes([0, 1, 254, 255, i]))        # blob numa coluna de texto
                        blob_posto = True
                    else:
                        valores.append(None)
                elif 'INT' in tipo or tipo == 'TIMESTAMP':
                    valores.append(2 ** 53 + i * 7 + cid if nome == 'user_id' else i * 100 + cid)
                elif 'REAL' in tipo:
                    valores.append(REAIS[(i + cid) % len(REAIS)])
                else:
                    valores.append(f'{TEXTOS[(i + cid) % len(TEXTOS)]} #{i}-{cid}')
            marcas = ', '.join('?' for _ in colunas)
            nomes = ', '.join(f'"{c[1]}"' for c in colunas)
            conn.execute(f'INSERT INTO "{tabela}" ({nomes}) VALUES ({marcas})', valores)
    # Um texto com UTF-8 inválido (o driver não consegue nem ler como str).
    conn.execute("UPDATE usuarios SET nome = CAST(X'FF61C3' AS TEXT) WHERE rowid = (SELECT MIN(rowid) FROM usuarios)")
    for (tabela,) in conn.execute("SELECT name FROM sqlite_sequence").fetchall():
        conn.execute(f'DELETE FROM "{tabela}" WHERE rowid = (SELECT MAX(rowid) FROM "{tabela}")')
    conn.commit()
    conn.close()


def retrato(caminho):
    """Esquema (tabelas e índices), linhas com o tipo de cada valor e a
    sqlite_sequence, para comparar dois bancos."""
    conn = sqlite3.connect(caminho)
    esquema = sorted(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                  "WHERE name NOT LIKE 'sqlite_%'").fetchall())
    dados = {}
    for _tipo, nome, _tabela, _sql in esquema:
        if _tipo != 'table':
            continue
        colunas = [c[1] for c in conn.execute(f'PRAGMA table_info("{nome}")')]
        selecao = ', '.join(f"""typeof("{c}"), CASE WHEN typeof("{c}") = 'text' THEN hex("{c}") ELSE "{c}" END"""
                            for c in colunas)
        dados[nome] = conn.execute(f'SELECT {selecao} FROM "{nome}" ORDER BY rowid').fetchall()
    tem_sequencia = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'sqlite_sequence'").fetchone()[0]
    sequencia = sorted(conn.execute("SELECT name, seq FROM sqlite_sequence").fetchall()) if tem_sequencia else []
    conn.close()
    return esquema, dados, sequencia


class ConexaoGravada:
    """Envolve uma conexão e guarda cada comando executado."""

    def __init__(self, conn):
        self.conn = conn
        self.comandos = []
        self.commits = 0
        self.mudancas_no_fim = None

    def execute(self, sql, *args):
        self.comandos.append(sql)
        return self.conn.execute(sql, *args)

    def commit(self):
        self.commits += 1
        return self.conn.commit()

    def rollback(self):
        return self.conn.rollback()

    def close(self):
        self.mudancas_no_fim = self.conn.total_changes
        return self.conn.close()


class TesteBackupBase(unittest.TestCase):
    def setUp(self):
        self._pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._pasta.cleanup)
        self.pasta = self._pasta.name
        self.origem = self.caminho('origem.db')
        criar_banco_do_bot(self.origem)
        popular(self.origem)
        self.arquivo_dump = self.caminho('dump.sql')
        self.conexoes = []

    def caminho(self, nome):
        return os.path.join(self.pasta, nome)

    def conectar_sqlite(self, caminho):
        def conectar(url, token):
            self.assertEqual((url, token), (self.url, TOKEN_FALSO))
            conexao = ConexaoGravada(sqlite3.connect(caminho))
            self.conexoes.append(conexao)
            return conexao
        return conectar

    def rodar(self, argv, conectar, url=URL_TESTE, confirmar=lambda prompt: 'sim'):
        self.url = url
        saida, erro = io.StringIO(), io.StringIO()
        self.confirmacoes = []

        def confirmar_gravando(prompt):
            self.confirmacoes.append(prompt)
            return confirmar(prompt)

        codigo = backup_banco.main(argv, conectar=conectar, credenciais=lambda: (url, TOKEN_FALSO),
                                   confirmar=confirmar_gravando, saida=saida, erro=erro)
        self.saida, self.erro = saida.getvalue(), erro.getvalue()
        for texto in (self.saida, self.erro):
            self.assertNotIn(TOKEN_FALSO, texto)
            self.assertNotIn(url, texto)
            self.assertNotIn('turso.io', texto)
        return codigo

    def dump(self, origem=None, url=URL_TESTE):
        return self.rodar(['dump', self.arquivo_dump], self.conectar_sqlite(origem or self.origem), url=url)

    def contagens_impressas(self, texto):
        inicio = texto.index('Linhas por tabela:')
        linhas = []
        for linha in texto[inicio:].splitlines()[1:]:
            if not linha.startswith('  '):
                break
            linhas.append(linha.strip())
        return linhas


# =============================================================================
# DUMP E RESTAURAÇÃO
# =============================================================================
class TestDumpERestauracao(TesteBackupBase):
    def test_banco_tem_todas_as_tabelas_do_iniciar_banco_e_linhas_em_todas(self):
        esquema, dados, sequencia = retrato(self.origem)
        self.assertEqual({n for t, n, _, _ in esquema if t == 'table'}, TABELAS_DO_BOT)
        self.assertTrue(any(t == 'index' for t, _, _, _ in esquema))
        for tabela, linhas in dados.items():
            self.assertGreaterEqual(len(linhas), 2, tabela)
        self.assertTrue(sequencia)

    def test_dump_e_restauracao_dao_as_mesmas_contagens_e_os_mesmos_dados(self):
        self.assertEqual(self.dump(), 0, self.erro)
        self.assertTrue(self.saida.startswith('Banco: TESTE\n'))
        contagens_dump = self.contagens_impressas(self.saida)
        self.assertEqual(len(contagens_dump), len(TABELAS_DO_BOT))
        self.assertIn('Dump gravado:', self.saida)

        with open(self.arquivo_dump, encoding='utf-8') as f:
            texto = f.read()
        comandos = backup_banco.ler_comandos(texto)
        self.assertEqual(comandos[0], 'BEGIN TRANSACTION;')
        self.assertEqual(comandos[-1], 'COMMIT;')
        self.assertTrue(comandos[1].startswith('CREATE TABLE'))
        primeiro_insert = next(i for i, c in enumerate(comandos) if c.startswith('INSERT'))
        self.assertTrue(all(c.startswith('CREATE') for c in comandos[1:primeiro_insert]))
        self.assertTrue(any(c.startswith('CREATE UNIQUE INDEX') for c in comandos[1:primeiro_insert]))
        self.assertIn('DELETE FROM sqlite_sequence;', comandos)
        self.assertTrue(comandos[-2].startswith('INSERT INTO sqlite_sequence'))

        destino = self.caminho('destino.db')
        sqlite3.connect(destino).close()
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(destino)), 0, self.erro)
        self.assertTrue(self.saida.startswith('Banco: TESTE\n'))
        self.assertEqual(self.contagens_impressas(self.saida), contagens_dump)
        self.assertIn('Restauração concluída: as contagens batem com as do dump.', self.saida)
        self.assertEqual(len(self.confirmacoes), 1)
        self.assertEqual(retrato(destino), retrato(self.origem))

    def test_dump_e_restauracao_pelo_libsql_local(self):
        origem_libsql = self.caminho('origem_libsql.db')
        criar_banco_do_bot(origem_libsql)
        popular(origem_libsql)

        def conectar(caminho):
            return lambda url, token: libsql.connect(database=caminho)

        self.assertEqual(self.rodar(['dump', self.arquivo_dump], conectar(origem_libsql)), 0, self.erro)
        contagens_dump = self.contagens_impressas(self.saida)
        destino = self.caminho('destino_libsql.db')
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], conectar(destino)), 0, self.erro)
        self.assertEqual(self.contagens_impressas(self.saida), contagens_dump)
        self.assertEqual(retrato(destino), retrato(origem_libsql))

    def test_dump_nao_altera_a_origem_e_so_le(self):
        antes_hash, antes = sha256(self.origem), retrato(self.origem)
        self.assertEqual(self.dump(), 0, self.erro)
        self.assertEqual(sha256(self.origem), antes_hash)
        self.assertEqual(retrato(self.origem), antes)
        conexao = self.conexoes[0]
        self.assertEqual(conexao.commits, 0)
        self.assertEqual(conexao.mudancas_no_fim, 0)
        self.assertTrue(conexao.comandos)
        for comando in conexao.comandos:
            self.assertTrue(comando.lstrip().upper().startswith('SELECT'), comando)

    def test_dump_recusa_arquivo_que_ja_existe_sem_conectar(self):
        with open(self.arquivo_dump, 'w', encoding='utf-8') as f:
            f.write('backup anterior')

        def nao_conecta(url, token):
            self.fail('não deveria conectar')

        self.assertEqual(self.rodar(['dump', self.arquivo_dump], nao_conecta), 2)
        self.assertIn('o arquivo já existe', self.erro)
        with open(self.arquivo_dump, encoding='utf-8') as f:
            self.assertEqual(f.read(), 'backup anterior')

    def test_rotulo_producao_e_a_mesma_regra_do_bot(self):
        self.assertEqual(self.dump(url=URL_PRODUCAO), 0, self.erro)
        self.assertTrue(self.saida.startswith('Banco: PRODUÇÃO\n'))
        for url in (URL_TESTE, URL_PRODUCAO, '', 'fin4ncetest-x.turso.io', 'libsql://x.io/fin4ncetest'):
            self.assertEqual(backup_banco.rotulo_banco(url), bot.rotulo_banco(url))

    def test_tabelas_internas_ficam_de_fora(self):
        conn = sqlite3.connect(self.origem)
        conn.execute('CREATE TABLE libsql_wasm_func_table (name TEXT PRIMARY KEY, body TEXT)')
        conn.execute('CREATE TABLE _litestream_seq (id INTEGER PRIMARY KEY, seq INTEGER)')
        conn.execute("INSERT INTO libsql_wasm_func_table VALUES ('f', 'x')")
        conn.commit()
        conn.close()
        self.assertEqual(self.dump(), 0, self.erro)
        with open(self.arquivo_dump, encoding='utf-8') as f:
            texto = f.read()
        self.assertNotIn('libsql_wasm_func_table', texto)
        self.assertNotIn('_litestream_seq', texto)

        # Um banco "vazio" que só tem tabela interna aceita a restauração.
        destino = self.caminho('destino.db')
        conn = sqlite3.connect(destino)
        conn.execute('CREATE TABLE libsql_wasm_func_table (name TEXT PRIMARY KEY, body TEXT)')
        conn.commit()
        conn.close()
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(destino)), 0, self.erro)


class TestRestauracaoRecusada(TesteBackupBase):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.dump(), 0, self.erro)

    def test_recusa_banco_nao_vazio_sem_gravar_nem_perguntar(self):
        destino = self.caminho('destino.db')
        conn = sqlite3.connect(destino)
        conn.execute('CREATE TABLE qualquer (x)')
        conn.execute('INSERT INTO qualquer VALUES (1)')
        conn.commit()
        conn.close()
        antes = (sha256(destino), retrato(destino))
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(destino)), 2)
        self.assertTrue(self.saida.startswith('Banco: TESTE\n'))
        self.assertIn('RECUSADO: o banco já tem 1 tabela(s)', self.erro)
        self.assertIn('Nada foi gravado.', self.erro)
        self.assertEqual(self.confirmacoes, [])
        self.assertEqual((sha256(destino), retrato(destino)), antes)

    def test_recusa_o_proprio_banco_de_origem(self):
        antes = retrato(self.origem)
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(self.origem)), 2)
        self.assertEqual(retrato(self.origem), antes)

    def test_sem_sim_nada_e_gravado(self):
        for resposta in ('', 'nao', 'não', 's', 'yes', None):
            with self.subTest(resposta=resposta):
                destino = self.caminho(f'destino-{resposta}.db')
                codigo = self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(destino),
                                    confirmar=lambda prompt: resposta)
                self.assertEqual(codigo, 2)
                self.assertIn('Cancelado: nada foi gravado.', self.erro)
                self.assertEqual(len(self.confirmacoes), 1)
                self.assertIn('Digite sim', self.confirmacoes[0])
                self.assertEqual(retrato(destino)[0], [])

    def test_sim_aceita_maiusculas_e_espacos(self):
        destino = self.caminho('destino.db')
        self.assertEqual(self.rodar(['restaurar', self.arquivo_dump], self.conectar_sqlite(destino),
                                    confirmar=lambda prompt: '  SIM \n'), 0, self.erro)

    def test_falha_no_meio_desfaz_tudo(self):
        with open(self.arquivo_dump, encoding='utf-8') as f:
            linhas = f.read().splitlines(keepends=True)
        meio = next(i for i, l in enumerate(linhas) if l.startswith('INSERT')) + 3
        linhas.insert(meio, 'INSERT INTO tabela_que_nao_existe VALUES (1);\n')
        estragado = self.caminho('estragado.sql')
        with open(estragado, 'w', encoding='utf-8', newline='\n') as f:
            f.writelines(linhas)
        destino = self.caminho('destino.db')
        self.assertEqual(self.rodar(['restaurar', estragado], self.conectar_sqlite(destino)), 1)
        self.assertIn('A transação foi desfeita: nada foi gravado.', self.erro)
        self.assertEqual(retrato(destino)[0], [])

    def test_arquivo_que_nao_e_dump_nem_conecta(self):
        def nao_conecta(url, token):
            self.fail('não deveria conectar')

        for nome, conteudo in (('lixo.sql', 'isto não é SQL'), ('sem_commit.sql', 'BEGIN TRANSACTION;\nCREATE TABLE a (x);\n'),
                               ('cortado.sql', "BEGIN TRANSACTION;\nINSERT INTO a VALUES ('sem fim\n")):
            with self.subTest(nome=nome):
                caminho = self.caminho(nome)
                with open(caminho, 'w', encoding='utf-8') as f:
                    f.write(conteudo)
                self.assertEqual(self.rodar(['restaurar', caminho], nao_conecta), 3)
                self.assertIn('ERRO:', self.erro)
        self.assertEqual(self.rodar(['restaurar', self.caminho('nao_existe.sql')], nao_conecta), 3)
        self.assertIn('não consegui ler o arquivo', self.erro)


# =============================================================================
# LITERAIS, AMBIENTE E ERROS
# =============================================================================
class TestLiterais(unittest.TestCase):
    def test_literais_basicos(self):
        self.assertEqual(backup_banco.literal_sql(None), 'NULL')
        self.assertEqual(backup_banco.literal_sql(True), '1')
        self.assertEqual(backup_banco.literal_sql(-42), '-42')
        self.assertEqual(backup_banco.literal_sql(2 ** 62), str(2 ** 62))
        self.assertEqual(backup_banco.literal_sql(10.0), '10.0')
        self.assertEqual(backup_banco.literal_sql("O'Brien"), "'O''Brien'")
        self.assertEqual(backup_banco.literal_sql(b'\x00\xff'), "X'00FF'")
        self.assertEqual(backup_banco.literal_sql(float('nan')), 'NULL')
        self.assertEqual(backup_banco.literal_sql(float('inf')), '1e999')
        self.assertEqual(backup_banco.literal_sql(float('-inf')), '-1e999')

    def test_valores_voltam_iguais_pelo_sqlite(self):
        conn = sqlite3.connect(':memory:')
        valores = [None, 0, -1, 2 ** 63 - 1, -2 ** 63, *REAIS, -0.5, 1 / 3, math.pi, 5e-324, 1.7976931348623157e308,
                   *TEXTOS, '', b'', b'\x00\x01', float('inf'), float('-inf')]
        for valor in valores:
            with self.subTest(valor=valor):
                lido = conn.execute(f'SELECT {backup_banco.literal_sql(valor)}').fetchone()[0]
                self.assertEqual(lido, valor)
                self.assertEqual(type(lido), type(valor))
        conn.close()

    def test_dinheiro_sai_legivel_e_expoente_extremo_sai_exato(self):
        self.assertEqual(backup_banco.literal_sql(18.5), '18.5')
        self.assertEqual(backup_banco.literal_sql(0.1 + 0.2), '0.30000000000000004')
        self.assertEqual(backup_banco.literal_sql(1234.56), '1234.56')
        exato = backup_banco.literal_sql(1e-300)
        self.assertTrue(exato.startswith('(CAST('), exato)
        conn = sqlite3.connect(':memory:')
        self.assertEqual(conn.execute(f'SELECT {exato}').fetchone()[0], 1e-300)
        conn.close()

    def test_texto_com_cr_nulo_ou_utf8_invalido_vai_em_hexadecimal(self):
        self.assertEqual(backup_banco.literal_sql('a\r\nb'), "CAST(X'610D0A62' AS TEXT)")
        self.assertEqual(backup_banco.literal_sql('a\x00b'), "CAST(X'610062' AS TEXT)")
        self.assertEqual(backup_banco.literal_sql(backup_banco.TextoBruto(b'\xffa')), "CAST(X'FF61' AS TEXT)")
        self.assertEqual(backup_banco.literal_sql(backup_banco.TextoBruto("ção'".encode())), "'ção'''")
        self.assertEqual(backup_banco.literal_sql('a\nb'), "'a\nb'")

    def test_tipo_desconhecido_e_recusado(self):
        with self.assertRaises(backup_banco.ErroFerramenta):
            backup_banco.literal_sql(object())


class TestAmbienteEErros(TesteBackupBase):
    def test_credenciais_do_env_da_raiz_sem_sobrescrever_o_ambiente(self):
        pasta = self.caminho('raiz_falsa')
        os.makedirs(pasta)
        with open(os.path.join(pasta, '.env'), 'w', encoding='utf-8') as f:
            f.write(f'TURSO_DATABASE_URL={URL_TESTE}\nTURSO_AUTH_TOKEN={TOKEN_FALSO}\n')
        with mock.patch.object(dotenv, 'load_dotenv', _LOAD_DOTENV_ORIGINAL), mock.patch.dict(os.environ, {}):
            os.environ.pop('TURSO_DATABASE_URL', None)
            os.environ.pop('TURSO_AUTH_TOKEN', None)
            self.assertEqual(backup_banco.carregar_credenciais(pasta), (URL_TESTE, TOKEN_FALSO))
            os.environ['TURSO_DATABASE_URL'] = URL_PRODUCAO      # o ambiente vence o .env, como no bot
            self.assertEqual(backup_banco.carregar_credenciais(pasta), (URL_PRODUCAO, TOKEN_FALSO))

    def test_credencial_ausente_cita_so_o_nome(self):
        pasta = self.caminho('raiz_vazia')
        os.makedirs(pasta)
        with mock.patch.object(dotenv, 'load_dotenv', _LOAD_DOTENV_ORIGINAL), mock.patch.dict(os.environ, {}):
            os.environ.pop('TURSO_DATABASE_URL', None)
            os.environ['TURSO_AUTH_TOKEN'] = TOKEN_FALSO
            with self.assertRaises(backup_banco.ErroFerramenta) as erro:
                backup_banco.carregar_credenciais(pasta)
        self.assertEqual(erro.exception.codigo, 3)
        self.assertEqual(str(erro.exception), 'variável ausente: TURSO_DATABASE_URL')

    def test_erro_do_banco_nao_mostra_url_nem_token(self):
        def falha(url, token):
            raise ConnectionError(f'falhou ao conectar em {url} com {token}')

        self.assertEqual(self.rodar(['dump', self.arquivo_dump], falha), 1)
        self.assertIn('ERRO do banco (ConnectionError)', self.erro)
        self.assertIn('***', self.erro)
        self.assertFalse(os.path.exists(self.arquivo_dump))

    def test_sem_arquivo_parcial_quando_o_dump_falha(self):
        def conectar(url, token):
            return ConexaoQuebrada(sqlite3.connect(self.origem))

        self.assertEqual(self.rodar(['dump', self.arquivo_dump], conectar), 1)
        self.assertFalse(os.path.exists(self.arquivo_dump))
        self.assertFalse(os.path.exists(self.arquivo_dump + '.parcial'))


class ConexaoQuebrada(ConexaoGravada):
    """Falha no meio da leitura dos dados (depois de escrever os CREATE)."""

    def execute(self, sql, *args):
        if sql.startswith('SELECT typeof') and 'FROM "transacoes"' in sql:
            raise sqlite3.OperationalError('disco cheio')
        return super().execute(sql, *args)


if __name__ == '__main__':
    unittest.main(verbosity=2)
