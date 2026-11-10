"""Regras do gasto recorrente pela API do Mini App (PRO-87): validação, lista,
criação (uma vez só por chave), edição e exclusão, sem IA. Mesmo padrão do
cadastro.py e do registro.py.

Desde a PRO-102, também a receita recorrente (tabela receitas_recorrentes):
as mesmas regras do gasto, sem categoria nem estabelecimento, e o lançamento
da receita no dia (lancar_receita_recorrente, usada pelo job das 05:00 do
bot), gravado na tabela receitas pela registro.registrar_receita.

Só depende da biblioteca padrão, do registro.py e do cadastro.py: não importa
telegram, bot, dotenv nem libsql, não lê o .env e não abre conexão ao ser
importado. A conexão chega por parâmetro ('conn').

Os erros são registro.ErroRegistro com um código (a API só traduz o código em
status HTTP). As regras reaproveitadas não são reescritas aqui: valor
(registro.validar_valor), chave (registro.validar_chave e gravar_com_chave),
categorias do usuário (registro.listar_categorias), estabelecimento
(registro.normalizar_estabelecimento, até registro.ESTABELECIMENTO_MAXIMO),
banco ocupado (registro.erro_passageiro e registro.ESPERAS_BANCO_OCUPADO) e
dia (cadastro.dia_valido).

O bot continua gravando o recorrente pelo chat com os próprios comandos
(D2 da PRO-87): por enquanto, a gravação fica escrita nos dois lugares.
"""
from typing import List, NamedTuple, Optional

import cadastro
import registro
from registro import ErroRegistro

# D5: igual à TAMANHO_MAX_NOME_RECORRENTE do bot.py (cabe num botão; conferido
# por teste, sem importar o bot).
DESCRICAO_MAXIMA_RECORRENTE = 30

TIPO_CHAVE = 'recorrente'  # valor de chaves_registro.tipo (lancamento_id = gastos_recorrentes.id)
# PRO-102 (D6): cada tipo aponta para uma tabela só.
TIPO_CHAVE_RECEITA = 'receita_recorrente'  # lancamento_id = receitas_recorrentes.id

# Faixa do INTEGER do SQLite: um id fora dela nem chega ao banco (seria erro
# do driver, não "não encontrado").
_ID_MINIMO, _ID_MAXIMO = -2 ** 63, 2 ** 63 - 1


class CamposRecorrente(NamedTuple):
    """Campos do pedido já validados e limpos, prontos para gravar."""
    dia_cobranca: int
    valor: float
    descricao: str
    estabelecimento: Optional[str]
    categoria: str


class Recorrente(NamedTuple):
    """Um gasto recorrente lido do banco. 'repetido' só importa na criação:
    True quando a mesma chave já tinha sido gravada (devolve o original)."""
    id: int
    descricao: str
    estabelecimento: Optional[str]
    categoria: str
    valor: float
    dia_cobranca: int
    repetido: bool = False


class ListaRecorrentes(NamedTuple):
    """Os recorrentes do usuário (por dia de cobrança, depois id) e a soma dos
    valores. A soma é só para exibir: não é cálculo de gasto nem de Sobra, que
    ficam no calculos.py."""
    recorrentes: List[Recorrente]
    total_mensal: float


_SQL_RECORRENTE = '''SELECT id, descricao, estabelecimento, categoria_nome, valor, dia_cobranca
                     FROM gastos_recorrentes'''


