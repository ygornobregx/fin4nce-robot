"""Regras da categoria (PRO-104): nome, nome repetido, limite, ranking do mês,
criar, renomear (com a propagação do nome) e excluir. Fonte única usada pela
API do Mini App (rotas /categorias) e pelo bot (Nova Categoria, Editar Nome e
Excluir Categoria do chat). Mesmo padrão do cadastro.py, do registro.py e do
recorrentes.py.

Só depende da biblioteca padrão e dos módulos de regra (calculos.py,
cadastro.py, registro.py e recorrentes.py): não importa telegram, bot, dotenv
nem libsql, não lê o .env e não abre conexão ao ser importado. A conexão chega
por parâmetro ('conn').

Os erros são registro.ErroRegistro com um código (a API só traduz o código em
status HTTP; o bot, em mensagem). As regras reaproveitadas não são reescritas
aqui: comparação de nomes (cadastro.normalizar_texto), tamanho e limite
(cadastro.CATEGORIA_MAXIMA e cadastro.CATEGORIAS_MAXIMAS), chave de toque
duplo (registro.validar_chave, buscar_chave e gravar_com_chave), banco ocupado
(recorrentes._com_novas_tentativas) e o gasto do mês
(calculos.calcular_resumo_fatura: aqui só se junta o id e se ordena).

O vínculo da categoria com os gastos é pelo NOME (não pelo id): renomear
propaga o nome para as 5 colunas da _REFERENCIAS_NOME_CATEGORIA, e excluir
apaga só a linha de 'categorias' (os gastos ficam no histórico e no total).

Regra do nome repetido (D6): a da categoria, sem caixa, SEM acento e com os
espaços juntos (cadastro.normalizar_texto): "Saude" = "Saúde". É diferente,
de propósito, da regra do nome do recorrente (recorrentes.py: casefold(), o
acento conta), que não muda.
"""
from typing import List, NamedTuple, Optional

import cadastro
import recorrentes
import registro
from calculos import calcular_resumo_fatura, obter_ciclo_atual
from registro import ErroRegistro

LIMITE_CATEGORIAS = cadastro.CATEGORIAS_MAXIMAS   # D11: até 20 categorias por usuário
NOME_MAXIMO = cadastro.CATEGORIA_MAXIMA           # D6: até 30 caracteres

TIPO_CHAVE = 'categoria'  # valor de chaves_registro.tipo (lancamento_id = categorias.id)

# Toda coluna que guarda o nome de uma categoria (o vínculo é por nome, não por
# id). Ao criar uma nova referência por nome em outra tabela, inclua-a aqui, ou
# o rename deixará esses registros órfãos.
_REFERENCIAS_NOME_CATEGORIA = (
    ('transacoes', 'categoria_nome'),
    ('parcelamentos', 'categoria_nome'),
    ('gastos_recorrentes', 'categoria_nome'),
    ('transferencias', 'categoria_origem'),
    ('transferencias', 'categoria_destino'),
)

