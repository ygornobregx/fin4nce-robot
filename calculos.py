"""Regras de cálculo do bot financeiro: ciclo de fatura, Gasto, Sobra do Mês
e parcelas. Fonte única usada pelo bot (bot.py) e pela API do Mini App.

A categoria só classifica o gasto (PRO-83): não há limite, orçamento nem
saldo por categoria. A Sobra do Mês é renda + receitas da fatura − gasto da
fatura (receitas avulsas da tabela 'receitas', PRO-86), e só existe a partir
da fatura vigente na data de corte (DATA_CORTE_SOBRA). As transações
negativas antigas de 'transacoes' (reembolsos anteriores à PRO-86) não entram
em nenhum cálculo.

Só depende da biblioteca padrão: não importa telegram nem bot.py, não abre
conexão com o banco e não executa nada ao ser importado. As funções recebem
a conexão ('conn') por parâmetro; as que dependem do "agora" aceitam 'agora'
opcional (padrão: agora no fuso de Brasília), para permitir testes com datas
fixas.
"""
import calendar
from datetime import date, datetime, timedelta
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

# Fuso de Brasília: usado em todas as datas, timestamps e agendamentos do bot.
FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

# Data de corte da Sobra do Mês (PRO-83): a primeira fatura com Sobra é a
# vigente nesta data (para o dia de fechamento de cada usuário), e as
# seguintes. As faturas anteriores mostram só o gasto, sem Sobra, porque o
# passado não é recalculado. Tem de ser um dia da fatura vigente no dia do
# deploy (de preferência, o próprio dia): conferir antes do commit.
DATA_CORTE_SOBRA = date(2026, 11, 4)