def _validar_dia_valor_descricao(dia, valor, descricao):
    """Os 3 campos comuns ao gasto e à receita recorrente, nesta ordem
    (PRO-102: extraída da validar_recorrente, sem mudar o comportamento):
      1. 'dia_invalido': inteiro de verdade (bool, texto e 12.0 não valem) de
         1 a 31 (cadastro.dia_valido);
      2. 'valor_invalido': registro.validar_valor (maior que 0, até
         R$ 1.000.000,00, 2 casas);
      3. 'descricao_invalida': não é texto, ou fica vazia depois de aparada;
      4. 'descricao_longa': mais de DESCRICAO_MAXIMA_RECORRENTE depois de aparada.
    Devolve (dia, valor arredondado, descrição aparada)."""
    if isinstance(dia, bool) or not isinstance(dia, int) or not cadastro.dia_valido(dia):
        raise ErroRegistro('dia_invalido')
    valor = registro.validar_valor(valor)
    if valor is None:
        raise ErroRegistro('valor_invalido')
    if not isinstance(descricao, str) or not descricao.strip():
        raise ErroRegistro('descricao_invalida')
    descricao = descricao.strip()
    if len(descricao) > DESCRICAO_MAXIMA_RECORRENTE:
        raise ErroRegistro('descricao_longa')
    return dia, valor, descricao


def _nome_repetido(linhas, descricao, ignorar_id=None):
    """True se alguma das linhas (id, descricao) tem a mesma descrição, pela
    regra do "Editar Nome" do bot: casefold() e sem os espaços das pontas da
    gravada; acento conta. 'ignorar_id' tira o próprio item."""
    procurada = descricao.casefold()
    return any(
        (outra or '').strip().casefold() == procurada
        for outro_id, outra in linhas
        if ignorar_id is None or outro_id != ignorar_id
    )


def validar_recorrente(dia, valor, descricao, estabelecimento, categoria, categorias):
    """Confere o pedido na ordem do contrato e devolve CamposRecorrente, ou
    levanta ErroRegistro com o primeiro código que falhar:
      1. 'dia_invalido': inteiro de verdade (bool, texto e 12.0 não valem) de
         1 a 31 (cadastro.dia_valido);
      2. 'valor_invalido': registro.validar_valor (maior que 0, até
         R$ 1.000.000,00, 2 casas);
      3. 'descricao_invalida': não é texto, ou fica vazia depois de aparada;
      4. 'descricao_longa': mais de DESCRICAO_MAXIMA_RECORRENTE depois de aparada;
      5. 'estabelecimento_longo': mais de registro.ESTABELECIMENTO_MAXIMO
         (vazio, só espaços ou não texto vira None, pela
         registro.normalizar_estabelecimento);
      6. 'categoria_nao_encontrada': exatamente uma de 'categorias' (a lista
         do usuário, de registro.listar_categorias)."""
    dia, valor, descricao = _validar_dia_valor_descricao(dia, valor, descricao)
    estabelecimento = registro._texto_opcional(
        estabelecimento, registro.ESTABELECIMENTO_MAXIMO, 'estabelecimento_longo',
        registro.normalizar_estabelecimento
    )
    if not isinstance(categoria, str) or categoria not in categorias:
        raise ErroRegistro('categoria_nao_encontrada')
    return CamposRecorrente(dia, valor, descricao, estabelecimento, categoria)


def tem_descricao_repetida(conn, user_id, categoria, descricao, ignorar_id=None):
    """True se outro recorrente do usuário, NA MESMA CATEGORIA, já tem esta
    descrição (D8 = B). Mesma comparação do "Editar Nome" do bot
    (salvar_novo_nome_recorrente): em Python, com casefold() e sem os espaços
    das pontas da descrição gravada; acento conta ("Água" != "Agua"). O
    LOWER() do SQLite só ignora maiúsculas ASCII, por isso não é usado.
    'descricao' já vem aparada (validar_recorrente). 'ignorar_id' tira o
    próprio item (no PUT, salvar o nome que ele já tem não é repetição)."""
    outras = conn.execute(
        'SELECT id, descricao FROM gastos_recorrentes WHERE user_id = ? AND categoria_nome = ?',
        (user_id, categoria)
    ).fetchall()
    return _nome_repetido(outras, descricao, ignorar_id)


def _ler(linha, repetido=False):
    return Recorrente(linha[0], linha[1], linha[2], linha[3], linha[4], linha[5], repetido)


