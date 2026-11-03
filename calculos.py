"""Regras de cálculo do bot financeiro: ciclo de fatura, Orçamento, Gasto,
Sobra e parcelas. Fonte única usada pelo bot (bot.py) e pela futura API do
Mini App.

Só depende da biblioteca padrão: não importa telegram nem bot.py, não abre
conexão com o banco e não executa nada ao ser importado. As funções recebem
a conexão ('conn') por parâmetro; as que dependem do "agora" aceitam 'agora'
opcional (padrão: agora no fuso de Brasília), para permitir testes com datas
fixas.
"""
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Fuso de Brasília: usado em todas as datas, timestamps e agendamentos do bot.
FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")

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

# --- ORÇAMENTO / GASTO / SOBRA (FONTE ÚNICA DE CÁLCULO) ---
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
    mesma definição de calcular_status_categoria. Inclui categorias já
    excluídas que ainda têm movimento (o histórico mensal as mostra)."""
    inicio, fim = _intervalo_fatura(ano_mes, dia_fechamento)
    linhas = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS + _SQL_ITENS_GASTO +
        'SELECT categoria_nome, SUM(valor) FROM itens_gasto GROUP BY categoria_nome',
        _parametros_itens_gasto(user_id, None, inicio, fim, ano_mes)
    ).fetchall()
    return dict(linhas)

def calcular_status_categoria(conn, user_id, nome, ano_mes=None, agora=None):
    """Fonte única de Orçamento, Gasto e Sobra de uma categoria numa fatura,
    calculados na hora a partir do banco:
      - Orçamento = limite fixo + transferências recebidas − enviadas no ciclo
      - Gasto     = despesas avulsas do ciclo + parcelas que caem na fatura
                    (ver _SQL_ITENS_GASTO)
      - Sobra     = Orçamento − Gasto + receitas do ciclo
    'ano_mes' ('YYYY-MM') escolhe a fatura (Resumo Mensal, histórico de
    meses); sem ele, usa a fatura vigente em 'agora'. Sem dia de fechamento configurado
    não há ciclo, e o cálculo considera todo o histórico.
    Retorna (orcamento, gasto, sobra), ou None se a categoria não existir."""
    linha_usuario = conn.execute('SELECT dia_fechamento FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()
    dia_fechamento = linha_usuario[0] if linha_usuario else None
    if not dia_fechamento:
        ano_mes = None
    elif ano_mes is None:
        ano_mes = obter_ciclo_atual(dia_fechamento, agora)
    inicio, fim = _intervalo_fatura(ano_mes, dia_fechamento)
    linha = conn.execute(
        _SQL_PARCELAS_EXPANDIDAS + _SQL_ITENS_GASTO +
        '''SELECT c.orcamento_mensal,
                  (SELECT COALESCE(SUM(valor), 0) FROM itens_gasto),
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
        _parametros_itens_gasto(user_id, nome, inicio, fim, ano_mes) +
        (inicio, fim, inicio, fim, inicio, fim, user_id, nome)
    ).fetchone()
    if not linha:
        return None
    limite_fixo, gasto, receitas, recebido, enviado = linha
    orcamento = (limite_fixo or 0.0) + recebido - enviado
    return orcamento, gasto, orcamento - gasto + receitas

def calcular_sobra_categoria(conn, user_id, nome, agora=None):
    """Atalho para as mensagens de 'Resta na Categoria'."""
    status = calcular_status_categoria(conn, user_id, nome, agora=agora)
    return status[2] if status else 0.0

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
    """Lista (nome, orcamento, sobra, gasto) da fatura 'ano_mes', no formato de
    montar_texto_painel, usada pelo histórico de meses e pelo Resumo Mensal:
    todas as categorias existentes na época (regra temporal por data de
    criação), mais as que têm movimento no mês mesmo sem existir mais (só o
    gasto, sem orçamento nem sobra). Orçamento, Gasto e Sobra vêm de
    calcular_status_categoria — nada é lido de 'saldo_atual'."""
    categorias_db = conn.execute(
        'SELECT nome, orcamento_mensal, data_criacao FROM categorias WHERE user_id = ? ORDER BY id',
        (user_id,)
    ).fetchall()
    gasto_mes = calcular_gasto_fatura(conn, user_id, ano_mes, dia_fechamento)

    def montar_entrada(cat, limite):
        gasto = gasto_mes.get(cat, 0.0)
        if limite is None:
            return (cat, None, None, gasto)
        orcamento, gasto, sobra = calcular_status_categoria(conn, user_id, cat, ano_mes)
        return (cat, orcamento, sobra, gasto)

    categorias = []
    for cat, limite, data_criacao in categorias_db:
        if cat in gasto_mes or categoria_existia_no_mes(data_criacao, ano_mes, dia_fechamento):
            categorias.append(montar_entrada(cat, limite))
    existentes = {c[0] for c in categorias_db}
    for cat in sorted(set(gasto_mes) - existentes):
        categorias.append(montar_entrada(cat, None))
    return categorias

def calcular_totais_mes(renda, categorias):
    """Definição única de Gasto total e Sobra do Mês, usada pelo painel ('Ver
    Resumo' e histórico) e pelo Resumo Mensal. 'categorias' segue o formato
    de montar_texto_painel. Retorna (comprometido, total_gastos, sobra_mes)."""
    comprometido = sum(limite for _, limite, _, _ in categorias if limite is not None)
    total_gastos = sum(gasto for _, _, _, gasto in categorias)
    total_excedido = sum(
        gasto - limite for _, limite, _, gasto in categorias
        if limite is not None and gasto > limite
    )

    # O abate automático na sobra do mês só ocorre se houver estouro
    return comprometido, total_gastos, (renda - comprometido) - total_excedido

def calcular_serie_gastos_mensais(conn, user_id, dia_fechamento, agora=None):
    """Gasto total de cada fatura com movimento, para o gráfico de barras do
    Mini App. Retorna [(ano_mes, total_gasto, situacao)] em ordem cronológica,
    com os meses de buscar_meses_com_movimento (parcelas, gastos e a fatura
    vigente, sem meses vazios no meio). 'situacao' é "atual" para a fatura
    vigente, "fechado" para as anteriores (mes_fechado) e "previsto" para as
    posteriores, que só têm parcelas.

    O total de cada mês é o mesmo de
    calcular_totais_mes(renda, montar_categorias_fatura(...))[1], porque ambos
    usam _SQL_ITENS_GASTO. Aqui é uma consulta por mês (calcular_gasto_fatura),
    em vez de uma por categoria por mês: isso importa no Turso, onde cada
    consulta vai pela rede. Os valores não são arredondados."""
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
    - só despesas (valor > 0); receitas (valor negativo) ficam de fora;
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
    Retorna None se o gasto não existir, for de outro usuário, for receita ou
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
def calcular_dados_resumo_mensal(conn, user_id, dia_fechamento, agora):
    """Fatura que acabou de fechar (a de ontem, já que o resumo sai no dia de
    fechamento), com as mesmas categorias, Gasto e Sobra do Mês do painel
    dessa fatura no histórico de meses. Retorna (ano_mes, top3, total_gasto,
    sobra_mes), com top3 como lista [(categoria, gasto)] só das categorias
    com gasto, do maior para o menor. O nome do mês fica a cargo de quem
    monta o texto."""
    ano_mes = calcular_ciclo_fatura(agora - timedelta(days=1), dia_fechamento)
    renda = conn.execute('SELECT renda_mensal FROM usuarios WHERE user_id = ?', (user_id,)).fetchone()[0] or 0.0

    categorias = montar_categorias_fatura(conn, user_id, ano_mes, dia_fechamento)
    _, total_gasto, sobra_mes = calcular_totais_mes(renda, categorias)
    gastos = sorted(
        ((cat, gasto) for cat, _, _, gasto in categorias if gasto > 0),
        key=lambda item: item[1], reverse=True
    )
    return ano_mes, gastos[:3], total_gasto, sobra_mes
