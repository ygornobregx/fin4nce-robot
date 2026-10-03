import os
import re
import json
import libsql
import asyncio
import calendar
from datetime import time, datetime, timedelta
from html import escape
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from google import genai
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

load_dotenv()

TOKEN = os.getenv('TOKEN')
ai_client = genai.Client()

TURSO_DATABASE_URL = os.getenv('TURSO_DATABASE_URL')
TURSO_AUTH_TOKEN = os.getenv('TURSO_AUTH_TOKEN')

def obter_conexao_db():
    """Abre uma conexão com o banco remoto no Turso. Centralizado aqui (em
    vez de espalhar url/token pelas ~30 chamadas do arquivo) para trocar de
    banco em um único lugar caso precise no futuro."""
    return libsql.connect(database=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)

# Fuso horário usado para agendar o resumo diário (independente do fuso do servidor)
FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

# Automação de gastos recorrentes: horário padrão de disparo e espaçamento
# entre lançamentos que caem no mesmo dia (evita rajada de mensagens). 09:00
# (em vez de 12:00) reduz a chance de coincidir com o horário do Resumo
# Diário, que é configurável por usuário e não tem um valor fixo a checar.
HORARIO_COBRANCA_RECORRENTE = time(hour=9, minute=0, tzinfo=FUSO_BRASILIA)
INTERVALO_ESPACAMENTO_RECORRENTE = 30 * 60

# Estados das conversas
NOME_USUARIO, RENDA_MENSAL, NOMES_CATEGORIAS, VALORES_CATEGORIAS, DIA_FECHAMENTO = range(5)
AGUARDANDO_NOME_CAT, AGUARDANDO_VALOR_CAT = range(10, 12)
GASTO_VALOR = 30
CONFIRMAR_PARCELA, ESCOLHER_PARCELAS, DIGITAR_PARCELAS = range(31, 34)
EDITAR_ULTIMO_GASTO = 34
METODO_PAGAMENTO = 35
TRANSFERIR_VALOR = 40
ALERTA_ESCOLHA, ALERTA_CUSTOM, ALERTA_VISUALIZACAO = range(50, 53)
EDITAR_NOME_CAT, EDITAR_VALOR_CAT = range(60, 62)
EDITAR_DIA_VENCIMENTO = 70
RECORRENTE_TEXTO_LIVRE, RECORRENTE_COMPLETAR = range(80, 82)
RECORRENTE_EDITAR_DIA, RECORRENTE_EDITAR_VALOR = range(82, 84)

# Colunas que cada tabela deve ter, além das já presentes no CREATE TABLE.
# Para adicionar uma coluna nova no futuro: acrescente aqui uma linha
# ('nome_coluna', 'definição SQL'). Na próxima inicialização do bot,
# migrar_colunas_faltantes() detecta a ausência via PRAGMA table_info e roda o
# ALTER TABLE sozinha em qualquer banco (novo ou já existente) que ainda não
# tenha a coluna. Se a coluna for NOT NULL, inclua um DEFAULT na definição
# (ex: "TEXT NOT NULL DEFAULT ''") para não quebrar as linhas já existentes.
COLUNAS_ESPERADAS = {
    'usuarios': [
        ('ultimo_reset', 'TEXT'),
    ],
    'categorias': [
        ('data_criacao', 'TEXT'),
    ],
    'transacoes': [
        ('metodo_pagamento', 'TEXT'),
    ],
    'parcelamentos': [
        ('ciclo_referencia', 'TEXT'),
    ],
}

def migrar_colunas_faltantes(cursor):
    """Garante que todas as tabelas tenham as colunas listadas em COLUNAS_ESPERADAS.

    Para cada tabela, lê as colunas que existem de fato no banco conectado
    (PRAGMA table_info) e compara com o que o código espera. Toda coluna
    faltante é criada com ALTER TABLE ... ADD COLUMN. Não mexe em dados
    existentes e não faz nada (sem erro) se todas as colunas já existirem —
    seguro de rodar toda vez que o bot inicia.
    """
    for tabela, colunas_esperadas in COLUNAS_ESPERADAS.items():
        if not colunas_esperadas:
            continue
        colunas_existentes = {
            linha[1] for linha in cursor.execute(f'PRAGMA table_info({tabela})').fetchall()
        }
        for nome_coluna, definicao_sql in colunas_esperadas:
            if nome_coluna not in colunas_existentes:
                cursor.execute(f'ALTER TABLE {tabela} ADD COLUMN {nome_coluna} {definicao_sql}')
                print(f"🔧 Migração: coluna '{nome_coluna}' adicionada em '{tabela}' ({definicao_sql}).")

def iniciar_banco():
    conn = obter_conexao_db()
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS usuarios (user_id INTEGER PRIMARY KEY, nome TEXT, renda_mensal REAL, dia_fechamento INTEGER, horario_alerta TEXT)')
    cursor.execute('CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT, orcamento_mensal REAL, saldo_atual REAL)')
    cursor.execute('CREATE TABLE IF NOT EXISTS transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT, valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')
    cursor.execute('''CREATE TABLE IF NOT EXISTS parcelamentos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        categoria_nome TEXT,
        descricao TEXT,
        valor_total REAL,
        valor_parcela REAL,
        total_parcelas INTEGER,
        parcelas_pagas INTEGER DEFAULT 1,
        data_criacao TEXT,
        ativo INTEGER DEFAULT 1
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gastos_recorrentes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        categoria_nome TEXT,
        descricao TEXT,
        valor REAL,
        dia_cobranca INTEGER,
        criado_em TEXT,
        ultimo_ciclo_cobrado TEXT
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS transferencias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        categoria_origem TEXT,
        categoria_destino TEXT,
        valor REAL,
        data_transferencia TEXT
    )''')

    migrar_colunas_faltantes(cursor)

    conn.commit()
    conn.close()

def formatar_real(valor):
    s = f"{valor:,.2f}"
    s = s.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"R$ {s}"

def parse_valor(texto):
    limpo = str(texto).replace('R$', '').strip()
    if '.' in limpo and ',' in limpo:
        limpo = limpo.replace('.', '').replace(',', '.')
    elif ',' in limpo:
        limpo = limpo.replace(',', '.')
    return float(limpo)

def dia_valido(dia):
    """Valida se o dia informado é um dia possível dentro de um mês (1 a 31)."""
    return 1 <= dia <= 31

def _somar_meses(ciclo_str, n):
    """Soma 'n' meses a um ciclo 'YYYY-MM', tratando virada de ano."""
    ano, mes = map(int, ciclo_str.split('-'))
    mes += n
    ano += (mes - 1) // 12
    mes = (mes - 1) % 12 + 1
    return f"{ano:04d}-{mes:02d}"

def calcular_ciclo_fatura(data, dia_fechamento):
    """
    Retorna a fatura ('YYYY-MM') à qual uma compra pertence, dado o dia de
    fechamento do cartão: compras feitas no próprio dia de fechamento ou
    depois já entram na fatura do mês seguinte; antes dele, ficam na fatura
    atual (o dia_fechamento é o primeiro dia do ciclo novo). Ajusta para
    meses mais curtos que o dia configurado (ex: fechamento dia 31 num mês
    com 30 dias, ou fevereiro) — mesma regra usada em obter_inicio_ciclo_atual.
    """
    ultimo_dia_mes = calendar.monthrange(data.year, data.month)[1]
    dia_alvo = min(dia_fechamento, ultimo_dia_mes)
    base = f"{data.year:04d}-{data.month:02d}"
    return _somar_meses(base, 1) if data.day >= dia_alvo else base

def obter_inicio_ciclo_atual(dia_fechamento, agora):
    """Calcula o início do ciclo mensal baseado no dia_fechamento do usuário:
    retorna a data do último vencimento (quando os saldos foram resetados
    para o valor fixo) que já passou, ajustando para meses mais curtos que o
    dia configurado — mesma regra usada em _executar_verificacao_resets."""
    ultimo_dia_mes_atual = calendar.monthrange(agora.year, agora.month)[1]
    dia_alvo_atual = min(dia_fechamento, ultimo_dia_mes_atual)

    if agora.day >= dia_alvo_atual:
        return datetime(agora.year, agora.month, dia_alvo_atual).strftime('%Y-%m-%d')

    # Ainda não chegou o fechamento deste mês: o ciclo vigente começou no
    # fechamento do mês anterior.
    ano_anterior, mes_anterior = (agora.year, agora.month - 1) if agora.month > 1 else (agora.year - 1, 12)
    ultimo_dia_mes_anterior = calendar.monthrange(ano_anterior, mes_anterior)[1]
    dia_alvo_anterior = min(dia_fechamento, ultimo_dia_mes_anterior)
    return datetime(ano_anterior, mes_anterior, dia_alvo_anterior).strftime('%Y-%m-%d')

# --- ORÇAMENTO / GASTO / SOBRA (FONTE ÚNICA DE CÁLCULO) ---
def obter_inicio_ciclo_usuario(conn, user_id):
    """Início do ciclo vigente do usuário ('YYYY-MM-DD'), ou None se ele ainda
    não configurou o dia de fechamento (nesse caso não há reset, e o cálculo
    considera todo o histórico)."""
    linha = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    if not linha or not linha[0]:
        return None
    return obter_inicio_ciclo_atual(linha[0], datetime.now(FUSO_BRASILIA))

def calcular_status_categoria(conn, user_id, nome, inicio_ciclo, fim_ciclo=None):
    """Fonte única de Orçamento, Gasto e Sobra de uma categoria no ciclo
    vigente, calculados na hora a partir do banco:
      - Orçamento = limite fixo + transferências recebidas − enviadas no ciclo
      - Gasto     = soma das despesas (valores positivos) do ciclo
      - Sobra     = Orçamento − Gasto + receitas do ciclo
    'fim_ciclo' ('YYYY-MM-DD', exclusivo) só é usado para consultar um ciclo
    já fechado (ex: Resumo Mensal); sem ele, o ciclo vai até hoje.
    Retorna (orcamento, gasto, sobra), ou None se a categoria não existir."""
    inicio = inicio_ciclo or '0000-01-01'
    fim = fim_ciclo or '9999-12-31'
    linha = conn.execute(
        '''SELECT c.orcamento_mensal,
                  (SELECT COALESCE(SUM(t.valor), 0) FROM transacoes t
                    WHERE t.user_id = c.user_id AND t.categoria_nome = c.nome
                      AND DATE(t.data_transacao) >= ? AND DATE(t.data_transacao) < ? AND t.valor > 0),
                  (SELECT COALESCE(SUM(-t.valor), 0) FROM transacoes t
                    WHERE t.user_id = c.user_id AND t.categoria_nome = c.nome
                      AND DATE(t.data_transacao) >= ? AND DATE(t.data_transacao) < ? AND t.valor < 0),
                  (SELECT COALESCE(SUM(tr.valor), 0) FROM transferencias tr
                    WHERE tr.user_id = c.user_id AND tr.categoria_destino = c.nome
                      AND DATE(tr.data_transferencia) >= ? AND DATE(tr.data_transferencia) < ?),
                  (SELECT COALESCE(SUM(tr.valor), 0) FROM transferencias tr
                    WHERE tr.user_id = c.user_id AND tr.categoria_origem = c.nome
                      AND DATE(tr.data_transferencia) >= ? AND DATE(tr.data_transferencia) < ?)
           FROM categorias c WHERE c.user_id = ? AND c.nome = ?''',
        (inicio, fim, inicio, fim, inicio, fim, inicio, fim, user_id, nome)
    ).fetchone()
    if not linha:
        return None
    limite_fixo, gasto, receitas, recebido, enviado = linha
    orcamento = (limite_fixo or 0.0) + recebido - enviado
    return orcamento, gasto, orcamento - gasto + receitas

def debitar_categoria(cursor, user_id, categoria, valor_armazenado, momento, metodo_pagamento=None):
    """Lançamento único usado por todo gasto que impacta uma categoria (comum,
    parcelado ou recorrente): grava a transação e mantém 'saldo_atual' em dia
    (legado, ainda lido pelo histórico e pela edição de limite). 'metodo_pagamento'
    só é informado pelo fluxo manual de registro (Crédito/Débito/Pix); recorrentes
    e receitas gravam NULL. Retorna (cat_id, transacao_id), ou None se a
    categoria não existir."""
    cat = cursor.execute('SELECT id FROM categorias WHERE user_id = ? AND nome = ?', (user_id, categoria)).fetchone()
    if not cat:
        return None
    cursor.execute('UPDATE categorias SET saldo_atual = saldo_atual - ? WHERE id = ?', (valor_armazenado, cat[0]))
    cursor.execute(
        'INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, metodo_pagamento) VALUES (?, ?, ?, ?, ?)',
        (user_id, categoria, valor_armazenado, momento, metodo_pagamento)
    )
    return cat[0], cursor.lastrowid

def calcular_sobra_categoria(conn, user_id, nome):
    """Atalho para as mensagens de 'Resta na Categoria'."""
    status = calcular_status_categoria(conn, user_id, nome, obter_inicio_ciclo_usuario(conn, user_id))
    return status[2] if status else 0.0

# --- FATURAS, PARCELAS E HISTÓRICO MENSAL ---
NOMES_MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
               'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

# Divisor curto para não quebrar linha em telas de celular
DIVISOR = "───────────────"

# A tabela 'parcelamentos' guarda uma linha por compra, não por parcela. Esta CTE
# recursiva expande cada parcelamento ativo em uma linha por parcela
# (pid, n, ano_mes), com n = índice 0-based da parcela: a parcela n+1 cai em
# 'ciclo_referencia' + n meses. Parâmetros: (user_id, categoria, categoria);
# categoria=None expande todas as categorias do usuário. Deve ser concatenada
# com um SELECT final sobre 'expandido'.
_SQL_PARCELAS_EXPANDIDAS = '''
    WITH RECURSIVE expandido(pid, n, ano_mes) AS (
        SELECT id, 0, COALESCE(NULLIF(ciclo_referencia, ''), substr(data_criacao, 1, 7))
        FROM parcelamentos
        WHERE user_id = ? AND ativo = 1 AND (? IS NULL OR categoria_nome = ?)
        UNION ALL
        SELECT e.pid, e.n + 1, strftime('%Y-%m', e.ano_mes || '-01', '+1 month')
        FROM expandido e JOIN parcelamentos p ON p.id = e.pid
        WHERE e.n + 1 < p.total_parcelas
    )
'''

# Espelho em SQL de calcular_ciclo_fatura, aplicado a 'data_transacao': devolve
# a fatura ('YYYY-MM') de cada transação. Recebe 1 parâmetro (dia_fechamento).
# MIN(?, último_dia_do_mês_da_transação) replica o ajuste de mês mais curto
# que o dia configurado (ex: fechamento dia 31 num mês de 30 dias).
_SQL_FATURA_TRANSACAO = '''(CASE
        WHEN CAST(substr(data_transacao, 9, 2) AS INTEGER) >= MIN(?,
            CAST(strftime('%d', date(substr(data_transacao, 1, 7) || '-01', '+1 month', '-1 day')) AS INTEGER))
        THEN strftime('%Y-%m', substr(data_transacao, 1, 7) || '-01', '+1 month')
        ELSE substr(data_transacao, 1, 7) END)'''

def obter_ciclo_atual(dia_fechamento):
    """Fatura vigente ('YYYY-MM') agora, no fuso de Brasília."""
    return calcular_ciclo_fatura(datetime.now(FUSO_BRASILIA), dia_fechamento)

def mes_fechado(ano_mes, dia_fechamento):
    """Estado 'Mês Fechado' é derivado, não armazenado: uma fatura está fechada
    quando é anterior à fatura vigente. 'YYYY-MM' ordena corretamente como texto."""
    return ano_mes < obter_ciclo_atual(dia_fechamento)

def rotulo_mes(ano_mes, com_ano=False):
    ano, mes = ano_mes.split('-')
    nome = NOMES_MESES[int(mes) - 1]
    return f"{nome[:3]}/{ano[2:]}" if com_ano else nome

def montar_grade(botoes, colunas=3):
    """Distribui os botões em linhas de 'colunas' itens (grelha para o telemóvel)."""
    return [botoes[i:i + colunas] for i in range(0, len(botoes), colunas)]

def buscar_meses_com_parcelas(conn, user_id, categoria=None, a_partir_de='0000-00'):
    """Meses ('YYYY-MM', ordem cronológica) que têm ao menos uma parcela de
    parcelamento ativo, opcionalmente só de uma categoria e a partir de um mês."""
    linhas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS +
        'SELECT DISTINCT ano_mes FROM expandido WHERE ano_mes >= ? ORDER BY ano_mes',
        (user_id, categoria, categoria, a_partir_de)
    ).fetchall()
    return [l[0] for l in linhas]

def buscar_meses_com_movimento(conn, user_id, dia_fechamento):
    """Meses ('YYYY-MM', ordem cronológica) que têm parcelas (passadas ou
    futuras) ou gastos registrados, mais a fatura vigente. Alimenta os
    botões de ano e de mês do histórico."""
    linhas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS +
        f'''SELECT ano_mes FROM expandido
            UNION
            SELECT {_SQL_FATURA_TRANSACAO} FROM transacoes WHERE user_id = ? AND valor > 0''',
        (user_id, None, None, dia_fechamento, user_id)
    ).fetchall()
    meses = {l[0] for l in linhas if l[0]}
    meses.add(obter_ciclo_atual(dia_fechamento))
    return sorted(meses)

def categoria_existia_no_mes(data_criacao, ano_mes, dia_fechamento):
    """Regra temporal: uma categoria só aparece em faturas iguais ou posteriores
    à da sua criação. Sem data (categorias anteriores à coluna) = sempre visível."""
    if not data_criacao:
        return True
    try:
        criada = datetime.strptime(data_criacao[:10], '%Y-%m-%d')
    except ValueError:
        return True
    return calcular_ciclo_fatura(criada, dia_fechamento) <= ano_mes