def buscar_recorrente(conn, user_id, recorrente_id, repetido=False):
    """Recorrente do usuário, ou None se não existe ou é de outro usuário (o
    id nunca basta sozinho: vai sempre com o user_id na consulta)."""
    if not _ID_MINIMO <= recorrente_id <= _ID_MAXIMO:
        return None
    linha = conn.execute(f'{_SQL_RECORRENTE} WHERE id = ? AND user_id = ?', (recorrente_id, user_id)).fetchone()
    return None if linha is None else _ler(linha, repetido)


def listar_recorrentes(conn, user_id):
    """ListaRecorrentes do usuário: ordenada por dia de cobrança e depois pelo
    id; total_mensal é a soma dos valores (0.0 sem nenhum)."""
    linhas = conn.execute(
        f'{_SQL_RECORRENTE} WHERE user_id = ? ORDER BY dia_cobranca, id', (user_id,)
    ).fetchall()
    recorrentes = [_ler(linha) for linha in linhas]
    return ListaRecorrentes(recorrentes, float(sum(r.valor for r in recorrentes)))


def _recorrente_da_chave(conn, user_id, chave_registro, repetido):
    """Recorrente apontado pela chave. Chave já usada num gasto ou numa
    receita, ou cujo recorrente foi apagado, dá 'chave_invalida' (a tela gera
    outra)."""
    if chave_registro.tipo != TIPO_CHAVE:
        raise ErroRegistro('chave_invalida')
    recorrente = buscar_recorrente(conn, user_id, chave_registro.lancamento_id, repetido)
    if recorrente is None:
        raise ErroRegistro('chave_invalida')
    return recorrente


def criar_recorrente_api(conn, user_id, chave, dia, valor, descricao, estabelecimento, categoria, momento):
    """POST /recorrentes: grava o recorrente UMA vez só por chave.

    1. Chave fora do formato: 'chave_invalida'. Chave já gravada por este
       usuário: devolve o recorrente original (como está agora), com
       repetido=True, sem validar o resto nem gravar (mesma regra do
       gravar_gasto_api); chave de gasto ou de receita, ou de recorrente
       apagado: 'chave_invalida'.
    2. validar_recorrente (ordem do contrato).
    3. Grava por registro.gravar_com_chave (tipo 'recorrente'), com
       criado_em = 'momento' (servidor, Brasília) e ultimo_ciclo_cobrado NULL:
       nada é lançado na hora (D7); o job das 05:00 lança no próximo dia de
       cobrança. Dentro da função 'gravar', na mesma transação: a categoria
       ainda existe (apagada no meio: 'categoria_nao_encontrada') e o nome não
       repete na categoria ('descricao_repetida'). Se repetir porque o MESMO
       pedido já gravou (toque duplo, reenvio depois de um erro de rede), a
       gravar_com_chave desfaz, acha a chave e devolve o original com
       repetido=True em vez do erro. Banco ocupado em todas as tentativas:
       'banco_ocupado'.

    Corrida aceita: dois pedidos com chaves DIFERENTES e o mesmo nome ao
    mesmo tempo podem passar os dois (a regra do nome é verificada no código,
    não por índice). O toque duplo do mesmo formulário é barrado pela chave."""
    chave = registro.validar_chave(chave)
    existente = registro.buscar_chave(conn, user_id, chave)
    if existente is not None:
        return _recorrente_da_chave(conn, user_id, existente, True)

    campos = validar_recorrente(dia, valor, descricao, estabelecimento, categoria,
                                registro.listar_categorias(conn, user_id))

    def gravar():
        cursor = conn.cursor()
        categoria_existe = cursor.execute(
            'SELECT 1 FROM categorias WHERE user_id = ? AND nome = ?', (user_id, campos.categoria)
        ).fetchone()
        if not categoria_existe:
            return None
        if tem_descricao_repetida(conn, user_id, campos.categoria, campos.descricao):
            raise ErroRegistro('descricao_repetida')
        cursor.execute(
            '''INSERT INTO gastos_recorrentes
               (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em, ultimo_ciclo_cobrado, estabelecimento)
               VALUES (?, ?, ?, ?, ?, ?, NULL, ?)''',
            (user_id, campos.categoria, campos.descricao, campos.valor, campos.dia_cobranca, momento,
             campos.estabelecimento)
        )
        return cursor.lastrowid

    chave_registro, repetido = registro.gravar_com_chave(conn, user_id, chave, TIPO_CHAVE, momento, gravar)
    if chave_registro is None:
        raise ErroRegistro('categoria_nao_encontrada')
    return _recorrente_da_chave(conn, user_id, chave_registro, repetido)