# --- CICLO DE FATURA ---
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
    com 30 dias, ou fevereiro) — mesma regra usada em obter_intervalo_ciclo.
    """
    ultimo_dia_mes = calendar.monthrange(data.year, data.month)[1]
    dia_alvo = min(dia_fechamento, ultimo_dia_mes)
    base = f"{data.year:04d}-{data.month:02d}"
    return _somar_meses(base, 1) if data.day >= dia_alvo else base

def fatura_tem_sobra(ano_mes, dia_fechamento, data_corte=None):
    """True se a fatura 'ano_mes' tem Sobra do Mês: é a fatura vigente na data
    de corte (para este dia de fechamento) ou uma posterior. 'data_corte'
    (date) substitui DATA_CORTE_SOBRA nos testes. 'YYYY-MM' ordena
    corretamente como texto."""
    corte = data_corte if data_corte is not None else DATA_CORTE_SOBRA
    return ano_mes >= calcular_ciclo_fatura(corte, dia_fechamento)

# --- GASTO E SOBRA DO MÊS (FONTE ÚNICA DE CÁLCULO) ---
def _intervalo_fatura(ano_mes, dia_fechamento):
    """Intervalo de datas da fatura 'ano_mes' (ver obter_intervalo_ciclo), ou
    todo o histórico quando não há fatura (usuário sem dia de fechamento)."""
    if ano_mes is None:
        return '0000-01-01', '9999-12-31'
    return obter_intervalo_ciclo(ano_mes, dia_fechamento)

def _parametros_itens_gasto(user_id, categoria, inicio, fim, ano_mes):
    """Parâmetros, na ordem, de _SQL_PARCELAS_EXPANDIDAS + _SQL_ITENS_GASTO."""
    return (user_id, categoria, categoria,
            user_id, categoria, categoria, inicio, fim,
            ano_mes, ano_mes)

def calcular_gasto_fatura(conn, user_id, ano_mes, dia_fechamento):
    """Gasto de cada categoria na fatura 'ano_mes' ({categoria: gasto}), pela
    mesma definição de calcular_gasto_categoria (_SQL_ITENS_GASTO). Inclui
    categorias já excluídas que ainda têm movimento (o histórico mensal as
    mostra). 'ano_mes' None = todo o histórico."""
    inicio, fim = _intervalo_fatura(ano_mes, dia_fechamento)
    linhas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS + _SQL_ITENS_GASTO +
        'SELECT categoria_nome, SUM(valor) FROM itens_gasto GROUP BY categoria_nome',
        _parametros_itens_gasto(user_id, None, inicio, fim, ano_mes)
    ).fetchall()
    return dict(linhas)

def calcular_gasto_categoria(conn, user_id, nome, ano_mes=None, agora=None):
    """Gasto de uma categoria numa fatura, calculado na hora a partir do banco:
    despesas avulsas do ciclo + parcelas que caem na fatura (definição única
    de _SQL_ITENS_GASTO; receitas e transferências não entram).
    'ano_mes' ('YYYY-MM') escolhe a fatura; sem ele, usa a fatura vigente em
    'agora'. Sem dia de fechamento configurado não há ciclo, e o cálculo
    considera todo o histórico. Retorna o gasto, ou None se a categoria não
    existir."""
    linha_usuario = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    dia_fechamento = linha_usuario[0] if linha_usuario else None
    if not dia_fechamento:
        ano_mes = None
    elif ano_mes is None:
        ano_mes = obter_ciclo_atual(dia_fechamento, agora)
    inicio, fim = _intervalo_fatura(ano_mes, dia_fechamento)
    linha = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS + _SQL_ITENS_GASTO +
        '''SELECT (SELECT COALESCE(SUM(valor), 0) FROM itens_gasto)
           FROM categorias c WHERE c.user_id = ? AND c.nome = ?''',
        _parametros_itens_gasto(user_id, nome, inicio, fim, ano_mes) + (user_id, nome)
    ).fetchone()
    if not linha:
        return None
    return linha[0]

# --- FATURAS, PARCELAS E HISTÓRICO MENSAL ---
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

# Definição única de Gasto de uma fatura, como uma fatura de cartão: despesas
# avulsas do ciclo mais as parcelas de parcelamentos ativos que caem nela. A
# parcela 1 também existe como transação (débito na hora da compra), então essa
# transação sai das avulsas para não contar duas vezes — a parcela 1 entra pela
# expansão, que respeita 'ciclo_referencia' ("Banco Adiou") e 'ativo'. Ela é
# reconhecida por data_criacao == data_transacao na mesma categoria.
# Melhoria futura: uma coluna de vínculo (transacoes.parcelamento_id) tornaria
# esse reconhecimento exato. Concatenar após _SQL_PARCELAS_EXPANDIDAS; os
# parâmetros vêm de _parametros_itens_gasto (ano_mes None = todas as parcelas).
_SQL_ITENS_GASTO = '''
    , itens_gasto(categoria_nome, valor) AS (
        SELECT t.categoria_nome, t.valor FROM transacoes t
        WHERE t.user_id = ? AND (? IS NULL OR t.categoria_nome = ?) AND t.valor > 0
          AND DATE(t.data_transacao) >= ? AND DATE(t.data_transacao) < ?
          AND NOT EXISTS (
              SELECT 1 FROM parcelamentos p
              WHERE p.user_id = t.user_id
                AND p.categoria_nome = t.categoria_nome
                AND p.data_criacao = t.data_transacao)
        UNION ALL
        SELECT p.categoria_nome, p.valor_parcela
        FROM expandido e JOIN parcelamentos p ON p.id = e.pid
        WHERE ? IS NULL OR e.ano_mes = ?
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

def _meses_entre(ano_mes_inicial, ano_mes_final):
    """Meses de 'YYYY-MM' inicial até o final; negativo se o final vier antes."""
    ano_i, mes_i = map(int, ano_mes_inicial.split('-'))
    ano_f, mes_f = map(int, ano_mes_final.split('-'))
    return (ano_f - ano_i) * 12 + (mes_f - mes_i)

def calcular_parcelas_pagas(ciclo_referencia, data_criacao, total_parcelas, dia_fechamento, agora=None):
    """Parcelas da compra que caem em faturas até a vigente (inclusive), de 0 a
    'total_parcelas' (PRO-80). Mesma regra da expansão de parcelas
    (_SQL_PARCELAS_EXPANDIDAS): a parcela n+1 cai em 'ciclo_referencia' + n
    meses, e a primeira fatura é 'ciclo_referencia' (ou, se estiver vazio ou
    None, os 7 primeiros caracteres de 'data_criacao', 'YYYY-MM'). Conta como
    paga a parcela da fatura vigente, que o bot já debita na hora da compra;
    por isso uma compra de hoje tem 1 parcela paga, e uma que o "Banco Adiou"
    empurrou para depois da fatura vigente tem 0.

    Função pura, sem conexão: derivada na hora, nunca lê nem grava a coluna
    parcelamentos.parcelas_pagas (abandonada). 'agora' opcional (padrão: agora,
    no fuso de Brasília), para testes com datas fixas."""
    primeira_fatura = ciclo_referencia or (data_criacao or '')[:7]
    pagas = _meses_entre(primeira_fatura, obter_ciclo_atual(dia_fechamento, agora)) + 1
    return max(0, min(pagas, total_parcelas))

def obter_ciclo_atual(dia_fechamento, agora=None):
    """Fatura vigente ('YYYY-MM') em 'agora' (padrão: agora, no fuso de Brasília)."""
    if agora is None:
        agora = datetime.now(FUSO_BRASILIA)
    return calcular_ciclo_fatura(agora, dia_fechamento)

def obter_intervalo_ciclo(ano_mes, dia_fechamento):
    """('YYYY-MM-DD' inicial inclusivo, 'YYYY-MM-DD' final exclusivo) da fatura
    'ano_mes'. É o inverso de calcular_ciclo_fatura: a fatura M vai do dia de
    fechamento do mês anterior (inclusive) até o de M (exclusive), com o ajuste
    de meses mais curtos que o dia configurado."""
    def fechamento(ano_mes_ref):
        ano, mes = map(int, ano_mes_ref.split('-'))
        dia = min(dia_fechamento, calendar.monthrange(ano, mes)[1])
        return f"{ano:04d}-{mes:02d}-{dia:02d}"
    return fechamento(_somar_meses(ano_mes, -1)), fechamento(ano_mes)

def mes_fechado(ano_mes, dia_fechamento, agora=None):
    """Estado 'Mês Fechado' é derivado, não armazenado: uma fatura está fechada
    quando é anterior à fatura vigente. 'YYYY-MM' ordena corretamente como texto."""
    return ano_mes < obter_ciclo_atual(dia_fechamento, agora)

def buscar_meses_com_parcelas(conn, user_id, categoria=None, a_partir_de='0000-00'):
    """Meses ('YYYY-MM', ordem cronológica) que têm ao menos uma parcela de
    parcelamento ativo, opcionalmente só de uma categoria e a partir de um mês."""
    linhas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS +
        'SELECT DISTINCT ano_mes FROM expandido WHERE ano_mes >= ? ORDER BY ano_mes',
        (user_id, categoria, categoria, a_partir_de)
    ).fetchall()
    return [l[0] for l in linhas]

def buscar_meses_com_movimento(conn, user_id, dia_fechamento, agora=None):
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
    meses.add(obter_ciclo_atual(dia_fechamento, agora))
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

def montar_categorias_fatura(conn, user_id, ano_mes, dia_fechamento):
    """Lista (nome, gasto) da fatura 'ano_mes', na ordem do painel: todas as
    categorias existentes na época (regra temporal por data de criação, na
    ordem de cadastro), mais as que têm gasto no mês mesmo sem existir mais
    (em ordem alfabética). O gasto vem de calcular_gasto_fatura (uma consulta
    para todas as categorias); de 'categorias' só se leem nome e data de
    criação. 'ano_mes' None (usuário sem dia de fechamento) = todo o
    histórico, com todas as categorias."""
    categorias_db = conn.execute(
        'SELECT nome, data_criacao FROM categorias WHERE user_id = ? ORDER BY id',
        (user_id,)
    ).fetchall()
    gasto_mes = calcular_gasto_fatura(conn, user_id, ano_mes, dia_fechamento)

    categorias = []
    for cat, data_criacao in categorias_db:
        if (ano_mes is None or cat in gasto_mes
                or categoria_existia_no_mes(data_criacao, ano_mes, dia_fechamento)):
            categorias.append((cat, gasto_mes.get(cat, 0.0)))
    existentes = {c[0] for c in categorias_db}
    for cat in sorted(set(gasto_mes) - existentes):
        categorias.append((cat, gasto_mes[cat]))
    return categorias

def calcular_receitas_fatura(conn, user_id, ano_mes, dia_fechamento):
    """Soma das receitas (tabela 'receitas', valor sempre positivo) da fatura
    'ano_mes', pelo MESMO intervalo do gasto (_intervalo_fatura: início
    inclusivo, fim exclusivo, sobre DATE(data_receita)). 'ano_mes' None =
    todo o histórico (usuário sem dia de fechamento). Sem receita, 0.0."""
    inicio, fim = _intervalo_fatura(ano_mes, dia_fechamento)
    linha = conn.execute(
        '''SELECT COALESCE(SUM(valor), 0) FROM receitas
           WHERE user_id = ? AND DATE(data_receita) >= ? AND DATE(data_receita) < ?''',
        (user_id, inicio, fim)
    ).fetchone()
    return float(linha[0]) if linha and linha[0] is not None else 0.0

def calcular_totais_mes(renda, categorias, tem_sobra=True, total_receitas=0.0):
    """Definição única de Gasto total e Sobra do Mês. 'categorias' é a lista
    (nome, gasto) de montar_categorias_fatura. Sobra do Mês = renda +
    receitas da fatura − gasto total (renda nula vale 0; pode dar negativa).
    'total_receitas' vem de calcular_receitas_fatura (PRO-86); sem ele, a
    conta é a de antes (renda − gasto). Retorna (total_gastos, sobra_mes),
    com sobra_mes None quando 'tem_sobra' for falso (fatura anterior à data
    de corte)."""
    total_gastos = sum(gasto for _, gasto in categorias)
    if not tem_sobra:
        return total_gastos, None
    return total_gastos, (renda or 0.0) + total_receitas - total_gastos

class ResumoFatura(NamedTuple):
    """Resumo de uma fatura, devolvido por calcular_resumo_fatura."""
    ano_mes: Optional[str]
    renda: float
    categorias: list
    total_gasto: float
    sobra_mes: Optional[float]
    total_receitas: float = 0.0  # receitas da fatura (PRO-86); campo novo no fim

def calcular_resumo_fatura(conn, user_id, ano_mes, dia_fechamento, data_corte=None):
    """Função única de resumo da fatura 'ano_mes', usada pelo 'Ver Resumo',
    pelo histórico de meses, pelo /resumo da API, pelo Resumo Mensal e pelo
    resumo diário. Devolve ResumoFatura(ano_mes, renda, categorias,
    total_gasto, sobra_mes, total_receitas):
      - renda: renda_mensal do usuário (nula vale 0);
      - categorias: lista (nome, gasto) de montar_categorias_fatura;
      - total_receitas: calcular_receitas_fatura (PRO-86), preenchido mesmo
        quando a fatura não tem Sobra;
      - total_gasto e sobra_mes: de calcular_totais_mes (renda + receitas −
        gasto), com sobra_mes None se a fatura for anterior à data de corte
        (fatura_tem_sobra).
    Sem dia de fechamento não há fatura: o gasto e as receitas são os de todo
    o histórico e a Sobra é None. 'data_corte' substitui DATA_CORTE_SOBRA nos
    testes. São quatro consultas, qualquer que seja o número de categorias."""
    if not dia_fechamento:
        ano_mes = None
    linha = conn.execute('SELECT renda_mensal FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    renda = (linha[0] if linha else None) or 0.0
    categorias = montar_categorias_fatura(conn, user_id, ano_mes, dia_fechamento)
    total_receitas = calcular_receitas_fatura(conn, user_id, ano_mes, dia_fechamento)
    tem_sobra = ano_mes is not None and fatura_tem_sobra(ano_mes, dia_fechamento, data_corte)
    total_gasto, sobra_mes = calcular_totais_mes(renda, categorias, tem_sobra, total_receitas)
    return ResumoFatura(ano_mes, renda, categorias, total_gasto, sobra_mes, total_receitas)

def calcular_serie_gastos_mensais(conn, user_id, dia_fechamento, agora=None):
    """Gasto total de cada fatura com movimento, para o gráfico de barras do
    Mini App. Retorna [(ano_mes, total_gasto, situacao)] em ordem cronológica,
    com os meses de buscar_meses_com_movimento (parcelas, gastos e a fatura
    vigente, sem meses vazios no meio). 'situacao' é "atual" para a fatura
    vigente, "fechado" para as anteriores (mes_fechado) e "previsto" para as
    posteriores, que só têm parcelas.

    O total de cada mês é o mesmo total_gasto de calcular_resumo_fatura,
    porque ambos usam calcular_gasto_fatura (_SQL_ITENS_GASTO). Aqui é uma
    consulta por mês: isso importa no Turso, onde cada consulta vai pela
    rede. Os valores não são arredondados."""
    atual = obter_ciclo_atual(dia_fechamento, agora)
    serie = []
    for ano_mes in buscar_meses_com_movimento(conn, user_id, dia_fechamento, agora):
        total_gasto = sum(calcular_gasto_fatura(conn, user_id, ano_mes, dia_fechamento).values())
        if ano_mes == atual:
            situacao = "atual"
        elif mes_fechado(ano_mes, dia_fechamento, agora):
            situacao = "fechado"
        else:
            situacao = "previsto"
        serie.append((ano_mes, total_gasto, situacao))
    return serie

# --- ÚLTIMOS GASTOS (EXTRATO DO MINI APP) ---
def buscar_gastos_recentes(conn, user_id, limite):
    """Os últimos 'limite' gastos do usuário, para o extrato da tela home do
    Mini App. Retorna [(id, descricao, categoria, valor, data, total_parcelas,
    valor_total, estabelecimento)], do mais recente para o mais antigo (empate
    no mesmo horário: maior id primeiro). 'data' é o dia "AAAA-MM-DD" já gravado
    no fuso de Brasília. 'total_parcelas' e 'valor_total' vêm None quando o
    gasto não é parcelado. 'estabelecimento' (PRO-81) vem sem espaços nas
    pontas e None quando está vazio, só com espaços ou é anterior à PRO-81;
    nunca vira texto substituto aqui, e um gasto sem estabelecimento continua
    na lista.

    Regras:
    - só despesas (valor > 0); as transações negativas antigas (reembolsos
      anteriores à PRO-86) ficam de fora, e as receitas da tabela 'receitas'
      não entram nesta lista;
    - só gastos com descrição preenchida (descrição vazia ou só com espaços
      não conta). Os gastos antigos, anteriores à PRO-78, não têm descrição e
      ficam fora de propósito: não se usa o nome da categoria no lugar. Eles
      seguem nos totais do painel e do histórico;
    - a compra parcelada é identificada só por transacoes.parcelamento_id,
      pelo LEFT JOIN com parcelamentos (a condição de dono, p.user_id =
      t.user_id, fica no ON para nunca misturar dados de outro usuário e para
      o gasto à vista continuar na lista). Ela aparece uma vez, pela parcela 1;
      as parcelas seguintes não são linhas de transacoes. 'valor' é o da
      parcela 1;
    - sem filtro por p.ativo: a compra aconteceu e continua no extrato;
    - transferências entre categorias não entram: ficam em transferencias, não
      em transacoes. Se um dia passarem a ser gravadas em transacoes, esta
      consulta terá de tratá-las."""
    return conn.execute(
        "SELECT t.id, TRIM(t.descricao), t.categoria_nome, t.valor, "
        "substr(t.data_transacao, 1, 10), p.total_parcelas, p.valor_total, "
        "NULLIF(TRIM(t.estabelecimento), '') "
        "FROM transacoes t "
        "LEFT JOIN parcelamentos p ON p.id = t.parcelamento_id AND p.user_id = t.user_id "
        "WHERE t.user_id = ? AND t.valor > 0 "
        "AND t.descricao IS NOT NULL AND TRIM(t.descricao) <> '' "
        "ORDER BY t.data_transacao DESC, t.id DESC "
        "LIMIT ?",
        (user_id, limite),
    ).fetchall()

def buscar_gasto(conn, user_id, id_gasto):
    """Um gasto do extrato pelo id, com os dados do parcelamento, para a tela de
    detalhe do Mini App (PRO-80). Retorna (id, descricao, categoria, valor,
    data, total_parcelas, valor_total, estabelecimento, ciclo_referencia,
    data_criacao), os oito primeiros iguais aos de buscar_gastos_recentes, e
    'ciclo_referencia' e 'data_criacao' do parcelamento (None à vista), que
    alimentam calcular_parcelas_pagas.

    Mesmas regras de buscar_gastos_recentes (só despesas, só com descrição,
    LEFT JOIN com a condição de dono no ON, estabelecimento vazio vira None).
    Retorna None se o gasto não existir, for de outro usuário, for negativo ou
    não tiver descrição, sem distinguir o motivo."""
    return conn.execute(
        "SELECT t.id, TRIM(t.descricao), t.categoria_nome, t.valor, "
        "substr(t.data_transacao, 1, 10), p.total_parcelas, p.valor_total, "
        "NULLIF(TRIM(t.estabelecimento), ''), p.ciclo_referencia, p.data_criacao "
        "FROM transacoes t "
        "LEFT JOIN parcelamentos p ON p.id = t.parcelamento_id AND p.user_id = t.user_id "
        "WHERE t.user_id = ? AND t.valor > 0 "
        "AND t.descricao IS NOT NULL AND TRIM(t.descricao) <> '' "
        "AND t.id = ?",
        (user_id, id_gasto),
    ).fetchone()

# --- RESUMO MENSAL (ENVIADO NO FECHAMENTO DA FATURA) ---
def calcular_dados_resumo_mensal(conn, user_id, dia_fechamento, agora, data_corte=None):
    """Fatura que acabou de fechar (a de ontem, já que o resumo sai no dia de
    fechamento), pela mesma função do painel (calcular_resumo_fatura).
    Retorna (ano_mes, top3, renda, total_gasto, sobra_mes, total_receitas),
    com top3 como lista [(categoria, gasto)] só das categorias com gasto, do
    maior para o menor, sobra_mes None se a fatura for anterior à data de
    corte e total_receitas as receitas da fatura (PRO-86; 0.0 sem receita).
    O nome do mês fica a cargo de quem monta o texto."""
    ano_mes = calcular_ciclo_fatura(agora - timedelta(days=1), dia_fechamento)
    resumo = calcular_resumo_fatura(conn, user_id, ano_mes, dia_fechamento, data_corte)
    gastos = sorted(
        ((cat, gasto) for cat, gasto in resumo.categorias if gasto > 0),
        key=lambda item: item[1], reverse=True
    )
    return ano_mes, gastos[:3], resumo.renda, resumo.total_gasto, resumo.sobra_mes, resumo.total_receitas
