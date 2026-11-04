"""Ferramenta do banco de TESTE (PRO-90): consulta e popula o banco `fin4ncetest`
sem abrir o painel do Turso.

Uso (a partir da raiz do repositório):

    python ferramentas/banco_teste.py "SELECT nome FROM categorias WHERE user_id = ?" -p 123
    python ferramentas/banco_teste.py --json "SELECT * FROM usuarios"
    python ferramentas/banco_teste.py --arquivo consulta.sql
    python ferramentas/banco_teste.py --escrita "INSERT INTO categorias (user_id, nome) VALUES (?, ?)" -p 123 -p Mercado

Opções:
    -p/--param VALOR   valor de um "?" da consulta, na ordem (número, null ou texto)
    --escrita          modo de escrita: aceita só INSERT ou UPDATE (UPDATE exige WHERE)
    --json             resultado em JSON (lista de objetos) em vez de tabela
    --arquivo CAMINHO  lê a consulta de um arquivo (um comando só)
    --env CAMINHO      arquivo de ambiente; padrão: .env.teste na raiz, se existir, senão .env

Travas:
    1. Antes de conectar, a URL do arquivo de ambiente tem de apontar para o
       banco fin4ncetest (primeiro pedaço do endereço exatamente igual a
       HOST_BANCO_TESTE). Senão, aborta.
    2. Leitura (padrão): um comando só, começando por SELECT ou WITH, sem
       nenhuma palavra de escrita; nunca faz commit.
    3. Escrita (--escrita): um comando só, INSERT ou UPDATE com WHERE. DELETE,
       DROP, ALTER, CREATE, PRAGMA e afins nunca passam, em modo nenhum.
    4. Nunca imprime token nem URL: toda mensagem de erro passa por um filtro
       que troca esses valores por ***. A saída padrão traz só o resultado.

Códigos de saída: 0 ok; 1 erro do banco; 2 comando bloqueado; 3 problema no
arquivo de ambiente ou banco que não é o de teste.
"""
import argparse
import json
import os
import re
import sys
from urllib.parse import urlparse

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --- TRAVA CONTRA PRODUÇÃO: O ÚNICO BANCO ACEITO ---
# No Turso, o endereço é "<banco>-<organização>.<região>.turso.io". A ferramenta
# só conecta se o primeiro pedaço do endereço for EXATAMENTE este (nem prefixo,
# nem "contém"): assim um banco "fin4ncetest-prod" ou de outra organização
# também é recusado. Para trocar o banco de teste, mude só estas duas linhas.
NOME_BANCO_TESTE = "fin4ncetest"
ORGANIZACAO_TURSO = "l1d3r"
HOST_BANCO_TESTE = f"{NOME_BANCO_TESTE}-{ORGANIZACAO_TURSO}"

# Palavras que nunca aparecem num comando aceito (fora de textos e comentários).
_PROIBIDAS = {
    "DELETE", "DROP", "ALTER", "CREATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM",
    "REINDEX", "ANALYZE", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
    "TRUNCATE",
}
_ESCRITA = {"INSERT", "UPDATE"}

# Textos entre aspas, identificadores entre aspas/colchetes e comentários: o que
# vier primeiro na leitura da esquerda para a direita é trocado por espaço.
_LITERAIS_E_COMENTARIOS = re.compile(
    r"'(?:[^']|'')*'"
    r'|"(?:[^"]|"")*"'
    r"|`[^`]*`"
    r"|\[[^\]]*\]"
    r"|--[^\n]*"
    r"|/\*.*?\*/",
    re.S,
)


class ErroFerramenta(Exception):
    """Erro com mensagem segura para mostrar e o código de saída."""

    def __init__(self, mensagem, codigo):
        super().__init__(mensagem)
        self.codigo = codigo


# --- AMBIENTE ---
def escolher_arquivo_env(caminho=None, raiz=RAIZ):
    """O arquivo de ambiente a usar: o informado, ou .env.teste na raiz se
    existir, ou .env na raiz."""
    if caminho:
        return caminho
    teste = os.path.join(raiz, ".env.teste")
    return teste if os.path.exists(teste) else os.path.join(raiz, ".env")