def renomear_categoria(conn, user_id, cat_id, nome_antigo, novo_nome):
    """Renomeia a categoria e propaga o novo nome para todas as tabelas que a
    referenciam pelo nome, numa única transação: se qualquer UPDATE falhar, o
    rollback desfaz tudo e o erro é propagado para quem chamou."""
    try:
        conn.execute('UPDATE categorias SET nome = ? WHERE id = ? AND user_id = ?', (novo_nome, cat_id, user_id))
        for tabela, coluna in _REFERENCIAS_NOME_CATEGORIA:
            conn.execute(
                f'UPDATE {tabela} SET {coluna} = ? WHERE user_id = ? AND {coluna} = ?',
                (novo_nome, user_id, nome_antigo)
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class Categoria(NamedTuple):
    """Uma categoria lida do banco. 'repetido' só importa na criação pela API:
    True quando a mesma chave já tinha sido gravada (devolve a original)."""
    id: int
    nome: str
    repetido: bool = False


class ItemRanking(NamedTuple):
    """Uma linha do ranking. Categoria já excluída que ainda tem gasto na
    fatura (D5 = A): id None e ativa False."""
    id: Optional[int]
    nome: str
    gasto: float
    ativa: bool


class Ranking(NamedTuple):
    """O ranking da fatura vigente: 'ano_mes' e 'total_gasto' são os da
    calculos.calcular_resumo_fatura (os mesmos do /resumo); 'limite' é o
    LIMITE_CATEGORIAS."""
    ano_mes: Optional[str]
    total_gasto: float
    categorias: List[ItemRanking]
    limite: int


def validar_nome(nome):
    """Nome da categoria como vai ser gravado: texto, strip().capitalize()
    (igual ao bot e ao cadastro), com 1 a NOME_MAXIMO caracteres. Também
    recusa o nome que não sobra nada na comparação (cadastro.normalizar_texto
    vazio, ex.: só acentos soltos), como o cadastro faz. Senão,
    ErroRegistro('nome_invalido')."""
    if not isinstance(nome, str):
        raise ErroRegistro('nome_invalido')
    nome = nome.strip().capitalize()
    if not nome or len(nome) > NOME_MAXIMO or not cadastro.normalizar_texto(nome):
        raise ErroRegistro('nome_invalido')
    return nome


def buscar_nome_repetido(conn, user_id, nome, ignorar_id=None):
    """O nome (como está gravado) da categoria do usuário igual a 'nome' pela
    cadastro.normalizar_texto (sem caixa, sem acento, espaços juntos), ou
    None. 'ignorar_id' tira a própria categoria (no renomear, corrigir só a
    caixa ou o acento do próprio nome é permitido)."""
    procurado = cadastro.normalizar_texto(nome)
    linhas = conn.execute('SELECT id, nome FROM categorias WHERE user_id = ? ORDER BY id', (user_id,)).fetchall()
    for outro_id, outro_nome in linhas:
        if ignorar_id is not None and outro_id == ignorar_id:
            continue
        if cadastro.normalizar_texto(outro_nome or '') == procurado:
            return outro_nome
    return None


def tem_nome_repetido(conn, user_id, nome, ignorar_id=None):
    """True se outra categoria do usuário tem o mesmo nome (buscar_nome_repetido)."""
    return buscar_nome_repetido(conn, user_id, nome, ignorar_id) is not None


def contar_categorias(conn, user_id):
    """Quantas linhas de 'categorias' o usuário tem."""
    return conn.execute('SELECT COUNT(*) FROM categorias WHERE user_id = ?', (user_id,)).fetchone()[0]


def buscar_categoria(conn, user_id, categoria_id, repetido=False):
    """Categoria do usuário, ou None se não existe ou é de outro usuário (o
    id nunca basta sozinho: vai sempre com o user_id na consulta)."""
    if not recorrentes._ID_MINIMO <= categoria_id <= recorrentes._ID_MAXIMO:
        return None
    linha = conn.execute(
        'SELECT id, nome FROM categorias WHERE id = ? AND user_id = ?', (categoria_id, user_id)
    ).fetchone()
    return None if linha is None else Categoria(linha[0], linha[1], repetido)


def listar_ranking(conn, user_id, dia_fechamento, agora=None):
    """Ranking da fatura vigente (D4 = A: a mesma da home). O gasto de cada
    categoria e o total vêm SÓ da calculos.calcular_resumo_fatura; aqui só se
    junta o id e se ordena.

    Cada item do resumo vira ItemRanking: se existe linha em 'categorias' do
    usuário com esse nome, id dela e ativa=True (nomes repetidos antigos na
    tabela: os ids são associados na ordem do id, um por item, na mesma ordem
    em que o resumo os lista); senão (categoria excluída com gasto), id None e
    ativa=False. Ordem: gasto decrescente (comparado em centavos, como a tela
    mostra), depois o nome pela cadastro.normalizar_texto ("Água" no "A"),
    depois o id (excluída por último)."""
    ano_mes = obter_ciclo_atual(dia_fechamento, agora)
    resumo = calcular_resumo_fatura(conn, user_id, ano_mes, dia_fechamento)

    ids_por_nome = {}
    for categoria_id, nome in conn.execute(
            'SELECT id, nome FROM categorias WHERE user_id = ? ORDER BY id', (user_id,)).fetchall():
        ids_por_nome.setdefault(nome, []).append(categoria_id)

    itens = []
    for nome, gasto in resumo.categorias:
        ids = ids_por_nome.get(nome)
        if ids:
            itens.append(ItemRanking(ids.pop(0), nome, gasto, True))
        else:
            itens.append(ItemRanking(None, nome, gasto, False))

    itens.sort(key=lambda item: (-round(item.gasto, 2), cadastro.normalizar_texto(item.nome or ''),
                                 item.id is None, item.id or 0))
    return Ranking(resumo.ano_mes, resumo.total_gasto, itens, LIMITE_CATEGORIAS)


def _conferir_limite_e_nome(conn, user_id, nome):
    """As duas regras da criação, nesta ordem: 'limite_categorias' (o usuário
    já tem LIMITE_CATEGORIAS linhas em 'categorias') e 'nome_repetido'."""
    if contar_categorias(conn, user_id) >= LIMITE_CATEGORIAS:
        raise ErroRegistro('limite_categorias')
    if tem_nome_repetido(conn, user_id, nome):
        raise ErroRegistro('nome_repetido')


def _inserir(conn, user_id, nome, momento):
    """INSERT da categoria, sem commit: orçamento e saldo 0.0 (sem uso, nunca
    nulos) e data_criacao = 'momento' ('AAAA-MM-DD HH:MM:SS' em Brasília).
    Devolve o id novo."""
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) VALUES (?, ?, ?, ?, ?)',
        (user_id, nome, 0.0, 0.0, momento)
    )
    return cursor.lastrowid