def _com_novas_tentativas(conn, operacao):
    """Roda 'operacao' (grava e faz o commit) com a regra de banco ocupado da
    registro.gravar_com_chave: em qualquer exceção, desfaz; ErroRegistro sobe
    na hora; erro passageiro (registro.erro_passageiro) espera
    (registro.ESPERAS_BANCO_OCUPADO) e tenta tudo de novo numa transação nova,
    até 4 tentativas, e depois 'banco_ocupado'; outro erro sobe como veio."""
    esperas = registro.ESPERAS_BANCO_OCUPADO
    tentativas = len(esperas) + 1
    for tentativa in range(tentativas):
        try:
            return operacao()
        except ErroRegistro:
            registro._desfazer(conn)
            raise
        except Exception as erro:
            registro._desfazer(conn)
            if not registro.erro_passageiro(erro):
                raise
            if tentativa == tentativas - 1:
                raise ErroRegistro('banco_ocupado') from None
            registro._esperar(esperas[tentativa])


def editar_recorrente(conn, user_id, recorrente_id, dia, valor, descricao, estabelecimento, categoria):
    """PUT /recorrentes/{id}: troca os 5 campos (dia, valor, descrição,
    estabelecimento e categoria) do recorrente do usuário. Ordem: campos
    (validar_recorrente) -> existência ('recorrente_nao_encontrado', igual
    para inexistente e de outro usuário) -> nome repetido na categoria do
    pedido, sem contar o próprio item ('descricao_repetida') -> UPDATE e
    commit, na mesma transação. NÃO mexe em ultimo_ciclo_cobrado nem em
    criado_em (a mudança vale a partir da próxima cobrança), nem nas
    transações já lançadas. Sem chave: repetir o mesmo PUT dá o mesmo
    resultado. Devolve o Recorrente como ficou gravado."""
    campos = validar_recorrente(dia, valor, descricao, estabelecimento, categoria,
                                registro.listar_categorias(conn, user_id))

    def operacao():
        if buscar_recorrente(conn, user_id, recorrente_id) is None:
            raise ErroRegistro('recorrente_nao_encontrado')
        if tem_descricao_repetida(conn, user_id, campos.categoria, campos.descricao, ignorar_id=recorrente_id):
            raise ErroRegistro('descricao_repetida')
        conn.execute(
            '''UPDATE gastos_recorrentes
               SET dia_cobranca = ?, valor = ?, descricao = ?, estabelecimento = ?, categoria_nome = ?
               WHERE id = ? AND user_id = ?''',
            (campos.dia_cobranca, campos.valor, campos.descricao, campos.estabelecimento, campos.categoria,
             recorrente_id, user_id)
        )
        conn.commit()
        return buscar_recorrente(conn, user_id, recorrente_id)

    return _com_novas_tentativas(conn, operacao)


