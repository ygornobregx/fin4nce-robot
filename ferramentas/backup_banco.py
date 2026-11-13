"""Backup e restauração do banco (PRO-94, D13): um dump completo em SQL e a
restauração desse dump num banco VAZIO.

Uso (a partir da raiz do repositório):

    python ferramentas/backup_banco.py dump <arquivo.sql>
    python ferramentas/backup_banco.py restaurar <arquivo.sql>

O banco é o do .env da raiz (TURSO_DATABASE_URL e TURSO_AUTH_TOKEN), o mesmo
que o bot usa (uma variável já presente no ambiente vence a do .env, como no
bot). Contra a produção, quem roda é o Ygor; os agentes testam só com SQLite
local (testes_backup_banco.py).

dump:
  * Só leitura: roda apenas SELECT no banco (nunca grava nem faz commit).
  * Grava, em UTF-8, entre BEGIN TRANSACTION e COMMIT: o CREATE de cada
    tabela, o CREATE de cada índice, o INSERT de cada linha, os gatilhos e
    visões (se houver, depois dos dados) e, no fim, o conteúdo da
    sqlite_sequence (o próximo id de cada tabela AUTOINCREMENT). As tabelas
    internas (sqlite_*, libsql_*, _litestream_*) ficam de fora.
  * Recusa um arquivo que já existe (não sobrescreve um backup anterior). O
    arquivo só aparece com o nome final quando o dump termina inteiro.
  * Imprime "Banco: TESTE" ou "Banco: PRODUÇÃO" (a mesma regra da linha de
    conferência do bot) e quantas linhas tem cada tabela.

restaurar:
  * Imprime "Banco: ...", recusa (sem gravar nada) se o banco já tiver
    qualquer tabela, pede para digitar "sim" e executa o dump numa transação
    só: se um comando falhar, nada fica gravado.
  * No fim, imprime as contagens e confere se batem com as do dump.

Nunca imprime a URL nem o token: toda mensagem de erro passa pelo mesmo filtro
da ferramentas/banco_teste.py.

Códigos de saída: 0 ok; 1 erro do banco; 2 recusado ou cancelado; 3 problema
no arquivo de ambiente ou no arquivo do dump.
"""
import argparse
import math
import os
import re
import sqlite3
import sys

PASTA = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(PASTA)
for _caminho in (PASTA, RAIZ):
    if _caminho not in sys.path:
        sys.path.insert(0, _caminho)

from banco_teste import ocultar_segredos  # noqa: E402

PREFIXOS_INTERNOS = ("sqlite_", "libsql_", "_litestream_")
INICIO = "BEGIN TRANSACTION;"
FIM = "COMMIT;"
_INSERT = re.compile(r'^INSERT INTO "((?:[^"]|"")*)"')


class ErroFerramenta(Exception):
    """Erro com mensagem segura para mostrar e o código de saída."""

    def __init__(self, mensagem, codigo):
        super().__init__(mensagem)
        self.codigo = codigo