def _categoria_da_chave(conn, user_id, chave_registro, repetido):
    """Categoria apontada pela chave. Chave já usada noutro tipo (gasto,
    receita, recorrente) ou cuja categoria foi excluída dá 'chave_invalida'
    (a tela gera outra)."""
    if chave_registro.tipo != TIPO_CHAVE:
        raise ErroRegistro('chave_invalida')
    categoria = buscar_categoria(conn, user_id, chave_registro.lancamento_id, repetido)
    if categoria is None:
        raise ErroRegistro('chave_invalida')
    return categoria


def criar_categoria_api(conn, user_id, chave, nome, momento):
    """POST /categorias: grava a categoria UMA vez só por chave (D12).

    1. Chave fora do formato: 'chave_invalida'. Chave já gravada por este
       usuário: devolve a categoria como está agora, com repetido=True, sem
       validar o resto nem gravar; chave de outro tipo, ou de categoria já
       excluída: 'chave_invalida'.
    2. validar_nome ('nome_invalido').
    3. Grava por registro.gravar_com_chave (tipo 'categoria'). Dentro da
       função 'gravar', na mesma transação: 'limite_categorias' e
       'nome_repetido'. Se repetir porque o MESMO pedido já gravou (toque
       duplo, reenvio depois de um erro de rede), a gravar_com_chave acha a
       chave e devolve a original com repetido=True em vez do erro. Banco
       ocupado em todas as tentativas: 'banco_ocupado'.

    Corrida aceita (como no recorrente): dois pedidos com chaves DIFERENTES e
    o mesmo nome ao mesmo tempo podem passar os dois (a regra do nome é
    verificada no código, não por índice)."""
    chave = registro.validar_chave(chave)
    existente = registro.buscar_chave(conn, user_id, chave)
    if existente is not None:
        return _categoria_da_chave(conn, user_id, existente, True)

    nome = validar_nome(nome)

    def gravar():
        _conferir_limite_e_nome(conn, user_id, nome)
        return _inserir(conn, user_id, nome, momento)

    chave_registro, repetido = registro.gravar_com_chave(conn, user_id, chave, TIPO_CHAVE, momento, gravar)
    return _categoria_da_chave(conn, user_id, chave_registro, repetido)