def apagar_recorrente(conn, user_id, recorrente_id):
    """DELETE /recorrentes/{id}: apaga o recorrente do usuário e faz o commit.
    Nenhuma linha (inexistente, de outro usuário ou já apagado):
    'recorrente_nao_encontrado' (a tela trata o 404 repetido como "já
    apagado"). As transações já lançadas por ele ficam. Devolve o id."""
    def operacao():
        if buscar_recorrente(conn, user_id, recorrente_id) is None:
            raise ErroRegistro('recorrente_nao_encontrado')
        conn.execute('DELETE FROM gastos_recorrentes WHERE id = ? AND user_id = ?', (recorrente_id, user_id))
        conn.commit()
        return recorrente_id

    return _com_novas_tentativas(conn, operacao)


# --- RECEITA RECORRENTE (PRO-102) ---
# Mesmas regras do gasto recorrente (dia, valor, descrição até 30, nome
# repetido, chave de toque duplo, banco ocupado), sem categoria nem
# estabelecimento. O nome repetido vale para todas as receitas recorrentes do
# usuário. Nada é lançado na hora (D4): o job das 05:00 do bot lança no dia,
# pela lancar_receita_recorrente, na tabela receitas (a Sobra vem do
# calculos.py, que soma toda linha de receitas da fatura).
class CamposReceitaRecorrente(NamedTuple):
    """Campos do pedido já validados e limpos, prontos para gravar."""
    dia_recebimento: int
    valor: float
    descricao: str


class ReceitaRecorrente(NamedTuple):
    """Uma receita recorrente lida do banco. 'repetido' só importa na criação:
    True quando a mesma chave já tinha sido gravada (devolve a original)."""
    id: int
    descricao: str
    valor: float
    dia_recebimento: int
    repetido: bool = False


class ListaReceitasRecorrentes(NamedTuple):
    """As receitas recorrentes do usuário (por dia de recebimento, depois id)
    e a soma dos valores. A soma é só para exibir: a Sobra continua no
    calculos.py."""
    receitas_recorrentes: List[ReceitaRecorrente]
    total_mensal: float


class ReceitaRecorrenteLancada(NamedTuple):
    """O que o job precisa para a mensagem diária: o id da linha nova em
    'receitas', a descrição e o valor lançados."""
    receita_id: int
    descricao: str
    valor: float


_SQL_RECEITA_RECORRENTE = '''SELECT id, descricao, valor, dia_recebimento
                             FROM receitas_recorrentes'''


def validar_receita_recorrente(dia, valor, descricao):
    """Confere o pedido na ordem do contrato e devolve CamposReceitaRecorrente,
    ou levanta ErroRegistro com o primeiro código que falhar: 'dia_invalido',
    'valor_invalido', 'descricao_invalida', 'descricao_longa' (as mesmas
    regras do gasto recorrente, pela _validar_dia_valor_descricao)."""
    return CamposReceitaRecorrente(*_validar_dia_valor_descricao(dia, valor, descricao))


def tem_receita_repetida(conn, user_id, descricao, ignorar_id=None):
    """True se outra receita recorrente DO USUÁRIO já tem esta descrição (sem
    categoria: vale para todas as dele). Mesma comparação do gasto
    (tem_descricao_repetida): casefold() e sem os espaços das pontas da
    gravada; acento conta. 'descricao' já vem aparada. 'ignorar_id' tira o
    próprio item (no PUT)."""
    outras = conn.execute(
        'SELECT id, descricao FROM receitas_recorrentes WHERE user_id = ?', (user_id,)
    ).fetchall()
    return _nome_repetido(outras, descricao, ignorar_id)


def _ler_receita(linha, repetido=False):
    return ReceitaRecorrente(linha[0], linha[1], linha[2], linha[3], repetido)


def buscar_receita_recorrente(conn, user_id, receita_recorrente_id, repetido=False):
    """Receita recorrente do usuário, ou None se não existe ou é de outro
    usuário (o id vai sempre com o user_id na consulta)."""
    if not _ID_MINIMO <= receita_recorrente_id <= _ID_MAXIMO:
        return None
    linha = conn.execute(
        f'{_SQL_RECEITA_RECORRENTE} WHERE id = ? AND user_id = ?', (receita_recorrente_id, user_id)
    ).fetchone()
    return None if linha is None else _ler_receita(linha, repetido)