def carregar_credenciais(caminho):
    """(url, token) do arquivo, sem mexer em os.environ. Os erros citam só o
    nome do arquivo e o nome da variável, nunca o valor."""
    import logging
    from dotenv import dotenv_values

    # O python-dotenv avisa linhas que não entende; a saída fica só com o resultado.
    logging.getLogger("dotenv.main").setLevel(logging.ERROR)
    if not os.path.exists(caminho):
        raise ErroFerramenta(f"arquivo de ambiente não encontrado: {os.path.basename(caminho)}", 3)
    valores = dotenv_values(caminho)
    url = (valores.get("TURSO_DATABASE_URL") or "").strip()
    token = (valores.get("TURSO_AUTH_TOKEN") or "").strip()
    for nome, valor in (("TURSO_DATABASE_URL", url), ("TURSO_AUTH_TOKEN", token)):
        if not valor:
            raise ErroFerramenta(f"variável ausente em {os.path.basename(caminho)}: {nome}", 3)
    return url, token


def e_banco_de_teste(url):
    """Trava contra produção: o primeiro pedaço do endereço (antes do primeiro
    ponto) precisa ser exatamente HOST_BANCO_TESTE. O nome em outro lugar da
    URL (ex.: num parâmetro ou num subdomínio) não vale."""
    host = (urlparse(url).hostname or "").lower()
    return host.split(".")[0] == HOST_BANCO_TESTE


def ocultar_segredos(texto, segredos):
    """Troca cada segredo (e o endereço do banco) por ***."""
    texto = str(texto)
    for segredo in segredos:
        if not segredo:
            continue
        texto = texto.replace(segredo, "***")
        host = urlparse(segredo).hostname if "://" in segredo else None
        if host:
            texto = texto.replace(host, "***")
    return texto


# --- VALIDAÇÃO DO COMANDO ---
def validar_sql(sql, escrita=False):
    """Devolve o comando limpo (sem ';' no fim) se ele puder rodar no modo
    pedido; senão, ErroFerramenta com o motivo (código 2)."""
    comando = (sql or "").strip()
    while comando.endswith(";"):
        comando = comando[:-1].rstrip()
    if not comando:
        raise ErroFerramenta("comando vazio", 2)

    limpo = _LITERAIS_E_COMENTARIOS.sub(" ", comando)
    if ";" in limpo:
        raise ErroFerramenta("um comando por vez (há mais de um ';')", 2)

    palavras = re.findall(r"[A-Za-z_]+", limpo.upper())
    if not palavras:
        raise ErroFerramenta("comando sem palavra-chave", 2)
    primeira = palavras[0]

    proibidas = _PROIBIDAS.intersection(palavras)
    if proibidas:
        raise ErroFerramenta(f"palavra não permitida: {', '.join(sorted(proibidas))}", 2)

    if not escrita:
        if primeira not in ("SELECT", "WITH"):
            raise ErroFerramenta("modo leitura aceita só SELECT (ou WITH ... SELECT); "
                                 "para INSERT/UPDATE use --escrita", 2)
        escritas = _ESCRITA.intersection(palavras) | ({"REPLACE"} if re.search(r"\bREPLACE\s+INTO\b", limpo, re.I) else set())
        if escritas:
            raise ErroFerramenta(f"modo leitura não aceita {', '.join(sorted(escritas))}; use --escrita", 2)
        return comando

    if primeira not in _ESCRITA:
        raise ErroFerramenta("modo escrita aceita só INSERT ou UPDATE", 2)
    if re.search(r"\bOR\s+REPLACE\b", limpo, re.I):
        raise ErroFerramenta("INSERT OR REPLACE não é aceito (pode sobrescrever linhas)", 2)
    if primeira == "UPDATE" and "WHERE" not in palavras:
        raise ErroFerramenta("UPDATE sem WHERE não é aceito (mudaria a tabela inteira)", 2)
    return comando


def converter_parametro(valor):
    """'12' -> 12, '12.5' -> 12.5, 'null' -> None, '"12"' -> '12', 'Mercado' -> 'Mercado'."""
    try:
        convertido = json.loads(valor)
    except ValueError:
        return valor
    if isinstance(convertido, bool):
        return int(convertido)
    if convertido is None or isinstance(convertido, (int, float, str)):
        return convertido
    return valor


