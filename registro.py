"""Regras do registro de gasto: a interpretação do texto pela IA e a gravação
no banco. Fonte única usada pelo bot (bot.py) e, a partir da PRO-88, pela API
do Mini App — a mesma ideia do calculos.py.

O bot cuida só do Telegram: mensagens, botões, estados da conversa e o
user_data. Tudo o que decide o que vai para o banco (o prompt com as
categorias do usuário, a leitura da resposta da IA, o INSERT do gasto avulso
e do parcelado, as edições do último gasto e, desde a PRO-86, a validação e o
INSERT da receita avulsa) fica aqui, para o bot e a API gravarem exatamente
as mesmas linhas.

Só depende da biblioteca padrão e do calculos.py: não importa telegram, bot,
dotenv nem libsql, não lê o .env e não abre conexão ao ser importado. A
conexão chega por parâmetro ('conn'), e o cliente da IA é criado só na
primeira chamada (obter_cliente_ia), com o google.genai importado lá dentro:
assim a API importa este módulo sem ter o pacote nem a chave da IA.

As exceções não são embrulhadas: a mensagem de erro do bot mostra str(e)
("Erro: 'valor'"), e uma classe de erro nova mudaria o texto visível.

A parte da API (PRO-88, no fim do arquivo) é outra: prévia com o mesmo prompt,
validação de tudo de novo na gravação, chave de toque duplo gravada na mesma
transação do lançamento, e os erros viram ErroRegistro com um código (nunca
str(e) da IA). O bot usa só a validar_valor desta parte (PRO-93).
"""
import asyncio
import json
import re
import time
from datetime import datetime
from typing import NamedTuple, Optional

from calculos import FUSO_BRASILIA, calcular_ciclo_fatura

# Modelo do Gemini usado em todas as chamadas (registro de gasto e recorrente).
MODELO_IA = 'gemini-3.6-flash'

# --- IA ---
_cliente_ia = None


def obter_cliente_ia():
    """Devolve o cliente do Gemini, criado na primeira chamada e reaproveitado
    nas seguintes. Sob demanda (e não no import) para que a API e os testes
    importem este módulo sem a chave nem o pacote google.genai. Sem a chave,
    genai.Client() levanta ValueError: por isso o bot chama esta função logo
    ao iniciar, para continuar não subindo quando falta a chave."""
    global _cliente_ia
    if _cliente_ia is None:
        from google import genai
        _cliente_ia = genai.Client()
    return _cliente_ia


def definir_cliente_ia(cliente):
    """Troca o cliente da IA (costura para os testes usarem uma IA falsa, sem
    rede). O código de produção não chama esta função."""
    global _cliente_ia
    _cliente_ia = cliente


async def gerar_conteudo_com_retry(prompt, tentativas=3, espera_inicial=2):
    """
    Chama a API do Gemini com retry automático (backoff exponencial) quando o
    modelo está sobrecarregado (erro 503/UNAVAILABLE) ou há limite de taxa (429).
    Para outros erros, propaga imediatamente sem tentar de novo.

    Desde a PRO-88 (D4), a chamada síncrona do pacote roda numa thread
    (asyncio.to_thread): enquanto o Gemini responde, o bot continua atendendo
    os outros usuários e a API não trava o servidor. O cliente é obtido aqui,
    fora da thread, e as novas tentativas, as esperas e as exceções são as
    mesmas de antes.
    """
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            return await asyncio.to_thread(
                obter_cliente_ia().models.generate_content,
                model=MODELO_IA,
                contents=prompt,
            )
        except Exception as e:
            ultimo_erro = e
            erro_str = str(e)
            eh_erro_temporario = '503' in erro_str or 'UNAVAILABLE' in erro_str or '429' in erro_str
            ultima_tentativa = tentativa == tentativas - 1
            if eh_erro_temporario and not ultima_tentativa:
                await asyncio.sleep(espera_inicial * (2 ** tentativa))
                continue
            raise ultimo_erro