def listar_receitas_recorrentes(conn, user_id):
    """ListaReceitasRecorrentes do usuário: ordenada por dia de recebimento e
    depois pelo id; total_mensal é a soma dos valores (0.0 sem nenhuma)."""
    linhas = conn.execute(
        f'{_SQL_RECEITA_RECORRENTE} WHERE user_id = ? ORDER BY dia_recebimento, id', (user_id,)
    ).fetchall()
    receitas = [_ler_receita(linha) for linha in linhas]
    return ListaReceitasRecorrentes(receitas, float(sum(r.valor for r in receitas)))


def _receita_recorrente_da_chave(conn, user_id, chave_registro, repetido):
    """Receita recorrente apontada pela chave. Chave de gasto, de receita
    avulsa ou de gasto recorrente, ou de receita recorrente apagada, dá
    'chave_invalida' (a tela gera outra)."""
    if chave_registro.tipo != TIPO_CHAVE_RECEITA:
        raise ErroRegistro('chave_invalida')
    receita = buscar_receita_recorrente(conn, user_id, chave_registro.lancamento_id, repetido)
    if receita is None:
        raise ErroRegistro('chave_invalida')
    return receita


def criar_receita_recorrente_api(conn, user_id, chave, dia, valor, descricao, momento):
    """POST /receitas-recorrentes: grava a receita recorrente UMA vez só por
    chave, com a mesma regra da criar_recorrente_api:

    1. Chave fora do formato: 'chave_invalida'. Chave já gravada por este
       usuário: devolve a receita recorrente original (como está agora), com
       repetido=True, sem validar o resto nem gravar; chave de outro tipo, ou
       de receita recorrente apagada: 'chave_invalida'.
    2. validar_receita_recorrente (ordem do contrato).
    3. Grava por registro.gravar_com_chave (tipo 'receita_recorrente'), com
       criado_em = 'momento' (servidor, Brasília) e ultimo_ciclo_recebido
       NULL: nada é lançado na hora (D4). Dentro da função 'gravar', na mesma
       transação, o nome não pode repetir ('descricao_repetida'); se repetir
       porque o MESMO pedido já gravou (toque duplo, reenvio), a
       gravar_com_chave acha a chave e devolve a original com repetido=True.
       Banco ocupado em todas as tentativas: 'banco_ocupado'.

    Corrida aceita (como no gasto): dois pedidos com chaves DIFERENTES e o
    mesmo nome ao mesmo tempo podem passar os dois."""
    chave = registro.validar_chave(chave)
    existente = registro.buscar_chave(conn, user_id, chave)
    if existente is not None:
        return _receita_recorrente_da_chave(conn, user_id, existente, True)

    campos = validar_receita_recorrente(dia, valor, descricao)

    def gravar():
        if tem_receita_repetida(conn, user_id, campos.descricao):
            raise ErroRegistro('descricao_repetida')
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO receitas_recorrentes
               (user_id, descricao, valor, dia_recebimento, criado_em, ultimo_ciclo_recebido)
               VALUES (?, ?, ?, ?, ?, NULL)''',
            (user_id, campos.descricao, campos.valor, campos.dia_recebimento, momento)
        )
        return cursor.lastrowid

    chave_registro, repetido = registro.gravar_com_chave(conn, user_id, chave, TIPO_CHAVE_RECEITA, momento,
                                                         gravar)
    return _receita_recorrente_da_chave(conn, user_id, chave_registro, repetido)


def editar_receita_recorrente(conn, user_id, receita_recorrente_id, dia, valor, descricao):
    """PUT /receitas-recorrentes/{id}: troca os 3 campos (dia, valor e
    descrição) da receita recorrente do usuário. Ordem: campos
    (validar_receita_recorrente) -> existência
    ('receita_recorrente_nao_encontrada', igual para inexistente e de outro
    usuário) -> nome repetido, sem contar o próprio item ('descricao_repetida')
    -> UPDATE e commit. NÃO mexe em ultimo_ciclo_recebido nem em criado_em,
    nem nas receitas já lançadas. Devolve a ReceitaRecorrente como ficou."""
    campos = validar_receita_recorrente(dia, valor, descricao)

    def operacao():
        if buscar_receita_recorrente(conn, user_id, receita_recorrente_id) is None:
            raise ErroRegistro('receita_recorrente_nao_encontrada')
        if tem_receita_repetida(conn, user_id, campos.descricao, ignorar_id=receita_recorrente_id):
            raise ErroRegistro('descricao_repetida')
        conn.execute(
            '''UPDATE receitas_recorrentes SET dia_recebimento = ?, valor = ?, descricao = ?
               WHERE id = ? AND user_id = ?''',
            (campos.dia_recebimento, campos.valor, campos.descricao, receita_recorrente_id, user_id)
        )
        conn.commit()
        return buscar_receita_recorrente(conn, user_id, receita_recorrente_id)

    return _com_novas_tentativas(conn, operacao)


def apagar_receita_recorrente(conn, user_id, receita_recorrente_id):
    """DELETE /receitas-recorrentes/{id}: apaga a receita recorrente do
    usuário e faz o commit. Nenhuma linha (inexistente, de outro usuário ou
    já apagada): 'receita_recorrente_nao_encontrada'. As receitas já lançadas
    ficam em 'receitas' (o receita_recorrente_id delas passa a apontar para
    um id que não existe mais: o vínculo é só informativo). Devolve o id."""
    def operacao():
        if buscar_receita_recorrente(conn, user_id, receita_recorrente_id) is None:
            raise ErroRegistro('receita_recorrente_nao_encontrada')
        conn.execute('DELETE FROM receitas_recorrentes WHERE id = ? AND user_id = ?',
                     (receita_recorrente_id, user_id))
        conn.commit()
        return receita_recorrente_id

    return _com_novas_tentativas(conn, operacao)


def lancar_receita_recorrente(conn, user_id, receita_recorrente_id, ciclo, momento):
    """Lança a receita recorrente no dia (job das 05:00 do bot). Relê a linha
    na hora (WHERE id = ? AND user_id = ?): se não existe mais, ou se
    ultimo_ciclo_recebido já é 'ciclo' ('AAAA-MM' do calendário), devolve
    None sem gravar nada. Senão, grava a receita em 'receitas' pela
    registro.registrar_receita (origem='recorrente', receita_recorrente_id e
    data = 'momento', em Brasília; sem commit), marca ultimo_ciclo_recebido =
    'ciclo' e faz UM commit: as duas coisas ou nenhuma. Em erro, desfaz e a
    exceção sobe (o job registra e segue para o próximo item). Não calcula
    nada: a receita entra na Sobra pelo calculos.py. Devolve
    ReceitaRecorrenteLancada."""
    linha = conn.execute(
        'SELECT descricao, valor, ultimo_ciclo_recebido FROM receitas_recorrentes WHERE id = ? AND user_id = ?',
        (receita_recorrente_id, user_id)
    ).fetchone()
    if linha is None:
        return None
    descricao, valor, ultimo_ciclo = linha
    if ultimo_ciclo == ciclo:
        return None
    try:
        receita_id = registro.registrar_receita(
            conn, user_id, valor, momento, descricao, origem='recorrente', commit=False,
            receita_recorrente_id=receita_recorrente_id
        )
        conn.execute(
            'UPDATE receitas_recorrentes SET ultimo_ciclo_recebido = ? WHERE id = ? AND user_id = ?',
            (ciclo, receita_recorrente_id, user_id)
        )
        conn.commit()
    except Exception:
        registro._desfazer(conn)
        raise
    return ReceitaRecorrenteLancada(receita_id, descricao, valor)
