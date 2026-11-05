"""Regras do registro de gasto: a interpretação do texto pela IA e a gravação
no banco. Fonte única usada pelo bot (bot.py) e, a partir da PRO-88, pela API
do Mini App — a mesma ideia do calculos.py.

O bot cuida só do Telegram: mensagens, botões, estados da conversa e o
user_data. Tudo o que decide o que vai para o banco (o prompt com as
categorias do usuário, a leitura da resposta da IA, o INSERT do gasto avulso
e do parcelado e as edições do último gasto) fica aqui, para o bot e a API
gravarem exatamente as mesmas linhas.

Só depende da biblioteca padrão e do calculos.py: não importa telegram, bot,
dotenv nem libsql, não lê o .env e não abre conexão ao ser importado. A
conexão chega por parâmetro ('conn'), e o cliente da IA é criado só na
primeira chamada (obter_cliente_ia), com o google.genai importado lá dentro:
assim a API importa este módulo sem ter o pacote nem a chave da IA.

As exceções não são embrulhadas: a mensagem de erro do bot mostra str(e)
("Erro: 'valor'"), e uma classe de erro nova mudaria o texto visível.
"""
import asyncio
import json
import re
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
    """
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            return obter_cliente_ia().models.generate_content(
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
    """O que a IA entendeu do texto. Na receita, só 'tipo' vem preenchido: o
    valor e a categoria nem são lidos (quem chama decide o que fazer com a
    receita; hoje o bot a recusa)."""
    tipo: str                         # dados['tipo'].strip().lower()
    valor: Optional[float]            # float(dados['valor']); None quando não é despesa
    categoria: Optional[str]          # dados['categoria'].capitalize()
    descricao: Optional[str]          # dados.get('descricao', ''), como veio da IA
    estabelecimento: Optional[str]    # já limpo por normalizar_estabelecimento
    categoria_valida: bool            # a categoria está na lista do usuário


async def interpretar_gasto(texto_usuario, categorias):
    """Monta o prompt com as categorias do usuário, chama a IA (com novas
    tentativas) e lê o JSON. A ordem das leituras é a de sempre: 'tipo'
    primeiro (na receita, devolve cedo, sem ler valor nem categoria, para um
    "recebi 5.000 de salário" nunca virar gasto nem dar erro de categoria);
    depois 'valor', 'categoria', 'descricao' e 'estabelecimento'.

    Não valida o valor (zero ou negativo passam, como antes) e não embrulha
    exceções: KeyError, ValueError, JSONDecodeError e os erros da IA sobem
    iguais, porque a mensagem de erro do bot mostra str(e). A checagem "a
    categoria está na lista" vira o campo 'categoria_valida'; a mensagem de
    categoria não encontrada fica com quem chama."""
    prompt = montar_prompt_gasto(categorias, texto_usuario)
    response = await gerar_conteudo_com_retry(prompt)
    dados = limpar_json_ia(response.text)

    tipo = dados['tipo'].strip().lower()
    if tipo != 'despesa':
        return GastoInterpretado(tipo, None, None, None, None, False)

    valor = float(dados['valor'])
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


def registrar_gasto_avulso(conn, user_id, categoria, valor, momento, metodo_pagamento=None, descricao=None, estabelecimento=None):
    """Gasto de uma vez só (Débito/Pix ou Crédito à vista): uma linha em
    'transacoes' com o valor cheio, e o commit. 'valor' já vem com o sinal do
    banco (despesa positiva). 'momento' é o texto 'AAAA-MM-DD HH:MM:SS' em
    Brasília (no bot, a hora da mensagem no Telegram; ver momento_agora).
    Devolve (cat_id, transacao_id), ou None se a categoria não existe mais
    (apagada no meio do fluxo): nesse caso nada é gravado. A conexão não é
    fechada aqui: quem abriu fecha."""
    cursor = conn.cursor()
    lancamento = debitar_categoria(cursor, user_id, categoria, valor, momento, metodo_pagamento, descricao=descricao, estabelecimento=estabelecimento)
    conn.commit()
    return lancamento


class ParceladoRegistrado(NamedTuple):
    """Resultado de registrar_gasto_parcelado (o bot monta a confirmação com ele)."""
    parcelamento_id: int
    transacao_id: int
    valor_parcela: float
    ciclo_referencia: str


def registrar_gasto_parcelado(conn, user_id, categoria, valor_total, total_parcelas, momento, metodo_pagamento=None, descricao=None, estabelecimento=None):
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
    PRO-85 e não muda aqui. A conexão não é fechada aqui: quem abriu fecha."""
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    return ParceladoRegistrado(parcelamento_id, lancamento[1], valor_parcela, ciclo_referencia)


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