# --- EXECUÇÃO E SAÍDA ---
def conectar_turso(url, token):
    import libsql
    return libsql.connect(database=url, auth_token=token)


def executar(conn, comando, parametros, escrita):
    """Leitura: (colunas, linhas), sem commit. Escrita: (linhas_afetadas, id), com commit."""
    cursor = conn.execute(comando, parametros)
    if escrita:
        conn.commit()
        return getattr(cursor, "rowcount", None), getattr(cursor, "lastrowid", None)
    colunas = [d[0] for d in (cursor.description or [])]
    linhas = cursor.fetchall()
    try:
        conn.rollback()
    except Exception:
        pass
    return colunas, linhas


def _texto(valor):
    return "NULL" if valor is None else str(valor)


def formatar_tabela(colunas, linhas):
    if not colunas:
        return ""
    tabela = [colunas] + [[_texto(v) for v in linha] for linha in linhas]
    larguras = [max(len(l[i]) for l in tabela) for i in range(len(colunas))]
    def montar(linha):
        return " | ".join(c.ljust(larguras[i]) for i, c in enumerate(linha)).rstrip()
    saida = [montar(colunas), "-+-".join("-" * w for w in larguras)]
    saida += [montar(l) for l in tabela[1:]]
    return "\n".join(saida)


def montar_argumentos():
    p = argparse.ArgumentParser(description="Consulta e popula o banco de TESTE (fin4ncetest).")
    p.add_argument("sql", nargs="?", help="o comando SQL (um só)")
    p.add_argument("-p", "--param", action="append", default=[], help="valor de um '?', na ordem")
    p.add_argument("--escrita", action="store_true", help="aceita INSERT ou UPDATE (só no banco de teste)")
    p.add_argument("--json", action="store_true", help="resultado em JSON")
    p.add_argument("--arquivo", help="lê o comando de um arquivo")
    p.add_argument("--env", help="arquivo de ambiente (padrão: .env.teste, senão .env)")
    return p


def main(argv=None, conectar=conectar_turso, saida=None, erro=None):
    saida = saida or sys.stdout
    erro = erro or sys.stderr
    args = montar_argumentos().parse_args(argv)
    segredos = []
    try:
        if args.arquivo:
            try:
                with open(args.arquivo, encoding="utf-8") as f:
                    sql = f.read()
            except OSError:
                raise ErroFerramenta(f"não consegui ler o arquivo: {os.path.basename(args.arquivo)}", 2)
        else:
            sql = args.sql
        comando = validar_sql(sql, escrita=args.escrita)
        parametros = tuple(converter_parametro(v) for v in args.param)

        url, token = carregar_credenciais(escolher_arquivo_env(args.env))
        segredos = [url, token]
        if not e_banco_de_teste(url):
            raise ErroFerramenta(f"ABORTADO: o arquivo de ambiente não aponta para o {NOME_BANCO_TESTE}. "
                                 "Nada foi consultado.", 3)

        conn = conectar(url, token)
        try:
            if args.escrita:
                afetadas, novo_id = executar(conn, comando, parametros, escrita=True)
                print(f"linhas afetadas: {_texto(afetadas)}", file=saida)
                if comando.split(None, 1)[0].upper() == "INSERT":
                    print(f"id gerado: {_texto(novo_id)}", file=saida)
            else:
                colunas, linhas = executar(conn, comando, parametros, escrita=False)
                if args.json:
                    print(json.dumps([dict(zip(colunas, l)) for l in linhas], ensure_ascii=False, indent=2), file=saida)
                else:
                    tabela = formatar_tabela(colunas, linhas)
                    if tabela:
                        print(tabela, file=saida)
                    saida.flush()
                    print(f"({len(linhas)} linha{'s' if len(linhas) != 1 else ''})", file=erro)
        finally:
            conn.close()
        return 0
    except ErroFerramenta as e:
        print(f"ERRO: {ocultar_segredos(e, segredos)}", file=erro)
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