def criar_categoria(conn, user_id, nome, momento):
    """"Nova Categoria" do chat do bot (sem chave): as mesmas regras da
    criar_categoria_api ('nome_invalido', 'limite_categorias',
    'nome_repetido'), INSERT e commit, com as novas tentativas do banco
    ocupado ('banco_ocupado'). Devolve a Categoria gravada."""
    nome = validar_nome(nome)

    def operacao():
        _conferir_limite_e_nome(conn, user_id, nome)
        categoria_id = _inserir(conn, user_id, nome, momento)
        conn.commit()
        return Categoria(categoria_id, nome)

    return recorrentes._com_novas_tentativas(conn, operacao)


def renomear_categoria_api(conn, user_id, categoria_id, nome):
    """PUT /categorias/{id}: renomeia a categoria do usuário. Ordem:
    validar_nome ('nome_invalido') -> existência ('categoria_inexistente',
    igual para inexistente e de outro usuário) -> o mesmo nome de agora:
    devolve sem mudar nada -> nome repetido com OUTRA categoria
    ('nome_repetido'; corrigir só a caixa ou o acento da própria é permitido)
    -> renomear_categoria (a categoria e as 5 colunas de referência do
    usuário, numa transação só; D7: sempre, não existe "só daqui para
    frente"). Banco ocupado: novas tentativas e depois 'banco_ocupado'.
    Devolve a Categoria como ficou."""
    nome = validar_nome(nome)

    def operacao():
        atual = buscar_categoria(conn, user_id, categoria_id)
        if atual is None:
            raise ErroRegistro('categoria_inexistente')
        if atual.nome == nome:
            return atual
        if tem_nome_repetido(conn, user_id, nome, ignorar_id=categoria_id):
            raise ErroRegistro('nome_repetido')
        renomear_categoria(conn, user_id, categoria_id, atual.nome, nome)
        return buscar_categoria(conn, user_id, categoria_id)

    return recorrentes._com_novas_tentativas(conn, operacao)


def tem_gasto_recorrente(conn, user_id, nome):
    """True se algum gasto recorrente do usuário está na categoria 'nome'
    (mesmo nome gravado, como o job das 05:00 procura)."""
    return conn.execute(
        'SELECT 1 FROM gastos_recorrentes WHERE user_id = ? AND categoria_nome = ? LIMIT 1', (user_id, nome)
    ).fetchone() is not None


def excluir_categoria(conn, user_id, categoria_id):
    """DELETE /categorias/{id} e "Excluir Categoria" do bot. Ordem:
    existência ('categoria_inexistente': não existe, é de outro usuário ou já
    foi excluída) -> tem gasto recorrente ('categoria_com_recorrentes', D9)
    -> é a única categoria do usuário ('ultima_categoria', D10) -> DELETE só
    da linha de 'categorias' (com o user_id) e commit. Não apaga nada de
    outra tabela (D8): os gastos e as parcelas ficam com o nome e continuam
    no histórico e no total da fatura. Banco ocupado: novas tentativas e
    depois 'banco_ocupado'. Devolve o id."""
    def operacao():
        atual = buscar_categoria(conn, user_id, categoria_id)
        if atual is None:
            raise ErroRegistro('categoria_inexistente')
        if tem_gasto_recorrente(conn, user_id, atual.nome):
            raise ErroRegistro('categoria_com_recorrentes')
        if contar_categorias(conn, user_id) <= 1:
            raise ErroRegistro('ultima_categoria')
        conn.execute('DELETE FROM categorias WHERE id = ? AND user_id = ?', (categoria_id, user_id))
        conn.commit()
        return categoria_id

    return recorrentes._com_novas_tentativas(conn, operacao)