# Lê o JSON da resposta da IA: tira as cercas de código (```json) e, se houver
# texto antes ou depois, fica só com o primeiro objeto ou lista encontrado.
def limpar_json_ia(texto_resposta):
    texto_limpo = texto_resposta.strip().replace("```json", "").replace("```", "")
    match = re.search(r'(\{.*\}|\[.*\])', texto_limpo, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(texto_limpo)


def normalizar_estabelecimento(valor):
    """Limpa o estabelecimento devolvido pela IA: texto sem espaços nas pontas,
    ou None quando não for texto ou vier vazio/só com espaços. Não muda
    maiúsculas nem minúsculas, para não estragar grafias como "iFood"."""
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    return valor or None


# --- INTERPRETAÇÃO DO GASTO ---
def listar_categorias(conn, user_id):
    """Nomes das categorias do usuário, na ordem em que o banco devolve (sem
    ORDER BY, como sempre foi: a ordem aparece no prompt e na mensagem de
    categoria não encontrada). A mesma lista alimenta o prompt da IA (regra
    crítica 2) e a checagem da categoria escolhida, sem segunda consulta."""
    categorias = conn.execute('SELECT nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall()
    return [c[0] for c in categorias]


def formatar_lista_categorias(categorias):
    """Lista textual das categorias ("Alimentação, Roupas"), usada no prompt e
    na mensagem de categoria não encontrada. Sem categorias, devolve
    "nenhuma categoria cadastrada"."""
    return ", ".join(categorias) if categorias else "nenhuma categoria cadastrada"


def montar_prompt_gasto(categorias, texto_usuario):
    """Prompt do registro de gasto, com a lista real de categorias do usuário
    e as regras obrigatórias de mapeamento, para a IA nunca inventar uma
    categoria que não exista no banco. O texto (inclusive o recuo de 8
    espaços e as quebras de linha) é exatamente o de antes da PRO-85: mudar
    um espaço muda o que a IA recebe."""
    lista_categorias = formatar_lista_categorias(categorias)
    return f"""
        Analise a mensagem de gasto/receita do usuário e extraia as informações em formato JSON puro.

        LISTA DE CATEGORIAS DISPONÍVEIS DO USUÁRIO:
        [{lista_categorias}]

        REGRAS OBRIGATÓRIAS DE CATEGORIZAÇÃO:
        1. Você deve escolher OBRIGATORIAMENTE uma categoria que esteja presente EXATAMENTE na lista acima.
        2. Faça mapeamento inteligente de sinônimos e contexto:
           - Exemplo: "iFood", "almoço", "burger" -> Mapear para "Alimentação".
           - Exemplo: "Uber", "gasolina", "estacionamento" -> Mapear para "Transporte".
           - Exemplo: "camisa", "tênis" -> Mapear para "Roupas e Estilo" (ou equivalente presente na lista).
        3. NUNCA crie uma nova categoria com nome diferente do que está na lista.
        4. Caso o gasto não tenha relação óbvia com nenhuma, selecione a categoria mais próxima ou "Outros" (se existir na lista).

        REGRAS DO ESTABELECIMENTO:
        1. Só preencha "estabelecimento" se o usuário mencionou o local na mensagem. NUNCA invente nem deduza: sem local citado, devolva null.
        2. Em apps e serviços (Uber, iFood, Netflix), o estabelecimento é o nome do app.
        3. Escreva o nome com a primeira letra maiúscula (ex: "Nike", "Casas Bahia"). Marcas com grafia própria mantêm a grafia oficial (ex: "iFood").
        4. O campo "estabelecimento" não muda o conteúdo da "descricao": ela traz as mesmas palavras de antes, inclusive o local, quando citado.

        REGRAS DA DESCRIÇÃO:
        1. Escreva a descrição em formato de título: inicial maiúscula em cada palavra.
        2. As palavras de ligação ficam em minúsculas: a, o, as, os, de, do, da, dos, das, em, no, na, nos, nas, e, com, para, por, ao.
        3. A primeira palavra sempre começa com maiúscula, mesmo sendo palavra de ligação.
        4. Marcas com grafia própria mantêm a grafia oficial (ex: "iFood").
        5. Exemplo: mensagem "pão na padaria cisne branco 12" -> "descricao": "Pão na Padaria Cisne Branco" e "estabelecimento": "Cisne Branco".

        FORMATO DE SAÍDA (JSON PURO):
        {{
          "tipo": "despesa" ou "receita",
          "valor": float (ex: 45.50),
          "categoria": "NOME_EXATO_DA_CATEGORIA_DA_LISTA",
          "descricao": "resumo curto, em formato de título (ver REGRAS DA DESCRIÇÃO)",
          "estabelecimento": "nome do local ou app, ou null"
        }}

        Mensagem do usuário: "{texto_usuario}"

        Retorne APENAS o JSON puro.
        """


class GastoInterpretado(NamedTuple):
    """O que a IA entendeu do texto. Na despesa, todos os campos. Na receita
    (PRO-86), 'valor' (ou None, se não for número) e 'descricao' (aparada, ou
    None se vazia); a categoria e o estabelecimento nem são lidos. Em outro
    'tipo' qualquer, só 'tipo' vem preenchido."""
    tipo: str                         # dados['tipo'].strip().lower()
    valor: Optional[float]            # _ler_valor_receita(dados.get('valor')): float, ou None se não for número
    categoria: Optional[str]          # dados['categoria'].capitalize(); None fora da despesa
    descricao: Optional[str]          # despesa: dados.get('descricao', ''), como veio; receita: aparada
    estabelecimento: Optional[str]    # já limpo por normalizar_estabelecimento; None fora da despesa
    categoria_valida: bool            # a categoria está na lista do usuário; False fora da despesa


def _ler_valor_receita(valor):
    """Valor da receita como veio da IA, sem levantar erro: número (int ou
    float, não bool) ou texto numérico viram float; qualquer outra coisa
    (texto não numérico, nulo, lista...) vira None. Não confere se é positivo
    nem finito: isso é com validar_valor_receita."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float, str)):
        return None
    try:
        return float(valor)
    except (ValueError, OverflowError):  # texto não numérico; inteiro grande demais
        return None


def _aparar_descricao(descricao):
    """Descrição sem espaços nas pontas, ou None quando não é texto ou fica vazia."""
    if not isinstance(descricao, str):
        return None
    return descricao.strip() or None


async def interpretar_gasto(texto_usuario, categorias):
    """Monta o prompt com as categorias do usuário, chama a IA (com novas
    tentativas) e lê o JSON. A ordem das leituras é a de sempre: 'tipo'
    primeiro; depois, na despesa, 'valor', 'categoria', 'descricao' e
    'estabelecimento'.

    Receita (PRO-86): lê só 'valor' e 'descricao', SEM levantar erro (valor
    que não é número vira None, para o bot mostrar a mensagem de valor
    inválido); categoria e estabelecimento são ignorados, para um "recebi
    5.000 de salário" nunca dar erro de categoria. Outro 'tipo' qualquer
    devolve cedo, sem ler nada além do tipo.

    Na despesa (PRO-93), o valor é lido como o da receita, sem exceção:
    número ou texto numérico vira float; o resto (sem 'valor', 'doze', nulo)
    vira None. Quem chama (o bot) confere com validar_valor e mostra a
    mensagem de valor inválido. As demais leituras não embrulham exceções:
    KeyError (sem 'tipo' ou sem 'categoria'), JSONDecodeError e os erros da
    IA sobem iguais, porque a mensagem de erro do bot mostra str(e). A
    checagem "a categoria está na lista" vira o campo 'categoria_valida'; a
    mensagem de categoria não encontrada fica com quem chama."""
    prompt = montar_prompt_gasto(categorias, texto_usuario)
    response = await gerar_conteudo_com_retry(prompt)
    return ler_resposta_gasto(response.text, categorias)


def ler_resposta_gasto(texto_resposta, categorias):
    """Lê o texto devolvido pela IA como o bot sempre leu (ver
    interpretar_gasto): separado da chamada à IA na PRO-88. Desde a PRO-93, o
    valor da despesa não levanta mais KeyError nem ValueError: vem como float
    ou None (_ler_valor_receita); as outras leituras e exceções são as de
    sempre."""
    dados = limpar_json_ia(texto_resposta)

    tipo = dados['tipo'].strip().lower()
    if tipo == 'receita':
        return GastoInterpretado(
            tipo, _ler_valor_receita(dados.get('valor')), None,
            _aparar_descricao(dados.get('descricao')), None, False
        )
    if tipo != 'despesa':
        return GastoInterpretado(tipo, None, None, None, None, False)

    valor = _ler_valor_receita(dados.get('valor'))
    categoria = dados['categoria'].capitalize()
    descricao = dados.get('descricao', '')
    estabelecimento = normalizar_estabelecimento(dados.get('estabelecimento'))
    return GastoInterpretado(tipo, valor, categoria, descricao, estabelecimento, categoria in categorias)


# --- GRAVAÇÃO ---
def debitar_categoria(cursor, user_id, categoria, valor_armazenado, momento, metodo_pagamento=None, descricao=None, parcelamento_id=None, estabelecimento=None):
    """Lançamento único usado por todo gasto de uma categoria (comum, parcelado
    ou recorrente): confere que a categoria existe e grava a transação. Desde a
    PRO-83 não mexe mais em 'saldo_atual' (coluna sem uso: o gasto é calculado
    na hora pelo calculos.py). 'metodo_pagamento'
    só é informado pelo fluxo manual de registro (Crédito/Débito/Pix); recorrentes
    gravam NULL. 'descricao' é o nome do gasto extraído pela IA (ou o
    da recorrência); se não for texto ou vier vazia, grava NULL. 'parcelamento_id'
    só é informado na parcela 1 de uma compra parcelada e aponta para a linha
    correspondente em 'parcelamentos'; os demais lançamentos gravam NULL.
    'estabelecimento' é o local/app citado na mensagem (extraído pela IA) e só
    vem preenchido em gasto avulso e na parcela 1 de compra parcelada; é limpo
    por normalizar_estabelecimento (recorrentes gravam NULL). Retorna
    (cat_id, transacao_id), ou None se a categoria não existir."""
    cat = cursor.execute('SELECT id FROM categorias WHERE user_id = ? AND nome = ?', (user_id, categoria)).fetchone()
    if not cat:
        return None
    descricao = descricao.strip() if isinstance(descricao, str) else None
    if not descricao:
        descricao = None
    estabelecimento = normalizar_estabelecimento(estabelecimento)
    cursor.execute(
        'INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, metodo_pagamento, descricao, parcelamento_id, estabelecimento) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (user_id, categoria, valor_armazenado, momento, metodo_pagamento, descricao, parcelamento_id, estabelecimento)
    )
    return cat[0], cursor.lastrowid


def registrar_gasto_avulso(conn, user_id, categoria, valor, momento, metodo_pagamento=None, descricao=None, estabelecimento=None, commit=True):
    """Gasto de uma vez só (Débito/Pix ou Crédito à vista): uma linha em
    'transacoes' com o valor cheio, e o commit. 'valor' já vem com o sinal do
    banco (despesa positiva). 'momento' é o texto 'AAAA-MM-DD HH:MM:SS' em
    Brasília (no bot, a hora da mensagem no Telegram; ver momento_agora).
    Devolve (cat_id, transacao_id), ou None se a categoria não existe mais
    (apagada no meio do fluxo): nesse caso nada é gravado. A conexão não é
    fechada aqui: quem abriu fecha.

    commit=False (PRO-88): grava sem o commit, para quem chama gravar mais
    linhas na mesma transação (a chave de registro) e fazer o commit ou o
    rollback. O bot usa o padrão (commit=True), como sempre."""
    cursor = conn.cursor()
    lancamento = debitar_categoria(cursor, user_id, categoria, valor, momento, metodo_pagamento, descricao=descricao, estabelecimento=estabelecimento)
    if commit:
        conn.commit()
    return lancamento


class ParceladoRegistrado(NamedTuple):
    """Resultado de registrar_gasto_parcelado (o bot monta a confirmação com ele)."""
    parcelamento_id: int
    transacao_id: int
    valor_parcela: float
    ciclo_referencia: str


def registrar_gasto_parcelado(conn, user_id, categoria, valor_total, total_parcelas, momento, metodo_pagamento=None, descricao=None, estabelecimento=None, commit=True):
    """Compra parcelada: divide o valor total pelo número de parcelas, grava o
    parcelamento — com a fatura ('ciclo_referencia') calculada a partir do dia
    de fechamento do usuário, base de todas as parcelas seguintes — e a
    parcela 1 em 'transacoes', apontando para ele.

    Compra e parcela 1 são gravadas numa única transação: o parcelamento vem
    primeiro para a transação já nascer apontando para ele, e o commit só
    acontece depois das duas gravações (nunca sobra compra órfã). Se a
    categoria não existe mais, desfaz tudo (rollback) e devolve None. Em
    qualquer erro, desfaz e a exceção sobe.

    'parcelamentos.descricao' guarda a descrição como veio; a parcela 1 em
    'transacoes' a guarda aparada (debitar_categoria). É assim desde antes da
    PRO-85 e não muda aqui. A conexão não é fechada aqui: quem abriu fecha.

    commit=False (PRO-88): não faz o commit no sucesso (quem chama grava a
    chave de registro na mesma transação e faz o commit); a categoria
    inexistente e os erros continuam desfazendo tudo, como no padrão."""
    valor_parcela = round(valor_total / total_parcelas, 2)
    try:
        cursor = conn.cursor()
        dia_fechamento = cursor.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()[0]

        data_compra = datetime.strptime(momento, '%Y-%m-%d %H:%M:%S')
        ciclo_referencia = calcular_ciclo_fatura(data_compra, dia_fechamento)

        cursor.execute(
            '''INSERT INTO parcelamentos
               (user_id, categoria_nome, descricao, valor_total, valor_parcela, total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 1)''',
            (user_id, categoria, descricao, valor_total, valor_parcela, total_parcelas, momento, ciclo_referencia)
        )
        parcelamento_id = cursor.lastrowid
        lancamento = debitar_categoria(
            cursor, user_id, categoria, valor_parcela, momento, metodo_pagamento,
            descricao=descricao, parcelamento_id=parcelamento_id, estabelecimento=estabelecimento
        )
        if lancamento is None:
            conn.rollback()
            return None
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ParceladoRegistrado(parcelamento_id, lancamento[1], valor_parcela, ciclo_referencia)


# --- RECEITA AVULSA (PRO-86) ---
def validar_valor_receita(valor):
    """Valor de receita aceito: número (int ou float; bool e texto NÃO valem),
    finito e maior que zero depois de arredondado a 2 casas (round do Python).
    Devolve o float arredondado, ou None se não servir. Sem teto: a prévia do
    bot mostra o valor antes de gravar (D6)."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    try:
        valor = float(valor)
    except OverflowError:  # inteiro grande demais para float
        return None
    # Fora do intervalo aberto: NaN (nunca compara) e ±infinito. Sem importar
    # math, para o registro.py continuar só com os imports de antes no topo.
    if not float('-inf') < valor < float('inf'):
        return None
    valor = round(valor, 2)
    return valor if valor > 0 else None


def registrar_receita(conn, user_id, valor, momento, descricao=None, origem='avulsa', commit=True):
    """Grava uma receita na tabela 'receitas' (valor sempre positivo) e faz o
    commit; devolve o id da linha nova. Valida o valor de novo
    (validar_valor_receita) e levanta ValueError ANTES do INSERT se ele não
    for número maior que zero (o CHECK do banco não barra texto). 'momento' é
    o texto 'AAAA-MM-DD HH:MM:SS' em Brasília (no bot, a hora da mensagem; na
    API, momento_agora). A descrição é aparada; vazia vira NULL. A receita não
    tem categoria nem forma de pagamento e não cria linha em 'transacoes'. A
    conexão não é fechada aqui: quem abriu fecha. Se o banco recusar a linha
    (ex.: user_id ou momento nulos, pelas restrições NOT NULL), desfaz e a
    exceção sobe. Base da rota POST /receitas da PRO-88.

    commit=False (PRO-88): não faz o commit no sucesso (quem chama grava a
    chave de registro na mesma transação e faz o commit); em erro, desfaz
    como no padrão."""
    valor_valido = validar_valor_receita(valor)
    if valor_valido is None:
        raise ValueError(f"Valor de receita inválido: {valor!r}")
    try:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO receitas (user_id, valor, descricao, data_receita, origem) VALUES (?, ?, ?, ?, ?)',
            (user_id, valor_valido, _aparar_descricao(descricao), momento, origem)
        )
        receita_id = cursor.lastrowid
        if commit:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return receita_id


# --- EDIÇÃO DO ÚLTIMO GASTO ---
# Todas filtram por 'user_id', além do id da transação: um usuário nunca lê nem
# altera o lançamento de outro, mesmo que o id venha de fora.
def buscar_lancamento(conn, user_id, transacao_id):
    """(valor, categoria_nome) da transação do usuário, ou None se ela não
    existe ou é de outro usuário."""
    linha = conn.execute(
        'SELECT valor, categoria_nome FROM transacoes WHERE id = ? AND user_id = ?',
        (transacao_id, user_id)
    ).fetchone()
    return tuple(linha) if linha else None


def alterar_valor_lancamento(conn, user_id, transacao_id, novo_valor_armazenado):
    """Troca só o valor da transação do usuário e faz o commit. O gasto da
    categoria é calculado na hora a partir de 'transacoes' (calculos.py), então
    não há outra linha a corrigir. 'novo_valor_armazenado' já vem com o sinal
    do banco; não é validado aqui (como antes da PRO-85). Devolve False, sem
    gravar, se a transação não existe ou é de outro usuário."""
    cursor = conn.cursor()
    linha = cursor.execute('SELECT valor FROM transacoes WHERE id = ? AND user_id = ?', (transacao_id, user_id)).fetchone()
    if not linha:
        return False
    cursor.execute('UPDATE transacoes SET valor = ? WHERE id = ? AND user_id = ?', (novo_valor_armazenado, transacao_id, user_id))
    conn.commit()
    return True


def mover_lancamento_de_categoria(conn, user_id, transacao_id, categoria_destino):
    """Move a transação do usuário para outra categoria e faz o commit. O gasto
    de cada categoria é calculado a partir de 'transacoes.categoria_nome'
    (calculos.py), então mover a transação já corrige as duas categorias.
    Quem chama confere antes que a transação existe e que o destino é uma
    categoria do usuário diferente da atual."""
    conn.execute(
        'UPDATE transacoes SET categoria_nome = ? WHERE id = ? AND user_id = ?',
        (categoria_destino, transacao_id, user_id)
    )
    conn.commit()


def momento_agora(agora=None):
    """Momento do gasto no formato gravado em 'transacoes.data_transacao'
    ('AAAA-MM-DD HH:MM:SS'), sempre no fuso de Brasília. Para a API, que não
    tem a hora da mensagem do Telegram. 'agora' (opcional) substitui o
    relógio nos testes: com fuso, é convertido para Brasília; sem fuso, é
    tomado como já sendo de Brasília."""
    if agora is None:
        agora = datetime.now(FUSO_BRASILIA)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=FUSO_BRASILIA)
    return agora.astimezone(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')


# --- REGISTRO PELA API DO MINI APP (PRO-88) ---
# Regras das rotas POST /registro/interpretar, POST /gastos e POST /receitas.
# A API só traduz ErroRegistro.codigo em status HTTP e monta o JSON. O bot usa
# a validar_valor e a VALOR_MAXIMO desta parte (PRO-93); o resto é só da API.
VALOR_MAXIMO = 1_000_000.0          # D9: teto do valor (gasto e receita)
TEXTO_MAXIMO = 200                  # D8: tamanho máximo do texto livre
DESCRICAO_MAXIMA = 100              # D9
ESTABELECIMENTO_MAXIMO = 60         # D9
PARCELAS_MAXIMAS = 12               # D9
METODOS_PAGAMENTO = ('Débito/Pix', 'Crédito')  # os mesmos textos que o bot grava
TIPOS_REGISTRO = ('gasto', 'receita')
_TIPO_DA_IA = {'despesa': 'gasto', 'receita': 'receita'}
# UUID v4 (crypto.randomUUID() da tela), comparado em minúsculas.
_FORMATO_CHAVE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')


class ErroRegistro(Exception):
    """Pedido recusado, com o código que a API devolve em {"detail": codigo}.
    A mensagem é só o código: nunca o texto do usuário nem o erro da IA."""

    def __init__(self, codigo):
        super().__init__(codigo)
        self.codigo = codigo


def validar_valor(valor):
    """Valor aceito nas gravações e na prévia da API (gasto e receita): número
    (int ou float; bool e texto NÃO valem), finito, maior que zero e até
    VALOR_MAXIMO depois de arredondado a 2 casas. Devolve o float arredondado,
    ou None se não servir. Mesma base da validar_valor_receita, mais o teto."""
    valor = validar_valor_receita(valor)
    if valor is None or valor > VALOR_MAXIMO:
        return None
    return valor


def validar_chave(chave):
    """Chave de idempotência da gravação: UUID v4 em texto. Devolve a chave em
    minúsculas; fora do formato, ErroRegistro('chave_invalida')."""
    if not isinstance(chave, str) or not _FORMATO_CHAVE.fullmatch(chave.lower()):
        raise ErroRegistro('chave_invalida')
    return chave.lower()


def validar_pedido_interpretacao(tipo, texto):
    """Confere o pedido da prévia antes de gastar a IA: 'tipo' é 'gasto' ou
    'receita'; o texto, aparado, tem de 1 a TEXTO_MAXIMO caracteres. Devolve
    (tipo, texto aparado)."""
    if not isinstance(tipo, str) or tipo not in TIPOS_REGISTRO:
        raise ErroRegistro('tipo_invalido')
    if not isinstance(texto, str):
        raise ErroRegistro('texto_invalido')
    texto = texto.strip()
    if not texto or len(texto) > TEXTO_MAXIMO:
        raise ErroRegistro('texto_invalido')
    return tipo, texto


class Previa(NamedTuple):
    """O que a IA entendeu, já validado ("Entendi assim:"). Na receita,
    'estabelecimento' e 'categoria' ficam None."""
    tipo: str
    valor: float
    descricao: Optional[str]
    estabelecimento: Optional[str]
    categoria: Optional[str]


def _cortar(texto, maximo):
    """Texto da IA cortado no tamanho máximo da gravação (para a prévia sempre
    poder ser confirmada); vazio vira None."""
    if texto is None:
        return None
    return texto[:maximo].rstrip() or None


async def interpretar_previa(tipo, texto, categorias):
    """Prévia da API: o MESMO prompt do bot (montar_prompt_gasto, com as
    categorias do usuário; D5) e a mesma chamada à IA, sem gravar nada. 'tipo'
    e 'texto' já passaram por validar_pedido_interpretacao.

    O texto do usuário é dado, não ordem (injeção de prompt): tudo o que a IA
    devolve é validado como entrada do usuário. Erros, sempre ErroRegistro:
      - a IA falhou (503/429 depois das novas tentativas, sem chave, qualquer
        outro erro): 'ia_indisponivel' (a exceção da IA é descartada);
      - JSON quebrado, sem 'tipo', sem 'valor', tipo desconhecido ou gasto sem
        'categoria': 'resposta_ia_invalida';
      - a IA entendeu o outro tipo: 'tipo_divergente' (D6);
      - valor que não é número, zero, negativo ou acima do teto:
        'valor_nao_entendido' (D7);
      - categoria fora da lista do usuário: 'categoria_nao_encontrada' (D7).
    A categoria passa pelo mesmo capitalize() do bot. Descrição e
    estabelecimento são aparados (vazios viram None) e cortados nos tamanhos
    das gravações."""
    prompt = montar_prompt_gasto(categorias, texto)
    try:
        resposta = await gerar_conteudo_com_retry(prompt)
    except Exception:
        raise ErroRegistro('ia_indisponivel') from None
    try:
        dados = limpar_json_ia(resposta.text)
        tipo_ia = dados['tipo'].strip().lower()
        valor_ia = dados['valor']
    except Exception:
        raise ErroRegistro('resposta_ia_invalida') from None

    tipo_lido = _TIPO_DA_IA.get(tipo_ia)
    if tipo_lido is None:
        raise ErroRegistro('resposta_ia_invalida')
    if tipo_lido != tipo:
        raise ErroRegistro('tipo_divergente')

    valor = validar_valor(_ler_valor_receita(valor_ia))
    if valor is None:
        raise ErroRegistro('valor_nao_entendido')
    descricao = _cortar(_aparar_descricao(dados.get('descricao')), DESCRICAO_MAXIMA)
    if tipo == 'receita':
        return Previa('receita', valor, descricao, None, None)

    if 'categoria' not in dados:
        raise ErroRegistro('resposta_ia_invalida')
    categoria = dados['categoria']
    categoria = categoria.capitalize() if isinstance(categoria, str) else None
    if categoria is None or categoria not in categorias:
        raise ErroRegistro('categoria_nao_encontrada')
    estabelecimento = _cortar(normalizar_estabelecimento(dados.get('estabelecimento')), ESTABELECIMENTO_MAXIMO)
    return Previa('gasto', valor, descricao, estabelecimento, categoria)


class ChaveRegistro(NamedTuple):
    """Linha de 'chaves_registro': o tipo e o id do lançamento da chave."""
    tipo: str
    lancamento_id: int


def buscar_chave(conn, user_id, chave):
    """ChaveRegistro da chave DESTE usuário, ou None. A chave vale por usuário
    (D1): a mesma chave de outro usuário nunca é encontrada aqui."""
    linha = conn.execute(
        'SELECT tipo, lancamento_id FROM chaves_registro WHERE user_id = ? AND chave = ?',
        (user_id, chave)
    ).fetchone()
    return ChaveRegistro(linha[0], linha[1]) if linha else None


def _desfazer(conn):
    """Rollback que não esconde o erro original se ele mesmo falhar."""
    try:
        conn.rollback()
    except Exception:
        pass


# Esperas entre as tentativas de gravar quando o banco está ocupado: até 4
# tentativas no total (a primeira e mais 3).
ESPERAS_BANCO_OCUPADO = (0.25, 0.5, 1.0)

# Sinais de erro PASSAGEIRO na mensagem (sem diferenciar maiúsculas): banco
# ocupado ou transação desfeita pelo servidor (o Turso desfaz a transação
# interativa de um stream parado: "interactive transaction was rolled back
# ... code: SQLITE_BUSY"). NÃO usar "stream error": ele também aparece na
# chave repetida (UNIQUE) do libsql.
_SINAIS_PASSAGEIRO = ('sqlite_busy', 'database is locked', 'was rolled back')


def erro_passageiro(erro):
    """True se a mensagem do erro indica banco ocupado ou transação desfeita
    pelo servidor (vale tentar de novo)."""
    texto = str(erro).lower()
    return any(sinal in texto for sinal in _SINAIS_PASSAGEIRO)


def _esperar(segundos):
    """Espera entre as tentativas (os testes trocam por uma que não dorme).
    As rotas que gravam são síncronas (rodam em thread), então dormir aqui
    não trava o servidor."""
    time.sleep(segundos)


def gravar_com_chave(conn, user_id, chave, tipo, momento, gravar):
    """Grava o lançamento e a linha da chave NA MESMA TRANSAÇÃO e faz um único
    commit. 'gravar' é uma função sem argumentos que grava o lançamento sem
    commit e devolve o id dele (ou None se a categoria não existe mais).

    Devolve (ChaveRegistro, repetido), ou (None, False) se 'gravar' devolveu
    None (nada fica gravado).

    Em QUALQUER exceção (no gravar, no INSERT da chave ou no commit), desfaz a
    transação e consulta a chave do usuário:
      - a chave existe: devolve a do pedido que gravou, com repetido=True. É o
        caso de dois pedidos juntos (o segundo bate na chave primária) e o de
        um commit que deu certo no servidor mas chegou como erro ao cliente;
      - a chave não existe e o erro é PASSAGEIRO (erro_passageiro, ou a
        própria consulta da chave falhou): espera (ESPERAS_BANCO_OCUPADO) e
        tenta a gravação inteira de novo, numa transação nova. Esgotadas as 4
        tentativas: ErroRegistro('banco_ocupado');
      - a chave não existe e o erro não é passageiro: a exceção original sobe.
    Não depende da classe do erro (o SQLite levanta sqlite3.IntegrityError; o
    libsql, ValueError com "SQLITE_CONSTRAINT" ou "SQLITE_BUSY"). Gravação em
    dobro é impossível: a chave primária (user_id, chave) entra na mesma
    transação do lançamento, e cada nova tentativa passa por ela."""
    tentativas = len(ESPERAS_BANCO_OCUPADO) + 1
    for tentativa in range(tentativas):
        try:
            lancamento_id = gravar()
            if lancamento_id is None:
                _desfazer(conn)
                return None, False
            conn.execute(
                'INSERT INTO chaves_registro (user_id, chave, tipo, lancamento_id, criado_em) VALUES (?, ?, ?, ?, ?)',
                (user_id, chave, tipo, lancamento_id, momento)
            )
            conn.commit()
            return ChaveRegistro(tipo, lancamento_id), False
        except Exception as erro:
            _desfazer(conn)
            try:
                existente = buscar_chave(conn, user_id, chave)
                passageiro = erro_passageiro(erro)
            except Exception:  # a consulta também falhou (stream quebrado): passageiro
                existente = None
                passageiro = True
            if existente is not None:
                return existente, True
            if not passageiro:
                raise erro
            if tentativa == tentativas - 1:
                raise ErroRegistro('banco_ocupado') from None
            _esperar(ESPERAS_BANCO_OCUPADO[tentativa])


class GastoRegistrado(NamedTuple):
    """Gasto gravado pela API, lido do banco. 'valor' é o total da compra;
    'valor_parcela' é o da parcela 1 (igual a 'valor' à vista); 'data' é o
    dia ('AAAA-MM-DD', Brasília)."""
    id: int
    valor: float
    valor_parcela: float
    parcelas: int
    descricao: Optional[str]
    estabelecimento: Optional[str]
    categoria: str
    metodo_pagamento: Optional[str]
    data: str
    repetido: bool


class ReceitaRegistrada(NamedTuple):
    """Receita gravada pela API, lida do banco ('data' = 'AAAA-MM-DD')."""
    id: int
    valor: float
    descricao: Optional[str]
    data: str
    repetido: bool


def buscar_gasto_registrado(conn, user_id, transacao_id, repetido=False):
    """GastoRegistrado da transação do usuário (com a compra, se for a parcela
    1 de um parcelado), ou None se ela não existe ou é de outro usuário."""
    linha = conn.execute(
        '''SELECT t.id, t.valor, t.descricao, t.estabelecimento, t.categoria_nome, t.metodo_pagamento,
                  t.data_transacao, p.valor_total, p.total_parcelas
           FROM transacoes t
           LEFT JOIN parcelamentos p ON p.id = t.parcelamento_id AND p.user_id = t.user_id
           WHERE t.id = ? AND t.user_id = ?''',
        (transacao_id, user_id)
    ).fetchone()
    if linha is None:
        return None
    (id_gasto, valor_parcela, descricao, estabelecimento, categoria, metodo_pagamento,
     data, valor_total, total_parcelas) = linha
    parcelado = total_parcelas is not None
    return GastoRegistrado(
        id_gasto, valor_total if parcelado else valor_parcela, valor_parcela,
        total_parcelas if parcelado else 1, descricao, estabelecimento, categoria,
        metodo_pagamento, str(data)[:10], repetido
    )


def buscar_receita_registrada(conn, user_id, receita_id, repetido=False):
    """ReceitaRegistrada do usuário, ou None se não existe ou é de outro."""
    linha = conn.execute(
        'SELECT id, valor, descricao, data_receita FROM receitas WHERE id = ? AND user_id = ?',
        (receita_id, user_id)
    ).fetchone()
    if linha is None:
        return None
    return ReceitaRegistrada(linha[0], linha[1], linha[2], str(linha[3])[:10], repetido)


def _lancamento_da_chave(conn, user_id, chave_registro, tipo, repetido):
    """Lê o lançamento apontado pela chave. A chave já usada no outro tipo, ou
    cujo lançamento não existe mais, dá 'chave_invalida' (a tela gera outra)."""
    if chave_registro.tipo != tipo:
        raise ErroRegistro('chave_invalida')
    buscar = buscar_gasto_registrado if tipo == 'gasto' else buscar_receita_registrada
    lancamento = buscar(conn, user_id, chave_registro.lancamento_id, repetido)
    if lancamento is None:
        raise ErroRegistro('chave_invalida')
    return lancamento


def _texto_opcional(texto, maximo, codigo, limpar=_aparar_descricao):
    """Texto opcional aparado (vazio ou não texto vira None); acima do máximo,
    ErroRegistro(codigo)."""
    texto = limpar(texto)
    if texto is not None and len(texto) > maximo:
        raise ErroRegistro(codigo)
    return texto


def gravar_gasto_api(conn, user_id, chave, valor, descricao, estabelecimento, categoria,
                     metodo_pagamento, parcelas, momento):
    """POST /gastos: valida tudo de novo (sem chamar a IA) e grava UMA vez só.

    1. Chave fora do formato: 'chave_invalida'. Chave já gravada por este
       usuário: devolve o lançamento original (repetido=True), sem validar o
       resto do pedido nem gravar.
    2. Valida: valor (validar_valor; 'valor_invalido'), descrição (até 100;
       'descricao_longa'), estabelecimento (até 60; 'estabelecimento_longo'),
       forma de pagamento ('Débito/Pix' ou 'Crédito';
       'metodo_pagamento_invalido'), parcelas (inteiro de 1 a 12;
       'parcelas_invalidas'; Débito/Pix força 1) e categoria (exatamente uma da
       lista do usuário; 'categoria_nao_encontrada').
    3. Grava com a chave (gravar_com_chave): 1 parcela vira gasto avulso (o
       Crédito 1x grava como à vista, igual ao bot); de 2 a 12, o parcelado
       (compra + parcela 1). Categoria apagada no meio: 'categoria_nao_encontrada'.
       Banco ocupado em todas as tentativas: 'banco_ocupado'.

    'momento' vem do servidor ('AAAA-MM-DD HH:MM:SS', Brasília). Devolve
    GastoRegistrado, lido do banco."""
    chave = validar_chave(chave)
    existente = buscar_chave(conn, user_id, chave)
    if existente is not None:
        return _lancamento_da_chave(conn, user_id, existente, 'gasto', True)

    valor = validar_valor(valor)
    if valor is None:
        raise ErroRegistro('valor_invalido')
    descricao = _texto_opcional(descricao, DESCRICAO_MAXIMA, 'descricao_longa')
    estabelecimento = _texto_opcional(estabelecimento, ESTABELECIMENTO_MAXIMO, 'estabelecimento_longo',
                                      normalizar_estabelecimento)
    if not isinstance(metodo_pagamento, str) or metodo_pagamento not in METODOS_PAGAMENTO:
        raise ErroRegistro('metodo_pagamento_invalido')
    if metodo_pagamento == 'Débito/Pix':
        parcelas = 1
    elif isinstance(parcelas, bool) or not isinstance(parcelas, int) or not 1 <= parcelas <= PARCELAS_MAXIMAS:
        raise ErroRegistro('parcelas_invalidas')
    if not isinstance(categoria, str) or categoria not in listar_categorias(conn, user_id):
        raise ErroRegistro('categoria_nao_encontrada')

    def gravar():
        if parcelas == 1:
            lancamento = registrar_gasto_avulso(
                conn, user_id, categoria, valor, momento, metodo_pagamento,
                descricao=descricao, estabelecimento=estabelecimento, commit=False
            )
            return None if lancamento is None else lancamento[1]
        lancamento = registrar_gasto_parcelado(
            conn, user_id, categoria, valor, parcelas, momento, metodo_pagamento,
            descricao=descricao, estabelecimento=estabelecimento, commit=False
        )
        return None if lancamento is None else lancamento.transacao_id

    chave_registro, repetido = gravar_com_chave(conn, user_id, chave, 'gasto', momento, gravar)
    if chave_registro is None:
        raise ErroRegistro('categoria_nao_encontrada')
    return _lancamento_da_chave(conn, user_id, chave_registro, 'gasto', repetido)


def gravar_receita_api(conn, user_id, chave, valor, descricao, momento):
    """POST /receitas: mesma regra da chave do gravar_gasto_api; valida o
    valor (validar_valor; 'valor_invalido') e a descrição (até 100;
    'descricao_longa') e grava com a chave (registrar_receita sem commit).
    Devolve ReceitaRegistrada, lida do banco."""
    chave = validar_chave(chave)
    existente = buscar_chave(conn, user_id, chave)
    if existente is not None:
        return _lancamento_da_chave(conn, user_id, existente, 'receita', True)

    valor = validar_valor(valor)
    if valor is None:
        raise ErroRegistro('valor_invalido')
    descricao = _texto_opcional(descricao, DESCRICAO_MAXIMA, 'descricao_longa')

    def gravar():
        return registrar_receita(conn, user_id, valor, momento, descricao, commit=False)

    chave_registro, repetido = gravar_com_chave(conn, user_id, chave, 'receita', momento, gravar)
    return _lancamento_da_chave(conn, user_id, chave_registro, 'receita', repetido)
