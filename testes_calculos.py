"""Harness de cenários das regras de cálculo (ciclo, Gasto, Sobra, parcelas).

Roda com `python testes_calculos.py`, num SQLite em memória (não toca no
Turso, não precisa de .env nem de internet). Usa as funções do `calculos.py`
quando ele existe; antes da extração, lê as mesmas funções direto do código
do `bot.py` (sem importá-lo, para não abrir conexão nem criar o cliente da IA).

A saída é determinística (o "agora" é congelado em 17/10/2026 12:00 BRT), então
dá para comparar a execução de antes e de depois linha a linha.
"""
import ast
import calendar
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

FUSO_BRASILIA = ZoneInfo("America/Sao_Paulo")
AGORA_FIXO = datetime(2026, 10, 17, 12, 0, tzinfo=FUSO_BRASILIA)
NOMES_MESES = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
               'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']

# Nomes lidos do bot.py quando o calculos.py ainda não existe (baseline).
NOMES_DO_BOT = {
    '_somar_meses', 'calcular_ciclo_fatura', '_intervalo_fatura', '_parametros_itens_gasto',
    'calcular_gasto_fatura', 'calcular_status_categoria', 'calcular_sobra_categoria',
    '_SQL_PARCELAS_EXPANDIDAS', '_SQL_ITENS_GASTO', '_SQL_FATURA_TRANSACAO',
    'obter_ciclo_atual', 'obter_intervalo_ciclo', 'mes_fechado',
    'buscar_meses_com_parcelas', 'buscar_meses_com_movimento',
    'categoria_existia_no_mes', 'montar_categorias_fatura', 'calcular_totais_mes',
    '_calcular_dados_resumo_mensal',
}


class _DatetimeCongelado(datetime):
    @classmethod
    def now(cls, tz=None):
        return AGORA_FIXO if tz is None else AGORA_FIXO.astimezone(tz)


def carregar_funcoes():
    """Devolve (origem, namespace com as funções), com o 'agora' congelado."""
    raiz = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, raiz)
    try:
        import calculos
    except ModuleNotFoundError:
        calculos = None
    if calculos is not None:
        calculos.datetime = _DatetimeCongelado
        return 'calculos.py', vars(calculos)

    with open(os.path.join(raiz, 'bot.py'), encoding='utf-8') as f:
        arvore = ast.parse(f.read())
    selecionados = []
    for no in arvore.body:
        if isinstance(no, ast.FunctionDef) and no.name in NOMES_DO_BOT:
            selecionados.append(no)
        elif isinstance(no, ast.Assign) and any(
                isinstance(alvo, ast.Name) and alvo.id in NOMES_DO_BOT for alvo in no.targets):
            selecionados.append(no)
    ns = {'calendar': calendar, 'datetime': _DatetimeCongelado, 'timedelta': timedelta,
          'FUSO_BRASILIA': FUSO_BRASILIA, 'NOMES_MESES': NOMES_MESES}
    exec(compile(ast.Module(body=selecionados, type_ignores=[]), 'bot.py', 'exec'), ns)
    faltando = NOMES_DO_BOT - set(ns)
    if faltando:
        sys.exit(f"Não encontrei no bot.py: {sorted(faltando)}")
    return 'bot.py', ns


ORIGEM, F = carregar_funcoes()

UID = 1
CICLO = '2026-10'