def montar_texto_painel(titulo, renda, dia_fechamento, categorias, rotulo_gastos="Gastos Atuais"):
    """Painel compartilhado pelo 'Ver Resumo' e pelo detalhe de cada mês do
    histórico (mesmo layout). 'categorias' é uma lista de (nome, limite, sobra,
    gasto): 'limite' e 'sobra' são None para categorias já excluídas (só
    mostram o gasto). O gasto é sempre o total consolidado da categoria."""
    comprometido = sum(limite for _, limite, _, _ in categorias if limite is not None)
    total_gastos = sum(gasto for _, _, _, gasto in categorias)
    total_excedido = sum(
        gasto - limite for _, limite, _, gasto in categorias
        if limite is not None and gasto > limite
    )

    # O abate automático na sobra do mês só ocorre se houver estouro
    sobra_mes = (renda - comprometido) - total_excedido

    texto = (
        f"{titulo}\n\n"
        f"{DIVISOR}\n\n"
        f"Receita: {formatar_real(renda)}\n"
        f"Comprometido: {formatar_real(comprometido)}\n"
        f"{rotulo_gastos}: {formatar_real(total_gastos)}\n"
        f"Sobra do Mês: {formatar_real(sobra_mes)}\n"
        f"Fechamento Fatura: Dia {dia_fechamento}\n\n"
        f"{DIVISOR}\n\n"
        "Categorias ↓\n\n"
        f"{DIVISOR}\n"
    )

    blocos = []
    for nome, limite, sobra, gasto in categorias:
        linhas = [f"📂 {escape(nome)}"]
        if limite is not None:
            linhas.append(f"Orçamento: {formatar_real(limite)}")
        linhas.append(f"Gastos: {formatar_real(gasto)}")
        if limite is not None:
            linhas.append(f"Sobra: {formatar_real(sobra)}")
        blocos.append("\n".join(linhas))
    if not blocos:
        blocos.append("Nenhuma categoria neste mês.")

    return texto + "\n\n" + f"\n\n{DIVISOR}\n\n".join(blocos)

def limpar_json_ia(texto_resposta):
    texto_limpo = texto_resposta.strip().replace("```json", "").replace("```", "")
    match = re.search(r'(\{.*\}|\[.*\])', texto_limpo, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    return json.loads(texto_limpo)

async def gerar_conteudo_com_retry(prompt, tentativas=3, espera_inicial=2):
    """
    Chama a API do Gemini com retry automático (backoff exponencial) quando o
    modelo está sobrecarregado (erro 503/UNAVAILABLE) ou há limite de taxa (429).
    Para outros erros, propaga imediatamente sem tentar de novo.
    """
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            return ai_client.models.generate_content(
                model='gemini-3.6-flash',
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

def mensagem_erro_ia(e):
    """Traduz erros comuns da API de IA em mensagens amigáveis para o usuário."""
    erro_str = str(e)
    if '503' in erro_str or 'UNAVAILABLE' in erro_str:
        return (
            "O serviço de IA está sobrecarregado no momento (alta demanda no Google).\n\n"
            "Já tentei automaticamente algumas vezes, mas não consegui completar.\n\n"
            "Tente novamente em instantes."
        )
    if '429' in erro_str:
        return "Limite de uso da IA atingido no momento. Tente novamente em alguns instantes."
    return f"Não consegui processar essa mensagem automaticamente.\n\nErro: {erro_str}"

# --- CONTROLE DE MENSAGENS (mantém o chat limpo) ---
async def responder_callback(query, texto=None):
    """Confirma o clique no Telegram (tira o ícone de 'carregando' do botão)
    sem deixar 'Query is too old'/timeout de rede interromper o resto do
    handler — o pior caso vira só um log no console, nunca uma exceção
    visível ou um fluxo quebrado no meio."""
    try:
        await query.answer(texto)
    except (BadRequest, NetworkError) as e:
        print(f"⚠️ Falha ao responder callback (ignorado): {e}")

def _rastrear_mensagem(context, message_id):
    """Registra o id de uma mensagem do bot na lista de mensagens do fluxo
    atual. Isso permite apagar em bloco, mais tarde, mensagens que sobraram
    de fluxos diferentes (ex: um menu antigo, um prompt de um fluxo que foi
    abandonado, etc.) mesmo que elas não tenham sido editadas em sequência."""
    lista = context.user_data.setdefault('msgs_fluxo', [])
    if message_id not in lista:
        lista.append(message_id)

async def enviar_fluxo(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
    """
    Edita a última mensagem de fluxo do bot em vez de mandar uma nova, para o
    chat não acumular mensagens antigas de passos já concluídos. Se não for
    possível editar (mensagem muito antiga, apagada, ou é a primeira do
    fluxo), envia uma mensagem nova e passa a rastreá-la a partir de agora.
    """
    msg_id = context.user_data.get('ultima_msg_bot')
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=texto,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
            _rastrear_mensagem(context, msg_id)
            return
        except Exception:
            pass
    try:
        nova_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=texto,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except NetworkError as e:
        print(f"⚠️ Falha de rede ao enviar mensagem para {chat_id} (ignorado): {e}")
        return
    context.user_data['ultima_msg_bot'] = nova_msg.message_id
    _rastrear_mensagem(context, nova_msg.message_id)

async def atualizar_callback(query, context, texto, reply_markup=None, parse_mode='HTML'):
    """Edita a mensagem por trás de um botão pressionado e mantém o rastreio
    da última mensagem do bot em sincronia com enviar_fluxo. Ignora
    'Message is not modified' (o usuário clicou em algo que já mostra
    exatamente o mesmo conteúdo) e falhas de rede (timeout, conexão caindo
    no meio da leitura, etc.) — qualquer outro BadRequest continua subindo
    normalmente."""
    try:
        await query.message.edit_text(texto, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if 'Message is not modified' not in str(e):
            raise
    except NetworkError as e:
        print(f"⚠️ Falha de rede ao editar mensagem {query.message.message_id} (ignorado): {e}")
    context.user_data['ultima_msg_bot'] = query.message.message_id
    _rastrear_mensagem(context, query.message.message_id)

async def limpar_historico_mensagens(context, chat_id, manter_id=None):
    """Apaga todas as mensagens rastreadas do fluxo atual (que podem ter
    sobrado de fluxos diferentes que passaram pelo chat), preservando apenas
    'manter_id' se for informado."""
    lista = context.user_data.get('msgs_fluxo', [])
    for msg_id in lista:
        if msg_id == manter_id:
            continue
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
        except Exception:
            pass
    context.user_data['msgs_fluxo'] = [manter_id] if manter_id else []

async def mostrar_resumo_temporario(context, chat_id, texto, segundos=10, parse_mode='HTML'):
    """
    Usado ao final de uma ação (registrar gasto, transferir, editar categoria
    etc.): edita a última mensagem de fluxo (ex: 'Processando...') para virar
    o resumo, em vez de apagá-la e mandar uma nova — evita que ela fique presa
    no histórico se o delete falhar (ex: mensagem antiga, rede lenta). Outras
    mensagens de fluxo que tenham sobrado são apagadas em seguida. Depois de
    'segundos' segundos, o resumo some e o menu principal aparece no lugar.
    """
    msg_id = context.user_data.get('ultima_msg_bot')
    resumo_msg_id = None

    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=texto,
                parse_mode=parse_mode
            )
            resumo_msg_id = msg_id
        except Exception:
            pass

    if resumo_msg_id:
        # Apaga só o que sobrou de outros fluxos, preservando a mensagem
        # que acabamos de editar para virar o resumo.
        await limpar_historico_mensagens(context, chat_id, manter_id=resumo_msg_id)
    else:
        # Não havia mensagem pra editar (ou a edição falhou): cai no
        # comportamento antigo, apagando tudo e mandando uma nova.
        await limpar_historico_mensagens(context, chat_id)
        resumo_msg = await context.bot.send_message(chat_id=chat_id, text=texto, parse_mode=parse_mode)
        resumo_msg_id = resumo_msg.message_id

    context.user_data['ultima_msg_bot'] = resumo_msg_id
    context.user_data['msgs_fluxo'] = [resumo_msg_id]

    nome_job = f"resumo_{chat_id}"
    for job in context.job_queue.get_jobs_by_name(nome_job):
        job.schedule_removal()

    context.job_queue.run_once(
        _reverter_para_menu,
        segundos,
        chat_id=chat_id,
        user_id=chat_id,
        data={'msg_id': resumo_msg_id},
        name=nome_job
    )

async def _reverter_para_menu(context: ContextTypes.DEFAULT_TYPE):
    """Job agendado por mostrar_resumo_temporario: apaga a mensagem de resumo
    e mostra o menu principal em uma mensagem nova e limpa."""
    chat_id = context.job.chat_id
    msg_id = context.job.data.get('msg_id')

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass

    nova_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=await montar_texto_menu_principal(chat_id),
        reply_markup=obter_teclado_menu(),
        parse_mode='HTML'
    )
    context.user_data['ultima_msg_bot'] = nova_msg.message_id
    context.user_data['msgs_fluxo'] = [nova_msg.message_id]

async def montar_texto_menu_principal(user_id):
    """
    Texto do cabeçalho do Menu Principal. As métricas financeiras (SALÁRIO,
    COMPROMETIDO, GASTOS, SOBRA DO MÊS) aparecem exclusivamente na tela
    "Ver Resumo" (botao_clicado) — aqui o Menu Principal fica limpo, sem
    poluir a navegação com números repetidos a cada clique.
    """
    return "Menu Principal"

def obter_teclado_menu():
    teclado = [
        [InlineKeyboardButton("Registrar Gasto", callback_data='menu_gastar')],
        [InlineKeyboardButton("Registrar Gasto Recorrente", callback_data='menu_recorrente')],
        [InlineKeyboardButton("Ver Resumo", callback_data='menu_resumo')],
        [InlineKeyboardButton("Alerta", callback_data='menu_alerta')]
    ]
    return InlineKeyboardMarkup(teclado)

# Teclado padrão de escape para telas secundárias e passos intermediários de
# sub-fluxos: nenhum outro botão do Menu Principal deve aparecer aqui junto.
TECLADO_VOLTAR = InlineKeyboardMarkup([[InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")]])

def linha_voltar_com_menu(callback_voltar):
    # Linha única "← Voltar" + "Voltar ao Menu" (lado a lado) para telas de
    # nível 2+: o primeiro sobe uma etapa, o segundo salta direto ao Menu Principal.
    return [
        InlineKeyboardButton("← Voltar", callback_data=callback_voltar, style="danger"),
        InlineKeyboardButton("Voltar ao Menu", callback_data='voltar_menu', style="danger")
    ]

# Chaves temporárias de sub-fluxos descartadas ao voltar ao Menu Principal.
CHAVES_ESTADO_TEMPORARIO = (
    'gasto_pendente', 'ultimo_gasto', 'cat_em_edicao', 'nova_cat_nome',
    'nome_temp', 'renda_temp', 'categorias_temp', 'orcamentos_temp', 'index_atual',
)

async def voltar_para_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Manipulador para cancelar o fluxo em andamento: limpa as variáveis
    # temporárias de qualquer sub-fluxo e re-exibe o Menu Principal limpo.
    query = update.callback_query
    await responder_callback(query)

    for chave in CHAVES_ESTADO_TEMPORARIO:
        context.user_data.pop(chave, None)

    texto_menu = await montar_texto_menu_principal(query.from_user.id)
    await atualizar_callback(query, context, texto_menu, reply_markup=obter_teclado_menu())
    return ConversationHandler.END

# --- FLUXO DE CONFIGURAÇÃO (/start) ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    iniciar_banco()
    try:
        await update.message.delete()
    except Exception:
        pass

    user_id = update.effective_user.id
    conn = obter_conexao_db()
    ja_cadastrado = conn.execute('SELECT 1 FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()

    if ja_cadastrado:
        # Conta já existe: nunca refaz o cadastro (a etapa final apaga e
        # recria as categorias do zero) — um '/start' acidental só reabre o
        # Menu Principal, sem tocar nos dados já configurados.
        texto_menu = await montar_texto_menu_principal(user_id)
        await enviar_fluxo(context, update.effective_chat.id, texto_menu, reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    await enviar_fluxo(
        context, update.effective_chat.id,
        "Boas-vindas ao seu Gestor Financeiro!\n\nPara começarmos, como você gostaria de ser chamado?"
    )
    return NOME_USUARIO

async def receber_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nome = update.message.text.strip()
    context.user_data['nome_temp'] = nome
    try:
        await update.message.delete()
    except Exception:
        pass

    texto = f"Prazer, <b>{nome}</b>! Agora, qual a sua <b>Renda Mensal</b>? (ex: <code>3.000,00</code>):"
    await enviar_fluxo(context, update.effective_chat.id, texto)
    return RENDA_MENSAL

async def receber_renda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['renda_temp'] = parse_valor(update.message.text)
        try:
            await update.message.delete()
        except Exception:
            pass

        texto = "Envie os nomes das categorias separados por vírgula (Ex: <code>Lazer, Alimentação, Mercado</code>):"
        await enviar_fluxo(context, update.effective_chat.id, texto)
        return NOMES_CATEGORIAS
    except ValueError:
        try:
            await update.message.delete()
        except Exception:
            pass
        return RENDA_MENSAL

async def receber_nomes_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass

    categorias = [c.strip().capitalize() for c in texto.split(',') if c.strip()]
    if not categorias:
        return NOMES_CATEGORIAS

    context.user_data['categorias_temp'] = categorias
    context.user_data['orcamentos_temp'] = {}
    context.user_data['index_atual'] = 0

    primeira_cat = categorias[0]
    texto_perq = f"Categorias anotadas! Vamos definir os orçamentos.\n\nQual o valor limite para a primeira categoria, <b>{primeira_cat}</b>? (ex: <code>500,00</code>)"
    await enviar_fluxo(context, update.effective_chat.id, texto_perq)
    return VALORES_CATEGORIAS

async def receber_valores_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        valor = parse_valor(texto)
        cats = context.user_data['categorias_temp']
        idx = context.user_data['index_atual']
        cat_atual = cats[idx]
        context.user_data['orcamentos_temp'][cat_atual] = valor
        idx += 1
        context.user_data['index_atual'] = idx

        nome = context.user_data.get('nome_temp', 'usuário')

        if idx < len(cats):
            proxima_cat = cats[idx]
            texto_perq = f"Perfeito! O limite de <b>{cat_atual}</b> foi registrado com sucesso.\n\nAgora, qual será o limite para a próxima categoria: <b>{proxima_cat}</b>?"
            await enviar_fluxo(context, update.effective_chat.id, texto_perq)
            return VALORES_CATEGORIAS
        else:
            texto_perq = f"Excelente, <b>{nome}</b>! Todas as categorias foram configuradas.\n\nAgora para finalizar, me informe a sua <b>data de vencimento da fatura</b> (dia entre 1 e 31, ex: <code>10</code>):"
            await enviar_fluxo(context, update.effective_chat.id, texto_perq)
            return DIA_FECHAMENTO
    except ValueError:
        return VALORES_CATEGORIAS

async def receber_dia_fechamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        dia = int(texto)
    except ValueError:
        await enviar_fluxo(context, update.effective_chat.id, "Por favor, digite apenas o número do dia (ex: <code>10</code>):")
        return DIA_FECHAMENTO

    if not dia_valido(dia):
        await enviar_fluxo(context, update.effective_chat.id, "Dia inválido. Digite um valor entre <b>1</b> e <b>31</b>:")
        return DIA_FECHAMENTO

    user_id = update.effective_user.id
    nome = context.user_data['nome_temp']
    renda = context.user_data['renda_temp']

    conn = obter_conexao_db()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) VALUES (?, ?, ?, ?, ?)', (user_id, nome, renda, dia, "20:00"))
    cursor.execute('DELETE FROM categorias WHERE user_id = ?', (user_id,))
    data_criacao = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')
    for cat_nome, valor in context.user_data['orcamentos_temp'].items():
        cursor.execute('INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) VALUES (?, ?, ?, ?, ?)', (user_id, cat_nome, valor, valor, data_criacao))
    conn.commit()
    conn.close()

    agendar_tarefa_alerta(context.job_queue, user_id, "20:00")

    texto_sucesso = f"Configuração Concluída, {nome}! Tudo pronto para começarmos."
    await enviar_fluxo(context, user_id, texto_sucesso, reply_markup=obter_teclado_menu())

    return ConversationHandler.END

# --- FLUXO DE GASTO INTELIGENTE COM IA ---
def _montar_tela_gasto_valor():
    """Tela inicial do fluxo de gasto. É a primeira etapa, então o '← Voltar'
    não tem um passo anterior pra retornar: cancela e volta ao Menu Principal."""
    texto = (
        "Registro Inteligente de Gastos\n\n"
        "Envie um áudio ou digite o quanto gastou e em qual categoria foi.\n\n"
        "<i>Exemplo: \"Gastei 45,90 no mercado\"</i>"
    )
    return texto, TECLADO_VOLTAR

def _montar_tela_metodo_pagamento():
    """Tela 'Qual Foi o Método de Pagamento?'. Só aparece para despesas, antes
    da pergunta de parcelamento. O '← Voltar' retorna para a etapa de digitar
    o gasto (voltar_gasto_valor), etapa imediatamente anterior."""
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("Crédito", callback_data='metodo_credito')],
        [InlineKeyboardButton("Débito / Pix", callback_data='metodo_debito_pix')],
        linha_voltar_com_menu('voltar_gasto_valor')
    ])
    return "Qual Foi o Método de Pagamento?", teclado

def _montar_tela_confirmar_parcela(valor, categoria):
    """Tela 'foi parcelado?' (só alcançada quando o método foi Crédito). O
    '← Voltar' aqui retorna para a pergunta de método de pagamento
    (voltar_metodo_pagamento), etapa imediatamente anterior."""
    texto = f"Esse gasto de <b>{formatar_real(valor)}</b> em <b>{categoria}</b> foi parcelado?"
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("Sim", callback_data='parcela_sim'), InlineKeyboardButton("Não", callback_data='parcela_nao')],
        linha_voltar_com_menu('voltar_metodo_pagamento')
    ])
    return texto, teclado

def _montar_tela_escolher_parcelas():
    """Tela de escolha do número de parcelas. O '← Voltar' retorna para a
    confirmação 'foi parcelado?' (voltar_confirmar_parcela), que ainda tem o
    gasto_pendente em cache."""
    teclado = InlineKeyboardMarkup([
        [InlineKeyboardButton("2x", callback_data='parcela_n_2'), InlineKeyboardButton("3x", callback_data='parcela_n_3')],
        [InlineKeyboardButton("4x", callback_data='parcela_n_4'), InlineKeyboardButton("5x", callback_data='parcela_n_5')],
        [InlineKeyboardButton("Digitar Total", callback_data='parcela_custom')],
        linha_voltar_com_menu('voltar_confirmar_parcela')
    ])
    return "Em quantas parcelas?", teclado

# Teclado da etapa de digitar o total de parcelas: '← Voltar' retorna para a
# escolha de parcelas (voltar_escolher_parcelas), etapa imediatamente anterior.
TECLADO_VOLTAR_ESCOLHER_PARCELAS = InlineKeyboardMarkup([linha_voltar_com_menu('voltar_escolher_parcelas')])

async def iniciar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    # Teclado isolado para a tela de registro: sem botões do Menu Principal.
    texto, teclado = _montar_tela_gasto_valor()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return GASTO_VALOR

