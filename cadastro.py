"""Regras do cadastro do usuário (PRO-96): nomes das categorias sem repetição,
dia de fechamento válido e a gravação de 'usuarios' e 'categorias'. Fonte
única usada pelo bot (bot.py, o cadastro antigo no chat) e pela API do Mini
App (POST /cadastro) — a mesma ideia do calculos.py e do registro.py.

Só depende da biblioteca padrão e do registro.py (a erro_passageiro): não
importa telegram, bot, dotenv nem libsql, não lê o .env e não abre conexão ao
ser importado. A conexão chega por parâmetro ('conn').

A parte do bot (normalizar_texto, dia_valido, montar_lista_categorias e
gravar_cadastro com substituir=True) faz exatamente o que o bot fazia antes da
extração (mesmo SHA-256 no testes_cadastro_caracterizacao.py) e não embrulha
exceções.

A parte da API (cadastrar_pela_api, no fim do arquivo) valida tudo, recusa
quem já tem cadastro e grava com renda 0.0 e horario_alerta NULL numa
transação só. Os erros viram ErroCadastro com um código (o mesmo padrão da
ErroRegistro do registro.py), e a API só traduz o código em status HTTP.
"""
import time
import unicodedata
from typing import List, NamedTuple

from registro import erro_passageiro


# --- REGRAS COMUNS (BOT E API) ---
def normalizar_texto(texto):
    """Forma de comparação sem maiúsculas, acentos e espaços repetidos
    ('  Alimentação ' -> 'alimentacao')."""
    sem_acento = ''.join(
        c for c in unicodedata.normalize('NFKD', texto) if not unicodedata.combining(c)
    )
    return ' '.join(sem_acento.casefold().split())


def dia_valido(dia):
    """Valida se o dia informado é um dia possível dentro de um mês (1 a 31)."""
    return 1 <= dia <= 31


def montar_lista_categorias(nomes):
    """Nomes das categorias do cadastro, na ordem: cada um passa por
    strip().capitalize(); vazios são descartados; repetidos ("Lazer, lazer",
    "Alimentação, alimentacao") viram uma categoria só, pela mesma comparação
    do renomear (normalizar_texto), mantendo a primeira grafia. O vínculo é
    por nome, então duas categorias iguais misturariam os registros."""
    categorias = []
    vistos = set()
    for nome in nomes:
        nome_cat = nome.strip().capitalize()
        chave = normalizar_texto(nome_cat)
        if not chave or chave in vistos:
            continue
        vistos.add(chave)
        categorias.append(nome_cat)
    return categorias


def gravar_cadastro(conn, user_id, nome, renda, dia, horario_alerta, categorias, momento, substituir,
                    commit=True):
    """Grava a linha de 'usuarios' e uma linha em 'categorias' por nome
    (orçamento e saldo 0, sem uso desde a PRO-83, mas nunca nulos: o código
    antigo, numa reversão, ainda os lê; data_criacao = 'momento',
    'AAAA-MM-DD HH:MM:SS' em Brasília).

    substituir=True (cadastro antigo do bot): INSERT OR REPLACE em
    'usuarios' e apaga e recria as categorias do usuário, como o bot sempre
    fez. substituir=False (API): INSERT simples, que o próprio banco recusa
    (chave primária) se o user_id já existir; nada é apagado.

    commit=False deixa a transação aberta para quem chamou."""
    if substituir:
        conn.execute(
            'INSERT OR REPLACE INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) '
            'VALUES (?, ?, ?, ?, ?)',
            (user_id, nome, renda, dia, horario_alerta)
        )
        conn.execute('DELETE FROM categorias WHERE user_id = ?', (user_id,))
    else:
        conn.execute(
            'INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) '
            'VALUES (?, ?, ?, ?, ?)',
            (user_id, nome, renda, dia, horario_alerta)
        )
    for cat_nome in categorias:
        conn.execute(
            'INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) '
            'VALUES (?, ?, ?, ?, ?)',
            (user_id, cat_nome, 0.0, 0.0, momento)
        )
    if commit:
        conn.commit()


# --- CADASTRO PELA API DO MINI APP (PRO-96) ---
NOME_MAXIMO = 30            # D4: cabe num botão (regra 4 do Regras.md)
CATEGORIA_MAXIMA = 30       # D4
CATEGORIAS_MAXIMAS = 20     # D4, contadas depois de juntar as repetidas

# Esperas entre as tentativas de gravar quando o banco está ocupado: até 4
# tentativas no total (a primeira e mais 3), como a gravar_com_chave do registro.py.
ESPERAS_BANCO_OCUPADO = (0.25, 0.5, 1.0)

# A consulta "o usuário já tem cadastro?", antes de gravar e depois de qualquer
# erro. Numa constante para a prova no Turso (ferramentas/prova_cadastro_turso.py)
# reconhecer a pré-checagem.
SQL_USUARIO_EXISTE = 'SELECT 1 FROM usuarios WHERE user_id = ?'


class ErroCadastro(Exception):
    """Pedido recusado, com o código que a API devolve em {"detail": codigo}:
    nome_invalido, dia_invalido, categorias_invalidas (422),
    usuario_ja_cadastrado (409) ou banco_ocupado (503). A mensagem é só o
    código: nunca o texto do usuário nem a mensagem do banco."""

    def __init__(self, codigo):
        super().__init__(codigo)
        self.codigo = codigo