def novo_banco(dia_fechamento=28, renda=5000.0):
    """Schema igual ao de iniciar_banco() + colunas de COLUNAS_ESPERADAS."""
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
        CREATE TABLE usuarios (user_id INTEGER PRIMARY KEY, nome TEXT, renda_mensal REAL,
            dia_fechamento INTEGER, horario_alerta TEXT, ultimo_reset TEXT);
        CREATE TABLE categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, nome TEXT,
            orcamento_mensal REAL, saldo_atual REAL, data_criacao TEXT);
        CREATE TABLE transacoes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            valor REAL, data_transacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP, metodo_pagamento TEXT,
            descricao TEXT, parcelamento_id INTEGER, estabelecimento TEXT);
        CREATE TABLE parcelamentos (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            descricao TEXT, valor_total REAL, valor_parcela REAL, total_parcelas INTEGER,
            parcelas_pagas INTEGER DEFAULT 1, data_criacao TEXT, ativo INTEGER DEFAULT 1, ciclo_referencia TEXT);
        CREATE TABLE gastos_recorrentes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_nome TEXT,
            descricao TEXT, valor REAL, dia_cobranca INTEGER, criado_em TEXT, ultimo_ciclo_cobrado TEXT);
        CREATE TABLE transferencias (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, categoria_origem TEXT,
            categoria_destino TEXT, valor REAL, data_transferencia TEXT);
    ''')
    conn.execute('INSERT INTO usuarios VALUES (?, ?, ?, ?, NULL, NULL)', (UID, 'Teste', renda, dia_fechamento))
    return conn


def categoria(conn, nome, limite, data_criacao='2026-01-01 10:00:00'):
    conn.execute('INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) VALUES (?, ?, ?, ?, ?)',
                 (UID, nome, limite, limite, data_criacao))


def transacao(conn, cat, valor, momento):
    conn.execute('INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) VALUES (?, ?, ?, ?)',
                 (UID, cat, valor, momento))


def parcelamento(conn, cat, total, n, momento, ciclo_referencia, ativo=1):
    """Igual a _finalizar_gasto_parcelado: a parcela 1 também vira transação."""
    parcela = round(total / n, 2)
    transacao(conn, cat, parcela, momento)
    conn.execute('''INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, valor_parcela,
                    total_parcelas, parcelas_pagas, data_criacao, ciclo_referencia, ativo)
                    VALUES (?, ?, 'compra', ?, ?, ?, 1, ?, ?, ?)''',
                 (UID, cat, total, parcela, n, momento, ciclo_referencia, ativo))


def transferencia(conn, origem, destino, valor, momento):
    conn.execute('INSERT INTO transferencias (user_id, categoria_origem, categoria_destino, valor, data_transferencia) VALUES (?, ?, ?, ?, ?)',
                 (UID, origem, destino, valor, momento))


def base_mercado():
    """Mercado, limite 1.000, avulso 100, receita 30, fechamento dia 28 (ciclo 2026-10 = 28/09 a 27/10)."""
    conn = novo_banco()
    categoria(conn, 'Mercado', 1000.0)
    categoria(conn, 'Lazer', 500.0)
    transacao(conn, 'Mercado', 100.0, '2026-10-05 10:00:00')
    transacao(conn, 'Mercado', -30.0, '2026-10-06 10:00:00')
    return conn


def c1(conn):  # Compra 3x de 300 no ciclo atual
    parcelamento(conn, 'Mercado', 300.0, 3, '2026-10-10 12:00:00', '2026-10')

def c2(conn):  # Parcela 2 de compra antiga (600 em 3x)
    parcelamento(conn, 'Mercado', 600.0, 3, '2026-09-10 12:00:00', '2026-09')

def c3(conn):  # "Banco Adiou" (400 em 2x): comprada no ciclo, 1ª parcela só na fatura seguinte
    parcelamento(conn, 'Mercado', 400.0, 2, '2026-10-12 12:00:00', '2026-11')

def c4(conn):  # Parcelamento inativo
    parcelamento(conn, 'Mercado', 300.0, 3, '2026-10-14 12:00:00', '2026-10', ativo=0)

def c5(conn):  # Tudo junto + transferência de 50 (Lazer -> Mercado)
    c1(conn); c2(conn); c3(conn); c4(conn)
    transferencia(conn, 'Lazer', 'Mercado', 50.0, '2026-10-15 09:00:00')

def c6(conn):  # Estouro (parcela 2 de 3.000 em 3x)
    parcelamento(conn, 'Mercado', 3000.0, 3, '2026-09-10 12:00:00', '2026-09')


CENARIOS = [
    ('1. Compra 3x de 300 no ciclo atual', c1, (200.0, 830.0)),
    ('2. Parcela 2 de compra antiga (600 em 3x)', c2, (300.0, 730.0)),
    ('3. "Banco Adiou" (400 em 2x)', c3, (100.0, 930.0)),
    ('4. Parcelamento inativo', c4, (100.0, 930.0)),
    ('5. Tudo junto + transferência de 50', c5, (400.0, 680.0)),
    ('6. Estouro (parcela 2 de 3.000 em 3x)', c6, (1100.0, -70.0)),
]

saida = []
falhas = []


def mostrar(rotulo, valor):
    saida.append(f"{rotulo}: {valor!r}")


def conferir(rotulo, obtido, esperado):
    ok = obtido == esperado
    saida.append(f"[{'OK' if ok else 'FALHOU'}] {rotulo}: {obtido!r}" + ('' if ok else f" (esperado {esperado!r})"))
    if not ok:
        falhas.append(rotulo)


def nome_mes_resumo(dados):
    """Normaliza o 1º item do resumo mensal para o nome do mês (o baseline já
    devolve o nome; a versão extraída pode devolver o 'YYYY-MM')."""
    mes = dados[0]
    if '-' in mes:
        mes = NOMES_MESES[int(mes.split('-')[1]) - 1]
    return (mes,) + tuple(dados[1:])


def resumo_mensal(conn, dia_fechamento, agora):
    if 'calcular_dados_resumo_mensal' in F:
        return nome_mes_resumo(F['calcular_dados_resumo_mensal'](conn, UID, dia_fechamento, agora))
    return nome_mes_resumo(F['_calcular_dados_resumo_mensal'](conn, UID, dia_fechamento, agora))


# --- Cenários de Gasto / Sobra (ciclo 2026-10, fechamento 28) ---
saida.append("== Cenários Mercado (Gasto / Sobra) ==")
for rotulo, montar, esperado in CENARIOS:
    conn = base_mercado()
    montar(conn)
    orcamento, gasto, sobra = F['calcular_status_categoria'](conn, UID, 'Mercado', CICLO)
    conferir(rotulo, (round(gasto, 2), round(sobra, 2)), esperado)
    mostrar("   status (orçamento, gasto, sobra)", (orcamento, gasto, sobra))
    mostrar("   status sem ano_mes (ciclo vigente)", F['calcular_status_categoria'](conn, UID, 'Mercado'))
    mostrar("   sobra_categoria", F['calcular_sobra_categoria'](conn, UID, 'Mercado'))
    mostrar("   gasto_fatura 2026-10", sorted(F['calcular_gasto_fatura'](conn, UID, CICLO, 28).items()))
    mostrar("   gasto_fatura 2026-11", sorted(F['calcular_gasto_fatura'](conn, UID, '2026-11', 28).items()))
    cats = F['montar_categorias_fatura'](conn, UID, CICLO, 28)
    mostrar("   categorias_fatura", cats)
    mostrar("   totais_mes (renda 5000)", F['calcular_totais_mes'](5000.0, cats))
    mostrar("   meses_com_parcelas", F['buscar_meses_com_parcelas'](conn, UID))
    mostrar("   meses_com_movimento", F['buscar_meses_com_movimento'](conn, UID, 28))
    mostrar("   resumo_mensal (agora 28/10 00:05)",
            resumo_mensal(conn, 28, datetime(2026, 10, 28, 0, 5, tzinfo=FUSO_BRASILIA)))
    conn.close()

# --- Fechamento dia 31 em mês curto ---
saida.append("")
saida.append("== Fechamento dia 31 em mês curto ==")
ciclo = F['calcular_ciclo_fatura']
intervalo = F['obter_intervalo_ciclo']
conferir("ciclo 27/02/2026 (dia 31)", ciclo(datetime(2026, 2, 27), 31), '2026-02')
conferir("ciclo 28/02/2026 (dia 31, último dia)", ciclo(datetime(2026, 2, 28), 31), '2026-03')
conferir("ciclo 29/04/2026 (dia 31)", ciclo(datetime(2026, 4, 29), 31), '2026-04')
conferir("ciclo 30/04/2026 (dia 31, último dia)", ciclo(datetime(2026, 4, 30), 31), '2026-05')
conferir("ciclo 29/02/2028 (bissexto, dia 31)", ciclo(datetime(2028, 2, 29), 31), '2028-03')
conferir("intervalo 2026-03 (dia 31)", intervalo('2026-03', 31), ('2026-02-28', '2026-03-31'))
conferir("intervalo 2026-05 (dia 31)", intervalo('2026-05', 31), ('2026-04-30', '2026-05-31'))
conferir("intervalo 2026-02 (dia 30)", intervalo('2026-02', 30), ('2026-01-30', '2026-02-28'))
conn = novo_banco(dia_fechamento=31)
categoria(conn, 'Mercado', 1000.0)
transacao(conn, 'Mercado', 70.0, '2026-04-29 23:00:00')
transacao(conn, 'Mercado', 40.0, '2026-04-30 08:00:00')
conferir("gasto fatura 2026-04 (dia 31)", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-04')[1], 70.0)
conferir("gasto fatura 2026-05 (dia 31)", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-05')[1], 40.0)
mostrar("meses_com_movimento (dia 31)", F['buscar_meses_com_movimento'](conn, UID, 31))
conn.close()

# --- Virada de ciclo (antes e depois do fechamento) ---
saida.append("")
saida.append("== Virada de ciclo (fechamento 28) ==")
conferir("ciclo 27/10/2026", ciclo(datetime(2026, 10, 27, 23, 59), 28), '2026-10')
conferir("ciclo 28/10/2026", ciclo(datetime(2026, 10, 28, 0, 0), 28), '2026-11')
conferir("ciclo 27/12/2026", ciclo(datetime(2026, 12, 27), 28), '2026-12')
conferir("ciclo 28/12/2026 (virada de ano)", ciclo(datetime(2026, 12, 28), 28), '2027-01')
conferir("intervalo 2026-10", intervalo('2026-10', 28), ('2026-09-28', '2026-10-28'))
conferir("intervalo 2027-01", intervalo('2027-01', 28), ('2026-12-28', '2027-01-28'))
conferir("ciclo vigente (agora 17/10)", F['obter_ciclo_atual'](28), '2026-10')
conferir("mes_fechado 2026-09", F['mes_fechado']('2026-09', 28), True)
conferir("mes_fechado 2026-10", F['mes_fechado']('2026-10', 28), False)
conn = novo_banco()
categoria(conn, 'Mercado', 1000.0)
transacao(conn, 'Mercado', 60.0, '2026-10-27 23:59:00')
transacao(conn, 'Mercado', 25.0, '2026-10-28 00:01:00')
transacao(conn, 'Mercado', 15.0, '2026-09-27 18:00:00')
conferir("gasto fatura 2026-09", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-09')[1], 15.0)
conferir("gasto fatura 2026-10", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-10')[1], 60.0)
conferir("gasto fatura 2026-11", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-11')[1], 25.0)
mostrar("meses_com_movimento", F['buscar_meses_com_movimento'](conn, UID, 28))
conn.close()

# --- Categoria criada depois do mês consultado / categoria excluída com gasto ---
saida.append("")
saida.append("== Regra temporal de categorias ==")
existia = F['categoria_existia_no_mes']
conferir("criada 05/11 existe em 2026-10", existia('2026-11-05 10:00:00', '2026-10', 28), False)
conferir("criada 05/11 existe em 2026-11", existia('2026-11-05 10:00:00', '2026-11', 28), True)
conferir("criada 28/10 existe em 2026-10", existia('2026-10-28 10:00:00', '2026-10', 28), False)
conferir("criada 28/10 existe em 2026-11", existia('2026-10-28 10:00:00', '2026-11', 28), True)
conferir("sem data_criacao", existia(None, '2020-01', 28), True)
conferir("data_criacao inválida", existia('lixo', '2020-01', 28), True)
conn = base_mercado()
categoria(conn, 'Viagem', 800.0, data_criacao='2026-11-05 10:00:00')
transacao(conn, 'Antiga', 45.0, '2026-10-02 10:00:00')  # categoria excluída, com gasto
nomes_out = [c[0] for c in F['montar_categorias_fatura'](conn, UID, '2026-10', 28)]
nomes_nov = [c[0] for c in F['montar_categorias_fatura'](conn, UID, '2026-11', 28)]
conferir("categorias em 2026-10", nomes_out, ['Mercado', 'Lazer', 'Antiga'])
conferir("categorias em 2026-11", nomes_nov, ['Mercado', 'Lazer', 'Viagem'])
cats = F['montar_categorias_fatura'](conn, UID, '2026-10', 28)
mostrar("categorias_fatura 2026-10", cats)
mostrar("totais_mes 2026-10 (renda 5000)", F['calcular_totais_mes'](5000.0, cats))
mostrar("status categoria inexistente", F['calcular_status_categoria'](conn, UID, 'Nada', '2026-10'))
mostrar("sobra categoria inexistente", F['calcular_sobra_categoria'](conn, UID, 'Nada'))
conn.close()

# --- Usuário sem dia de fechamento (todo o histórico) ---
saida.append("")
saida.append("== Sem dia de fechamento ==")
conn = novo_banco(dia_fechamento=None)
categoria(conn, 'Mercado', 1000.0)
transacao(conn, 'Mercado', 100.0, '2025-01-05 10:00:00')
transacao(conn, 'Mercado', 50.0, '2026-10-05 10:00:00')
mostrar("status sem fechamento", F['calcular_status_categoria'](conn, UID, 'Mercado'))
mostrar("status sem fechamento, ano_mes 2026-10", F['calcular_status_categoria'](conn, UID, 'Mercado', '2026-10'))
conn.close()

print(f"Origem das funções: {ORIGEM}")
print("\n".join(saida))
print()
print(f"{len(falhas)} falha(s)." if falhas else "Todos os cenários conferem.")
sys.exit(1 if falhas else 0)