async def voltar_gasto_valor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da pergunta de método de pagamento para a etapa de digitar o gasto.
    query = update.callback_query
    await responder_callback(query)
    context.user_data.pop('gasto_pendente', None)
    texto, teclado = _montar_tela_gasto_valor()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return GASTO_VALOR

async def voltar_metodo_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da confirmação de parcelamento para a pergunta de método de pagamento.
    query = update.callback_query
    await responder_callback(query)
    pendente = context.user_data.get('gasto_pendente')
    if not pendente:
        return await voltar_para_menu(update, context)
    texto, teclado = _montar_tela_metodo_pagamento()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return METODO_PAGAMENTO

async def voltar_confirmar_parcela(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da escolha de parcelas para a confirmação "foi parcelado?".
    query = update.callback_query
    await responder_callback(query)
    pendente = context.user_data.get('gasto_pendente')
    if not pendente:
        return await voltar_para_menu(update, context)
    texto, teclado = _montar_tela_confirmar_parcela(pendente['valor'], pendente['categoria'])
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return CONFIRMAR_PARCELA

async def voltar_escolher_parcelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da digitação do total de parcelas para a escolha de parcelas.
    query = update.callback_query
    await responder_callback(query)
    texto, teclado = _montar_tela_escolher_parcelas()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ESCOLHER_PARCELAS

def obter_categorias_usuario(user_id):
    # Busca as categorias cadastradas no SQLite para o usuário atual.
    # Essa lista alimenta tanto o prompt da IA quanto a validação
    # posterior, sem precisar de uma segunda consulta ao banco.
    conn = obter_conexao_db()
    categorias = conn.execute('SELECT nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()
    return [c[0] for c in categorias]

async def salvar_gasto_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    user_id = update.effective_user.id
    # Horário real de envio, segundo o próprio Telegram (update.message.date
    # vem em UTC) — não depende de quanto tempo a IA ou uma fila de updates
    # (bot offline) demoram pra processar essa mensagem.
    momento_envio = update.message.date.astimezone(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')

    try:
        await update.message.delete()
    except Exception:
        pass

    if not texto_usuario:
        return ConversationHandler.END

    await enviar_fluxo(context, user_id, "Registrando seu gasto...")

    try:
        # Busca as categorias cadastradas no SQLite e formata em lista textual
        # para injetar dinamicamente no prompt da IA.
        categorias_existentes = obter_categorias_usuario(user_id)
        lista_categorias = ", ".join(categorias_existentes) if categorias_existentes else "nenhuma categoria cadastrada"

        # Monta o prompt com a lista real de categorias do usuário e regras
        # obrigatórias de mapeamento, para a IA nunca inventar uma categoria
        # que não exista no banco.
        prompt = f"""
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

        FORMATO DE SAÍDA (JSON PURO):
        {{
          "tipo": "despesa" ou "receita",
          "valor": float (ex: 45.50),
          "categoria": "NOME_EXATO_DA_CATEGORIA_DA_LISTA",
          "descricao": "resumo curto"
        }}

        Mensagem do usuário: "{texto_usuario}"

        Retorne APENAS o JSON puro.
        """

        # Envia o prompt para o Gemini (com retry/backoff) e limpa o JSON retornado.
        response = await gerar_conteudo_com_retry(prompt)
        dados = limpar_json_ia(response.text)

        valor = float(dados['valor'])
        categoria = dados['categoria'].capitalize()
        tipo = dados['tipo'].strip().lower()
        descricao = dados.get('descricao', '')

        # Validação do retorno: mesmo com o prompt restringindo as opções,
        # confirmamos em Python se a categoria escolhida pela IA está de fato
        # na lista do usuário antes de mexer em saldo ou gravar a transação.
        if categoria not in categorias_existentes:
            await enviar_fluxo(
                context, user_id,
                f"Categoria '{categoria}' não encontrada.\n\n"
                f"Suas categorias atuais são: {lista_categorias}.\n\n"
                f"Crie a categoria antes de registrar um gasto nela, ou informe o nome corretamente.",
                reply_markup=obter_teclado_menu()
            )
            return ConversationHandler.END

        # Receitas não entram no fluxo de parcelamento — fecham direto como antes.
        if tipo != 'despesa':
            await _finalizar_gasto_simples(context, user_id, categoria, valor, tipo, descricao, momento_envio)
            return ConversationHandler.END

        # Despesa: ainda não debitamos nada. Guardamos o gasto em memória até
        # sabermos se é parcelado, pois o valor debitado muda em cada caso.
        context.user_data['gasto_pendente'] = {
            'categoria': categoria,
            'valor': valor,
            'descricao': descricao,
            'momento_envio': momento_envio,
        }

        # Teclado isolado para a pergunta de método de pagamento.
        texto, teclado = _montar_tela_metodo_pagamento()
        await enviar_fluxo(context, user_id, texto, reply_markup=teclado)
        return METODO_PAGAMENTO

    except Exception as e:
        await enviar_fluxo(context, user_id, mensagem_erro_ia(e), reply_markup=obter_teclado_menu())
        return ConversationHandler.END

async def responder_metodo_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    pendente = context.user_data.get('gasto_pendente')
    if not pendente:
        await atualizar_callback(query, context, "Sessão expirada. Registre o gasto novamente.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    if query.data == 'metodo_debito_pix':
        pendente['metodo_pagamento'] = 'Débito/Pix'

        # Débito/Pix pula a pergunta de parcelamento e registra direto.
        await atualizar_callback(query, context, "Registrando...")
        await asyncio.sleep(2)

        context.user_data.pop('gasto_pendente', None)
        await _finalizar_gasto_simples(
            context, user_id, pendente['categoria'], pendente['valor'], 'despesa',
            pendente['descricao'], pendente['momento_envio'], pendente['metodo_pagamento']
        )
        return ConversationHandler.END

    # Crédito segue para a pergunta de parcelamento já existente, sem alterações nela.
    pendente['metodo_pagamento'] = 'Crédito'
    texto, teclado = _montar_tela_confirmar_parcela(pendente['valor'], pendente['categoria'])
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return CONFIRMAR_PARCELA

async def _finalizar_gasto_simples(context, user_id, categoria, valor, tipo, descricao, momento_envio, metodo_pagamento=None):
    """Registra um gasto/receita de uma vez só (sem parcelamento): debita o
    valor cheio da categoria e grava uma única linha em 'transacoes'.
    'metodo_pagamento' só vem preenchido para despesas do fluxo manual."""
    conn = obter_conexao_db()
    cursor = conn.cursor()
    valor_armazenado = valor if tipo == 'despesa' else -valor
    cat_id, transacao_id = debitar_categoria(cursor, user_id, categoria, valor_armazenado, momento_envio, metodo_pagamento)
    conn.commit()
    novo_saldo = calcular_sobra_categoria(conn, user_id, categoria)
    conn.close()

    # Guarda a referência do gasto recém-criado para permitir a correção
    # imediata do valor pelo botão "Editar Gasto" na tela de confirmação.
    context.user_data['ultimo_gasto'] = {
        'transacao_id': transacao_id,
        'categoria_id': cat_id,
        'categoria': categoria,
        'tipo': tipo,
    }

    # Teclado isolado para a confirmação: permanece na tela (sem auto-revert)
    # para o botão "Editar Gasto" continuar clicável.
    teclado = [
        [InlineKeyboardButton("Editar Gasto", callback_data='editar_ultimo_gasto')],
        [InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")]
    ]
    await enviar_fluxo(
        context, user_id,
        f"Gasto Registrado com Sucesso!\n\n"
        f"Categoria: <b>{categoria}</b>\n"
        f"Valor: <b>{formatar_real(valor)}</b>\n"
        f"Resta na Categoria: <b>{formatar_real(novo_saldo)}</b>\n"
        f"Descrição: {descricao}",
        reply_markup=InlineKeyboardMarkup(teclado)
    )

async def iniciar_editar_ultimo_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Botão para editar gasto recém-registrado.
    query = update.callback_query
    await responder_callback(query)

    if not context.user_data.get('ultimo_gasto'):
        await atualizar_callback(query, context, "Sessão expirada. Registre o gasto novamente.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    await atualizar_callback(query, context, "Envie o valor correto para este gasto:", reply_markup=TECLADO_VOLTAR)
    return EDITAR_ULTIMO_GASTO

async def salvar_valor_editado_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    user_id = update.effective_user.id
    try:
        await update.message.delete()
    except Exception:
        pass

    ultimo_gasto = context.user_data.get('ultimo_gasto')
    if not ultimo_gasto:
        await enviar_fluxo(context, user_id, "Sessão expirada. Registre o gasto novamente.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    try:
        novo_valor = parse_valor(texto)
    except ValueError:
        await enviar_fluxo(context, user_id, "Valor inválido. Digite no formato correto (ex: <code>45,90</code>):", reply_markup=TECLADO_VOLTAR)
        return EDITAR_ULTIMO_GASTO

    transacao_id = ultimo_gasto['transacao_id']
    cat_id = ultimo_gasto['categoria_id']
    categoria = ultimo_gasto['categoria']
    tipo = ultimo_gasto['tipo']
    novo_valor_armazenado = novo_valor if tipo == 'despesa' else -novo_valor

    # Atualiza o valor do gasto no banco de dados, ajustando o saldo da
    # categoria pela diferença entre o valor antigo e o novo.
    conn = obter_conexao_db()
    cursor = conn.cursor()
    linha = cursor.execute('SELECT valor FROM transacoes WHERE id = ? AND user_id = ?', (transacao_id, user_id)).fetchone()
    if not linha:
        conn.close()
        await enviar_fluxo(context, user_id, "Gasto não encontrado. Registre novamente.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    valor_antigo_armazenado = linha[0]
    cursor.execute('UPDATE transacoes SET valor = ? WHERE id = ? AND user_id = ?', (novo_valor_armazenado, transacao_id, user_id))
    cursor.execute(
        'UPDATE categorias SET saldo_atual = saldo_atual - ? WHERE id = ?',
        (novo_valor_armazenado - valor_antigo_armazenado, cat_id)
    )
    conn.commit()
    novo_saldo = calcular_sobra_categoria(conn, user_id, categoria)
    conn.close()

    context.user_data.pop('ultimo_gasto', None)

    await mostrar_resumo_temporario(
        context, user_id,
        f"Gasto Corrigido com Sucesso!\n\n"
        f"Categoria: <b>{categoria}</b>\n"
        f"Novo Valor: <b>{formatar_real(novo_valor)}</b>\n"
        f"Resta na Categoria: <b>{formatar_real(novo_saldo)}</b>"
    )
    return ConversationHandler.END

async def _finalizar_gasto_parcelado(context, user_id, total_parcelas):
    """Fecha o fluxo de parcelamento: divide o valor total pelo número de
    parcelas, debita a 1ª parcela agora e grava o parcelamento — incluindo
    a fatura ('ciclo_referencia') calculada a partir do dia de fechamento
    do usuário, que serve de base para todas as parcelas seguintes."""
    pendente = context.user_data.get('gasto_pendente')
    if not pendente:
        await enviar_fluxo(context, user_id, "Sessão expirada. Registre o gasto novamente.", reply_markup=obter_teclado_menu())
        return

    categoria = pendente['categoria']
    valor_total = pendente['valor']
    descricao = pendente['descricao']
    momento_envio = pendente['momento_envio']
    metodo_pagamento = pendente.get('metodo_pagamento')
    valor_parcela = round(valor_total / total_parcelas, 2)

    conn = obter_conexao_db()
    try:
        cursor = conn.cursor()
        dia_fechamento = cursor.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()[0]

        data_compra = datetime.strptime(momento_envio, '%Y-%m-%d %H:%M:%S')
        ciclo_referencia = calcular_ciclo_fatura(data_compra, dia_fechamento)

        debitar_categoria(cursor, user_id, categoria, valor_parcela, momento_envio, metodo_pagamento)
        cursor.execute(
            '''INSERT INTO parcelamentos
               (user_id, categoria_nome, descricao, valor_total, valor_parcela, total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, 1)''',
            (user_id, categoria, descricao, valor_total, valor_parcela, total_parcelas, momento_envio, ciclo_referencia)
        )
        conn.commit()
        novo_saldo = calcular_sobra_categoria(conn, user_id, categoria)
    finally:
        conn.close()

    context.user_data.pop('gasto_pendente', None)

    await mostrar_resumo_temporario(
        context, user_id,
        f"Compra Parcelada Registrada!\n\n"
        f"Categoria: <b>{categoria}</b>\n"
        f"Total: <b>{formatar_real(valor_total)}</b> em <b>{total_parcelas}x</b> de <b>{formatar_real(valor_parcela)}</b>\n"
        f"Resta na Categoria: <b>{formatar_real(novo_saldo)}</b>\n"
        f"Fatura de referência: <b>{ciclo_referencia}</b>\n\n"
        f"Parcela 1/{total_parcelas} debitada agora. Se o banco jogar essa compra pra fatura seguinte, "
        f"corrija em Categorias → {categoria} → Conferir Parcelas."
    )

async def responder_parcela_sim_nao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    if query.data == 'parcela_nao':
        pendente = context.user_data.get('gasto_pendente')
        if not pendente:
            await atualizar_callback(query, context, "Sessão expirada. Registre o gasto novamente.", reply_markup=obter_teclado_menu())
            return ConversationHandler.END
        context.user_data.pop('gasto_pendente', None)

        # Exclui a mensagem de pergunta de parcelamento após a escolha: edita
        # o balão do clique para remover o teclado e dar feedback do "Não"
        # antes de aguardar e substituir pela confirmação.
        await atualizar_callback(query, context, "Registrando...")
        await asyncio.sleep(2)

        # Exibe mensagem temporária de confirmação do gasto registrado.
        await _finalizar_gasto_simples(
            context, user_id, pendente['categoria'], pendente['valor'], 'despesa',
            pendente['descricao'], pendente['momento_envio'], pendente.get('metodo_pagamento')
        )
        return ConversationHandler.END

    # Teclado isolado para a escolha do número de parcelas.
    texto, teclado = _montar_tela_escolher_parcelas()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ESCOLHER_PARCELAS

async def escolher_numero_parcelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    if query.data == 'parcela_custom':
        await atualizar_callback(query, context, "Digite o <b>número total de parcelas</b> (ex: <code>10</code>):", reply_markup=TECLADO_VOLTAR_ESCOLHER_PARCELAS)
        return DIGITAR_PARCELAS

    total_parcelas = int(query.data.replace('parcela_n_', ''))

    # Exclui a mensagem de pergunta de parcelamento após a escolha: edita o
    # balão do clique para remover o teclado e dar feedback da escolha antes
    # de aguardar e substituir pela confirmação.
    await atualizar_callback(query, context, "Registrando...")
    await asyncio.sleep(2)

    # Exibe mensagem temporária de confirmação do gasto registrado.
    await _finalizar_gasto_parcelado(context, user_id, total_parcelas)
    return ConversationHandler.END

async def digitar_numero_parcelas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    user_id = update.effective_user.id
    try:
        await update.message.delete()
    except Exception:
        pass

    if not texto.isdigit() or int(texto) < 2:
        await enviar_fluxo(context, user_id, "Digite um número válido de parcelas (mínimo 2):", reply_markup=TECLADO_VOLTAR_ESCOLHER_PARCELAS)
        return DIGITAR_PARCELAS

    await _finalizar_gasto_parcelado(context, user_id, int(texto))
    return ConversationHandler.END

# --- ADICIONAR NOVA CATEGORIA ---
def _montar_tela_nome_categoria():
    """Tela inicial do fluxo de nova categoria. Primeira etapa: o '← Voltar'
    cancela e volta ao Menu Principal, pois não há passo anterior."""
    return "Qual será o <b>nome</b> da nova categoria?", TECLADO_VOLTAR

# Teclado da etapa de valor: '← Voltar' retorna para a etapa de nome
# (voltar_nome_cat), etapa imediatamente anterior.
TECLADO_VOLTAR_NOME_CAT = InlineKeyboardMarkup([[InlineKeyboardButton("← Voltar", callback_data='voltar_nome_cat', style="danger")]])

async def iniciar_adicionar_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    texto, teclado = _montar_tela_nome_categoria()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return AGUARDANDO_NOME_CAT

async def voltar_nome_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da etapa de valor para a etapa de nome da nova categoria.
    query = update.callback_query
    await responder_callback(query)
    context.user_data.pop('nova_cat_nome', None)
    texto, teclado = _montar_tela_nome_categoria()
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return AGUARDANDO_NOME_CAT

async def receber_nome_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['nova_cat_nome'] = update.message.text.strip().capitalize()
    try:
        await update.message.delete()
    except Exception:
        pass
    await enviar_fluxo(context, update.effective_chat.id, "Qual o <b>limite mensal</b> dela? (ex: <code>500,00</code>)", reply_markup=TECLADO_VOLTAR_NOME_CAT)
    return AGUARDANDO_VALOR_CAT

async def salvar_nova_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        valor = parse_valor(texto)

        conn = obter_conexao_db()
        data_criacao = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) VALUES (?, ?, ?, ?, ?)', (update.effective_user.id, context.user_data['nova_cat_nome'], valor, valor, data_criacao))
        conn.commit()
        conn.close()

        await mostrar_resumo_temporario(context, update.effective_user.id, "Categoria criada com sucesso!")
        return ConversationHandler.END
    except ValueError:
        await enviar_fluxo(context, update.effective_user.id, "Valor inválido. Digite no formato correto (ex: <code>500,00</code>):", reply_markup=TECLADO_VOLTAR_NOME_CAT)
        return AGUARDANDO_VALOR_CAT

# --- EDITAR CATEGORIAS E DADOS DA CONTA ---
async def menu_editar_categorias(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    conn = obter_conexao_db()
    cats = conn.execute('SELECT id, nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()

    # Listagem limpa: só o nome da categoria, sem valores ao lado.
    teclado = [[InlineKeyboardButton(nome, callback_data=f"edit_cat_{cid}")] for cid, nome in cats]

    teclado.append([InlineKeyboardButton("Nova Categoria", callback_data='menu_add_cat')])
    teclado.append([InlineKeyboardButton("Alterar Dia de Vencimento", callback_data='menu_editar_dia')])
    teclado.append(linha_voltar_com_menu('menu_resumo'))

    aviso = "" if cats else "Você ainda não tem categorias cadastradas.\n\n"
    await atualizar_callback(
        query, context,
        f"Categorias\n\n{aviso}Selecione uma categoria para ver os detalhes, crie uma nova, ou altere o dia de vencimento do cartão:",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    # Tela só é acessada de dentro de "Ver Resumo" (menu_resumo), por isso o
    # '← Voltar' sobe só um nível pra lá, e não direto pro Menu Principal.
    return ConversationHandler.END

async def gerenciar_categoria_especifica(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    data = query.data

    cat_id = int(data.replace('edit_cat_', ''))
    context.user_data['cat_em_edicao'] = cat_id
    user_id = query.from_user.id

    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome, orcamento_mensal FROM categorias WHERE id = ? AND user_id = ?', (cat_id, user_id)).fetchone()

    if not cat:
        conn.close()
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    nome, limite = cat

    linha_dia = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    dia_fechamento = linha_dia[0] if linha_dia else None

    # Orçamento/Gasto/Sobra do ciclo vigente: mesma fonte da tela "Ver Resumo".
    orcamento, gasto, saldo = calcular_status_categoria(conn, user_id, nome, obter_inicio_ciclo_usuario(conn, user_id))
    conn.close()

    # Nome do mês da fatura vigente (mesmo rótulo usado no histórico); sem
    # dia de fechamento configurado ainda, usa o mês corrente como aproximação.
    if dia_fechamento:
        mes_num = int(obter_ciclo_atual(dia_fechamento).split('-')[1])
    else:
        mes_num = datetime.now(FUSO_BRASILIA).month
    nome_mes = NOMES_MESES[mes_num - 1]

    # Só mostra o orçamento do ciclo quando transferências o tiraram do limite fixo.
    linha_orcamento = f"└ Orçamento em {nome_mes}: <b>{formatar_real(orcamento)}</b>\n" if abs(orcamento - limite) >= 0.005 else ""

    # Retorna para a lista de categorias (etapa imediatamente anterior),
    # reaproveitando o mesmo handler da tela 'menu_editar_cat'.
    teclado = [
        [InlineKeyboardButton("Editar Nome", callback_data=f"acao_renomear_{cat_id}")],
        [InlineKeyboardButton("Editar Limite", callback_data=f"acao_limite_{cat_id}")],
        [InlineKeyboardButton("Conferir Parcelas", callback_data=f"view_installments_{cat_id}")],
        [InlineKeyboardButton("Excluir Categoria", callback_data=f"acao_excluir_{cat_id}")],
        linha_voltar_com_menu('menu_editar_cat')
    ]

    await atualizar_callback(
        query, context,
        f"Categoria: {nome}\n\n"
        f"└ Limite Atual: <b>{formatar_real(limite)}</b>\n"
        f"{linha_orcamento}"
        f"└ Gasto em {nome_mes}: <b>{formatar_real(gasto)}</b>\n"
        f"└ Saldo Atual: <b>{formatar_real(saldo)}</b>\n\n"
        "O Que Você Deseja Fazer?",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    # Tela alcançável a partir de um estado rastreado (ex: EDITAR_NOME_CAT via
    # '← Voltar'); encerrar aqui evita que a conversa fique presa nesse estado.
    return ConversationHandler.END

def _montar_tela_meses_parcelas(user_id, cat_id):
    """Tela 'Conferir Parcelas': botões só dos meses que ainda têm parcelas da
    categoria, da fatura vigente em diante, em ordem cronológica. Parcelas de
    faturas já fechadas saem daqui e ficam apenas no histórico mensal."""
    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ? AND user_id = ?', (cat_id, user_id)).fetchone()
    user_data = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    if not cat or not user_data:
        conn.close()
        return None, None

    nome_cat = cat[0]
    meses = buscar_meses_com_parcelas(conn, user_id, nome_cat, a_partir_de=obter_ciclo_atual(user_data[0]))
    conn.close()

    # Só mostra o ano no rótulo quando as parcelas atravessam mais de um ano.
    com_ano = len({m[:4] for m in meses}) > 1
    botoes = [
        InlineKeyboardButton(rotulo_mes(m, com_ano), callback_data=f"view_installments_month_{cat_id}_{m}")
        for m in meses
    ]
    # Retorna para o detalhe da categoria (etapa imediatamente anterior).
    teclado = montar_grade(botoes) + [linha_voltar_com_menu(f"edit_cat_{cat_id}")]

    if not meses:
        return f"Parcelas de {nome_cat}\n\nNenhuma parcela pendente nessa categoria no momento.", InlineKeyboardMarkup(teclado)
    return f"Parcelas de {nome_cat}\n\nSelecione o mês que deseja conferir:", InlineKeyboardMarkup(teclado)

async def ver_parcelas_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('view_installments_', ''))

    texto, teclado = _montar_tela_meses_parcelas(query.from_user.id, cat_id)
    if texto is None:
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return

    await atualizar_callback(query, context, texto, reply_markup=teclado)

async def ver_parcelas_mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detalhe de um mês dentro de 'Conferir Parcelas': lista as parcelas da
    categoria que caem naquela fatura, com o atalho 'Banco Adiou' de cada uma."""
    query = update.callback_query
    await responder_callback(query)
    cat_id, ano_mes = re.match(r'^view_installments_month_(\d+)_(\d{4}-\d{2})$', query.data).groups()
    cat_id = int(cat_id)
    user_id = query.from_user.id

    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ? AND user_id = ?', (cat_id, user_id)).fetchone()
    if not cat:
        conn.close()
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return
    nome_cat = cat[0]

    parcelas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS +
        '''SELECT p.id, p.descricao, p.valor_parcela, e.n + 1, p.total_parcelas, p.valor_total
           FROM expandido e JOIN parcelamentos p ON p.id = e.pid
           WHERE e.ano_mes = ? ORDER BY p.id''',
        (user_id, nome_cat, nome_cat, ano_mes)
    ).fetchall()
    conn.close()

    # Retorna para a grade de meses (etapa imediatamente anterior).
    teclado = [linha_voltar_com_menu(f"view_installments_{cat_id}")]
    ano, mes = ano_mes.split('-')
    titulo = f"Parcelas de {escape(nome_cat)} — {NOMES_MESES[int(mes) - 1]} de {ano}"

    if not parcelas:
        await atualizar_callback(query, context, f"{titulo}\n\nNenhuma parcela neste mês.", reply_markup=InlineKeyboardMarkup(teclado))
        return

    blocos = []
    for pid, desc, valor, numero, total, valor_total in parcelas:
        blocos.append(
            f"• {escape(desc or 'Compra parcelada')}\n\n"
            f"  └ Valor Total: <b>{formatar_real(valor_total)}</b>\n"
            f"  └ Parcela: <b>{numero}/{total}</b>\n"
            f"  └ Valor Parcela: <b>{formatar_real(valor)}</b>"
        )
        teclado.insert(-1, [InlineKeyboardButton(f"Banco Adiou: {desc or 'compra'}"[:40], callback_data=f"ajustar_fatura_{pid}")])

    total_mes = sum(p[2] for p in parcelas)
    texto = f"{titulo}\n\n" + "\n\n".join(blocos) + f"\n\nTotal do mês: <b>{formatar_real(total_mes)}</b>"
    await atualizar_callback(query, context, texto, reply_markup=InlineKeyboardMarkup(teclado))

async def ajustar_fatura_parcelamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Corrige a fatura de referência de um parcelamento (ex: banco empurrou a
    1ª parcela pra fatura seguinte). Como todas as parcelas seguintes são
    calculadas a partir de 'ciclo_referencia', mudar esse único campo já
    desloca a fila inteira em cascata — sem tocar em nenhuma outra linha.
    """
    query = update.callback_query
    await responder_callback(query, "Fatura ajustada — parcelas seguintes deslocadas.")
    pid = int(query.data.replace('ajustar_fatura_', ''))
    user_id = query.from_user.id

    conn = obter_conexao_db()
    parc = conn.execute('SELECT categoria_nome, ciclo_referencia, data_criacao FROM parcelamentos WHERE id = ?', (pid,)).fetchone()
    if not parc:
        conn.close()
        await atualizar_callback(query, context, "Parcelamento não encontrado.", reply_markup=obter_teclado_menu())
        return

    categoria_nome, ciclo_atual, data_criacao = parc
    ciclo_atual = ciclo_atual or data_criacao[:7]
    conn.execute('UPDATE parcelamentos SET ciclo_referencia = ? WHERE id = ?', (_somar_meses(ciclo_atual, 1), pid))
    cat = conn.execute('SELECT id FROM categorias WHERE user_id = ? AND nome = ?', (user_id, categoria_nome)).fetchone()
    conn.commit()
    conn.close()

    if not cat:
        await atualizar_callback(query, context, "Fatura ajustada.", reply_markup=obter_teclado_menu())
        return

    texto, teclado = _montar_tela_meses_parcelas(user_id, cat[0])
    await atualizar_callback(query, context, texto, reply_markup=teclado)

async def iniciar_renomear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('acao_renomear_', ''))
    context.user_data['cat_em_edicao'] = cat_id

    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ?', (cat_id,)).fetchone()
    conn.close()
    nome_atual = cat[0] if cat else "Categoria"

    # '← Voltar' retorna para o detalhe da categoria (etapa anterior).
    await atualizar_callback(
        query, context,
        f"O Nome Atual da Categoria: <b>{nome_atual}</b>.\n\n"
        "Envie o <b>Novo Nome</b> Para Esta Categoria:",
        reply_markup=InlineKeyboardMarkup([linha_voltar_com_menu(f"edit_cat_{cat_id}")])
    )
    return EDITAR_NOME_CAT

async def salvar_novo_nome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    novo_nome = update.message.text.strip().capitalize()
    cat_id = context.user_data.get('cat_em_edicao')
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    if not cat_id:
        await enviar_fluxo(context, user_id, "Erro de sessão. Volte ao menu.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    conn = obter_conexao_db()
    cat_antiga = conn.execute('SELECT nome FROM categorias WHERE id = ?', (cat_id,)).fetchone()
    if cat_antiga:
        nome_antigo = cat_antiga[0]
        conn.execute('UPDATE transacoes SET categoria_nome = ? WHERE user_id = ? AND categoria_nome = ?', (novo_nome, user_id, nome_antigo))

    conn.execute('UPDATE categorias SET nome = ? WHERE id = ?', (novo_nome, cat_id))
    conn.commit()
    conn.close()

    await mostrar_resumo_temporario(context, user_id, f"Categoria renomeada para <b>{novo_nome}</b> com sucesso!")
    return ConversationHandler.END

async def iniciar_mudar_limite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('acao_limite_', ''))
    context.user_data['cat_em_edicao'] = cat_id

    conn = obter_conexao_db()
    user_id = query.from_user.id
    cat = conn.execute('SELECT nome, orcamento_mensal FROM categorias WHERE id = ? AND user_id = ?', (cat_id, user_id)).fetchone()
    if cat:
        nome, limite = cat
        orcamento, _, saldo = calcular_status_categoria(conn, user_id, nome, obter_inicio_ciclo_usuario(conn, user_id))
        # Efeito líquido das transferências no ciclo: orçamento (já com
        # transferências) menos o limite fixo cadastrado.
        transferencias = orcamento - limite
    else:
        nome, limite, saldo, transferencias = "Categoria", 0.0, 0.0, 0.0
    conn.close()

    # '← Voltar' retorna para o detalhe da categoria (etapa anterior).
    await atualizar_callback(
        query, context,
        f"A Categoria <b>{nome}</b> Está Atualmente Com:\n\n"
        f"└ Limite Atual: <b>{formatar_real(limite)}</b>\n"
        f"└ Saldo Disponível: <b>{formatar_real(saldo)}</b>\n"
        f"└ Transferências: <b>{formatar_real(transferencias)}</b>\n\n"
        "Envie o <b>Novo Valor de Limite /</b> (Exemplo: <code>545,00</code>):",
        reply_markup=InlineKeyboardMarkup([linha_voltar_com_menu(f"edit_cat_{cat_id}")])
    )
    return EDITAR_VALOR_CAT

async def salvar_novo_limite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    cat_id = context.user_data.get('cat_em_edicao')
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        novo_limite = parse_valor(texto)

        if not cat_id:
            await enviar_fluxo(context, user_id, "Erro de sessão. Volte ao menu.", reply_markup=obter_teclado_menu())
            return ConversationHandler.END

        conn = obter_conexao_db()
        cat = conn.execute('SELECT orcamento_mensal, saldo_atual FROM categorias WHERE id = ?', (cat_id,)).fetchone()
        if cat:
            limite_antigo, saldo_atual = cat
            gasto_total = limite_antigo - saldo_atual
            novo_saldo = novo_limite - gasto_total
            conn.execute('UPDATE categorias SET orcamento_mensal = ?, saldo_atual = ? WHERE id = ?', (novo_limite, novo_saldo, cat_id))
        conn.commit()
        conn.close()

        await mostrar_resumo_temporario(context, user_id, f"Limite atualizado para <b>{formatar_real(novo_limite)}</b> com sucesso!")
        return ConversationHandler.END
    except ValueError:
        # cat_id pode não existir se a sessão já tiver expirado; nesse caso,
        # o '← Voltar' cai no fallback padrão (Menu Principal).
        teclado_voltar = InlineKeyboardMarkup([linha_voltar_com_menu(f"edit_cat_{cat_id}")]) if cat_id else TECLADO_VOLTAR
        await enviar_fluxo(
            context, user_id,
            "Valor inválido. Digite no formato correto (ex: <code>500,00</code>):",
            reply_markup=teclado_voltar
        )
        return EDITAR_VALOR_CAT

async def excluir_categoria_botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('acao_excluir_', ''))

    conn = obter_conexao_db()
    conn.execute('DELETE FROM categorias WHERE id = ?', (cat_id,))
    conn.commit()
    conn.close()

    await mostrar_resumo_temporario(context, query.message.chat_id, "Categoria excluída com sucesso!")

# Teclado da etapa de dia de vencimento: '← Voltar' retorna para a lista de
# categorias (menu_editar_cat), de onde essa etapa é sempre acessada.
TECLADO_VOLTAR_LISTA_CAT = InlineKeyboardMarkup([[InlineKeyboardButton("← Voltar", callback_data='menu_editar_cat', style="danger")]])

async def iniciar_editar_dia_vencimento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    conn = obter_conexao_db()
    usuario = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()

    dia_atual = usuario[0] if usuario else None
    texto_dia_atual = f"Atualmente configurado para o dia <b>{dia_atual}</b>.\n\n" if dia_atual else ""

    await atualizar_callback(
        query, context,
        "Alterar Dia de Vencimento do Cartão\n\n"
        f"{texto_dia_atual}"
        "Envie o <b>novo dia</b> de vencimento (entre 1 e 31, ex: <code>10</code>):",
        reply_markup=TECLADO_VOLTAR_LISTA_CAT
    )
    return EDITAR_DIA_VENCIMENTO

async def salvar_novo_dia_vencimento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    user_id = update.effective_user.id

    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        dia = int(texto)
    except ValueError:
        await enviar_fluxo(context, user_id, "Por favor, digite apenas o número do dia (ex: <code>10</code>):", reply_markup=TECLADO_VOLTAR_LISTA_CAT)
        return EDITAR_DIA_VENCIMENTO

    if not dia_valido(dia):
        await enviar_fluxo(context, user_id, "Dia inválido. Digite um valor entre <b>1</b> e <b>31</b>:", reply_markup=TECLADO_VOLTAR_LISTA_CAT)
        return EDITAR_DIA_VENCIMENTO

    conn = obter_conexao_db()
    conn.execute('UPDATE usuarios SET dia_fechamento = ? WHERE user_id = ?', (dia, user_id))
    conn.commit()
    conn.close()

    await mostrar_resumo_temporario(context, user_id, f"Dia de vencimento atualizado para <b>{dia}</b> com sucesso!")
    return ConversationHandler.END

# --- GASTOS RECORRENTES ---
async def menu_gastos_recorrentes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Tela de entrada (acessada direto do Menu Principal): lista as categorias
    # do usuário para escolher em qual mexer com gastos recorrentes.
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    conn = obter_conexao_db()
    cats = conn.execute('SELECT id, nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall()
    conn.close()

    teclado = [[InlineKeyboardButton(nome, callback_data=f"rec_cat_{cid}")] for cid, nome in cats]
    teclado.append([InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")])

    aviso = "" if cats else "Você ainda não tem categorias cadastradas.\n\n"
    await atualizar_callback(
        query, context,
        f"Gastos Recorrentes\n\n{aviso}Em Qual Categoria Deseja Adicionar ou Visualizar Gasto Recorrente?",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    return ConversationHandler.END

async def recorrente_escolher_acao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Categoria escolhida: oferece as duas ações (Registrar / Visualizar).
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('rec_cat_', ''))

    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ?', (cat_id,)).fetchone()
    conn.close()

    if not cat:
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    nome = cat[0]
    # Retorna para a lista de categorias (etapa imediatamente anterior).
    teclado = [
        [InlineKeyboardButton("Registrar Gasto", callback_data=f"rec_criar_{cat_id}")],
        [InlineKeyboardButton("Visualizar Gastos Recorrentes", callback_data=f"rec_ver_{cat_id}")],
        linha_voltar_com_menu('menu_recorrente')
    ]
    await atualizar_callback(
        query, context,
        f"Categoria: <b>{nome}</b>\n\nO Que Deseja Fazer?",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    return ConversationHandler.END

def _montar_teclado_acao_recorrente(cat_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Registrar Gasto", callback_data=f"rec_criar_{cat_id}")],
        [InlineKeyboardButton("Visualizar Gastos Recorrentes", callback_data=f"rec_ver_{cat_id}")],
        linha_voltar_com_menu('menu_recorrente')
    ])

async def iniciar_criar_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('rec_criar_', ''))

    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ?', (cat_id,)).fetchone()
    conn.close()

    if not cat:
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    context.user_data['recorrente_cat_id'] = cat_id
    context.user_data['recorrente_cat_nome'] = cat[0]

    texto = (
        f"Registrar Gasto Recorrente em <b>{cat[0]}</b>\n\n"
        "Digite a Descrição, o Valor e o Dia da Cobrança Mensal.\n\n"
        "<i>Exemplo: \"Assinatura do Spotify, 12,90 todo dia 14\"</i>"
    )
    teclado = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_cat_{cat_id}")])
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return RECORRENTE_TEXTO_LIVRE

# Ordem fixa em que os campos faltantes são perguntados, um de cada vez.
CAMPOS_RECORRENTE = [
    ('descricao', 'Qual o <b>nome/descrição</b> desse gasto recorrente? (ex: <code>Assinatura do Spotify</code>)'),
    ('valor', 'Qual o <b>valor</b> da cobrança mensal? (ex: <code>12,90</code>)'),
    ('dia_cobranca', 'Em qual <b>dia do mês</b> (1 a 31) a cobrança acontece? (ex: <code>14</code>)'),
]

async def _perguntar_proximo_campo_recorrente(context, user_id):
    """Verifica o próximo campo ainda não preenchido em 'recorrente_pendente'
    e pergunta por ele; se todos já estiverem preenchidos, salva o gasto
    recorrente e encerra a conversa."""
    pendente = context.user_data.get('recorrente_pendente', {})
    cat_id = context.user_data.get('recorrente_cat_id')

    for campo, pergunta in CAMPOS_RECORRENTE:
        if pendente.get(campo) is None:
            teclado = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_cat_{cat_id}")])
            await enviar_fluxo(context, user_id, pergunta, reply_markup=teclado)
            return RECORRENTE_COMPLETAR

    return await _salvar_gasto_recorrente(context, user_id)

async def _salvar_gasto_recorrente(context, user_id):
    """Todos os campos já preenchidos: grava o gasto recorrente e mostra a
    confirmação, voltando para a tela de ações da categoria (Registrar/Visualizar)."""
    cat_id = context.user_data.get('recorrente_cat_id')
    cat_nome = context.user_data.get('recorrente_cat_nome')
    pendente = context.user_data.get('recorrente_pendente', {})

    descricao = pendente['descricao']
    valor = pendente['valor']
    dia_cobranca = pendente['dia_cobranca']
    criado_em = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')

    conn = obter_conexao_db()
    conn.execute(
        '''INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em)
           VALUES (?, ?, ?, ?, ?, ?)''',
        (user_id, cat_nome, descricao, valor, dia_cobranca, criado_em)
    )
    conn.commit()
    conn.close()

    for chave in ('recorrente_pendente', 'recorrente_cat_id', 'recorrente_cat_nome'):
        context.user_data.pop(chave, None)

    await enviar_fluxo(
        context, user_id,
        f"Gasto Recorrente Registrado com Sucesso!\n\n"
        f"Categoria: <b>{cat_nome}</b>\n"
        f"Informações: {escape(descricao)}\n"
        f"Dia da Cobrança: <b>{dia_cobranca}</b>\n"
        f"Valor da Cobrança: <b>{formatar_real(valor)}</b>",
        reply_markup=_montar_teclado_acao_recorrente(cat_id)
    )
    return ConversationHandler.END

async def receber_texto_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    user_id = update.effective_user.id
    cat_id = context.user_data.get('recorrente_cat_id')

    try:
        await update.message.delete()
    except Exception:
        pass

    if not cat_id:
        await enviar_fluxo(context, user_id, "Sessão expirada. Volte ao menu.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    teclado_voltar = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_cat_{cat_id}")])
    await enviar_fluxo(context, user_id, "Analisando...", reply_markup=teclado_voltar)

    try:
        prompt = f"""
Analise a mensagem do usuário sobre um gasto recorrente (assinatura ou cobrança mensal fixa) e extraia as informações em formato JSON puro.

Extraia:
- "descricao": nome/descrição curta do gasto (ex: "Assinatura do Spotify"). Se não conseguir identificar, use null.
- "valor": valor numérico da cobrança mensal (float, ex: 12.90). Se não conseguir identificar, use null.
- "dia_cobranca": dia do mês (número inteiro de 1 a 31) em que a cobrança acontece. Se não conseguir identificar, use null.

FORMATO DE SAÍDA (JSON PURO):
{{
  "descricao": string ou null,
  "valor": float ou null,
  "dia_cobranca": int ou null
}}

Mensagem do usuário: "{texto_usuario}"

Retorne APENAS o JSON puro.
"""
        response = await gerar_conteudo_com_retry(prompt)
        dados = limpar_json_ia(response.text)
    except Exception as e:
        await enviar_fluxo(context, user_id, mensagem_erro_ia(e), reply_markup=teclado_voltar)
        return RECORRENTE_TEXTO_LIVRE

    descricao = dados.get('descricao')
    valor_bruto = dados.get('valor')
    dia_bruto = dados.get('dia_cobranca')

    pendente = {'descricao': None, 'valor': None, 'dia_cobranca': None}

    if isinstance(descricao, str) and descricao.strip():
        pendente['descricao'] = descricao.strip()

    if valor_bruto is not None:
        try:
            valor = float(valor_bruto)
            if valor > 0:
                pendente['valor'] = valor
        except (TypeError, ValueError):
            pass

    if dia_bruto is not None:
        try:
            dia = int(dia_bruto)
            if dia_valido(dia):
                pendente['dia_cobranca'] = dia
        except (TypeError, ValueError):
            pass

    context.user_data['recorrente_pendente'] = pendente
    return await _perguntar_proximo_campo_recorrente(context, user_id)

async def receber_campo_faltante_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    user_id = update.effective_user.id
    cat_id = context.user_data.get('recorrente_cat_id')
    pendente = context.user_data.get('recorrente_pendente')

    try:
        await update.message.delete()
    except Exception:
        pass

    if not cat_id or pendente is None:
        await enviar_fluxo(context, user_id, "Sessão expirada. Volte ao menu.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    teclado_erro = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_cat_{cat_id}")])
    campo_atual = next((c for c, _ in CAMPOS_RECORRENTE if pendente.get(c) is None), None)

    if campo_atual == 'descricao':
        if not texto:
            await enviar_fluxo(context, user_id, "Descrição inválida. Digite um nome curto para o gasto:", reply_markup=teclado_erro)
            return RECORRENTE_COMPLETAR
        pendente['descricao'] = texto

    elif campo_atual == 'valor':
        try:
            valor = parse_valor(texto)
            if valor <= 0:
                raise ValueError
        except ValueError:
            await enviar_fluxo(context, user_id, "Valor inválido. Digite no formato correto (ex: <code>12,90</code>):", reply_markup=teclado_erro)
            return RECORRENTE_COMPLETAR
        pendente['valor'] = valor

    elif campo_atual == 'dia_cobranca':
        try:
            dia = int(texto)
        except ValueError:
            await enviar_fluxo(context, user_id, "Por favor, digite apenas o número do dia (ex: <code>14</code>):", reply_markup=teclado_erro)
            return RECORRENTE_COMPLETAR
        if not dia_valido(dia):
            await enviar_fluxo(context, user_id, "Dia inválido. Digite um valor entre <b>1</b> e <b>31</b>:", reply_markup=teclado_erro)
            return RECORRENTE_COMPLETAR
        pendente['dia_cobranca'] = dia

    context.user_data['recorrente_pendente'] = pendente
    return await _perguntar_proximo_campo_recorrente(context, user_id)

def _montar_tela_listagem_recorrentes(user_id, cat_id, aviso=""):
    """Tela de listagem (item 5 do fluxograma): soma total no topo e um botão
    clicável por gasto recorrente, mostrando só o nome (valor e dia ficam na
    tela do item, seguindo o padrão das outras listas do bot).
    Reaproveitada tanto na entrada (rec_ver_<cat_id>) quanto depois de editar
    ou excluir um item, prefixando 'aviso' com a mensagem de sucesso."""
    conn = obter_conexao_db()
    cat = conn.execute('SELECT nome FROM categorias WHERE id = ? AND user_id = ?', (cat_id, user_id)).fetchone()
    if not cat:
        conn.close()
        return None, None
    nome_cat = cat[0]

    itens = conn.execute(
        'SELECT id, descricao, valor, dia_cobranca FROM gastos_recorrentes WHERE user_id = ? AND categoria_nome = ? ORDER BY criado_em',
        (user_id, nome_cat)
    ).fetchall()
    conn.close()

    total = sum(valor for _, _, valor, _ in itens)
    teclado = [
        [InlineKeyboardButton(descricao, callback_data=f"rec_item_{gr_id}")]
        for gr_id, descricao, _, _ in itens
    ]
    teclado.append(linha_voltar_com_menu(f"rec_cat_{cat_id}"))

    corpo = "Selecione um Item Para Editar ou Excluir:" if itens else "Nenhum Gasto Recorrente Cadastrado Nesta Categoria."
    texto = (
        f"{aviso}Gastos Recorrentes de <b>{escape(nome_cat)}</b>\n\n"
        f"Total Mensal: <b>{formatar_real(total)}</b>\n\n"
        f"{corpo}"
    )
    return texto, InlineKeyboardMarkup(teclado)

async def ver_recorrentes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    cat_id = int(query.data.replace('rec_ver_', ''))

    texto, teclado = _montar_tela_listagem_recorrentes(query.from_user.id, cat_id)
    if texto is None:
        await atualizar_callback(query, context, "Categoria não encontrada.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ConversationHandler.END

def _buscar_item_recorrente(user_id, gr_id):
    """Busca um gasto recorrente do usuário junto com o id da categoria (necessário
    pra montar os botões de volta), retornando None se não existir ou não for dele."""
    conn = obter_conexao_db()
    item = conn.execute(
        '''SELECT g.id, g.descricao, g.valor, g.dia_cobranca, c.id
           FROM gastos_recorrentes g
           JOIN categorias c ON c.user_id = g.user_id AND c.nome = g.categoria_nome
           WHERE g.id = ? AND g.user_id = ?''',
        (gr_id, user_id)
    ).fetchone()
    conn.close()
    return item

async def ver_item_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    gr_id = int(query.data.replace('rec_item_', ''))
    user_id = query.from_user.id

    item = _buscar_item_recorrente(user_id, gr_id)
    if not item:
        await atualizar_callback(query, context, "Gasto recorrente não encontrado.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    _, descricao, valor, dia, cat_id = item
    teclado = [
        [InlineKeyboardButton("Editar Data da Cobrança", callback_data=f"rec_editdia_{gr_id}")],
        [InlineKeyboardButton("Editar Valor da Cobrança", callback_data=f"rec_editvalor_{gr_id}")],
        [InlineKeyboardButton("Excluir Cobrança Recorrente", callback_data=f"rec_excluir_{gr_id}")],
        linha_voltar_com_menu(f"rec_ver_{cat_id}")
    ]
    await atualizar_callback(
        query, context,
        f"{escape(descricao)}\n\n"
        f"└ Valor Mensal: <b>{formatar_real(valor)}</b>\n"
        f"└ Dia da Cobrança: <b>{dia}</b>\n\n"
        "Qual Edição Deseja Realizar?",
        reply_markup=InlineKeyboardMarkup(teclado)
    )
    return ConversationHandler.END

async def iniciar_editar_dia_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    gr_id = int(query.data.replace('rec_editdia_', ''))
    context.user_data['recorrente_item_editando'] = gr_id

    # '← Voltar' retorna para o submenu do item (etapa imediatamente anterior).
    await atualizar_callback(
        query, context,
        "Envie o <b>novo dia</b> da cobrança (entre 1 e 31, ex: <code>10</code>):",
        reply_markup=InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_item_{gr_id}")])
    )
    return RECORRENTE_EDITAR_DIA

async def salvar_novo_dia_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    user_id = update.effective_user.id
    gr_id = context.user_data.get('recorrente_item_editando')

    try:
        await update.message.delete()
    except Exception:
        pass

    teclado_erro = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_item_{gr_id}")]) if gr_id else TECLADO_VOLTAR

    if not gr_id:
        await enviar_fluxo(context, user_id, "Sessão expirada. Volte ao menu.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    try:
        dia = int(texto)
    except ValueError:
        await enviar_fluxo(context, user_id, "Por favor, digite apenas o número do dia (ex: <code>10</code>):", reply_markup=teclado_erro)
        return RECORRENTE_EDITAR_DIA

    if not dia_valido(dia):
        await enviar_fluxo(context, user_id, "Dia inválido. Digite um valor entre <b>1</b> e <b>31</b>:", reply_markup=teclado_erro)
        return RECORRENTE_EDITAR_DIA

    item = _buscar_item_recorrente(user_id, gr_id)
    if not item:
        await enviar_fluxo(context, user_id, "Gasto recorrente não encontrado.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END
    cat_id = item[4]

    conn = obter_conexao_db()
    conn.execute('UPDATE gastos_recorrentes SET dia_cobranca = ? WHERE id = ? AND user_id = ?', (dia, gr_id, user_id))
    conn.commit()
    conn.close()

    context.user_data.pop('recorrente_item_editando', None)
    texto_lista, teclado_lista = _montar_tela_listagem_recorrentes(user_id, cat_id, aviso="Gasto Recorrente Atualizado com Sucesso!\n\n")
    await enviar_fluxo(context, user_id, texto_lista, reply_markup=teclado_lista)
    return ConversationHandler.END

async def iniciar_editar_valor_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    gr_id = int(query.data.replace('rec_editvalor_', ''))
    context.user_data['recorrente_item_editando'] = gr_id

    # '← Voltar' retorna para o submenu do item (etapa imediatamente anterior).
    await atualizar_callback(
        query, context,
        "Envie o <b>novo valor</b> da cobrança mensal (ex: <code>12,90</code>):",
        reply_markup=InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_item_{gr_id}")])
    )
    return RECORRENTE_EDITAR_VALOR

async def salvar_novo_valor_recorrente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text
    user_id = update.effective_user.id
    gr_id = context.user_data.get('recorrente_item_editando')

    try:
        await update.message.delete()
    except Exception:
        pass

    teclado_erro = InlineKeyboardMarkup([linha_voltar_com_menu(f"rec_item_{gr_id}")]) if gr_id else TECLADO_VOLTAR

    if not gr_id:
        await enviar_fluxo(context, user_id, "Sessão expirada. Volte ao menu.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END

    try:
        valor = parse_valor(texto)
        if valor <= 0:
            raise ValueError
    except ValueError:
        await enviar_fluxo(context, user_id, "Valor inválido. Digite no formato correto (ex: <code>12,90</code>):", reply_markup=teclado_erro)
        return RECORRENTE_EDITAR_VALOR

    item = _buscar_item_recorrente(user_id, gr_id)
    if not item:
        await enviar_fluxo(context, user_id, "Gasto recorrente não encontrado.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END
    cat_id = item[4]

    conn = obter_conexao_db()
    conn.execute('UPDATE gastos_recorrentes SET valor = ? WHERE id = ? AND user_id = ?', (valor, gr_id, user_id))
    conn.commit()
    conn.close()

    context.user_data.pop('recorrente_item_editando', None)
    texto_lista, teclado_lista = _montar_tela_listagem_recorrentes(user_id, cat_id, aviso="Gasto Recorrente Atualizado com Sucesso!\n\n")
    await enviar_fluxo(context, user_id, texto_lista, reply_markup=teclado_lista)
    return ConversationHandler.END

async def excluir_recorrente_botao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    gr_id = int(query.data.replace('rec_excluir_', ''))
    user_id = query.from_user.id

    item = _buscar_item_recorrente(user_id, gr_id)
    if not item:
        await atualizar_callback(query, context, "Gasto recorrente não encontrado.", reply_markup=obter_teclado_menu())
        return ConversationHandler.END
    cat_id = item[4]

    conn = obter_conexao_db()
    conn.execute('DELETE FROM gastos_recorrentes WHERE id = ? AND user_id = ?', (gr_id, user_id))
    conn.commit()
    conn.close()

    texto_lista, teclado_lista = _montar_tela_listagem_recorrentes(user_id, cat_id, aviso="Gasto Recorrente Excluído com Sucesso!\n\n")
    await atualizar_callback(query, context, texto_lista, reply_markup=teclado_lista)
    return ConversationHandler.END

# --- FLUXO DE TRANSFERÊNCIA INTELIGENTE COM IA ---
async def iniciar_transferencia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    # Teclado isolado para a tela de transferência: sem botões do Menu Principal.
    await atualizar_callback(
        query, context,
        "Transferência Inteligente entre Categorias\n\n"
        "Digite livremente como deseja transferir o saldo entre suas categorias.\n\n"
        "<i>Exemplo: \"Quero transferir 50,00 de Lazer para Alimentação\"</i>",
        reply_markup=TECLADO_VOLTAR
    )
    return TRANSFERIR_VALOR

async def salvar_transferencia_livre(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_usuario = update.message.text
    user_id = update.effective_user.id
    try:
        await update.message.delete()
    except Exception:
        pass

    if not texto_usuario:
        return ConversationHandler.END

    await enviar_fluxo(context, user_id, "Processando sua transferência...")

    try:
        prompt = f"""
        Analise a mensagem de um usuário sobre transferência de valores entre categorias financeiras e extraia os dados em formato JSON puro.
        As chaves devem ser obrigatoriamente: 
        - "categoria_origem": string com o nome da categoria de onde sai o dinheiro
        - "categoria_destino": string com o nome da categoria para onde vai o dinheiro
        - "valor": número float (ex: 50.00)

        Mensagem do usuário: "{texto_usuario}"
        
        Retorne APENAS o JSON puro.
        """

        response = await gerar_conteudo_com_retry(prompt)

        dados = limpar_json_ia(response.text)
        origem = dados['categoria_origem'].capitalize()
        destino = dados['categoria_destino'].capitalize()
        valor = float(dados['valor'])

        conn = obter_conexao_db()
        cursor = conn.cursor()

        # Sobra calculada na hora (mesma fonte do "Ver Resumo"), não o saldo guardado.
        inicio_ciclo = obter_inicio_ciclo_usuario(conn, user_id)
        status_origem = calcular_status_categoria(conn, user_id, origem, inicio_ciclo)
        status_destino = calcular_status_categoria(conn, user_id, destino, inicio_ciclo)

        if not status_origem or not status_destino:
            await enviar_fluxo(context, user_id, "Uma das categorias informadas não foi encontrada.", reply_markup=obter_teclado_menu())
            conn.close()
            return ConversationHandler.END

        if origem == destino or valor <= 0:
            await enviar_fluxo(context, user_id, "Transferência inválida: informe duas categorias diferentes e um valor positivo.", reply_markup=obter_teclado_menu())
            conn.close()
            return ConversationHandler.END

        saldo_origem = status_origem[2]

        # Valida se há saldo suficiente na categoria de origem
        if saldo_origem < valor:
            await enviar_fluxo(
                context, user_id,
                f"Saldo insuficiente!\n\n"
                f"Categoria de origem ({origem}) tem saldo de apenas {formatar_real(saldo_origem)}, "
                f"mas você tentou transferir {formatar_real(valor)}.",
                reply_markup=obter_teclado_menu()
            )
            conn.close()
            return ConversationHandler.END

        # Um único registro move o orçamento dos dois lados (soma zero): a
        # origem perde e o destino ganha exatamente 'valor' no ciclo. Tudo no
        # mesmo commit, junto do 'saldo_atual' legado, pra nunca ficar um lado
        # atualizado e o outro não.
        momento = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute(
            'INSERT INTO transferencias (user_id, categoria_origem, categoria_destino, valor, data_transferencia) VALUES (?, ?, ?, ?, ?)',
            (user_id, origem, destino, valor, momento)
        )
        cursor.execute('UPDATE categorias SET saldo_atual = saldo_atual - ? WHERE user_id = ? AND nome = ?', (valor, user_id, origem))
        cursor.execute('UPDATE categorias SET saldo_atual = saldo_atual + ? WHERE user_id = ? AND nome = ?', (valor, user_id, destino))
        conn.commit()

        orcamento_origem = calcular_status_categoria(conn, user_id, origem, inicio_ciclo)[0]
        orcamento_destino = calcular_status_categoria(conn, user_id, destino, inicio_ciclo)[0]
        conn.close()

        await mostrar_resumo_temporario(
            context, user_id,
            f"Transferência Realizada com Sucesso!\n\n"
            f"Valor: <b>{formatar_real(valor)}</b>\n"
            f"De: {origem} (Orçamento: {formatar_real(orcamento_origem)})\n"
            f"Para: {destino} (Orçamento: {formatar_real(orcamento_destino)})"
        )

    except Exception as e:
        await enviar_fluxo(context, user_id, mensagem_erro_ia(e), reply_markup=obter_teclado_menu())

    return ConversationHandler.END

# --- FLUXO DE ALERTA DIÁRIO ---
def _buscar_horario_alerta(user_id):
    conn = obter_conexao_db()
    usuario = conn.execute('SELECT horario_alerta FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    conn.close()
    horario = usuario[0] if usuario and usuario[0] else None

    # Sanitização defensiva: só devolve o valor se for de fato um horário
    # 'HH:MM' válido. Se o banco tiver algo corrompido (ex: um callback_data
    # de navegação salvo por um bug antigo), trata como "sem alerta
    # configurado" em vez de deixar a string crua vazar para a tela.
    if not horario or not re.match(r'^\d{2}:\d{2}$', horario):
        return None
    hora, minuto = map(int, horario.split(':'))
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    return horario

def _parse_horario_alerta(texto):
    """Interpreta o horário digitado pelo usuário: aceita o formato padrão
    'HH:MM' e também números simples de 1 ou 2 dígitos como hora cheia
    (ex: '17' ou '9' viram '17:00' e '09:00'). Retorna None se inválido."""
    texto = texto.strip()

    if re.match(r'^\d{1,2}$', texto):
        hora = int(texto)
        return f"{hora:02d}:00" if 0 <= hora <= 23 else None

    if re.match(r'^\d{2}:\d{2}$', texto):
        hora, minuto = map(int, texto.split(':'))
        return texto if (0 <= hora <= 23 and 0 <= minuto <= 59) else None

    return None

def _montar_tela_alerta_escolha(horario_atual):
    """Tela de escolha/edição de horário. Se já existe um alerta configurado,
    o '← Voltar' retorna para a tela de visualização (etapa anterior); caso
    contrário, é a primeira etapa do fluxo e volta direto ao Menu Principal."""
    if horario_atual:
        texto_estado = f"Horário Atual: <b>{horario_atual}</b> (Horário de Brasília)\n\n"
        # Nível 2 (visualização -> edição): ganha o atalho para o Menu Principal.
        linha_voltar = linha_voltar_com_menu('voltar_alerta_visualizacao')
    else:
        texto_estado = "Você ainda não tem um alerta diário configurado.\n\n"
        # Nível 1: '← Voltar' já leva direto ao Menu Principal.
        linha_voltar = [InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")]

    teclado = [
        [InlineKeyboardButton("08:00", callback_data='alerta_08:00'), InlineKeyboardButton("12:00", callback_data='alerta_12:00')],
        [InlineKeyboardButton("18:00", callback_data='alerta_18:00'), InlineKeyboardButton("20:00", callback_data='alerta_20:00')],
        [InlineKeyboardButton("Digitar Outro Horário", callback_data='alerta_custom')],
        linha_voltar
    ]
    texto = (
        "<b>Resumo Diário de Gastos</b>\n\n"
        f"{texto_estado}"
        "Selecione o Horário em Que Deseja Receber Sua Notificação Diária:"
    )
    return texto, InlineKeyboardMarkup(teclado)

def _montar_tela_alerta_visualizacao(horario_atual):
    """Tela estática exibida quando já existe um alerta configurado: apenas
    informa o horário, sem contador/tempo restante (nenhum cálculo, nenhum
    botão de atualizar — só re-renderiza se o horário for editado)."""
    texto = (
        "<b>Resumo Diário de Gastos</b>\n\n"
        f"Seu Alerta Diário Está Configurado Para: <b>{horario_atual}</b>."
    )
    teclado = [
        [InlineKeyboardButton("Editar Horário", callback_data='editar_horario_alerta')],
        [InlineKeyboardButton("Excluir Alerta", callback_data='excluir_alerta')],
        [InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")]
    ]
    return texto, InlineKeyboardMarkup(teclado)

# Teclado da etapa de horário customizado: '← Voltar' retorna para a tela de
# escolha (voltar_alerta_escolha), etapa imediatamente anterior.
TECLADO_VOLTAR_ALERTA_ESCOLHA = InlineKeyboardMarkup([linha_voltar_com_menu('voltar_alerta_escolha')])

async def iniciar_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    horario_atual = _buscar_horario_alerta(query.from_user.id)

    # Com alerta já configurado: mostra a tela de visualização (horário +
    # tempo restante). Sem alerta: vai direto pra escolha de horário.
    if horario_atual:
        texto, teclado = _montar_tela_alerta_visualizacao(horario_atual)
        await atualizar_callback(query, context, texto, reply_markup=teclado)
        return ALERTA_VISUALIZACAO

    texto, teclado = _montar_tela_alerta_escolha(horario_atual)
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ALERTA_ESCOLHA

async def editar_horario_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Revela os botões de escolha de horário (padrão/digitar) a partir da
    # tela de visualização.
    query = update.callback_query
    await responder_callback(query)
    horario_atual = _buscar_horario_alerta(query.from_user.id)
    texto, teclado = _montar_tela_alerta_escolha(horario_atual)
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ALERTA_ESCOLHA

async def excluir_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Apaga o horário salvo e cancela o job agendado, voltando para o
    # Estado 1 (sem alerta configurado).
    query = update.callback_query
    await responder_callback(query)
    user_id = query.from_user.id

    conn = obter_conexao_db()
    conn.execute('UPDATE usuarios SET horario_alerta = NULL WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

    for job in context.job_queue.get_jobs_by_name(str(user_id)):
        job.schedule_removal()

    texto, teclado = _montar_tela_alerta_escolha(None)
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ALERTA_ESCOLHA

async def voltar_alerta_visualizacao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da escolha de horário para a tela de visualização.
    query = update.callback_query
    await responder_callback(query)
    horario_atual = _buscar_horario_alerta(query.from_user.id)

    if not horario_atual:
        texto, teclado = _montar_tela_alerta_escolha(horario_atual)
        await atualizar_callback(query, context, texto, reply_markup=teclado)
        return ALERTA_ESCOLHA

    texto, teclado = _montar_tela_alerta_visualizacao(horario_atual)
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ALERTA_VISUALIZACAO

async def voltar_alerta_escolha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Retorna da etapa de horário customizado para a tela de escolha.
    query = update.callback_query
    await responder_callback(query)
    horario_atual = _buscar_horario_alerta(query.from_user.id)
    texto, teclado = _montar_tela_alerta_escolha(horario_atual)
    await atualizar_callback(query, context, texto, reply_markup=teclado)
    return ALERTA_ESCOLHA

def agendar_tarefa_alerta(job_queue, user_id, horario_str):
    """
    Agenda (ou reagenda) o envio do resumo diário no horário informado.
    
    Usa job_queue.run_daily, que por baixo dos panos é um cron trigger do
    APScheduler (a mesma lib que 'schedule'/'APScheduler' ofereceriam por
    fora) - já é a forma robusta e correta de fazer isso, sem sofrer de
    drift e sem precisar reimplementar manualmente o cálculo de "próxima
    execução". O único cuidado necessário é passar um horário com tzinfo
    (Brasília), o que já é feito abaixo.
    """
    if job_queue is None:
        print(f"❌ JobQueue indisponível (None) — não foi possível agendar o alerta do usuário {user_id}. "
              f"Verifique se 'python-telegram-bot[job-queue]' está instalado. O bot continua rodando normalmente.")
        return
    if not horario_str or not re.match(r'^\d{2}:\d{2}$', horario_str):
        print(f"⚠️ Horário inválido ignorado ao agendar alerta para o usuário {user_id}: {horario_str!r}")
        return
    try:
        hora, minuto = map(int, horario_str.split(':'))
        if not (0 <= hora <= 23 and 0 <= minuto <= 59):
            print(f"⚠️ Horário fora do intervalo válido ao agendar alerta para o usuário {user_id}: {horario_str!r}")
            return

        t = time(hour=hora, minute=minuto, tzinfo=FUSO_BRASILIA)

        # Remove qualquer job antigo desse usuário antes de recriar, pra
        # nunca ficar com dois alertas duplicados agendados.
        for job in job_queue.get_jobs_by_name(str(user_id)):
            job.schedule_removal()

        job_queue.run_daily(enviar_resumo_diario, t, chat_id=user_id, user_id=user_id, name=str(user_id))

        agora = datetime.now(FUSO_BRASILIA)
        dispara_hoje = (agora.hour, agora.minute) < (hora, minuto)
        quando = "HOJE" if dispara_hoje else "AMANHÃ (esse horário já passou hoje)"
        print(f"Alerta diário agendado para o usuário {user_id} às {horario_str} — próximo disparo: {quando} (horário de Brasília).")
    except Exception as e:
        print(f"Erro ao agendar alerta para o usuário {user_id}: {e}")

def _montar_mensagem_resumo_diario(nome, gastos_hoje, sobras_categoria):
    """
    Monta a mensagem do resumo diário de forma direta, sem IA: o total
    gasto, a categoria de maior gasto em destaque (só o nome, o valor já
    aparece na lista abaixo) e a lista de categorias com gasto e sobra do
    ciclo. Sem depender do Gemini, a formatação é sempre limpa e
    consistente, e a mensagem nunca atrasa por causa de instabilidade ou
    alta demanda do Google. 'sobras_categoria' é um dict {categoria: sobra}
    com a sobra do ciclo vigente de cada categoria que teve gasto hoje.
    """
    if not gastos_hoje:
        return (
            " <b>Resumo Financeiro de Hoje</b>\n\n"
            f"Nenhum gasto registrado hoje. Tudo tranquilo, {nome}! "
        )

    total_gasto = sum(valor for _, valor in gastos_hoje)
    categorias_ordenadas = sorted(gastos_hoje, key=lambda item: item[1], reverse=True)
    categoria_maior, _ = categorias_ordenadas[0]

    linhas_categorias = "\n\n".join(
        f"{cat}:\n"
        f"└ Gasto: <b>{formatar_real(valor)}</b>\n"
        f"└ Sobra: <b>{formatar_real(sobras_categoria.get(cat, 0.0))}</b>"
        for cat, valor in categorias_ordenadas
    )

    return (
        "<b>Resumo Financeiro de Hoje</b>\n\n"
        f"Total Gasto: <b>{formatar_real(total_gasto)}</b>\n\n"
        f"Maior Gasto: <b>{categoria_maior}</b>\n\n"
        f"Categorias:\n\n{linhas_categorias}"
    )

async def enviar_resumo_diario(context: ContextTypes.DEFAULT_TYPE):
    """
    Notificação diária agendada, independente do fluxo de conversa em
    andamento. Fica fixa na tela até o usuário clicar em '← Menu Principal'
    - assim ela continua acessível mesmo se o usuário não puder checar o
    celular no momento em que o alerta chega.
    """
    user_id = context.job.chat_id
    agora_brasilia = datetime.now(FUSO_BRASILIA)
    print(f"📤 Enviando resumo diário para usuário {user_id} às {agora_brasilia.strftime('%H:%M:%S')} (Brasília)")

    conn = obter_conexao_db()
    user_data = conn.execute('SELECT nome FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()

    if not user_data:
        conn.close()
        print(f"⚠️ Usuário {user_id} não encontrado no banco de dados")
        return

    nome = user_data[0]

    data_brasilia_str = agora_brasilia.strftime('%Y-%m-%d')
    gastos_hoje = conn.execute(
        '''SELECT categoria_nome, SUM(valor) FROM transacoes
           WHERE user_id = ? AND DATE(data_transacao) = ? AND valor > 0
           GROUP BY categoria_nome''',
        (user_id, data_brasilia_str)
    ).fetchall()
    sobras_categoria = {
        cat: calcular_sobra_categoria(conn, user_id, cat) for cat, _ in gastos_hoje
    }

    conn.close()

    mensagem = _montar_mensagem_resumo_diario(nome, gastos_hoje, sobras_categoria)

    # Limpa qualquer mensagem antiga de fluxo (igual mostrar_resumo_temporario
    # fazia), pra o resumo aparecer como a única mensagem do chat.
    await limpar_historico_mensagens(context, user_id)

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Menu Principal", callback_data='resumo_diario_menu', style="danger")
    ]])

    resumo_msg = await context.bot.send_message(
        chat_id=user_id, text=mensagem, reply_markup=teclado, parse_mode='HTML'
    )
    context.user_data['ultima_msg_bot'] = resumo_msg.message_id
    context.user_data['msgs_fluxo'] = [resumo_msg.message_id]

async def resumo_diario_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback do botão '← Menu Principal' (vermelho) do resumo diário: apaga
    a notificação e mostra o Menu Principal em seguida. Não há garantia de
    que exista um Menu Principal fixo por trás dessa notificação (ela não
    sabe o que estava no chat antes de ser enviada), então precisa exibi-lo
    explicitamente para não deixar o chat vazio.
    """
    query = update.callback_query
    await responder_callback(query)
    chat_id = query.message.chat_id

    try:
        await query.message.delete()
    except Exception:
        pass

    nova_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=await montar_texto_menu_principal(chat_id),
        reply_markup=obter_teclado_menu(),
        parse_mode='HTML'
    )
    context.user_data['ultima_msg_bot'] = nova_msg.message_id
    context.user_data['msgs_fluxo'] = [nova_msg.message_id]

# --- RESUMO MENSAL (ENVIADO NO FECHAMENTO DA FATURA) ---
def _calcular_dados_resumo_mensal(conn, user_id, dia_fechamento, agora):
    """Ciclo que acabou de fechar (o de ontem, já que o resumo sai no dia de
    fechamento), calculado pela mesma função central de Orçamento/Gasto/Sobra.
    Retorna (nome_do_mes, top3, total_gasto, sobra_total), com top3 como lista
    [(categoria, gasto)] só das categorias com gasto, do maior para o menor."""
    ontem = agora - timedelta(days=1)
    inicio = obter_inicio_ciclo_atual(dia_fechamento, ontem)
    fim = obter_inicio_ciclo_atual(dia_fechamento, agora)
    nome_mes = NOMES_MESES[int(calcular_ciclo_fatura(ontem, dia_fechamento).split('-')[1]) - 1]

    gastos, sobra_total = [], 0.0
    for (cat,) in conn.execute('SELECT nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall():
        _, gasto, sobra = calcular_status_categoria(conn, user_id, cat, inicio, fim)
        sobra_total += sobra
        if gasto > 0:
            gastos.append((cat, gasto))

    gastos.sort(key=lambda item: item[1], reverse=True)
    return nome_mes, gastos[:3], sum(g for _, g in gastos), sobra_total

def _montar_mensagem_resumo_mensal(nome, nome_mes, top3, total_gasto, sobra_total):
    """Texto do Resumo Mensal, direto e sem IA, seguindo o template aprovado.
    Com menos de 3 categorias com gasto mostra só as existentes; sem nenhuma,
    omite os blocos numerados."""
    texto = f"Olá, {escape(nome)}! Tudo bem? Segue abaixo o seu Resumo Mensal de Gastos no Mês {nome_mes}!\n\n\n"
    for posicao, (cat, valor) in enumerate(top3, start=1):
        texto += (
            f"{posicao}º Maior Gasto no Mês de {nome_mes}: {escape(cat)}\n"
            f"└ Total Gasto: {formatar_real(valor)}\n\n"
            f"{DIVISOR}\n\n"
        )
    texto += (
        f"Total Gasto do Mês Inteiro: {formatar_real(total_gasto)}\n"
        f"Sobra do Mês: {formatar_real(sobra_total)}\n\n"
        f"{DIVISOR}\n\n"
        "Hoje, suas categorias zeram os valores de gastos e voltam à estaca zero, "
        "seguindo a lógica de fechamento da sua fatura."
    )
    return texto

async def enviar_resumo_mensal(bot, user_data, user_id, nome, dia_fechamento, agora):
    """Envia o Resumo Mensal pelo mesmo mecanismo do Resumo Diário: limpa o
    histórico rastreado, manda uma única mensagem com o botão '← Menu
    Principal' (callback 'resumo_diario_menu') e registra o id dela."""
    conn = obter_conexao_db()
    try:
        dados = _calcular_dados_resumo_mensal(conn, user_id, dia_fechamento, agora)
    finally:
        conn.close()
    mensagem = _montar_mensagem_resumo_mensal(nome, *dados)

    # limpar_historico_mensagens só usa .bot e .user_data do contexto.
    await limpar_historico_mensagens(SimpleNamespace(bot=bot, user_data=user_data), user_id)

    teclado = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Menu Principal", callback_data='resumo_diario_menu', style="danger")
    ]])
    resumo_msg = await bot.send_message(chat_id=user_id, text=mensagem, reply_markup=teclado, parse_mode='HTML')
    user_data['ultima_msg_bot'] = resumo_msg.message_id
    user_data['msgs_fluxo'] = [resumo_msg.message_id]

# --- RESET DE SALDOS NO DIA DE VENCIMENTO ---
async def _executar_verificacao_resets(bot, user_data_global):
    """
    Lógica central do reset: para cada usuário, verifica se hoje (no fuso de
    Brasília) é o dia de vencimento configurado e, se for e ainda não tiver
    sido resetado hoje, restaura o saldo de todas as categorias para o
    "Valor Fixo" (orcamento_mensal) — apagando o efeito de transferências e
    gastos acumulados no ciclo que terminou.

    Trata o caso de meses mais curtos que o dia configurado (ex: vencimento
    dia 31 num mês com 30 dias, ou fevereiro) disparando no último dia do
    mês nesse caso.

    Idempotente: usa 'ultimo_reset' pra nunca resetar duas vezes no mesmo
    dia, mesmo se essa função for chamada tanto pelo job diário quanto pela
    checagem de segurança feita na subida do bot.
    """
    agora = datetime.now(FUSO_BRASILIA)
    hoje_str = agora.strftime('%Y-%m-%d')
    dia_hoje = agora.day
    ultimo_dia_mes = calendar.monthrange(agora.year, agora.month)[1]

    conn = obter_conexao_db()
    usuarios = conn.execute(
        'SELECT user_id, nome, dia_fechamento, ultimo_reset FROM usuarios WHERE dia_fechamento IS NOT NULL'
    ).fetchall()

    resets_feitos = 0
    for user_id, nome, dia_fechamento, ultimo_reset in usuarios:
        # Se o vencimento é dia 31 mas o mês só tem 30 dias (ou 28/29 em
        # fevereiro), o reset acontece no último dia do mês.
        dia_alvo = min(dia_fechamento, ultimo_dia_mes)

        if dia_hoje != dia_alvo:
            continue
        if ultimo_reset == hoje_str:
            continue  # já resetado hoje, evita duplicar

        cursor = conn.cursor()
        cursor.execute('UPDATE categorias SET saldo_atual = orcamento_mensal WHERE user_id = ?', (user_id,))
        cursor.execute('UPDATE usuarios SET ultimo_reset = ? WHERE user_id = ?', (hoje_str, user_id))
        conn.commit()
        resets_feitos += 1

        try:
            await enviar_resumo_mensal(bot, user_data_global[user_id], user_id, nome, dia_fechamento, agora)
        except Exception as e:
            print(f"⚠️ Não consegui enviar o Resumo Mensal ao usuário {user_id}: {e}")

    conn.close()
    if resets_feitos:
        print(f"🔄 {resets_feitos} usuário(s) tiveram os saldos resetados para os valores fixos em {hoje_str}.")

async def verificar_resets_vencimento(context: ContextTypes.DEFAULT_TYPE):
    """Job diário (agendado às 00:05 de Brasília) que aciona a verificação
    de reset de saldos para todos os usuários."""
    await _executar_verificacao_resets(context.bot, context.application.user_data)

# --- AUTOMAÇÃO DE GASTOS RECORRENTES ---
async def verificar_gastos_recorrentes(context: ContextTypes.DEFAULT_TYPE):
    """
    Job diário (agendado às 09:00 de Brasília): identifica, entre todos os
    usuários, os gastos recorrentes com vencimento hoje e ainda não cobrados
    neste ciclo, e agenda o lançamento de cada um via job_queue.run_once,
    espaçados em INTERVALO_ESPACAMENTO_RECORRENTE (30min) entre si, na ordem
    de criação (mais antigo dispara primeiro, às 09:00 em ponto).

    Vencimento no dia 31 (ou 30/29) num mês mais curto dispara no último dia
    do mês, mesma regra já usada em _executar_verificacao_resets.

    Cada item já sai agendado sabendo sua posição e o total do dia (pro
    indicador de página "X-Y"), e com user_id/chat_id associados ao job para
    _lancar_gasto_recorrente_job poder usar context.user_data.
    """
    agora = datetime.now(FUSO_BRASILIA)
    dia_hoje = agora.day
    ciclo_atual = agora.strftime('%Y-%m')
    ultimo_dia_mes = calendar.monthrange(agora.year, agora.month)[1]

    conn = obter_conexao_db()
    candidatos = conn.execute(
        '''SELECT id, dia_cobranca, user_id FROM gastos_recorrentes
           WHERE ultimo_ciclo_cobrado IS NULL OR ultimo_ciclo_cobrado != ?
           ORDER BY criado_em''',
        (ciclo_atual,)
    ).fetchall()
    conn.close()

    itens = [
        (gr_id, user_id) for gr_id, dia_cobranca, user_id in candidatos
        if min(dia_cobranca, ultimo_dia_mes) == dia_hoje
    ]
    total_do_dia = len(itens)

    for indice, (gr_id, user_id) in enumerate(itens):
        atraso = indice * INTERVALO_ESPACAMENTO_RECORRENTE
        context.job_queue.run_once(
            _lancar_gasto_recorrente_job,
            atraso,
            chat_id=user_id,
            user_id=user_id,
            data={'gr_id': gr_id, 'ciclo': ciclo_atual, 'posicao': indice + 1, 'total_dia': total_do_dia},
            name=f"recorrente_{gr_id}_{ciclo_atual}"
        )

    if itens:
        print(f"🔁 {len(itens)} gasto(s) recorrente(s) agendado(s) para hoje, espaçados em "
              f"{INTERVALO_ESPACAMENTO_RECORRENTE // 60}min entre si.")

def _montar_texto_recorrente_diario(item):
    """Texto de uma página da mensagem diária de cobranças recorrentes:
    indicador "posição-total" + corpo já pronto daquela cobrança."""
    return f"{item['posicao']}-{item['total_dia']}\n\nCobrança Recorrente Lançada\n\n{item['corpo']}"

def _teclado_recorrente_diario():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Próxima -->", callback_data='rec_diario_proxima'),
        InlineKeyboardButton("← Menu Principal", callback_data='rec_diario_menu', style="danger")
    ]])

async def _lancar_gasto_recorrente_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Executa o lançamento de um único gasto recorrente: debita a categoria,
    grava a transação (mesma lógica de um "Registrar Gasto" comum) e
    notifica o usuário. Todas as cobranças do mesmo dia (Brasília) editam a
    MESMA mensagem (paginável com "Próxima -->"), em vez de uma mensagem
    nova por cobrança; ela só é apagada por clique explícito em "← Menu
    Principal" ou ao passar da última página — nunca sozinha.

    Reconfirma 'ultimo_ciclo_cobrado' na hora de rodar (não só no momento em
    que verificar_gastos_recorrentes agendou o job), pra nunca cobrar duas
    vezes no mesmo ciclo. 'posicao'/'total_dia' (indicador de página) já
    vêm prontos de verificar_gastos_recorrentes, que conhece o total do dia
    desde o agendamento.
    """
    gr_id = context.job.data['gr_id']
    ciclo = context.job.data['ciclo']
    posicao = context.job.data['posicao']
    total_dia = context.job.data['total_dia']
    user_id = context.job.chat_id

    conn = obter_conexao_db()
    item = conn.execute(
        'SELECT categoria_nome, descricao, valor, ultimo_ciclo_cobrado FROM gastos_recorrentes WHERE id = ?',
        (gr_id,)
    ).fetchone()

    if not item:
        conn.close()
        return

    categoria_nome, descricao, valor, ultimo_ciclo_cobrado = item
    if ultimo_ciclo_cobrado == ciclo:
        conn.close()
        return

    momento = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d %H:%M:%S')
    cursor = conn.cursor()
    # Mesmo lançamento de um gasto comum: o impacto em Orçamento/Gasto/Sobra
    # sai da fonte única (calcular_status_categoria), sem cálculo próprio aqui.
    if not debitar_categoria(cursor, user_id, categoria_nome, valor, momento):
        conn.close()
        print(f"⚠️ Gasto recorrente {gr_id} não lançado: categoria '{categoria_nome}' não existe mais para o usuário {user_id}.")
        return

    cursor.execute('UPDATE gastos_recorrentes SET ultimo_ciclo_cobrado = ? WHERE id = ?', (ciclo, gr_id))
    conn.commit()
    novo_saldo = calcular_sobra_categoria(conn, user_id, categoria_nome)
    conn.close()

    corpo = (
        f"Descrição: <b>{escape(descricao)}</b>\n"
        f"Categoria: <b>{escape(categoria_nome)}</b>\n"
        f"Valor: <b>{formatar_real(valor)}</b>\n"
        f"Resta na Categoria: <b>{formatar_real(novo_saldo)}</b>"
    )

    # Estado guardado numa chave própria (fora de 'ultima_msg_bot'/'msgs_fluxo'),
    # pra nenhuma limpeza genérica do fluxo de conversa normal apagar essa
    # mensagem sem o usuário clicar em "← Menu Principal". Reseta sozinho
    # quando muda o dia (Brasília).
    hoje_str = datetime.now(FUSO_BRASILIA).strftime('%Y-%m-%d')
    estado = context.user_data.get('recorrente_diario')
    if not estado or estado['data'] != hoje_str:
        estado = {'data': hoje_str, 'message_id': None, 'itens': []}

    novo_item = {'posicao': posicao, 'total_dia': total_dia, 'corpo': corpo}
    estado['itens'].append(novo_item)
    estado['pagina_atual'] = len(estado['itens']) - 1
    texto = _montar_texto_recorrente_diario(novo_item)
    teclado = _teclado_recorrente_diario()

    editado = False
    if estado['message_id']:
        try:
            await context.bot.edit_message_text(
                chat_id=user_id, message_id=estado['message_id'],
                text=texto, reply_markup=teclado, parse_mode='HTML'
            )
            editado = True
        except Exception:
            pass  # mensagem de hoje não existe mais (ex: apagada manualmente) — manda uma nova abaixo

    if not editado:
        try:
            nova_msg = await context.bot.send_message(
                chat_id=user_id, text=texto, reply_markup=teclado, parse_mode='HTML'
            )
            estado['message_id'] = nova_msg.message_id
        except Exception as e:
            print(f"⚠️ Não consegui notificar o usuário {user_id} sobre o gasto recorrente {gr_id}: {e}")

    context.user_data['recorrente_diario'] = estado

async def recorrente_diario_proxima(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botão 'Próxima -->' da mensagem diária de cobranças recorrentes:
    avança pelas cobranças JÁ LANÇADAS hoje (não pelas ainda não disparadas).
    Ao passar da última, avisa por alguns segundos e volta ao Menu Principal
    sozinho, sem precisar de clique adicional."""
    query = update.callback_query
    await responder_callback(query)
    chat_id = query.message.chat_id

    estado = context.user_data.get('recorrente_diario')
    if not estado or not estado.get('itens'):
        await _fechar_recorrente_diario(context, chat_id, query.message.message_id)
        return

    proxima_pagina = estado['pagina_atual'] + 1
    if proxima_pagina < len(estado['itens']):
        estado['pagina_atual'] = proxima_pagina
        item = estado['itens'][proxima_pagina]
        try:
            await query.message.edit_text(
                _montar_texto_recorrente_diario(item),
                reply_markup=_teclado_recorrente_diario(),
                parse_mode='HTML'
            )
        except BadRequest as e:
            if 'Message is not modified' not in str(e):
                raise
        context.user_data['recorrente_diario'] = estado
        return

    # Já estava na última cobrança já lançada: avisa e volta ao menu sozinho.
    try:
        await query.message.edit_text(
            "Sem Mais Cobranças Recorrentes Lançadas Hoje, Voltando ao Menu Principal."
        )
    except Exception:
        pass
    await asyncio.sleep(3.5)
    await _fechar_recorrente_diario(context, chat_id, query.message.message_id)

async def recorrente_diario_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Botão '← Menu Principal' (vermelho) da mensagem diária de cobranças
    recorrentes: apaga a mensagem a qualquer momento, por escolha explícita."""
    query = update.callback_query
    await responder_callback(query)
    await _fechar_recorrente_diario(context, query.message.chat_id, query.message.message_id)

async def _fechar_recorrente_diario(context, chat_id, message_id):
    """Apaga a mensagem diária de cobranças recorrentes e mostra o Menu
    Principal em seguida — ela não sabe o que estava no chat antes de ser
    enviada, então não há garantia de um Menu Principal fixo por trás.
    Mantém o histórico ('itens') de cobranças já lançadas hoje — só limpa o
    'message_id': se uma nova cobrança disparar depois no mesmo dia, ela
    reabre com uma mensagem nova."""
    estado = context.user_data.get('recorrente_diario')
    if estado:
        estado['message_id'] = None
        context.user_data['recorrente_diario'] = estado

    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

    nova_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=await montar_texto_menu_principal(chat_id),
        reply_markup=obter_teclado_menu(),
        parse_mode='HTML'
    )
    context.user_data['ultima_msg_bot'] = nova_msg.message_id
    context.user_data['msgs_fluxo'] = [nova_msg.message_id]

async def selecionar_horario_alerta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    # Trava de validação: só assume o clique se for de fato um horário fixo
    # ou o botão "Digitar Outro Horário". Qualquer outro callback_data (ex:
    # um botão de navegação que escape do filtro do ConversationHandler) é
    # ignorado aqui, sem responder o clique nem tocar no banco, deixando o
    # handler correto (voltar/menu) assumir o controle.
    if not re.match(r'^alerta_(\d{2}:\d{2}|custom)$', data):
        return

    await responder_callback(query)

    if data == 'alerta_custom':
        await atualizar_callback(
            query, context,
            "Envie o horário desejado no formato <b>HH:MM</b>:",
            reply_markup=TECLADO_VOLTAR_ALERTA_ESCOLHA
        )
        return ALERTA_CUSTOM

    horario = data.replace('alerta_', '')

    # Segunda camada de defesa: o regex acima já garante o formato HH:MM,
    # mas não garante o intervalo válido (ex: '99:99' passaria no formato).
    # Reaproveita a mesma validação de _parse_horario_alerta antes de
    # converter para int, para nunca gravar um horário inválido no banco
    # nem deixar um ValueError escapar para o chat do usuário.
    try:
        hora, minuto = map(int, horario.split(':'))
    except ValueError:
        return
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return

    user_id = query.from_user.id

    try:
        conn = obter_conexao_db()
        conn.execute('UPDATE usuarios SET horario_alerta = ? WHERE user_id = ?', (horario, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Erro ao gravar horário de alerta no Turso para o usuário {user_id}: {e}")
        await atualizar_callback(
            query, context,
            "⚠️ Não foi possível salvar o horário agora (falha de conexão com o banco). Tente novamente.",
            reply_markup=TECLADO_VOLTAR
        )
        return ConversationHandler.END

    agendar_tarefa_alerta(context.job_queue, user_id, horario)

    await atualizar_callback(
        query, context,
        f"Alerta Diário Configurado Para as <b>{horario}</b>.",
        reply_markup=TECLADO_VOLTAR
    )
    # Encerra a conversa aqui: a tela de visualização (com "Editar"/"Excluir")
    # só é reaberta pelo entry_point 'menu_alerta', então deixar o fluxo
    # pendente em ALERTA_VISUALIZACAO travava o botão "Alerta" do menu
    # principal (o clique não caía em nenhum handler do estado nem dos
    # fallbacks, então nunca era respondido -> ícone de carregando infinito).
    return ConversationHandler.END

async def salvar_horario_custom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = update.message.text.strip()
    user_id = update.effective_user.id
    try:
        await update.message.delete()
    except Exception:
        pass

    # Aceita 'HH:MM' e também números simples de hora cheia (ex: '21' -> '21:00').
    horario = _parse_horario_alerta(texto)
    if not horario:
        await enviar_fluxo(
            context, user_id,
            "⚠️ Horário inválido. Envie no formato <b>HH:MM</b> ou só a hora cheia (Ex: <code>21:30</code> ou <code>21</code>):",
            reply_markup=TECLADO_VOLTAR_ALERTA_ESCOLHA
        )
        return ALERTA_CUSTOM

    try:
        conn = obter_conexao_db()
        conn.execute('UPDATE usuarios SET horario_alerta = ? WHERE user_id = ?', (horario, user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"❌ Erro ao gravar horário de alerta no Turso para o usuário {user_id}: {e}")
        await enviar_fluxo(
            context, user_id,
            "⚠️ Não foi possível salvar o horário agora (falha de conexão com o banco). Tente novamente.",
            reply_markup=TECLADO_VOLTAR
        )
        return ConversationHandler.END

    agendar_tarefa_alerta(context.job_queue, user_id, horario)

    await enviar_fluxo(
        context, user_id,
        f"Alerta Diário Configurado Para as <b>{horario}</b>.",
        reply_markup=TECLADO_VOLTAR
    )
    # Mesmo motivo do callback de horário fixo: encerrar aqui evita deixar a
    # conversa presa em ALERTA_VISUALIZACAO e travando o entry_point 'menu_alerta'.
    return ConversationHandler.END

async def mensagem_solta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    await enviar_fluxo(
        context, update.effective_user.id,
        "Por favor, selecione uma das opções abaixo para executar a ação desejada.",
        reply_markup=obter_teclado_menu()
    )

async def botao_clicado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)

    if query.data == 'menu_resumo':
        user_id = query.from_user.id
        conn = obter_conexao_db()
        user_data = conn.execute('SELECT nome, renda_mensal, dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()

        if not user_data:
            await atualizar_callback(query, context, "Conta não configurada. Use /start", reply_markup=obter_teclado_menu())
            conn.close()
            return

        nome, renda, dia_fechamento = user_data
        categorias = conn.execute('SELECT nome FROM categorias WHERE user_id = ?', (user_id,)).fetchall()
        inicio_ciclo_str = obter_inicio_ciclo_usuario(conn, user_id)

        # Orçamento (já com transferências), Gasto e Sobra de cada categoria
        # vêm da fonte única; o cabeçalho e os blocos (mesmo layout do
        # histórico mensal) são montados por montar_texto_painel.
        gastos_por_categoria = []
        for (n,) in categorias:
            orcamento, gasto, sobra = calcular_status_categoria(conn, user_id, n, inicio_ciclo_str)
            gastos_por_categoria.append((n, orcamento, sobra, gasto))

        conn.close()

        texto = montar_texto_painel(
            f"Painel Financeiro de {nome}", renda, dia_fechamento, gastos_por_categoria
        )

        teclado = InlineKeyboardMarkup([
            [InlineKeyboardButton("Categorias", callback_data='menu_editar_cat'), InlineKeyboardButton("Transferir", callback_data='menu_transferir')],
            [InlineKeyboardButton("Ver Meses Anteriores e Futuros", callback_data='history_years')],
            [InlineKeyboardButton("← Voltar", callback_data='voltar_menu', style="danger")]
        ])
        await atualizar_callback(query, context, texto, reply_markup=teclado)

def _carregar_meses_historico(user_id):
    """(nome, dia_fechamento, meses) do usuário para as telas de histórico,
    ou None se a conta não estiver configurada."""
    conn = obter_conexao_db()
    user_data = conn.execute('SELECT nome, dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    if not user_data or not user_data[1]:
        conn.close()
        return None
    meses = buscar_meses_com_movimento(conn, user_id, user_data[1])
    conn.close()
    return user_data[0], user_data[1], meses

async def ver_anos_historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'Ver Meses Anteriores e Futuros': um botão por ano que tenha registros
    (parcelas até 2027 fazem o botão de 2027 existir)."""
    query = update.callback_query
    await responder_callback(query)

    dados = _carregar_meses_historico(query.from_user.id)
    if not dados:
        await atualizar_callback(query, context, "Conta não configurada. Use /start", reply_markup=obter_teclado_menu())
        return

    anos = sorted({m[:4] for m in dados[2]})
    botoes = [InlineKeyboardButton(ano, callback_data=f"select_year_{ano}") for ano in anos]
    # Retorna para o resumo do mês atual (etapa imediatamente anterior).
    teclado = montar_grade(botoes) + [linha_voltar_com_menu('menu_resumo')]
    await atualizar_callback(query, context, "De Qual Ano Deseja Ver os Seus Gastos?", reply_markup=InlineKeyboardMarkup(teclado))

async def ver_meses_do_ano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await responder_callback(query)
    ano = query.data.replace('select_year_', '')

    dados = _carregar_meses_historico(query.from_user.id)
    if not dados:
        await atualizar_callback(query, context, "Conta não configurada. Use /start", reply_markup=obter_teclado_menu())
        return

    _, dia_fechamento, meses = dados
    ciclo_atual = obter_ciclo_atual(dia_fechamento)

    botoes = [
        InlineKeyboardButton("Mês Atual", callback_data=f"select_month_history_{m}", style="success")
        if m == ciclo_atual else
        InlineKeyboardButton(rotulo_mes(m), callback_data=f"select_month_history_{m}")
        for m in meses if m.startswith(ano)
    ]
    # Retorna para a escolha do ano (etapa imediatamente anterior).
    teclado = montar_grade(botoes) + [linha_voltar_com_menu('history_years')]
    await atualizar_callback(query, context, f"Gastos de {ano}\n\nSelecione o mês:", reply_markup=InlineKeyboardMarkup(teclado))

async def ver_mes_historico(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fatura de um mês: gastos avulsos (sem as parcelas 1/N, já contadas na
    lista de parcelas) + parcelas que caem naquele mês, por categoria. O
    cabeçalho vira 'Mês Fechado' quando a fatura já passou."""
    query = update.callback_query
    await responder_callback(query)
    ano_mes = query.data.replace('select_month_history_', '')
    ano, mes = ano_mes.split('-')
    user_id = query.from_user.id

    conn = obter_conexao_db()
    user_data = conn.execute('SELECT renda_mensal, dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    if not user_data or not user_data[1]:
        conn.close()
        await atualizar_callback(query, context, "Conta não configurada. Use /start", reply_markup=obter_teclado_menu())
        return
    renda, dia_fechamento = user_data

    categorias_db = conn.execute(
        'SELECT nome, orcamento_mensal, saldo_atual, data_criacao FROM categorias WHERE user_id = ? ORDER BY id',
        (user_id,)
    ).fetchall()
    avulsos = conn.execute(
        f'''SELECT categoria_nome, SUM(valor) FROM transacoes
            WHERE user_id = ? AND valor > 0 AND {_SQL_FATURA_TRANSACAO} = ?
              AND NOT EXISTS (
                  SELECT 1 FROM parcelamentos p
                  WHERE p.user_id = transacoes.user_id
                    AND p.categoria_nome = transacoes.categoria_nome
                    AND p.data_criacao = transacoes.data_transacao)
            GROUP BY categoria_nome''',
        (user_id, dia_fechamento, ano_mes)
    ).fetchall()
    parcelas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS +
        '''SELECT p.categoria_nome, SUM(p.valor_parcela)
           FROM expandido e JOIN parcelamentos p ON p.id = e.pid
           WHERE e.ano_mes = ? GROUP BY p.categoria_nome''',
        (user_id, None, None, ano_mes)
    ).fetchall()
    conn.close()

    gasto_avulso = dict(avulsos)
    gasto_parcelas = dict(parcelas)

    ciclo_atual = obter_ciclo_atual(dia_fechamento)
    mes_atual = ano_mes == ciclo_atual

    def montar_entrada(cat, limite, saldo_atual):
        # Gasto do mês = avulsos + parcelas que caem nessa fatura, consolidados
        # num único valor. No mês atual, a sobra é o saldo real da categoria
        # (igual ao 'Ver Resumo'); nos demais, é o limite menos o gasto daquele mês.
        gasto = gasto_avulso.get(cat, 0.0) + gasto_parcelas.get(cat, 0.0)
        if limite is None:
            return (cat, None, None, gasto)
        return (cat, limite, saldo_atual if mes_atual else limite - gasto, gasto)

    # Todas as categorias existentes na época (regra temporal por data de
    # criação), mais as que têm movimento no mês mesmo sem existir mais.
    categorias = []
    for cat, limite, saldo_atual, data_criacao in categorias_db:
        tem_movimento = cat in gasto_avulso or cat in gasto_parcelas
        if tem_movimento or categoria_existia_no_mes(data_criacao, ano_mes, dia_fechamento):
            categorias.append(montar_entrada(cat, limite, saldo_atual))
    existentes = {c[0] for c in categorias_db}
    for cat in sorted((set(gasto_avulso) | set(gasto_parcelas)) - existentes):
        categorias.append(montar_entrada(cat, None, None))

    nome_mes = NOMES_MESES[int(mes) - 1]
    if mes_atual:
        titulo = "Mês Atual"
    else:
        titulo = nome_mes if ano == ciclo_atual[:4] else f"{nome_mes} de {ano}"

    texto = montar_texto_painel(
        titulo, renda, dia_fechamento, categorias,
        rotulo_gastos="Gastos Atuais" if mes_atual else "Gastos do Mês"
    )

    # Limite de 4096 caracteres por mensagem do Telegram.
    if len(texto) > 4000:
        texto = texto[:3990] + "\n…"

    # Retorna para a grade de meses do ano (etapa imediatamente anterior).
    teclado = InlineKeyboardMarkup([linha_voltar_com_menu(f"select_year_{ano}")])
    await atualizar_callback(query, context, texto, reply_markup=teclado)

async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    await enviar_fluxo(context, update.effective_chat.id, "Operação cancelada.", reply_markup=obter_teclado_menu())
    return ConversationHandler.END

async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.delete()
    except Exception:
        pass
    texto_menu = await montar_texto_menu_principal(update.effective_chat.id)
    await enviar_fluxo(context, update.effective_chat.id, texto_menu, reply_markup=obter_teclado_menu())

async def tratar_erro(update, context: ContextTypes.DEFAULT_TYPE):
    """Handler global: evita que uma exceção não tratada em qualquer
    callback vire só um traceback perdido no console. 'Message is not
    modified' é filtrado aqui também, como segunda camada de proteção para
    qualquer edit_text futuro que não passe por atualizar_callback."""
    erro = context.error
    if isinstance(erro, BadRequest) and 'Message is not modified' in str(erro):
        return
    print(f"❌ Erro não tratado: {erro}")

# --- EXECUÇÃO DO BOT ---
if __name__ == '__main__':
    print("Iniciando Bot Corrigido...")
    app = (
        ApplicationBuilder()
        .token(TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .build()
    )

    async def post_init(application):
        iniciar_banco()
        conn = obter_conexao_db()
        usuarios_alertas = conn.execute('SELECT user_id, horario_alerta FROM usuarios WHERE horario_alerta IS NOT NULL').fetchall()
        conn.close()
        for uid, hor in usuarios_alertas:
            agendar_tarefa_alerta(application.job_queue, uid, hor)
        print(f"🔄 {len(usuarios_alertas)} alertas diários recarregados com sucesso!")

        # Agenda a checagem diária de reset de saldos no dia de vencimento
        # (00:05 de Brasília, pra rodar logo após a virada do dia).
        if application.job_queue is None:
            print("❌ JobQueue indisponível (None) — não foi possível agendar a checagem diária de resets. "
                  "Verifique se 'python-telegram-bot[job-queue]' está instalado. O bot continua rodando normalmente.")
        else:
            application.job_queue.run_daily(
                verificar_resets_vencimento,
                time(hour=0, minute=5, tzinfo=FUSO_BRASILIA),
                name="verificar_resets_vencimento"
            )
            application.job_queue.run_daily(
                verificar_gastos_recorrentes,
                HORARIO_COBRANCA_RECORRENTE,
                name="verificar_gastos_recorrentes"
            )

        # Roda uma checagem imediata na subida do bot: se o processo tiver
        # ficado fora do ar bem no horário do reset, isso evita perder o
        # reset daquele dia (a idempotência por 'ultimo_reset' garante que
        # não vai duplicar se o job das 00:05 também disparar depois).
        await _executar_verificacao_resets(application.bot, application.user_data)

    app.post_init = post_init

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            NOME_USUARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome)],
            RENDA_MENSAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_renda)],
            NOMES_CATEGORIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nomes_categorias)],
            VALORES_CATEGORIAS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_valores_categorias)],
            DIA_FECHAMENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_dia_fechamento)],
        },
        fallbacks=[CommandHandler('cancelar', cancelar)],
    )

    conv_gasto = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(iniciar_gasto, pattern='^menu_gastar$'),
            # A tela de confirmação chega com a conversa anterior já encerrada
            # (ConversationHandler.END), então o clique em "Editar Gasto" só é
            # capturado se também estiver registrado como entry_point.
            CallbackQueryHandler(iniciar_editar_ultimo_gasto, pattern='^editar_ultimo_gasto$'),
        ],
        states={
            GASTO_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_gasto_livre)],
            # Retorna do estado de método de pagamento para o estado de digitar o gasto.
            METODO_PAGAMENTO: [
                CallbackQueryHandler(responder_metodo_pagamento, pattern='^metodo_(credito|debito_pix)$'),
                CallbackQueryHandler(voltar_gasto_valor, pattern='^voltar_gasto_valor$'),
            ],
            # Retorna do estado de parcelamento para o estado de método de pagamento.
            CONFIRMAR_PARCELA: [
                CallbackQueryHandler(responder_parcela_sim_nao, pattern='^parcela_(sim|nao)$'),
                CallbackQueryHandler(voltar_metodo_pagamento, pattern='^voltar_metodo_pagamento$'),
            ],
            # Retorna do estado de escolha de parcelas para a confirmação "foi parcelado?".
            ESCOLHER_PARCELAS: [
                CallbackQueryHandler(escolher_numero_parcelas, pattern='^parcela_(n_\d+|custom)$'),
                CallbackQueryHandler(voltar_confirmar_parcela, pattern='^voltar_confirmar_parcela$'),
            ],
            # Retorna do estado de digitar o total de parcelas para a escolha de parcelas.
            DIGITAR_PARCELAS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, digitar_numero_parcelas),
                CallbackQueryHandler(voltar_escolher_parcelas, pattern='^voltar_escolher_parcelas$'),
            ],
            EDITAR_ULTIMO_GASTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_valor_editado_gasto)],
        },
        # Manipulador para cancelar o fluxo e voltar ao menu, disponível em qualquer estado.
        fallbacks=[CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'), CommandHandler('cancelar', cancelar)],
    )

    conv_add_cat = ConversationHandler(
        entry_points=[CallbackQueryHandler(iniciar_adicionar_categoria, pattern='^menu_add_cat$')],
        states={
            AGUARDANDO_NOME_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_nome_categoria)],
            # Retorna do estado de valor para o estado de nome da categoria.
            AGUARDANDO_VALOR_CAT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_nova_categoria),
                CallbackQueryHandler(voltar_nome_categoria, pattern='^voltar_nome_cat$'),
            ],
        },
        # Manipulador para cancelar o fluxo e voltar ao menu, disponível em qualquer estado.
        fallbacks=[CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'), CommandHandler('cancelar', cancelar)],
    )

    conv_transferir = ConversationHandler(
        entry_points=[CallbackQueryHandler(iniciar_transferencia, pattern='^menu_transferir$')],
        states={
            TRANSFERIR_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_transferencia_livre)],
        },
        # Manipulador para cancelar o fluxo e voltar ao menu, disponível em qualquer estado.
        fallbacks=[CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'), CommandHandler('cancelar', cancelar)],
    )

    conv_alerta = ConversationHandler(
        entry_points=[CallbackQueryHandler(iniciar_alerta, pattern='^menu_alerta$')],
        states={
            # Tela estática de visualização (alerta já configurado): revelar
            # a escolha de horário para editar, ou excluir o alerta atual.
            ALERTA_VISUALIZACAO: [
                CallbackQueryHandler(editar_horario_alerta, pattern='^editar_horario_alerta$'),
                CallbackQueryHandler(excluir_alerta, pattern='^excluir_alerta$'),
            ],
            # Pattern restrito a 'alerta_' para não engolir o callback 'voltar_menu' do fallback.
            ALERTA_ESCOLHA: [
                CallbackQueryHandler(selecionar_horario_alerta, pattern='^alerta_'),
                # Retorna da escolha de horário para a visualização (quando já havia alerta configurado).
                CallbackQueryHandler(voltar_alerta_visualizacao, pattern='^voltar_alerta_visualizacao$'),
            ],
            # Retorna do estado de horário customizado para a escolha de horários.
            ALERTA_CUSTOM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_horario_custom),
                CallbackQueryHandler(voltar_alerta_escolha, pattern='^voltar_alerta_escolha$'),
            ],
        },
        # Manipulador para cancelar o fluxo e voltar ao menu, disponível em qualquer estado.
        fallbacks=[CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'), CommandHandler('cancelar', cancelar)],
    )

    # CORREÇÃO DA EDIÇÃO: Adicionados os gatilhos como entry_points oficiais do ConversationHandler
    conv_editar_cat = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(ajustar_fatura_parcelamento, pattern='^ajustar_fatura_'),
            CallbackQueryHandler(menu_editar_categorias, pattern='^menu_editar_cat$'),
            CallbackQueryHandler(gerenciar_categoria_especifica, pattern='^edit_cat_'),
            CallbackQueryHandler(iniciar_renomear, pattern='^acao_renomear_'),
            CallbackQueryHandler(iniciar_mudar_limite, pattern='^acao_limite_'),
            CallbackQueryHandler(iniciar_editar_dia_vencimento, pattern='^menu_editar_dia$'),
            # Telas desta conversa costumam ficar sem estado rastreado (retornam None),
            # então o botão "← Voltar" também precisa estar nos entry_points para ser
            # capturado a partir de qualquer uma delas, não só dos estados com texto.
            CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'),
        ],
        states={
            EDITAR_NOME_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_novo_nome)],
            EDITAR_VALOR_CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_novo_limite)],
            EDITAR_DIA_VENCIMENTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_novo_dia_vencimento)],
        },
        fallbacks=[
            CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'),
            CallbackQueryHandler(menu_editar_categorias, pattern='^menu_editar_cat$'),
            # Retorna dos estados de renomear/mudar limite (EDITAR_NOME_CAT,
            # EDITAR_VALOR_CAT) para o detalhe da categoria (edit_cat_<id>).
            CallbackQueryHandler(gerenciar_categoria_especifica, pattern='^edit_cat_'),
            CommandHandler('cancelar', cancelar)
        ],
    )

    conv_recorrente = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(menu_gastos_recorrentes, pattern='^menu_recorrente$'),
            CallbackQueryHandler(recorrente_escolher_acao, pattern=r'^rec_cat_\d+$'),
            CallbackQueryHandler(iniciar_criar_recorrente, pattern=r'^rec_criar_\d+$'),
            CallbackQueryHandler(ver_recorrentes, pattern=r'^rec_ver_\d+$'),
            CallbackQueryHandler(ver_item_recorrente, pattern=r'^rec_item_\d+$'),
            CallbackQueryHandler(iniciar_editar_dia_recorrente, pattern=r'^rec_editdia_\d+$'),
            CallbackQueryHandler(iniciar_editar_valor_recorrente, pattern=r'^rec_editvalor_\d+$'),
            CallbackQueryHandler(excluir_recorrente_botao, pattern=r'^rec_excluir_\d+$'),
            # Telas desta conversa costumam ficar sem estado rastreado (retornam None),
            # então o botão "← Voltar ao Menu" também precisa estar nos entry_points
            # para ser capturado a partir de qualquer uma delas.
            CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'),
        ],
        states={
            RECORRENTE_TEXTO_LIVRE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_texto_recorrente)],
            RECORRENTE_COMPLETAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, receber_campo_faltante_recorrente)],
            RECORRENTE_EDITAR_DIA: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_novo_dia_recorrente)],
            RECORRENTE_EDITAR_VALOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, salvar_novo_valor_recorrente)],
        },
        fallbacks=[
            CallbackQueryHandler(voltar_para_menu, pattern='^voltar_menu$'),
            # Retorna dos estados de texto (RECORRENTE_TEXTO_LIVRE, RECORRENTE_COMPLETAR)
            # para a tela de ações da categoria (Registrar/Visualizar).
            CallbackQueryHandler(recorrente_escolher_acao, pattern=r'^rec_cat_\d+$'),
            # Retorna dos estados de edição (RECORRENTE_EDITAR_DIA, RECORRENTE_EDITAR_VALOR)
            # para o submenu do item.
            CallbackQueryHandler(ver_item_recorrente, pattern=r'^rec_item_\d+$'),
            CommandHandler('cancelar', cancelar),
        ],
    )

    app.add_handler(CallbackQueryHandler(excluir_categoria_botao, pattern='^acao_excluir_'))
    app.add_handler(CallbackQueryHandler(resumo_diario_menu_callback, pattern='^resumo_diario_menu$'))

    # Mensagem diária de cobranças recorrentes: solta (enviada por job), não
    # faz parte de nenhum ConversationHandler — registrada antes deles para
    # responder mesmo se o usuário estiver no meio de outro fluxo.
    app.add_handler(CallbackQueryHandler(recorrente_diario_proxima, pattern='^rec_diario_proxima$'))
    app.add_handler(CallbackQueryHandler(recorrente_diario_menu, pattern='^rec_diario_menu$'))

    # Conferir Parcelas (por categoria) e histórico mensal. Patterns ancorados
    # para 'view_installments_<id>' não engolir 'view_installments_month_...'.
    app.add_handler(CallbackQueryHandler(ver_parcelas_categoria, pattern=r'^view_installments_\d+$'))
    app.add_handler(CallbackQueryHandler(ver_parcelas_mes, pattern=r'^view_installments_month_\d+_\d{4}-\d{2}$'))
    app.add_handler(CallbackQueryHandler(ver_anos_historico, pattern='^history_years$'))
    app.add_handler(CallbackQueryHandler(ver_meses_do_ano, pattern=r'^select_year_\d{4}$'))
    app.add_handler(CallbackQueryHandler(ver_mes_historico, pattern=r'^select_month_history_\d{4}-\d{2}$'))

    app.add_handler(conv_handler)
    app.add_handler(conv_gasto)
    app.add_handler(conv_add_cat)
    app.add_handler(conv_transferir)
    app.add_handler(conv_alerta)
    app.add_handler(conv_editar_cat)
    app.add_handler(conv_recorrente)

    app.add_error_handler(tratar_erro)
    app.add_handler(CallbackQueryHandler(botao_clicado))
    app.add_handler(CommandHandler('menu', comando_menu))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, mensagem_solta))

    print("Bot rodando com sucesso!")
    # bootstrap_retries: tenta reconectar automaticamente algumas vezes antes
    # de desistir, em vez de encerrar na primeira falha de rede momentânea.
    app.run_polling(bootstrap_retries=5)