# --- AMBIENTE E CONEXÃO ---
def carregar_credenciais(raiz=RAIZ):
    """(url, token) do banco: o .env da raiz carregado sem sobrescrever o
    ambiente (como o bot faz). Os erros citam só o nome da variável."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(raiz, ".env"), override=False)
    url = (os.getenv("TURSO_DATABASE_URL") or "").strip()
    token = (os.getenv("TURSO_AUTH_TOKEN") or "").strip()
    for nome, valor in (("TURSO_DATABASE_URL", url), ("TURSO_AUTH_TOKEN", token)):
        if not valor:
            raise ErroFerramenta(f"variável ausente: {nome}", 3)
    return url, token


def conectar_turso(url, token):
    import libsql
    return libsql.connect(database=url, auth_token=token)


def rotulo_banco(url):
    """TESTE ou PRODUÇÃO, pela mesma regra da linha de conferência do bot (D7)."""
    from bot import rotulo_banco as regra_do_bot
    return regra_do_bot(url)


# --- SQL ---
def nome_interno(nome):
    return (nome or "").lower().startswith(PREFIXOS_INTERNOS)


def citar_id(nome):
    return '"' + nome.replace('"', '""') + '"'


_conferidores = None
_reais_conferidos = {}


def _conexoes_conferidoras():
    """Conexões em memória (sqlite3 e, se houver, libsql) para conferir se o
    SQLite lê um número de volta igual. Nada aqui toca em banco de verdade."""
    global _conferidores
    if _conferidores is None:
        _conferidores = [sqlite3.connect(":memory:")]
        try:
            import libsql
            _conferidores.append(libsql.connect(":memory:"))
        except Exception:
            pass
    return _conferidores


def _real_exato(valor):
    """O real como conta exata: um inteiro de até 53 bits multiplicado ou
    dividido por potências de 2 (cada passo é exato em ponto flutuante)."""
    mantissa, expoente = math.frexp(valor)
    inteiro, expoente = int(mantissa * 2 ** 53), expoente - 53
    partes = [f"CAST({inteiro} AS REAL)"]
    while expoente < 0:
        passo = min(-expoente, 62)
        partes.append(f"/ {2 ** passo}")
        expoente += passo
    while expoente > 0:
        passo = min(expoente, 62)
        partes.append(f"* {2 ** passo}")
        expoente -= passo
    return "(" + " ".join(partes) + ")"


def _literal_real(valor):
    """repr (o menor texto que volta ao mesmo número) quando o SQLite lê esse
    texto de volta igual; senão, a conta exata. Valores de dinheiro sempre
    saem pelo repr; só expoentes extremos (como 1e-300) precisam da conta."""
    if valor not in _reais_conferidos:
        texto = repr(valor)
        try:
            iguais = all(c.execute(f"SELECT {texto}").fetchone()[0] == valor for c in _conexoes_conferidoras())
        except Exception:
            iguais = False
        _reais_conferidos[valor] = texto if iguais else _real_exato(valor)
    return _reais_conferidos[valor]


def literal_sql(valor):
    """O valor como literal SQL, sem perder o tipo nem a precisão."""
    if valor is None:
        return "NULL"
    if isinstance(valor, TextoBruto):
        return literal_texto_bruto(valor)
    if isinstance(valor, bool):
        return str(int(valor))
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        if math.isnan(valor):
            return "NULL"          # o SQLite guarda NaN como NULL
        if math.isinf(valor):
            return "1e999" if valor > 0 else "-1e999"
        return _literal_real(valor)
    if isinstance(valor, str):
        # Caractere nulo ou "\r": o texto vai em hexadecimal, para nenhum
        # editor nem conversão de fim de linha (CRLF) mudar o dado.
        if "\x00" in valor or "\r" in valor:
            return f"CAST(X'{valor.encode('utf-8').hex().upper()}' AS TEXT)"
        return "'" + valor.replace("'", "''") + "'"
    if isinstance(valor, (bytes, bytearray, memoryview)):
        return "X'" + bytes(valor).hex().upper() + "'"
    raise ErroFerramenta(f"tipo de valor não suportado no dump: {type(valor).__name__}", 1)


def objetos_do_banco(conn):
    """(tipo, nome, tabela, sql) de sqlite_master, na ordem de criação."""
    return conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY rowid").fetchall()


def tabelas_de_usuario(conn):
    return [nome for tipo, nome, _, sql in objetos_do_banco(conn)
            if tipo == "table" and sql and not nome_interno(nome)]


def contar_linhas(conn, tabelas):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {citar_id(t)}").fetchone()[0] for t in tabelas}


class TextoBruto(bytes):
    """Um valor TEXT lido como os bytes gravados (hex() no SELECT): assim um
    caractere nulo ou um UTF-8 inválido não se perdem no driver."""


def literal_texto_bruto(bruto):
    try:
        texto = bytes(bruto).decode("utf-8")
    except UnicodeDecodeError:
        return f"CAST(X'{bytes(bruto).hex().upper()}' AS TEXT)"
    return literal_sql(texto)


def _linhas_da_tabela(conn, tabela):
    """(colunas, linhas). Cada coluna vem com o typeof e, quando é texto, em
    hexadecimal; aqui ela volta ao valor original (o texto como TextoBruto)."""
    colunas = [nome for (nome,) in conn.execute(
        "SELECT name FROM pragma_table_info(?) ORDER BY cid", (tabela,)).fetchall()]
    selecao = ", ".join(f"typeof({citar_id(c)}), CASE WHEN typeof({citar_id(c)}) = 'text' "
                        f"THEN hex({citar_id(c)}) ELSE {citar_id(c)} END" for c in colunas)
    try:
        cursor = conn.execute(f"SELECT {selecao} FROM {citar_id(tabela)} ORDER BY rowid")
    except Exception:          # tabela WITHOUT ROWID
        cursor = conn.execute(f"SELECT {selecao} FROM {citar_id(tabela)}")
    linhas = []
    for bruta in cursor.fetchall():
        linha = []
        for tipo, valor in zip(bruta[0::2], bruta[1::2]):
            linha.append(TextoBruto(bytes.fromhex(valor)) if tipo == "text" else valor)
        linhas.append(tuple(linha))
    return colunas, linhas


def escrever_dump(conn, arquivo):
    """Escreve o dump em `arquivo` (aberto em texto) e devolve as contagens."""
    objetos = objetos_do_banco(conn)
    tabelas = [(nome, sql) for tipo, nome, _, sql in objetos
               if tipo == "table" and sql and not nome_interno(nome)]
    indices = [sql for tipo, nome, tabela, sql in objetos
               if tipo == "index" and sql and not nome_interno(nome) and not nome_interno(tabela)]
    extras = [sql for tipo, nome, tabela, sql in objetos
              if tipo in ("view", "trigger") and sql and not nome_interno(nome) and not nome_interno(tabela)]
    tem_sequencia = any(tipo == "table" and nome == "sqlite_sequence" for tipo, nome, _, _ in objetos)

    arquivo.write("-- Dump do banco (ferramentas/backup_banco.py, PRO-94).\n")
    arquivo.write("-- Restaurar só num banco vazio: python ferramentas/backup_banco.py restaurar <arquivo>\n")
    arquivo.write(INICIO + "\n")
    for _, sql in tabelas:
        arquivo.write(sql + ";\n")
    for sql in indices:
        arquivo.write(sql + ";\n")
    contagens = {}
    for tabela, _ in tabelas:
        colunas, linhas = _linhas_da_tabela(conn, tabela)
        lista_colunas = ", ".join(citar_id(c) for c in colunas)
        for linha in linhas:
            valores = ", ".join(literal_sql(v) for v in linha)
            arquivo.write(f"INSERT INTO {citar_id(tabela)} ({lista_colunas}) VALUES ({valores});\n")
        contagens[tabela] = len(linhas)
    for sql in extras:
        arquivo.write(sql + ";\n")
    if tem_sequencia:
        arquivo.write("DELETE FROM sqlite_sequence;\n")
        for nome, seq in conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall():
            arquivo.write(f"INSERT INTO sqlite_sequence (name, seq) VALUES ({literal_sql(nome)}, {literal_sql(seq)});\n")
    arquivo.write(FIM + "\n")
    return contagens


def ler_comandos(texto):
    """Os comandos do dump, um por item, sem os comentários de linha inteira.
    Usa o sqlite3.complete_statement, que entende ';' e quebras de linha
    dentro de textos."""
    comandos, atual = [], ""
    # Só o "\n" separa linhas (o splitlines também cortaria em "\x0b", "\x1c",
    # " " e outros, que podem estar dentro de um texto).
    for linha in (pedaco + "\n" for pedaco in texto.split("\n")):
        if not atual and (not linha.strip() or linha.lstrip().startswith("--")):
            continue
        atual += linha
        if sqlite3.complete_statement(atual):
            comandos.append(atual.strip())
            atual = ""
    if atual.strip():
        raise ErroFerramenta("o dump termina no meio de um comando (arquivo cortado?)", 3)
    if len(comandos) < 2 or comandos[0] != INICIO or comandos[-1] != FIM:
        raise ErroFerramenta("o arquivo não é um dump desta ferramenta (falta o BEGIN ou o COMMIT)", 3)
    return comandos


def contagens_do_dump(comandos):
    contagens = {}
    for comando in comandos:
        achado = _INSERT.match(comando)
        if achado:
            tabela = achado.group(1).replace('""', '"')
            if not nome_interno(tabela):
                contagens[tabela] = contagens.get(tabela, 0) + 1
    return contagens


def imprimir_contagens(contagens, saida):
    print("Linhas por tabela:", file=saida)
    for tabela, quantidade in contagens.items():
        print(f"  {tabela}: {quantidade}", file=saida)


def _tentar(acao):
    try:
        acao()
    except Exception:
        pass


def recusar_arquivo_existente(caminho):
    if os.path.exists(caminho):
        raise ErroFerramenta(f"o arquivo já existe, escolha outro nome: {os.path.basename(caminho)}", 2)


# --- AÇÕES ---
def fazer_dump(conn, caminho, saida):
    recusar_arquivo_existente(caminho)
    parcial = caminho + ".parcial"
    try:
        with open(parcial, "w", encoding="utf-8", newline="\n") as arquivo:
            contagens = escrever_dump(conn, arquivo)
        os.replace(parcial, caminho)
    finally:
        if os.path.exists(parcial):
            os.remove(parcial)
    imprimir_contagens(contagens, saida)
    print(f"Dump gravado: {caminho} ({len(contagens)} tabelas, {sum(contagens.values())} linhas)", file=saida)


def restaurar(conn, comandos, confirmar, saida):
    existentes = tabelas_de_usuario(conn)
    if existentes:
        raise ErroFerramenta(f"RECUSADO: o banco já tem {len(existentes)} tabela(s). A restauração só grava "
                             "num banco vazio. Nada foi gravado.", 2)
    esperadas = contagens_do_dump(comandos)
    resposta = confirmar("Digite sim para restaurar o dump neste banco: ")
    if (resposta or "").strip().lower() != "sim":
        raise ErroFerramenta("Cancelado: nada foi gravado.", 2)
    numero = 0
    try:
        for numero, comando in enumerate(comandos, start=1):
            conn.execute(comando)
        _tentar(conn.commit)        # o COMMIT do dump já gravou; isto só garante
    except Exception as falha:
        _tentar(conn.rollback)
        _tentar(lambda: conn.execute("ROLLBACK"))
        raise ErroFerramenta(f"o comando {numero} de {len(comandos)} do dump falhou ({type(falha).__name__}: "
                             f"{falha}). A transação foi desfeita: nada foi gravado.", 1) from None
    contagens = contar_linhas(conn, tabelas_de_usuario(conn))
    imprimir_contagens(contagens, saida)
    batem = set(esperadas) <= set(contagens) and all(n == esperadas.get(t, 0) for t, n in contagens.items())
    if not batem:
        raise ErroFerramenta("ATENÇÃO: as contagens não batem com as do dump. Confira antes de usar este banco.", 1)
    print("Restauração concluída: as contagens batem com as do dump.", file=saida)


def montar_argumentos():
    p = argparse.ArgumentParser(description="Dump e restauração do banco do .env.")
    p.add_argument("acao", choices=("dump", "restaurar"))
    p.add_argument("arquivo", help="o arquivo .sql do dump")
    return p


def main(argv=None, conectar=conectar_turso, credenciais=carregar_credenciais, confirmar=input,
         saida=None, erro=None):
    saida = saida or sys.stdout
    erro = erro or sys.stderr
    args = montar_argumentos().parse_args(argv)
    segredos = []
    try:
        comandos = None
        if args.acao == "dump":
            recusar_arquivo_existente(args.arquivo)
        else:
            try:
                with open(args.arquivo, encoding="utf-8", newline="") as f:
                    comandos = ler_comandos(f.read())
            except OSError:
                raise ErroFerramenta(f"não consegui ler o arquivo: {os.path.basename(args.arquivo)}", 3) from None

        url, token = credenciais()
        segredos = [url, token]
        print(f"Banco: {rotulo_banco(url)}", file=saida)
        saida.flush()

        conn = conectar(url, token)
        try:
            if args.acao == "dump":
                try:
                    fazer_dump(conn, args.arquivo, saida)
                finally:
                    _tentar(conn.rollback)    # só leitura: nada a confirmar
            else:
                restaurar(conn, comandos, confirmar, saida)
        finally:
            conn.close()
        return 0
    except ErroFerramenta as e:
        print(f"ERRO: {ocultar_segredos(e, segredos)}" if e.codigo != 2 else ocultar_segredos(e, segredos),
              file=erro)
        return e.codigo
    except Exception as e:  # erro do banco ou da rede: nunca vaza segredo
        print(f"ERRO do banco ({type(e).__name__}): {ocultar_segredos(e, segredos)}", file=erro)
        return 1


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main())