class CadastroCriado(NamedTuple):
    """O que foi gravado agora: as categorias como ficaram no banco, na ordem."""
    user_id: int
    nome: str
    dia_fechamento: int
    categorias: List[str]


def validar_nome(nome):
    """Texto; aparado, de 1 a NOME_MAXIMO caracteres. Gravado como veio (sem
    mudar maiúsculas). Devolve o nome aparado ou ErroCadastro('nome_invalido')."""
    if not isinstance(nome, str):
        raise ErroCadastro('nome_invalido')
    nome = nome.strip()
    if not nome or len(nome) > NOME_MAXIMO:
        raise ErroCadastro('nome_invalido')
    return nome


def validar_dia(dia):
    """Inteiro de verdade (bool, texto e 5.5 não valem) de 1 a 31 (dia_valido).
    Devolve o dia ou ErroCadastro('dia_invalido')."""
    if isinstance(dia, bool) or not isinstance(dia, int) or not dia_valido(dia):
        raise ErroCadastro('dia_invalido')
    return dia


def validar_categorias(categorias):
    """Lista de textos, nenhum vazio (nem só espaços); cada um passa pela
    montar_lista_categorias (repetidas viram uma só, primeira grafia, na
    ordem); cada nome com até CATEGORIA_MAXIMA caracteres; de 1 a
    CATEGORIAS_MAXIMAS depois de juntar as repetidas. Devolve a lista como vai
    ser gravada ou ErroCadastro('categorias_invalidas')."""
    if not isinstance(categorias, list):
        raise ErroCadastro('categorias_invalidas')
    for nome in categorias:
        if not isinstance(nome, str) or not nome.strip():
            raise ErroCadastro('categorias_invalidas')
    lista = montar_lista_categorias(categorias)
    if not 1 <= len(lista) <= CATEGORIAS_MAXIMAS:
        raise ErroCadastro('categorias_invalidas')
    if any(len(nome) > CATEGORIA_MAXIMA for nome in lista):
        raise ErroCadastro('categorias_invalidas')
    return lista


def usuario_existe(conn, user_id):
    """True se já há linha em 'usuarios' para o user_id."""
    return conn.execute(SQL_USUARIO_EXISTE, (user_id,)).fetchone() is not None


def _desfazer(conn):
    """Rollback que não esconde o erro original se ele mesmo falhar."""
    try:
        conn.rollback()
    except Exception:
        pass


def _esperar(segundos):
    """Espera entre as tentativas (os testes trocam por uma que não dorme). A
    rota de cadastro é síncrona (roda em thread), então dormir aqui não trava
    o servidor."""
    time.sleep(segundos)


def cadastrar_pela_api(conn, user_id, nome, dia, categorias, momento):
    """Cadastro pelo Mini App: valida nome, dia e categorias, recusa quem já
    tem cadastro e grava 'usuarios' (renda 0.0, horario_alerta NULL: nenhum
    alerta diário) e as categorias (data_criacao = 'momento', Brasília) numa
    transação só, com um único commit. 'user_id' vem do initData validado.

    Em QUALQUER exceção (na pré-checagem, num INSERT ou no commit), desfaz a
    transação e consulta 'usuarios':
      - o usuário existe: ErroCadastro('usuario_ja_cadastrado'). É o toque
        duplo (dois pedidos passam juntos pela pré-checagem e o segundo bate
        na chave primária) e o commit que deu certo no servidor mas chegou
        como erro;
      - não existe e o erro é PASSAGEIRO (erro_passageiro do registro.py, ou
        a própria consulta falhou): espera (ESPERAS_BANCO_OCUPADO) e tenta
        tudo de novo numa transação nova. Esgotadas as 4 tentativas:
        ErroCadastro('banco_ocupado');
      - não existe e o erro não é passageiro: a exceção original sobe (a API
        responde 500, sem detalhes).
    A decisão não depende da classe do erro (o SQLite levanta
    sqlite3.IntegrityError; o libsql, ValueError com "SQLITE_CONSTRAINT").
    Cadastro em dobro é impossível: o user_id é a chave primária de
    'usuarios', e as categorias entram na mesma transação.

    Devolve CadastroCriado."""
    nome = validar_nome(nome)
    dia = validar_dia(dia)
    lista = validar_categorias(categorias)

    tentativas = len(ESPERAS_BANCO_OCUPADO) + 1
    for tentativa in range(tentativas):
        try:
            if usuario_existe(conn, user_id):
                raise ErroCadastro('usuario_ja_cadastrado')
            gravar_cadastro(conn, user_id, nome, 0.0, dia, None, lista, momento, substituir=False,
                            commit=False)
            conn.commit()
            return CadastroCriado(user_id, nome, dia, list(lista))
        except ErroCadastro:
            _desfazer(conn)
            raise
        except Exception as erro:
            _desfazer(conn)
            try:
                existe = usuario_existe(conn, user_id)
                passageiro = erro_passageiro(erro)
            except Exception:  # a consulta também falhou (stream quebrado): passageiro
                existe = False
                passageiro = True
            if existe:
                raise ErroCadastro('usuario_ja_cadastrado') from None
            if not passageiro:
                raise erro
            if tentativa == tentativas - 1:
                raise ErroCadastro('banco_ocupado') from None
            _esperar(ESPERAS_BANCO_OCUPADO[tentativa])
