"""Regras da assinatura (PRO-106): teste grátis de 10 dias, Plano Pro e admin,
a checagem de acesso, os comandos de admin e o limite diário de registros com
IA. Fonte única usada pela API do Mini App (rotas /assinatura,
/assinaturas/teste, bloco "assinatura" do /me, dependência acesso_liberado e o
limite do /registro/interpretar) e pelo bot (porteiro, comandos de admin,
botão "Testar Grátis por 10 Dias", jobs e o limite nos pontos que chamam a IA).
Mesmo padrão do categorias.py e do recorrentes.py.

Só depende da biblioteca padrão e dos módulos de regra (calculos.py,
registro.py e recorrentes.py): não importa telegram, bot, dotenv nem libsql,
não lê o ambiente ao ser importado (as variáveis ADMIN_IDS,
ASSINATURAS_LIGADAS, LIMITE_IA_DIA, PRECO_PRO_STARS e, na PRO-109,
CADASTRO_ABERTO e LIMITE_TESTES_DIA são lidas na hora, em cada chamada) e recebe a conexão por parâmetro ('conn'). O "agora" entra por
parâmetro em todas as funções (os testes fixam o relógio); sem ele, vale o
relógio de Brasília (FUSO_BRASILIA). Nunca UTC puro.

Modelo (D4 = A): uma linha em 'assinaturas' por período de acesso (cada teste,
cada pagamento, cada Pro dado pelo admin). O acesso é CALCULADO na hora
("existe período válido que cobre agora?"), nunca guardado.

Regra única de soma (D17): cada lançamento de Pro soma os seus dias ao FIM
ÚNICO do Pro atual (o maior 'fim' entre as linhas Pro válidas) ou, sem Pro
valendo, começa agora. As linhas Pro de uma pessoa ficam encadeadas, sem se
sobrepor. O teste não entra na soma. Só adicionar_pro cria linha Pro.

Os erros são registro.ErroRegistro com um código (a API traduz em status; o
bot, em mensagem): 'teste_ja_usado', 'plano_ativo', 'usuario_nao_encontrado',
'dias_invalidos', 'sem_teste', 'banco_ocupado' e, na PRO-107,
'assinaturas_desligadas', 'sem_assinatura_stars', 'reativacao_recusada' e
'pagamento_nao_conferido' e, na PRO-109, 'vagas_teste_esgotadas'.

Trava de cadastro (PRO-109): CADASTRO_ABERTO (cadastro_esta_aberto) fecha os
cadastros novos no bot e no POST /cadastro, independente da
ASSINATURAS_LIGADAS; LIMITE_TESTES_DIA (limite_testes_dia) limita quantos
testes grátis novos começam por dia de Brasília, contados na própria tabela
'assinaturas' (pode_comecar_teste e o INSERT ... SELECT do iniciar_teste).
O admin passa pelas duas, e o pagamento nunca olha nenhuma delas.

Pagamento em Telegram Stars (PRO-107, no fim do arquivo): a fatura de
assinatura (montar_fatura_pro, usada pela API com o HTTPX e pelo bot com o
PTB), a leitura do payload, a conferência do pre-checkout, a gravação do
pagamento e do reembolso (pela adicionar_pro e pela retirar_pro, D17), a
renovação (cancelar e reativar), os avisos de vencimento e o texto único dos
termos. A chamada à Bot API fica fora daqui (o módulo não conhece o Telegram).

A palavra "admin" nunca vai para o JSON (D7.4): o admin aparece como Pro, sem
datas, com selo "pro" e selo_tocavel false (D18).
"""
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from typing import List, NamedTuple, Optional

import recorrentes
import registro
from calculos import FUSO_BRASILIA
from registro import ErroRegistro

_log = logging.getLogger(__name__)

# --- CONSTANTES ---
DIAS_TESTE = 10                     # D4: o teste vale 10 dias exatos (240 horas)
PRECO_REAIS_APROXIMADO = 19         # D2/D12: "≈ R$ 19 (varia conforme a loja)"
LIMITE_IA_DIA_PADRAO = 17           # D5: registros com IA por dia, por pessoa
LIMITE_TESTES_DIA_PADRAO = 20       # PRO-109: testes grátis novos por dia (todas as pessoas juntas)
PRECO_PRO_STARS_PADRAO = 150        # D2
PRECO_PRO_STARS_MAXIMO = 10000      # máximo da Bot API para assinatura
DIAS_PRO_PADRAO = 30                # D16: /dar_pro sem número
DIAS_PRO_MAXIMO = 366               # D3/D16: de 1 a 366
DIAS_ESTENDER_TESTE_MAXIMO = 60     # /estender_teste: de 1 a 60
TOLERANCIA_RENOVACAO = timedelta(hours=24)   # D7.9: só no Pro de Stars que renova
LIMITE_LISTA_USUARIOS = 50          # /usuarios

FORMATO_MOMENTO = '%Y-%m-%d %H:%M:%S'
FORMATO_DATA = '%Y-%m-%d'

ESTADOS_VALIDOS = ('ativa', 'cancelada')   # 'cancelada' = não renova, vale até o fim
MODO_REEMBOLSO = 'reembolso'
MODO_DIAS_NAO_USADOS = 'dias_nao_usados'

# Tabelas e índices (aditivos; só CREATE ... IF NOT EXISTS). Executado pelo
# iniciar_banco() do bot.py, um comando de cada vez (comandos_criar_tabelas),
# e pelos testes (executescript). Não tem ponto e vírgula dentro dos comandos.
SQL_CRIAR_TABELAS = '''
CREATE TABLE IF NOT EXISTS assinaturas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    plano TEXT NOT NULL,
    origem TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fim TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'ativa',
    dias INTEGER NOT NULL,
    renovacao INTEGER NOT NULL DEFAULT 0,
    renova_em TEXT,
    pagamento_id TEXT,
    assinatura_ref TEXT,
    valor REAL,
    moeda TEXT,
    ultimo_aviso TEXT,
    criado_em TEXT NOT NULL,
    criado_por INTEGER
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_assinaturas_pagamento ON assinaturas (pagamento_id) WHERE pagamento_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_assinaturas_teste ON assinaturas (user_id) WHERE origem = 'teste';
CREATE INDEX IF NOT EXISTS ix_assinaturas_usuario ON assinaturas (user_id, fim);
CREATE TABLE IF NOT EXISTS uso_ia (
    user_id INTEGER NOT NULL,
    dia TEXT NOT NULL,
    chamadas INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, dia)
);
CREATE TABLE IF NOT EXISTS pagamentos_recebidos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pagamento_id TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    valor INTEGER NOT NULL,
    moeda TEXT NOT NULL,
    assinatura_ref TEXT,
    recorrente INTEGER NOT NULL DEFAULT 0,
    recebido_em TEXT NOT NULL,
    situacao TEXT NOT NULL DEFAULT 'recebido',
    erro TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_pagamentos_recebidos_pagamento ON pagamentos_recebidos (pagamento_id);
'''


def comandos_criar_tabelas():
    """Os comandos do SQL_CRIAR_TABELAS, um por item (o libsql executa um
    comando por vez)."""
    return [comando.strip() for comando in SQL_CRIAR_TABELAS.split(';') if comando.strip()]


# --- VARIÁVEIS DE AMBIENTE (lidas na hora, nunca no import) ---
_NUMERO = re.compile(r'[0-9]+')


def ler_admin_ids():
    """Os ids de admin da variável ADMIN_IDS (separados por vírgula, ponto e
    vírgula ou espaço). Entrada que não é número inteiro positivo é ignorada,
    com um aviso no log que conta quantas foram (sem o valor). Vazia ou
    ausente: ninguém é admin. Admin nunca vem do banco nem do cliente (D7.2)."""
    bruto = os.getenv('ADMIN_IDS') or ''
    ids = set()
    ignoradas = 0
    for parte in re.split(r'[,;\s]+', bruto.strip()):
        if not parte:
            continue
        if _NUMERO.fullmatch(parte) and int(parte) > 0:
            ids.add(int(parte))
        else:
            ignoradas += 1
    if ignoradas:
        _log.warning('ADMIN_IDS: %d entrada(s) ignorada(s) (não é número inteiro positivo)', ignoradas)
    return frozenset(ids)


def e_admin(user_id, admin_ids=None):
    """True se o id (inteiro de verdade, não bool) está no ADMIN_IDS."""
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return False
    if admin_ids is None:
        admin_ids = ler_admin_ids()
    return user_id in admin_ids


def assinaturas_ligadas():
    """ASSINATURAS_LIGADAS: '1' (ou 'true'/'sim') liga a checagem de plano.
    Ausente ou outro valor: desligada (o padrão; tudo como antes da PRO-106,
    menos o limite diário de IA, que vale sempre)."""
    return (os.getenv('ASSINATURAS_LIGADAS') or '').strip().lower() in ('1', 'true', 'sim')


def _ler_inteiro(nome, minimo, maximo, padrao):
    bruto = (os.getenv(nome) or '').strip()
    if _NUMERO.fullmatch(bruto):
        valor = int(bruto)
        if minimo <= valor <= (maximo if maximo is not None else valor):
            return valor
    return padrao


def limite_ia_dia():
    """LIMITE_IA_DIA (D5): inteiro de 1 para cima; ausente ou inválido vale 17."""
    return _ler_inteiro('LIMITE_IA_DIA', 1, None, LIMITE_IA_DIA_PADRAO)


def preco_pro_stars():
    """PRECO_PRO_STARS (D2): de 1 a 10.000 Stars; ausente ou inválido vale 150.
    É o preço das faturas novas (PRO-107: montar_fatura_pro e o
    pre-checkout), do preco.stars do GET /assinatura e do item 1 dos termos.
    Quem já assina continua pagando o valor do link original."""
    return _ler_inteiro('PRECO_PRO_STARS', 1, PRECO_PRO_STARS_MAXIMO, PRECO_PRO_STARS_PADRAO)


def limite_testes_dia():
    """LIMITE_TESTES_DIA (PRO-109): quantos testes grátis novos podem começar
    por dia de Brasília, somando todas as pessoas. Inteiro de 0 para cima (0 =
    nenhum teste novo hoje). Ausente ou vazia vale 20, sem log; inválida
    (texto, negativo, decimal) vale 20, com um aviso no log que cita só o
    NOME da variável, nunca o valor."""
    bruto = (os.getenv('LIMITE_TESTES_DIA') or '').strip()
    if not bruto:
        return LIMITE_TESTES_DIA_PADRAO
    if _NUMERO.fullmatch(bruto):
        return int(bruto)
    _log.warning('LIMITE_TESTES_DIA inválida (não é inteiro de 0 para cima): valendo o padrão')
    return LIMITE_TESTES_DIA_PADRAO


_CADASTRO_ABERTO_SIM = ('1', 'true', 'sim')
_CADASTRO_ABERTO_NAO = ('0', 'false', 'nao', 'não')


def cadastro_esta_aberto():
    """CADASTRO_ABERTO (PRO-109): False fecha os cadastros NOVOS (bot e POST
    /cadastro; quem já tem conta segue igual). Independente da
    ASSINATURAS_LIGADAS: sem a variável, nada muda. Ausente ou vazia: aberto.
    '1', 'true' ou 'sim': aberto; '0', 'false', 'nao' ou 'não': fechado (sem
    diferenciar maiúsculas, sem os espaços das pontas). Outro valor: aberto (o
    padrão), com um aviso no log que cita só o NOME da variável. O admin passa
    sempre (quem chama confere o e_admin)."""
    bruto = (os.getenv('CADASTRO_ABERTO') or '').strip().lower()
    if not bruto or bruto in _CADASTRO_ABERTO_SIM:
        return True
    if bruto in _CADASTRO_ABERTO_NAO:
        return False
    _log.warning('CADASTRO_ABERTO inválida (use 1 ou 0): valendo o padrão (aberto)')
    return True


# --- RELÓGIO ---
def agora_brasilia(agora=None):
    """'agora' em Brasília, sem os microssegundos (as datas gravadas têm
    segundos). Sem 'agora', o relógio de Brasília; com fuso, é convertido;
    sem fuso, é tomado como já sendo de Brasília."""
    if agora is None:
        agora = datetime.now(FUSO_BRASILIA)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=FUSO_BRASILIA)
    return agora.astimezone(FUSO_BRASILIA).replace(microsecond=0)


def _texto(momento):
    return momento.strftime(FORMATO_MOMENTO)


def _ler_momento(texto):
    if texto is None:
        return None
    return datetime.strptime(str(texto)[:19], FORMATO_MOMENTO).replace(tzinfo=FUSO_BRASILIA)


def data_texto(momento):
    """'AAAA-MM-DD' do momento (em Brasília), ou None."""
    return None if momento is None else momento.astimezone(FUSO_BRASILIA).strftime(FORMATO_DATA)


def dia_de_hoje(agora=None):
    """O dia de Brasília ('AAAA-MM-DD') usado pelo limite diário."""
    return agora_brasilia(agora).strftime(FORMATO_DATA)


def intervalo_do_dia(agora=None):
    """[início, fim) do dia de Brasília de 'agora', como texto do banco
    ('AAAA-MM-DD 00:00:00' e o 00:00:00 do dia seguinte): 23:59:59 ainda é o
    dia; 00:00:00 já é o dia novo. Usado pela contagem dos testes do dia
    (PRO-109)."""
    hoje = agora_brasilia(agora).date()
    amanha = hoje + timedelta(days=1)
    return f'{hoje.strftime(FORMATO_DATA)} 00:00:00', f'{amanha.strftime(FORMATO_DATA)} 00:00:00'


def dias_restantes(fim, agora=None):
    """Data do fim menos a data de hoje, em dias do calendário de Brasília,
    nunca abaixo de 0 (teste de 11/11 a 21/11: 10 no dia 11, 7 no dia 14, 0
    no dia 21). None sem fim. A mesma função dos avisos da PRO-107."""
    if fim is None:
        return None
    hoje = agora_brasilia(agora).date()
    return max(0, (fim.astimezone(FUSO_BRASILIA).date() - hoje).days)


def segundos_ate_meia_noite(agora=None):
    """Segundos (inteiro, para cima, no mínimo 1) até a próxima 00:00 de
    Brasília: o Retry-After do 429 limite_diario_ia. 23:59:30 -> 30."""
    if agora is None:
        agora = datetime.now(FUSO_BRASILIA)
    elif agora.tzinfo is None:
        agora = agora.replace(tzinfo=FUSO_BRASILIA)
    agora = agora.astimezone(FUSO_BRASILIA)
    amanha = agora.date() + timedelta(days=1)
    meia_noite = datetime(amanha.year, amanha.month, amanha.day, tzinfo=FUSO_BRASILIA)
    return max(1, math.ceil((meia_noite - agora).total_seconds()))


# --- LINHAS DA TABELA ---
_COLUNAS = ('id, user_id, plano, origem, inicio, fim, estado, dias, renovacao, renova_em, pagamento_id, '
            'assinatura_ref, valor, moeda, criado_em, criado_por')


class Linha(NamedTuple):
    """Uma linha de 'assinaturas', com as datas já como datetime de Brasília."""
    id: int
    user_id: int
    plano: str
    origem: str
    inicio: datetime
    fim: datetime
    estado: str
    dias: int
    renovacao: bool
    renova_em: Optional[datetime]
    pagamento_id: Optional[str]
    assinatura_ref: Optional[str]
    valor: Optional[float]
    moeda: Optional[str]
    criado_em: str
    criado_por: Optional[int]

    @property
    def valida(self):
        return self.estado in ESTADOS_VALIDOS


def _linha(bruta):
    (id_linha, user_id, plano, origem, inicio, fim, estado, dias, renovacao, renova_em, pagamento_id,
     assinatura_ref, valor, moeda, criado_em, criado_por) = bruta
    return Linha(id_linha, user_id, plano, origem, _ler_momento(inicio), _ler_momento(fim), estado, dias,
                 bool(renovacao), _ler_momento(renova_em), pagamento_id, assinatura_ref, valor, moeda,
                 criado_em, criado_por)


def ler_linhas(conn, user_id):
    """Todas as linhas da pessoa, por início e id."""
    linhas = conn.execute(
        f'SELECT {_COLUNAS} FROM assinaturas WHERE user_id = ? ORDER BY inicio, id', (user_id,)
    ).fetchall()
    return [_linha(bruta) for bruta in linhas]


def buscar_linha(conn, user_id, linha_id):
    bruta = conn.execute(
        f'SELECT {_COLUNAS} FROM assinaturas WHERE id = ? AND user_id = ?', (linha_id, user_id)
    ).fetchone()
    return None if bruta is None else _linha(bruta)


def _pros_validas(linhas):
    return sorted((l for l in linhas if l.plano == 'pro' and l.valida), key=lambda l: (l.inicio, l.id))


def _linha_teste(linhas):
    return next((l for l in linhas if l.origem == 'teste'), None)


def _fim_unico(linhas):
    """O fim único do Pro (D17): o maior 'fim' entre as linhas Pro válidas."""
    return max((l.fim for l in _pros_validas(linhas)), default=None)


def fim_do_pro(conn, user_id):
    """O fim único do Pro da pessoa (D17), ou None se ela nunca teve Pro
    válido. Pode estar no passado (Pro vencido)."""
    return _fim_unico(ler_linhas(conn, user_id))


def _renova(linha, agora):
    """A linha de Stars que o Telegram ainda vai renovar: ativa, com
    renovacao = 1 e o renova_em sem ter passado de 24 horas (D7.9)."""
    return (linha is not None and linha.origem == 'stars' and linha.estado == 'ativa' and linha.renovacao
            and linha.renova_em is not None and agora < linha.renova_em + TOLERANCIA_RENOVACAO)


def _stars_em_vigor(validas, agora):
    """A linha de Stars em vigor (contrato do GET /assinatura): a de Stars
    mais recente entre as válidas (não reembolsada), cujo próprio fim (mais a
    tolerância de 24 horas, se ela renova) ainda não passou. None sem ela.
    Usada pelo selo e pela folha e, na PRO-107, pela renovação."""
    stars = [l for l in validas if l.origem == 'stars']
    if not stars:
        return None
    recente = stars[-1]
    fim_efetivo = recente.fim + (TOLERANCIA_RENOVACAO if _renova(recente, agora) else timedelta(0))
    return recente if agora < fim_efetivo else None


def _blocos(linhas):
    """Períodos contínuos [início, fim) das linhas (as que se tocam ou se
    cruzam viram um só), em ordem."""
    blocos = []
    for linha in sorted(linhas, key=lambda l: (l.inicio, l.id)):
        if linha.fim <= linha.inicio:
            continue
        if blocos and linha.inicio <= blocos[-1][1]:
            blocos[-1][1] = max(blocos[-1][1], linha.fim)
        else:
            blocos.append([linha.inicio, linha.fim])
    return blocos


# --- SITUAÇÃO DE ACESSO ---
class Situacao(NamedTuple):
    """O que vale para a pessoa agora. 'estado': 'sem_plano', 'teste', 'pro',
    'teste_vencido' ou 'pro_vencido'. Os campos do selo e da folha (ajuste de
    2026-11-12) já vêm calculados; 'selo' ('gratis', 'pro', 'assinar' nos
    vencidos, ou None) aqui não olha a ASSINATURAS_LIGADAS
    (montar_bloco_assinatura apaga o selo com a chave desligada)."""
    estado: str
    tem_acesso: bool
    inicio: Optional[datetime]
    fim: Optional[datetime]
    renovacao: Optional[bool]
    teste_disponivel: bool
    teste_previsto: Optional[tuple]      # (início, fim) do teste que começaria agora
    selo: Optional[str]
    selo_tocavel: bool
    dias_restantes: Optional[int]
    valor_stars: Optional[int]
    proxima_renovacao: Optional[datetime]
    renovacao_cancelada: bool
    vale_ate: Optional[datetime]


def calcular_situacao(linhas, agora=None, admin=False):
    """Situação da pessoa a partir das linhas dela (sem banco). Regras:

    - admin: 'pro', com acesso, sem datas nem renovação, selo 'pro' sem toque
      (D7.4 e D18); nada vem das linhas, menos o teste_disponivel.
    - Pro ativo: existe linha Pro válida ('ativa' ou 'cancelada') que começou
      e o agora é antes do fim único (D17). Tolerância de 24 horas sobre o fim
      único só quando a linha de Stars mais recente renova (D7.9) e a cobrança
      dela (renova_em) não fica depois do fim único.
    - senão, teste ativo: a linha do teste vale e cobre agora ('inicio <= agora
      < fim': vence na hora exata).
    - senão, vencido: o período (Pro ou teste) que terminou por último, com
      selo 'assinar' (tocável; modo somente leitura); sem nenhum período,
      'sem_plano', sem selo.
    Linha de Stars em vigor (valor_stars, proxima_renovacao e
    renovacao_cancelada): a de Stars mais recente, não reembolsada, cujo
    próprio fim (mais a tolerância, se renova) ainda não passou."""
    agora = agora_brasilia(agora)
    teste = _linha_teste(linhas)
    teste_disponivel = teste is None
    teste_previsto = (agora, agora + timedelta(days=DIAS_TESTE)) if teste_disponivel else None
    base = dict(teste_disponivel=teste_disponivel, teste_previsto=teste_previsto)

    if admin:
        return Situacao('pro', True, None, None, None, selo='pro', selo_tocavel=False, dias_restantes=None,
                        valor_stars=None, proxima_renovacao=None, renovacao_cancelada=False, vale_ate=None,
                        **base)

    validas = _pros_validas(linhas)
    fim_unico = max((l.fim for l in validas), default=None)
    stars = [l for l in validas if l.origem == 'stars']
    stars_recente = stars[-1] if stars else None
    limite_pro = fim_unico
    # A tolerância cobre a renovação que atrasa: só quando a cobrança do
    # Telegram vence junto com o fim único, ou antes dele. Depois de um
    # reembolso que encolheu o Pro, a próxima cobrança fica depois do fim e
    # não estica nada.
    if (fim_unico is not None and _renova(stars_recente, agora)
            and stars_recente.renova_em <= fim_unico):
        limite_pro = fim_unico + TOLERANCIA_RENOVACAO
    pro_ativo = limite_pro is not None and agora < limite_pro and any(l.inicio <= agora for l in validas)

    if pro_ativo:
        bloco = next((b for b in _blocos(validas) if b[1] == fim_unico), [validas[0].inicio, fim_unico])
        em_vigor = _stars_em_vigor(validas, agora)
        proxima = em_vigor.renova_em if em_vigor is not None and _renova(em_vigor, agora) else None
        valor_stars = None
        if em_vigor is not None and em_vigor.valor is not None:
            valor_stars = int(round(em_vigor.valor))
        return Situacao('pro', True, bloco[0], fim_unico, proxima is not None, selo='pro', selo_tocavel=True,
                        dias_restantes=dias_restantes(fim_unico, agora), valor_stars=valor_stars,
                        proxima_renovacao=proxima, renovacao_cancelada=em_vigor is not None and proxima is None,
                        vale_ate=fim_unico, **base)

    if teste is not None and teste.valida and teste.inicio <= agora < teste.fim:
        return Situacao('teste', True, teste.inicio, teste.fim, False, selo='gratis', selo_tocavel=True,
                        dias_restantes=dias_restantes(teste.fim, agora), valor_stars=None, proxima_renovacao=None,
                        renovacao_cancelada=False, vale_ate=None, **base)

    blocos_pro = _blocos([l for l in linhas if l.plano == 'pro'])
    ultimo_pro = blocos_pro[-1] if blocos_pro else None
    ultimo_teste = (teste.inicio, teste.fim) if teste is not None and teste.fim > teste.inicio else None
    sem_folha = dict(dias_restantes=None, valor_stars=None, proxima_renovacao=None, renovacao_cancelada=False,
                     vale_ate=None)
    # Vencido (modo somente leitura, PRO-106): selo "assinar", que leva à
    # folha de assinatura. Sem plano: sem selo.
    vencido = dict(selo='assinar', selo_tocavel=True, **sem_folha)
    if ultimo_pro is not None and (ultimo_teste is None or ultimo_pro[1] >= ultimo_teste[1]):
        return Situacao('pro_vencido', False, ultimo_pro[0], ultimo_pro[1], False, **vencido, **base)
    if ultimo_teste is not None:
        return Situacao('teste_vencido', False, ultimo_teste[0], ultimo_teste[1], False, **vencido, **base)
    return Situacao('sem_plano', False, None, None, False, selo=None, selo_tocavel=False, **sem_folha, **base)


def situacao_acesso(conn, user_id, agora=None, admin=None):
    """calcular_situacao com as linhas do banco. 'admin' None: pelo ADMIN_IDS."""
    if admin is None:
        admin = e_admin(user_id)
    return calcular_situacao(ler_linhas(conn, user_id), agora, admin)


def tem_acesso(conn, user_id, agora=None, admin=None):
    """True para o admin (sem consultar o banco) ou para quem tem teste ou Pro
    valendo agora."""
    if admin is None:
        admin = e_admin(user_id)
    if admin:
        return True
    return situacao_acesso(conn, user_id, agora, admin=False).tem_acesso


def usuario_existe(conn, user_id):
    """True se o id tem linha em 'usuarios' ou em 'assinaturas' (o teste
    começa antes do cadastro, D9)."""
    return conn.execute(
        'SELECT 1 FROM usuarios WHERE user_id = ? UNION ALL SELECT 1 FROM assinaturas WHERE user_id = ? LIMIT 1',
        (user_id, user_id)
    ).fetchone() is not None


def tem_cadastro(conn, user_id):
    return conn.execute('SELECT 1 FROM usuarios WHERE user_id = ?', (user_id,)).fetchone() is not None


def montar_bloco_assinatura(conn, user_id, agora=None, admin=None, ligada=None):
    """O JSON do GET /assinatura e do bloco "assinatura" do GET /me (e, na
    PRO-107, da resposta do POST /assinaturas/renovacao): uma função só.
    Datas em 'AAAA-MM-DD' (Brasília). Nunca leva a palavra "admin".
    'modo_leitura' (PRO-106, modo somente leitura): true só com a chave
    ligada, para quem não é admin e está vencido (teste ou Pro): a tela mostra
    os dados, sem registrar.
    'cadastro_aberto' (PRO-109): true para o admin; senão, o
    CADASTRO_ABERTO (cadastro_esta_aberto), independente da chave 'ligada'
    (a escolha inicial do Mini App apaga o "Sou Novo Por Aqui" com false)."""
    agora = agora_brasilia(agora)
    if admin is None:
        admin = e_admin(user_id)
    if ligada is None:
        ligada = assinaturas_ligadas()
    situacao = situacao_acesso(conn, user_id, agora, admin=admin)
    selo = situacao.selo if ligada else None
    previsto = situacao.teste_previsto
    return {
        "ligada": ligada,
        "estado": situacao.estado,
        "inicio": data_texto(situacao.inicio),
        "fim": data_texto(situacao.fim),
        "renovacao": situacao.renovacao,
        "teste_disponivel": situacao.teste_disponivel,
        "teste_previsto": None if previsto is None else {"inicio": data_texto(previsto[0]),
                                                          "fim": data_texto(previsto[1])},
        "preco": {"stars": preco_pro_stars(), "reais_aproximado": PRECO_REAIS_APROXIMADO},
        "limite_ia_dia": None if admin else limite_ia_dia(),
        "uso_ia_hoje": uso_ia_hoje(conn, user_id, agora),
        "selo": selo,
        "selo_tocavel": selo is not None and situacao.selo_tocavel,
        "dias_restantes": situacao.dias_restantes,
        "valor_stars": situacao.valor_stars,
        "proxima_renovacao": data_texto(situacao.proxima_renovacao),
        "renovacao_cancelada": situacao.renovacao_cancelada,
        "vale_ate": data_texto(situacao.vale_ate),
        "modo_leitura": bool(ligada and not admin and situacao.estado in ('teste_vencido', 'pro_vencido')),
        "cadastro_aberto": True if admin else cadastro_esta_aberto(),    # PRO-109
    }


# --- GRAVAÇÃO COM NOVA TENTATIVA ---
def _gravar_uma_vez(conn, operacao, ja_gravado):
    """Roda 'operacao' (grava e faz o commit) com a regra de banco ocupado da
    registro.gravar_com_chave: em qualquer exceção, desfaz e chama
    'ja_gravado()' (o que o pedido queria criar já existe? É o caso do índice
    único recusando o segundo de dois pedidos juntos); se existe, devolve o
    que ela devolveu. Senão: ErroRegistro sobe na hora; erro passageiro (ou a
    própria consulta falhou) espera e tenta tudo de novo, até 4 vezes, e
    depois 'banco_ocupado'; outro erro sobe como veio."""
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
            try:
                existente = ja_gravado()
                passageiro = registro.erro_passageiro(erro)
            except ErroRegistro:
                raise
            except Exception:
                existente = None
                passageiro = True
            if existente is not None:
                return existente
            if not passageiro:
                raise
            if tentativa == tentativas - 1:
                raise ErroRegistro('banco_ocupado') from None
            registro._esperar(esperas[tentativa])


# --- TESTE GRÁTIS ---
class TesteIniciado(NamedTuple):
    inicio: datetime
    fim: datetime
    repetido: bool


# Quantos testes começaram no dia (PRO-109): as linhas 'teste' com início no
# dia de Brasília. Contagem derivada da própria tabela (sem tabela nova).
_SQL_TESTES_DO_DIA = "SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste' AND inicio >= ? AND inicio < ?"


def testes_do_dia(conn, agora=None):
    """Quantos testes grátis começaram no dia de Brasília de 'agora'."""
    return int(conn.execute(_SQL_TESTES_DO_DIA, intervalo_do_dia(agora)).fetchone()[0])


def pode_comecar_teste(conn, agora=None, limite=None):
    """True se ainda há vaga de teste novo no dia de Brasília de 'agora': os
    testes que começaram no dia são menos que o 'limite' (None:
    LIMITE_TESTES_DIA). Só uma consulta, para quem quer saber antes; quem
    decide de verdade é o INSERT ... SELECT do iniciar_teste (dois pedidos
    juntos no último lugar: só um passa)."""
    if limite is None:
        limite = limite_testes_dia()
    return testes_do_dia(conn, agora) < limite


def iniciar_teste(conn, user_id, agora=None, admin=None, criado_por=None, limite=None):
    """POST /assinaturas/teste e o botão "Testar Grátis por 10 Dias" do bot:
    início = agora, fim = agora + 10 dias exatos, uma vez na vida (o índice
    único ux_assinaturas_teste recusa o segundo, mesmo com dois toques
    juntos). Não exige cadastro em 'usuarios' (D9).

    - admin, ou Pro ativo: ErroRegistro('plano_ativo');
    - já existe teste ativo (toque duplo): devolve o dele, repetido=True
      (sem olhar o limite do dia);
    - o teste já foi usado e venceu: ErroRegistro('teste_ja_usado');
    - PRO-109: o dia já tem 'limite' testes novos (None: LIMITE_TESTES_DIA):
      ErroRegistro('vagas_teste_esgotadas'). A conta e a gravação são UM
      comando só (INSERT ... SELECT ... WHERE (contagem do dia) < limite, o
      padrão do adicionar_pro): dois pedidos juntos no último lugar nunca
      passam os dois;
    - banco ocupado em todas as tentativas: ErroRegistro('banco_ocupado')."""
    agora = agora_brasilia(agora)
    if admin is None:
        admin = e_admin(user_id)
    if admin:
        raise ErroRegistro('plano_ativo')
    if limite is None:
        limite = limite_testes_dia()
    inicio_dia, fim_dia = intervalo_do_dia(agora)

    def resultado_existente(linhas):
        teste = _linha_teste(linhas)
        if teste is None:
            return None
        if teste.valida and teste.inicio <= agora < teste.fim:
            return TesteIniciado(teste.inicio, teste.fim, True)
        raise ErroRegistro('teste_ja_usado')

    def operacao():
        linhas = ler_linhas(conn, user_id)
        if calcular_situacao(linhas, agora).estado == 'pro':
            raise ErroRegistro('plano_ativo')
        existente = resultado_existente(linhas)
        if existente is not None:
            return existente
        fim = agora + timedelta(days=DIAS_TESTE)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, estado, dias, renovacao, criado_em, '
            "criado_por) SELECT ?, 'teste', 'teste', ?, ?, 'ativa', ?, 0, ?, ? "
            f'WHERE ({_SQL_TESTES_DO_DIA}) < ?',
            (user_id, _texto(agora), _texto(fim), DIAS_TESTE, _texto(agora), criado_por, inicio_dia, fim_dia, limite)
        )
        inseridas = cursor.rowcount
        conn.commit()
        if inseridas is not None and inseridas > 0:
            return TesteIniciado(agora, fim, False)
        # Nada gravado: o dia já tem o limite. Antes de recusar, confere o
        # toque duplo que ganhou a corrida (o outro pedido gravou o teste
        # desta pessoa no último lugar): aí devolve o dele, repetido=True.
        existente = resultado_existente(ler_linhas(conn, user_id))
        if existente is not None:
            return existente
        raise ErroRegistro('vagas_teste_esgotadas')

    return _gravar_uma_vez(conn, operacao, lambda: resultado_existente(ler_linhas(conn, user_id)))


# --- PRO (D17) ---
class ProLancado(NamedTuple):
    linha: Linha                 # a linha criada (ou a já existente, se repetido)
    fim_do_pro: datetime         # o fim único depois do lançamento
    repetido: bool               # o mesmo pagamento_id já tinha sido gravado


def _validar_dias(dias, maximo, padrao=None):
    """Dias como inteiro de 1 a 'maximo'. None vale 'padrao' (se houver);
    texto só com algarismos é aceito ("30"); bool, 0, negativo, acima do
    máximo e texto qualquer: ErroRegistro('dias_invalidos')."""
    if dias is None and padrao is not None:
        return padrao
    if isinstance(dias, str) and _NUMERO.fullmatch(dias.strip()):
        dias = int(dias.strip())
    if isinstance(dias, bool) or not isinstance(dias, int) or not 1 <= dias <= maximo:
        raise ErroRegistro('dias_invalidos')
    return dias


def adicionar_pro(conn, user_id, dias, origem, agora=None, criado_por=None, renovacao=False, renova_em=None,
                  pagamento_id=None, assinatura_ref=None, valor=None, moeda=None):
    """O ÚNICO jeito de criar linha Pro: o /dar_pro agora e os pagamentos da
    PRO-107. Regra única de soma (D17): inicio = o maior entre o fim único
    atual e agora; fim = inicio + 'dias'. A conta e o INSERT são UM comando só
    (INSERT ... SELECT com a subconsulta do fim único): dois lançamentos
    juntos nunca leem o mesmo fim (nenhum período sobreposto).

    'origem': 'manual' (Pro dado; 'criado_por' = id do admin) ou 'stars' (com
    'renova_em' = a próxima cobrança do Telegram, 'renovacao', 'pagamento_id',
    'assinatura_ref', 'valor' e 'moeda'). O mesmo pagamento_id nunca vale duas
    vezes (índice único): repetido, devolve a linha original com
    repetido=True, sem gravar."""
    agora = agora_brasilia(agora)
    dias = _validar_dias(dias, DIAS_PRO_MAXIMO)
    if origem not in ('manual', 'stars', 'pix'):
        raise ValueError(f'origem inválida: {origem!r}')
    if isinstance(renova_em, datetime):
        renova_em = _texto(agora_brasilia(renova_em))

    def ja_gravado():
        if pagamento_id is None:
            return None
        bruta = conn.execute(f'SELECT {_COLUNAS} FROM assinaturas WHERE pagamento_id = ?', (pagamento_id,)).fetchone()
        if bruta is None:
            return None
        linha = _linha(bruta)
        return ProLancado(linha, fim_do_pro(conn, linha.user_id), True)

    def operacao():
        existente = ja_gravado()
        if existente is not None:
            return existente
        agora_txt = _texto(agora)
        cursor = conn.cursor()
        cursor.execute(
            '''INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, estado, dias, renovacao, renova_em,
                                        pagamento_id, assinatura_ref, valor, moeda, criado_em, criado_por)
               SELECT ?, 'pro', ?, base.inicio, datetime(base.inicio, ?), 'ativa', ?, ?, ?, ?, ?, ?, ?, ?, ?
               FROM (SELECT MAX(?, COALESCE((SELECT MAX(fim) FROM assinaturas
                                             WHERE user_id = ? AND plano = 'pro' AND estado IN ('ativa', 'cancelada')),
                                            ?)) AS inicio) AS base''',
            (user_id, origem, f'+{dias} days', dias, 1 if renovacao else 0, renova_em, pagamento_id, assinatura_ref,
             valor, moeda, agora_txt, criado_por, agora_txt, user_id, agora_txt)
        )
        linha_id = cursor.lastrowid
        conn.commit()
        linha = buscar_linha(conn, user_id, linha_id)
        return ProLancado(linha, fim_do_pro(conn, user_id), False)

    return _gravar_uma_vez(conn, operacao, ja_gravado)


class ProRetirado(NamedTuple):
    alterou: bool                 # alguma linha mudou
    linhas_retiradas: List[int]   # ids das linhas marcadas (reembolsada ou encerrada)
    fim_do_pro: Optional[datetime]  # o fim único depois (None sem Pro válido)
    tem_pro_pago: bool            # sobrou linha Pro válida que não é 'manual' cobrindo agora ou depois


def retirar_pro(conn, user_id, modo, agora=None, pagamento_id=None, admin_id=None):
    """Tira dias do Pro (D17), recuando as linhas que vinham depois, sem
    deixar período sobreposto. Numa transação só.

    modo 'reembolso' (PRO-107, pelo pagamento_id): a linha fica 'reembolsada'
    e sai da conta, com fim = agora se estava em uso (fim = início se ainda
    não tinha começado; sem mudança se já tinha acabado). As linhas válidas
    encadeadas logo depois dela recuam os dias dela inteiros: o fim único cai
    exatos esses dias; se cair antes de agora, o Pro acaba agora.

    modo 'dias_nao_usados' (/tirar_pro): cada linha 'manual' em vigor fica
    'encerrada' com fim = agora; cada 'manual' futura fica 'encerrada' com fim
    = início; as linhas seguintes recuam o que foi tirado. As 'manual' que já
    acabaram ficam como estão. 'admin_id' vai para o log (UPDATE não tem
    criado_por)."""
    if modo not in (MODO_REEMBOLSO, MODO_DIAS_NAO_USADOS):
        raise ValueError(f'modo inválido: {modo!r}')
    agora = agora_brasilia(agora)

    def operacao():
        # Um UPDATE sem efeito primeiro: pega a trava de escrita antes de ler,
        # para outro lançamento não mudar o encadeamento no meio da conta.
        conn.execute("UPDATE assinaturas SET estado = estado WHERE user_id = ? AND plano = 'pro'", (user_id,))
        linhas = ler_linhas(conn, user_id)
        validas = _pros_validas(linhas)
        mudancas = {}      # id -> (inicio, fim, estado)
        retiradas = []
        deslocamento = timedelta(0)
        fim_anterior_original = None

        if modo == MODO_REEMBOLSO:
            alvo = next((l for l in linhas if l.pagamento_id is not None and l.pagamento_id == pagamento_id), None)
            if alvo is None or not alvo.valida or alvo.plano != 'pro':
                registro._desfazer(conn)
                return ProRetirado(False, [], _fim_unico(linhas), _tem_pro_pago(linhas, agora))

        for linha in validas:
            # Só recua o que vem encadeado: uma linha que começa depois de um
            # buraco (o Pro tinha vencido antes) não é empurrada.
            if fim_anterior_original is not None and linha.inicio > fim_anterior_original:
                deslocamento = timedelta(0)
            fim_anterior_original = linha.fim
            inicio = linha.inicio - deslocamento
            fim = linha.fim - deslocamento

            if modo == MODO_REEMBOLSO and linha.id == alvo.id:
                novo_fim = min(max(agora, linha.inicio), linha.fim)
                mudancas[linha.id] = (linha.inicio, novo_fim, 'reembolsada')
                retiradas.append(linha.id)
                deslocamento = deslocamento + (linha.fim - linha.inicio)
                continue
            if modo == MODO_DIAS_NAO_USADOS and linha.origem == 'manual' and fim > agora:
                novo_fim = agora if inicio <= agora else inicio
                mudancas[linha.id] = (inicio, novo_fim, 'encerrada')
                retiradas.append(linha.id)
                deslocamento = deslocamento + (fim - novo_fim)
                continue
            if inicio != linha.inicio or fim != linha.fim:
                mudancas[linha.id] = (inicio, fim, linha.estado)

        if not retiradas:
            registro._desfazer(conn)
            return ProRetirado(False, [], _fim_unico(linhas), _tem_pro_pago(linhas, agora))

        # "Se cair antes de agora, o Pro acaba agora": a linha que valia agora
        # ou depois e foi empurrada para o passado termina agora; as
        # seguintes não começam antes do fim da anterior (sem sobreposição).
        fim_anterior_novo = None
        for linha in validas:
            if linha.id in retiradas:
                continue
            inicio, fim, estado = mudancas.get(linha.id, (linha.inicio, linha.fim, linha.estado))
            if linha.fim > agora and fim < agora:
                fim = agora
            if fim_anterior_novo is not None and inicio < fim_anterior_novo and linha.fim > agora:
                inicio = fim_anterior_novo
                fim = max(fim, inicio)
            if (inicio, fim) != (linha.inicio, linha.fim):
                mudancas[linha.id] = (inicio, fim, estado)
            fim_anterior_novo = fim

        for linha_id, (inicio, fim, estado) in mudancas.items():
            conn.execute('UPDATE assinaturas SET inicio = ?, fim = ?, estado = ? WHERE id = ? AND user_id = ?',
                         (_texto(inicio), _texto(fim), estado, linha_id, user_id))
        conn.commit()
        _log.info('Pro retirado (%s): user_id=%s linhas=%s admin_id=%s', modo, user_id, retiradas, admin_id)
        depois = ler_linhas(conn, user_id)
        return ProRetirado(True, retiradas, _fim_unico(depois), _tem_pro_pago(depois, agora))

    return recorrentes._com_novas_tentativas(conn, operacao)


def _tem_pro_pago(linhas, agora):
    """Sobrou linha Pro válida que não é Pro dado, valendo agora ou depois."""
    return any(l.origem != 'manual' and l.fim > agora for l in _pros_validas(linhas))


# --- COMANDOS DE ADMIN ---
def _exigir_usuario(conn, user_id):
    if not usuario_existe(conn, user_id):
        raise ErroRegistro('usuario_nao_encontrado')


def dar_pro_manual(conn, user_id, dias, admin_id, agora=None):
    """/dar_pro <id> [dias] (D16 e D19): Pro dado de 1 a 366 dias (sem
    número, 30), pela adicionar_pro (origem 'manual', criado_por = admin).
    Soma ao fim único: se a pessoa já tem Pro, é assim que se estende.
    'usuario_nao_encontrado' sem linha em 'usuarios' nem em 'assinaturas';
    'dias_invalidos' fora de 1 a 366. Devolve ProLancado."""
    dias = _validar_dias(dias, DIAS_PRO_MAXIMO, padrao=DIAS_PRO_PADRAO)
    _exigir_usuario(conn, user_id)
    return adicionar_pro(conn, user_id, dias, 'manual', agora, criado_por=admin_id)


def tirar_pro_manual(conn, user_id, admin_id, agora=None):
    """/tirar_pro <id>: retirar_pro no modo 'dias não usados'. Devolve
    ProRetirado (alterou=False: a pessoa não tem Pro dado em vigor ou futuro;
    tem_pro_pago: sobrou Pro pago, que só sai com o /reembolsar da PRO-107)."""
    _exigir_usuario(conn, user_id)
    return retirar_pro(conn, user_id, MODO_DIAS_NAO_USADOS, agora, admin_id=admin_id)


def estender_teste(conn, user_id, dias, admin_id, agora=None):
    """/estender_teste <id> <dias>: soma de 1 a 60 dias ao fim do teste
    (vencido ou não), na mesma linha. 'sem_teste' se a pessoa nunca testou.
    Devolve o fim novo."""
    dias = _validar_dias(dias, DIAS_ESTENDER_TESTE_MAXIMO)
    _exigir_usuario(conn, user_id)

    def operacao():
        teste = _linha_teste(ler_linhas(conn, user_id))
        if teste is None:
            raise ErroRegistro('sem_teste')
        fim = teste.fim + timedelta(days=dias)
        conn.execute('UPDATE assinaturas SET fim = ?, dias = dias + ? WHERE id = ? AND user_id = ?',
                     (_texto(fim), dias, teste.id, user_id))
        conn.commit()
        _log.info('Teste estendido: user_id=%s dias=%s admin_id=%s', user_id, dias, admin_id)
        return fim

    return recorrentes._com_novas_tentativas(conn, operacao)


def reiniciar_teste(conn, user_id, admin_id, agora=None):
    """/reiniciar_teste <id>: início = agora e fim = agora + 10 dias, na mesma
    linha (cria a linha, com criado_por = admin, se a pessoa nunca testou).
    Devolve TesteIniciado (repetido=False)."""
    agora = agora_brasilia(agora)
    _exigir_usuario(conn, user_id)
    fim = agora + timedelta(days=DIAS_TESTE)

    def operacao():
        teste = _linha_teste(ler_linhas(conn, user_id))
        if teste is None:
            conn.execute(
                'INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, estado, dias, renovacao, criado_em, '
                "criado_por) VALUES (?, 'teste', 'teste', ?, ?, 'ativa', ?, 0, ?, ?)",
                (user_id, _texto(agora), _texto(fim), DIAS_TESTE, _texto(agora), admin_id)
            )
        else:
            conn.execute("UPDATE assinaturas SET inicio = ?, fim = ?, estado = 'ativa', dias = ? "
                         "WHERE id = ? AND user_id = ?",
                         (_texto(agora), _texto(fim), DIAS_TESTE, teste.id, user_id))
        conn.commit()
        _log.info('Teste reiniciado: user_id=%s admin_id=%s', user_id, admin_id)
        return TesteIniciado(agora, fim, False)

    return recorrentes._com_novas_tentativas(conn, operacao)


class UsuarioComPlano(NamedTuple):
    user_id: int
    nome: Optional[str]
    estado: str                  # o da Situacao, ou 'admin' (só no bot, nunca na API)
    fim: Optional[datetime]      # fim único do Pro, fim do teste ou do último período
    origem: Optional[str]        # origem da linha mais recente


def listar_usuarios_com_plano(conn, agora=None, limite=LIMITE_LISTA_USUARIOS, admin_ids=None):
    """/usuarios: até 'limite' contas (de 'usuarios' e de 'assinaturas'),
    as de validade mais próxima primeiro: teste e Pro ativos pelo fim
    crescente; depois os vencidos (o mais recente primeiro); depois sem plano;
    os admins por último."""
    agora = agora_brasilia(agora)
    if admin_ids is None:
        admin_ids = ler_admin_ids()
    nomes = dict(conn.execute('SELECT user_id, nome FROM usuarios').fetchall())
    por_usuario = {}
    for bruta in conn.execute(f'SELECT {_COLUNAS} FROM assinaturas ORDER BY inicio, id').fetchall():
        linha = _linha(bruta)
        por_usuario.setdefault(linha.user_id, []).append(linha)
    itens = []
    for user_id in set(nomes) | set(por_usuario):
        linhas = por_usuario.get(user_id, [])
        recente = max(linhas, key=lambda l: (l.criado_em or '', l.id), default=None)
        origem = None if recente is None else recente.origem
        if e_admin(user_id, admin_ids):
            itens.append((3, 0, UsuarioComPlano(user_id, nomes.get(user_id), 'admin', None, origem)))
            continue
        situacao = calcular_situacao(linhas, agora)
        item = UsuarioComPlano(user_id, nomes.get(user_id), situacao.estado, situacao.fim, origem)
        if situacao.tem_acesso:
            itens.append((0, situacao.fim.timestamp(), item))
        elif situacao.fim is not None:
            itens.append((1, -situacao.fim.timestamp(), item))
        else:
            itens.append((2, 0, item))
    itens.sort(key=lambda t: (t[0], t[1], t[2].user_id))
    return [item for _, _, item in itens[:limite]]


# --- LIMITE DIÁRIO DE REGISTROS COM IA (D5) ---
def uso_ia_hoje(conn, user_id, agora=None):
    """Quantos registros com IA a pessoa reservou hoje (dia de Brasília)."""
    linha = conn.execute('SELECT chamadas FROM uso_ia WHERE user_id = ? AND dia = ?',
                         (user_id, dia_de_hoje(agora))).fetchone()
    return 0 if linha is None else int(linha[0])


def reservar_uso_ia(conn, user_id, agora=None, limite=None, admin=None):
    """Reserva UMA vaga do dia antes de chamar a IA, num comando só ("soma 1
    se ainda for menor que o limite"), sem janela entre conferir e gravar:
    dois pedidos juntos nunca passam do limite. Devolve o dia reservado
    ('AAAA-MM-DD', para a devolver_uso_ia) ou None se não há vaga (aí quem
    chama recusa SEM chamar a IA). O admin nunca é recusado, mas a contagem
    dele é gravada (para medir). 'limite' None: LIMITE_IA_DIA; 'admin' None:
    pelo ADMIN_IDS. Banco ocupado: novas tentativas e 'banco_ocupado'."""
    dia = dia_de_hoje(agora)
    if admin is None:
        admin = e_admin(user_id)
    if limite is None:
        limite = limite_ia_dia()

    def operacao():
        if admin:
            cursor = conn.execute(
                'INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, ?, 1) '
                'ON CONFLICT (user_id, dia) DO UPDATE SET chamadas = chamadas + 1',
                (user_id, dia))
        else:
            cursor = conn.execute(
                'INSERT INTO uso_ia (user_id, dia, chamadas) VALUES (?, ?, 1) '
                'ON CONFLICT (user_id, dia) DO UPDATE SET chamadas = chamadas + 1 WHERE chamadas < ?',
                (user_id, dia, limite))
        mudou = cursor.rowcount
        conn.commit()
        return dia if mudou and mudou > 0 else None

    return recorrentes._com_novas_tentativas(conn, operacao)


def devolver_uso_ia(conn, user_id, dia):
    """Devolve a vaga reservada no 'dia' quando a IA não respondeu (fora do
    ar) ou o pedido parou antes de chegar à IA. Nunca fica abaixo de 0."""
    def operacao():
        conn.execute('UPDATE uso_ia SET chamadas = chamadas - 1 WHERE user_id = ? AND dia = ? AND chamadas > 0',
                     (user_id, dia))
        conn.commit()

    recorrentes._com_novas_tentativas(conn, operacao)


# =============================================================================
# PAGAMENTO EM TELEGRAM STARS (PRO-107)
# =============================================================================
# O Mini App (ou o bot) pede a fatura; o Telegram cobra; o bot só grava o Pro
# quando chega o successful_payment conferido (D1). Nenhuma rota grava
# pagamento. As chamadas à Bot API (createInvoiceLink, editUserStarSubscription
# e refundStarPayment) ficam na API (HTTPX) e no bot (PTB); aqui ficam só as
# regras, com a conexão e o "agora" por parâmetro.
DIAS_PAGAMENTO_STARS = 30                   # cada cobrança soma 30 dias (D17)
PERIODO_ASSINATURA_SEGUNDOS = 2592000       # subscription_period: a Bot API só aceita 30 dias
MOEDA_STARS = 'XTR'
PREFIXO_PAYLOAD = 'pro1'
TAMANHO_MAXIMO_PAYLOAD = 128                # bytes (Bot API)
TITULO_FATURA = 'L1d3r Pro'
DESCRICAO_FATURA = 'Plano Pro do L1d3r por 30 dias. Renova sozinho; cancele quando quiser.'
ROTULO_ITEM_FATURA = 'Plano Pro (30 dias)'

# payload: pro1:<user_id>:<stars>:<8 caracteres aleatórios>. O sufixo vem do
# os.urandom (4 bytes em hexadecimal): cada fatura é uma assinatura diferente,
# e as renovações trazem o mesmo payload (vira o assinatura_ref).
_PAYLOAD = re.compile(r'pro1:([1-9][0-9]{0,18}):([1-9][0-9]{0,4}):([0-9a-f]{8})')


def _inteiro_de_verdade(valor):
    return isinstance(valor, int) and not isinstance(valor, bool)


def montar_fatura_pro(user_id, preco=None, sufixo=None):
    """Os campos da fatura de assinatura do Plano Pro, com os nomes da Bot API
    (createInvoiceLink): título, descrição, payload, moeda XTR, um item só com
    o preço e o subscription_period de 30 dias. A API manda este dicionário
    como JSON pelo HTTPX; o bot passa para o create_invoice_link do PTB. Um
    lugar só para os dois (sem copiar). 'preco' None: PRECO_PRO_STARS de agora;
    'sufixo' None: 8 caracteres aleatórios."""
    if not _inteiro_de_verdade(user_id) or user_id <= 0:
        raise ValueError('user_id inválido')
    if preco is None:
        preco = preco_pro_stars()
    if not _inteiro_de_verdade(preco) or not 1 <= preco <= PRECO_PRO_STARS_MAXIMO:
        raise ValueError('preço inválido')
    if sufixo is None:
        sufixo = os.urandom(4).hex()
    payload = f'{PREFIXO_PAYLOAD}:{user_id}:{preco}:{sufixo}'
    if ler_payload(payload) is None:
        raise ValueError('payload inválido')
    return {
        'title': TITULO_FATURA,
        'description': DESCRICAO_FATURA,
        'payload': payload,
        'currency': MOEDA_STARS,
        'prices': [{'label': ROTULO_ITEM_FATURA, 'amount': preco}],
        'subscription_period': PERIODO_ASSINATURA_SEGUNDOS,
    }


class Payload(NamedTuple):
    user_id: int
    stars: int
    sufixo: str


def ler_payload(payload):
    """Payload(user_id, stars, sufixo) de 'pro1:<user_id>:<stars>:<sufixo>', ou
    None se não está nesse formato (ou passa de 128 bytes). Nunca levanta erro
    e nunca registra o payload no log."""
    if not isinstance(payload, str) or len(payload.encode('utf-8')) > TAMANHO_MAXIMO_PAYLOAD:
        return None
    achado = _PAYLOAD.fullmatch(payload)
    if achado is None:
        return None
    return Payload(int(achado.group(1)), int(achado.group(2)), achado.group(3))


def _conferir_cobranca(dados, pagador_id, moeda, total):
    """As conferências que não dependem do banco nem do relógio: a fatura é
    de quem paga, em XTR, e o valor é o do payload. Código da recusa ou None."""
    if dados is None:
        return 'payload_invalido'
    if not _inteiro_de_verdade(pagador_id) or pagador_id != dados.user_id:
        return 'outra_pessoa'
    if moeda != MOEDA_STARS:
        return 'moeda'
    if not _inteiro_de_verdade(total) or total != dados.stars:
        return 'valor'
    return None


def conferir_pre_checkout(conn, pagador_id, moeda, total, payload, agora=None, admin=None, ligada=None):
    """O "posso cobrar?" do Telegram (pre_checkout_query), respondido em menos
    de 10 s: sem IA e sem rede, só o payload e UMA consulta ao banco (as linhas
    da pessoa). Devolve None (aceita) ou o código da recusa (só para o log; o
    bot mostra sempre o mesmo texto):

    - 'payload_invalido', 'outra_pessoa' (o user_id do payload não é quem
      paga), 'moeda' (não é XTR), 'valor' (o total não é o do payload);
    - 'admin': o admin não paga (D7);
    - renovação (já existe linha de Stars dessa pessoa com o mesmo payload):
      aceita pelo valor do link original, mesmo que o PRECO_PRO_STARS tenha
      mudado (D2) e mesmo com a chave desligada (quem já assina não perde a
      renovação). Não está documentado se a renovação passa por aqui;
    - 'assinaturas_desligadas': com ASSINATURAS_LIGADAS desligada ninguém
      começa assinatura nova;
    - 'preco_mudou': o valor do payload não é o PRECO_PRO_STARS de agora;
    - 'plano_ativo': a pessoa já tem Pro de Stars renovando (evita duas
      assinaturas cobrando ao mesmo tempo)."""
    dados = ler_payload(payload)
    codigo = _conferir_cobranca(dados, pagador_id, moeda, total)
    if codigo is not None:
        return codigo
    if admin is None:
        admin = e_admin(pagador_id)
    if admin:
        return 'admin'
    linhas = ler_linhas(conn, pagador_id)
    if any(l.origem == 'stars' and l.assinatura_ref == payload for l in linhas):
        return None
    if ligada is None:
        ligada = assinaturas_ligadas()
    if not ligada:
        return 'assinaturas_desligadas'
    if dados.stars != preco_pro_stars():
        return 'preco_mudou'
    if calcular_situacao(linhas, agora).proxima_renovacao is not None:
        return 'plano_ativo'
    return None


def momento_da_expiracao(expiracao):
    """subscription_expiration_date do Telegram em Brasília (sem
    microssegundos). Aceita o número Unix da Bot API ou o datetime do PTB (com
    fuso; sem fuso, é o Unix em UTC que o Telegram manda). None sem data."""
    if expiracao is None:
        return None
    if isinstance(expiracao, datetime):
        if expiracao.tzinfo is None:
            expiracao = expiracao.replace(tzinfo=timezone.utc)
        return agora_brasilia(expiracao)
    if isinstance(expiracao, (int, float)) and not isinstance(expiracao, bool):
        return datetime.fromtimestamp(expiracao, FUSO_BRASILIA).replace(microsecond=0)
    raise ValueError('data de expiração inválida')


class PagamentoRegistrado(NamedTuple):
    linha: Linha                 # a linha do pagamento (a já gravada, se repetido)
    fim_do_pro: datetime         # o fim único depois do pagamento
    repetido: bool               # o mesmo pagamento já tinha sido gravado (nada mudou)
    renovacao: bool              # cobrança de renovação (is_recurring sem is_first_recurring)


def registrar_pagamento_stars(conn, pagador_id, moeda, total, payload, pagamento_id, expiracao,
                              recorrente=False, primeira_recorrente=False, agora=None):
    """O successful_payment: o ÚNICO lugar onde nasce Pro pago. Confere de
    novo o payload, a pessoa, a moeda e o valor (o dinheiro já saiu: o preço de
    agora e o admin não são conferidos aqui, o pre-checkout já conferiu) e
    grava pela adicionar_pro (D17): 30 dias somados ao fim único (ou a partir
    de agora), renova_em = subscription_expiration_date em Brasília,
    pagamento_id, assinatura_ref = payload, valor e moeda. O mesmo pagamento
    entregue duas vezes grava uma linha só (índice único do pagamento_id):
    a segunda devolve repetido=True. Conferência que falha:
    ErroRegistro('pagamento_nao_conferido'), sem gravar nada.

    'renovacao': o Telegram marca is_recurring em toda cobrança de assinatura
    e is_first_recurring só na primeira; renovação = recorrente e não é a
    primeira."""
    dados = ler_payload(payload)
    if _conferir_cobranca(dados, pagador_id, moeda, total) is not None:
        raise ErroRegistro('pagamento_nao_conferido')
    if not isinstance(pagamento_id, str) or not pagamento_id.strip():
        raise ErroRegistro('pagamento_nao_conferido')
    renova_em = momento_da_expiracao(expiracao)
    renovacao = bool(recorrente) and not bool(primeira_recorrente)
    if renovacao and not any(l.origem == 'stars' and l.assinatura_ref == payload
                             for l in ler_linhas(conn, pagador_id)):
        _log.warning('Renovação sem a primeira cobrança gravada: user_id=%s', pagador_id)
    lancado = adicionar_pro(conn, pagador_id, DIAS_PAGAMENTO_STARS, 'stars', agora,
                            renovacao=renova_em is not None, renova_em=renova_em, pagamento_id=pagamento_id,
                            assinatura_ref=payload, valor=total, moeda=MOEDA_STARS)
    if not lancado.repetido:
        _log.info('Pagamento em Stars gravado: user_id=%s linha=%s renovacao=%s', pagador_id, lancado.linha.id,
                  renovacao)
    return PagamentoRegistrado(lancado.linha, lancado.fim_do_pro, lancado.repetido, renovacao)


def buscar_pagamento(conn, pagamento_id):
    """A linha do pagamento (pelo telegram_payment_charge_id), ou None."""
    if not isinstance(pagamento_id, str) or not pagamento_id:
        return None
    bruta = conn.execute(f'SELECT {_COLUNAS} FROM assinaturas WHERE pagamento_id = ?', (pagamento_id,)).fetchone()
    return None if bruta is None else _linha(bruta)


def ultimo_pagamento_reembolsavel(conn, user_id):
    """/reembolsar: o último pagamento em Stars da pessoa que ainda não foi
    reembolsado (ou None)."""
    bruta = conn.execute(
        f"SELECT {_COLUNAS} FROM assinaturas WHERE user_id = ? AND origem = 'stars' AND pagamento_id IS NOT NULL "
        "AND estado <> 'reembolsada' ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    return None if bruta is None else _linha(bruta)


class ReembolsoRegistrado(NamedTuple):
    user_id: int
    valor: int                   # Stars daquele pagamento (o valor do link, não o preço de hoje)
    assinatura_ref: Optional[str]
    alterou: bool                # False: o pagamento já estava reembolsado
    pro_valendo: bool            # sobrou Pro (dado ou pago) valendo agora
    vale_ate: Optional[datetime]  # o fim único que sobrou (None sem Pro)


def registrar_reembolso(conn, pagamento_id, agora=None, user_id=None):
    """O reembolso de um pagamento (o /reembolsar do admin e o
    refunded_payment, inclusive o estorno pela loja): retirar_pro no modo
    reembolso (D17): a linha fica 'reembolsada', as seguintes recuam os 30
    dias dela, o fim único cai 30 dias (se cair antes de agora, acaba agora).
    Pro dado e outros pagamentos continuam. Idempotente: de novo, nada muda
    (alterou=False). None se o id não é de um pagamento em Stars conhecido (ou
    é de outra pessoa, quando 'user_id' vem)."""
    agora = agora_brasilia(agora)
    linha = buscar_pagamento(conn, pagamento_id)
    if linha is None or linha.origem != 'stars' or (user_id is not None and linha.user_id != user_id):
        return None
    retirado = retirar_pro(conn, linha.user_id, MODO_REEMBOLSO, agora, pagamento_id=pagamento_id)
    marcar_comprovante(conn, pagamento_id, SITUACAO_REEMBOLSADO)
    situacao = situacao_acesso(conn, linha.user_id, agora, admin=False)
    pro_valendo = situacao.estado == 'pro'
    return ReembolsoRegistrado(linha.user_id, int(round(linha.valor or 0)), linha.assinatura_ref,
                               retirado.alterou, pro_valendo, situacao.vale_ate if pro_valendo else None)


# --- COMPROVANTE DE CADA PAGAMENTO RECEBIDO (PEDIDO DO YGOR, 2026-11-12) ---
# Gravado ANTES de qualquer conferência ou gravação do Pro: se o resto quebrar,
# o id da cobrança (o que o refundStarPayment pede) fica no banco, e não só no
# log da Discloud (que guarda poucas linhas). Uma linha por cobrança.
# situacao: 'recebido' (comprovante gravado, Pro ainda não), 'pro_gravado',
# 'falhou' (com o código em 'erro') e 'reembolsado'.
SITUACAO_RECEBIDO = 'recebido'
SITUACAO_PRO_GRAVADO = 'pro_gravado'
SITUACAO_FALHOU = 'falhou'
SITUACAO_REEMBOLSADO = 'reembolsado'


class Comprovante(NamedTuple):
    id: int
    pagamento_id: str
    user_id: int
    valor: int
    moeda: str
    assinatura_ref: Optional[str]
    recorrente: bool
    recebido_em: str
    situacao: str
    erro: Optional[str]


_COLUNAS_COMPROVANTE = ('id, pagamento_id, user_id, valor, moeda, assinatura_ref, recorrente, recebido_em, '
                        'situacao, erro')


def _comprovante(bruta):
    return Comprovante(bruta[0], bruta[1], bruta[2], bruta[3], bruta[4], bruta[5], bool(bruta[6]), bruta[7],
                       bruta[8], bruta[9])


def registrar_comprovante(conn, pagamento_id, user_id, valor, moeda, assinatura_ref, recorrente=False,
                          agora=None):
    """Grava o comprovante do successful_payment exatamente como chegou, sem
    conferir nada (a conferência vem depois e pode falhar). O mesmo pagamento
    de novo não grava outra linha. True se gravou agora; False se já existia.
    Sem id da cobrança: ErroRegistro('pagamento_nao_conferido') (sem ele, nem
    o reembolso é possível)."""
    if not isinstance(pagamento_id, str) or not pagamento_id.strip():
        raise ErroRegistro('pagamento_nao_conferido')
    agora = agora_brasilia(agora)
    valor_inteiro = valor if _inteiro_de_verdade(valor) else 0
    ref = assinatura_ref if isinstance(assinatura_ref, str) else None

    def operacao():
        cursor = conn.execute(
            'INSERT INTO pagamentos_recebidos (pagamento_id, user_id, valor, moeda, assinatura_ref, recorrente, '
            'recebido_em, situacao) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(pagamento_id) DO NOTHING',
            (pagamento_id, user_id, valor_inteiro, str(moeda or ''), ref, 1 if recorrente else 0, _texto(agora),
             SITUACAO_RECEBIDO))
        gravou = cursor.rowcount
        conn.commit()
        return bool(gravou and gravou > 0)

    return recorrentes._com_novas_tentativas(conn, operacao)


def marcar_comprovante(conn, pagamento_id, situacao, erro=None):
    """Atualiza a situação do comprovante. Um comprovante 'reembolsado' nunca
    volta atrás. True se mudou alguma linha."""
    def operacao():
        cursor = conn.execute(
            'UPDATE pagamentos_recebidos SET situacao = ?, erro = ? WHERE pagamento_id = ? AND situacao <> ?',
            (situacao, erro, pagamento_id, SITUACAO_REEMBOLSADO))
        mudou = cursor.rowcount
        conn.commit()
        return bool(mudou and mudou > 0)

    return recorrentes._com_novas_tentativas(conn, operacao)


def buscar_comprovante(conn, user_id, comprovante_id):
    bruta = conn.execute(f'SELECT {_COLUNAS_COMPROVANTE} FROM pagamentos_recebidos WHERE id = ? AND user_id = ?',
                         (comprovante_id, user_id)).fetchone()
    return None if bruta is None else _comprovante(bruta)


def comprovante_sem_pro(conn, user_id):
    """/reembolsar quando o Pro não foi gravado: o último comprovante da
    pessoa que ficou 'recebido' ou 'falhou' (dinheiro recebido, sem linha de
    Pro e sem reembolso), ou None."""
    bruta = conn.execute(
        f'SELECT {_COLUNAS_COMPROVANTE} FROM pagamentos_recebidos WHERE user_id = ? AND situacao IN (?, ?) '
        'ORDER BY id DESC LIMIT 1', (user_id, SITUACAO_RECEBIDO, SITUACAO_FALHOU)).fetchone()
    return None if bruta is None else _comprovante(bruta)


def registrar_reembolso_sem_pro(conn, pagamento_id, user_id):
    """O reembolso de uma cobrança cujo Pro nunca foi gravado (o comprovante
    está 'recebido' ou 'falhou'): marca 'reembolsado'. Devolve o comprovante
    se mudou agora; None se não existe, é de outra pessoa, tem Pro gravado ou
    já estava reembolsado (nada muda)."""
    def operacao():
        cursor = conn.execute(
            'UPDATE pagamentos_recebidos SET situacao = ? WHERE pagamento_id = ? AND user_id = ? AND situacao IN (?, ?)',
            (SITUACAO_REEMBOLSADO, pagamento_id, user_id, SITUACAO_RECEBIDO, SITUACAO_FALHOU))
        mudou = cursor.rowcount
        conn.commit()
        return bool(mudou and mudou > 0)

    if not recorrentes._com_novas_tentativas(conn, operacao):
        return None
    bruta = conn.execute(f'SELECT {_COLUNAS_COMPROVANTE} FROM pagamentos_recebidos WHERE pagamento_id = ?',
                         (pagamento_id,)).fetchone()
    return None if bruta is None else _comprovante(bruta)


def primeiro_pagamento_conhecido(conn, user_id, assinatura_ref):
    """O id da PRIMEIRA cobrança da assinatura (o editUserStarSubscription
    pede esse id): pela tabela assinaturas e, se o Pro nunca foi gravado,
    pelos comprovantes. None sem nenhum."""
    primeiro = primeiro_pagamento_da_assinatura(conn, user_id, assinatura_ref)
    if primeiro is not None or not assinatura_ref:
        return primeiro
    linha = conn.execute('SELECT pagamento_id FROM pagamentos_recebidos WHERE user_id = ? AND assinatura_ref = ? '
                         'ORDER BY id LIMIT 1', (user_id, assinatura_ref)).fetchone()
    return None if linha is None else linha[0]


def primeiro_pagamento_da_assinatura(conn, user_id, assinatura_ref):
    """O telegram_payment_charge_id da PRIMEIRA cobrança daquela assinatura
    (o editUserStarSubscription pede esse id), ou None."""
    if not assinatura_ref:
        return None
    linha = conn.execute(
        "SELECT pagamento_id FROM assinaturas WHERE user_id = ? AND origem = 'stars' AND assinatura_ref = ? "
        "AND pagamento_id IS NOT NULL ORDER BY id LIMIT 1", (user_id, assinatura_ref)
    ).fetchone()
    return None if linha is None else linha[0]


# --- FATURA ---
def preparar_fatura(conn, user_id, agora=None, admin=None, ligada=None, preco=None):
    """A fatura (montar_fatura_pro) para quem pode assinar agora:
    'assinaturas_desligadas' com a chave desligada; 'plano_ativo' para o admin
    e para quem tem Pro de Stars renovando (pela mesma regra do selo:
    proxima_renovacao). Teste, Pro dado, Pro de Stars com a renovação
    cancelada e vencidos recebem a fatura: o pagamento soma 30 dias ao fim
    atual (D17)."""
    if ligada is None:
        ligada = assinaturas_ligadas()
    if not ligada:
        raise ErroRegistro('assinaturas_desligadas')
    if admin is None:
        admin = e_admin(user_id)
    if admin:
        raise ErroRegistro('plano_ativo')
    if situacao_acesso(conn, user_id, agora, admin=False).proxima_renovacao is not None:
        raise ErroRegistro('plano_ativo')
    return montar_fatura_pro(user_id, preco)


# --- RENOVAÇÃO (CANCELAR E REATIVAR) ---
class AlvoRenovacao(NamedTuple):
    user_id: int
    assinatura_ref: str
    pagamento_id: str            # o da PRIMEIRA cobrança (editUserStarSubscription)
    ja_no_estado: bool           # o pedido não muda nada: sem chamar o Telegram


def preparar_renovacao(conn, user_id, cancelar, agora=None, admin=None):
    """O que o "Cancelar Renovação" / "Reativar Renovação" vai mexer: a linha
    de Stars em vigor da pessoa (a mesma do selo). 'sem_assinatura_stars' para
    o admin e para quem não tem Pro de Stars em vigor (teste, Pro dado ou
    vencido). 'ja_no_estado' (idempotência): cancelar o que já está cancelado
    (pela linha) ou reativar o que já renova (pelo selo) não chama o Telegram
    nem grava."""
    agora = agora_brasilia(agora)
    if admin is None:
        admin = e_admin(user_id)
    if admin:
        raise ErroRegistro('sem_assinatura_stars')
    linhas = ler_linhas(conn, user_id)
    situacao = calcular_situacao(linhas, agora)
    em_vigor = _stars_em_vigor(_pros_validas(linhas), agora) if situacao.estado == 'pro' else None
    if em_vigor is None or not em_vigor.assinatura_ref:
        raise ErroRegistro('sem_assinatura_stars')
    pagamento_id = primeiro_pagamento_da_assinatura(conn, user_id, em_vigor.assinatura_ref)
    if pagamento_id is None:
        raise ErroRegistro('sem_assinatura_stars')
    if cancelar:
        ja = not em_vigor.renovacao or em_vigor.estado == 'cancelada'
    else:
        ja = situacao.proxima_renovacao is not None
    return AlvoRenovacao(user_id, em_vigor.assinatura_ref, pagamento_id, ja)


def gravar_renovacao(conn, user_id, assinatura_ref, cancelar):
    """Marca as linhas válidas daquela assinatura: cancelar = renovacao 0 e
    estado 'cancelada' (vale até o fim); reativar = renovacao 1 e 'ativa'.
    As reembolsadas e encerradas não mudam."""
    def operacao():
        conn.execute(
            "UPDATE assinaturas SET renovacao = ?, estado = ? WHERE user_id = ? AND origem = 'stars' "
            "AND assinatura_ref = ? AND estado IN ('ativa', 'cancelada')",
            (0 if cancelar else 1, 'cancelada' if cancelar else 'ativa', user_id, assinatura_ref))
        conn.commit()

    recorrentes._com_novas_tentativas(conn, operacao)
    _log.info('Renovação %s: user_id=%s', 'cancelada' if cancelar else 'reativada', user_id)


def concluir_renovacao(conn, alvo, cancelar, telegram_recusou=False):
    """Depois da chamada ao editUserStarSubscription (feita pelo bot ou pela
    API): o Telegram aceitou, grava; recusou a REATIVAÇÃO (a pessoa cancelou
    nas configurações do Telegram, D15), ErroRegistro('reativacao_recusada')
    sem mexer no banco; recusou o CANCELAMENTO (por exemplo, já cancelada pela
    pessoa), grava o cancelamento mesmo assim: o que a pessoa pediu (não
    renovar) já vale. Falha de rede não chega aqui (quem chama responde que o
    Telegram está indisponível, sem gravar)."""
    if alvo.ja_no_estado:
        return
    if telegram_recusou and not cancelar:
        raise ErroRegistro('reativacao_recusada')
    if telegram_recusou:
        _log.warning('Cancelamento recusado pelo Telegram (gravado mesmo assim): user_id=%s', alvo.user_id)
    gravar_renovacao(conn, alvo.user_id, alvo.assinatura_ref, cancelar)


# --- AVISOS DE VENCIMENTO (JOB DAS 10:00) ---
MARCO_DOIS_DIAS = '2_dias'
MARCO_ULTIMO_DIA = 'ultimo_dia'
MARCO_VENCEU = 'venceu'
TEXTO_AVISO_VENCEU = ('Sua assinatura venceu. Seus dados continuam guardados, e você ainda pode vê-los no L1d3r '
                      '(só para ver, sem registrar).')


class AvisoDevido(NamedTuple):
    user_id: int
    linha_id: int                # a linha que guarda o ultimo_aviso
    chave: str                   # '<marco>:<fim>' (o mesmo aviso nunca sai duas vezes)
    anterior: Optional[str]      # o ultimo_aviso antes (para desfazer se o envio falhar)
    marco: str
    texto: str


def _aviso_da_situacao(situacao, linhas):
    """(marco, linha, fim, texto) do aviso que vale hoje, ou None. "2 dias" e
    "último dia" usam a dias_restantes (a mesma conta da folha)."""
    if situacao.estado == 'teste':
        linha = _linha_teste(linhas)
        dias = situacao.dias_restantes
        if dias == 2:
            return MARCO_DOIS_DIAS, linha, situacao.fim, f'Seu teste termina em 2 dias ({situacao.fim:%d/%m}).'
        if dias == 0:
            return MARCO_ULTIMO_DIA, linha, situacao.fim, 'Seu teste termina hoje.'
        return None
    if situacao.estado == 'pro':
        if situacao.proxima_renovacao is not None or situacao.vale_ate is None:
            return None   # renova sozinho: sem aviso
        linha = max(_pros_validas(linhas), key=lambda l: (l.fim, l.id))
        dias = situacao.dias_restantes
        if dias == 2:
            return MARCO_DOIS_DIAS, linha, situacao.vale_ate, 'Seu Plano Pro termina em 2 dias e não vai renovar.'
        if dias == 0:
            return MARCO_ULTIMO_DIA, linha, situacao.vale_ate, 'Seu Plano Pro termina hoje.'
        return None
    if situacao.estado == 'teste_vencido':
        return MARCO_VENCEU, _linha_teste(linhas), situacao.fim, TEXTO_AVISO_VENCEU
    if situacao.estado == 'pro_vencido':
        linha = max((l for l in linhas if l.plano == 'pro'), key=lambda l: (l.fim, l.id))
        return MARCO_VENCEU, linha, situacao.fim, TEXTO_AVISO_VENCEU
    return None


def avisos_devidos(conn, agora=None, admin_ids=None, ligada=None):
    """Os avisos de vencimento que ainda não saíram (o job das 10:00 e o
    /avisos_agora). Um por marco, controlado pela coluna ultimo_aviso da linha
    que define o fim ('<marco>:<fim>': se o fim muda, por exemplo com o
    /estender_teste, os avisos valem de novo):

    - 2 dias antes e último dia: teste ativo e Pro que não renova (cancelado
      ou dado), pela dias_restantes (2 e 0);
    - venceu: todo vencido (teste ou Pro), uma vez.
    O admin nunca recebe. Com ASSINATURAS_LIGADAS desligada: nenhum."""
    if ligada is None:
        ligada = assinaturas_ligadas()
    if not ligada:
        return []
    agora = agora_brasilia(agora)
    if admin_ids is None:
        admin_ids = ler_admin_ids()
    por_usuario = {}
    for bruta in conn.execute(f'SELECT {_COLUNAS} FROM assinaturas ORDER BY inicio, id').fetchall():
        linha = _linha(bruta)
        por_usuario.setdefault(linha.user_id, []).append(linha)
    avisados = dict(conn.execute('SELECT id, ultimo_aviso FROM assinaturas').fetchall())
    avisos = []
    for user_id in sorted(por_usuario):
        if e_admin(user_id, admin_ids):
            continue
        linhas = por_usuario[user_id]
        achado = _aviso_da_situacao(calcular_situacao(linhas, agora), linhas)
        if achado is None:
            continue
        marco, linha, fim, texto = achado
        chave = f'{marco}:{_texto(fim)}'
        anterior = avisados.get(linha.id)
        if anterior == chave:
            continue
        avisos.append(AvisoDevido(user_id, linha.id, chave, anterior, marco, texto))
    return avisos


def marcar_aviso(conn, aviso):
    """Reserva o aviso ANTES de enviar (um comando só): True se esta chamada
    marcou (o aviso pode sair); False se outra execução já marcou (não envia
    de novo)."""
    def operacao():
        cursor = conn.execute(
            'UPDATE assinaturas SET ultimo_aviso = ? WHERE id = ? AND (ultimo_aviso IS NULL OR ultimo_aviso <> ?)',
            (aviso.chave, aviso.linha_id, aviso.chave))
        mudou = cursor.rowcount
        conn.commit()
        return bool(mudou and mudou > 0)

    return recorrentes._com_novas_tentativas(conn, operacao)


def desmarcar_aviso(conn, aviso):
    """O envio falhou: volta o ultimo_aviso de antes (o aviso sai na próxima)."""
    def operacao():
        conn.execute('UPDATE assinaturas SET ultimo_aviso = ? WHERE id = ? AND ultimo_aviso = ?',
                     (aviso.anterior, aviso.linha_id, aviso.chave))
        conn.commit()

    recorrentes._com_novas_tentativas(conn, operacao)


# --- TERMOS (/terms E GET /termos) ---
# Texto aprovado pelo Ygor em 2026-11-12 (7 itens), sem mudança, exceto o
# item 4, mudado em 2026-11-13 (PRO-111: os serviços de IA e o processamento
# fora do Brasil). Só o preço do item 1 vem do PRECO_PRO_STARS, para o texto
# nunca dizer um preço diferente da fatura.
TERMOS_TITULO = 'Termos do L1d3r Pro'
TERMOS_ITENS = (
    'O Plano Pro custa {preco} Stars a cada 30 dias e renova sozinho, usando as Stars da sua conta.',
    'Você pode cancelar a renovação quando quiser, em /assinatura, na folha Minha Assinatura do L1d3r ou nas '
    'configurações do Telegram. O Pro vale até o fim do período pago.',
    'Há um teste grátis de 10 dias, uma vez por pessoa. No teste e no Pro, o limite é de 17 registros com IA por '
    'dia (horário de Brasília).',
    'Os textos que você manda para registrar gastos e receitas são enviados a serviços de IA de terceiros '
    '(DeepSeek e, quando ele não responde, Google Gemini) só para identificar categoria e valor, e podem ser '
    'processados fora do Brasil.',
    'Reembolso integral se pedido em até 7 dias da primeira cobrança, por /paysupport.',
    'Quando a assinatura vence, seus dados ficam guardados por 12 meses. Você pode vê-los no L1d3r, mas não '
    'registrar até assinar de novo.',
    'O suporte do Telegram não atende compras feitas neste bot. Fale com a gente por /paysupport.',
)


def texto_termos(html=False):
    """O texto único dos termos: o GET /termos devolve em texto puro; o
    /terms do bot, com html=True, só põe o título em negrito (o resto é o
    mesmo texto, sem nenhum caractere especial de HTML)."""
    itens = '\n'.join(f'{numero}. {item.format(preco=preco_pro_stars())}'
                      for numero, item in enumerate(TERMOS_ITENS, start=1))
    titulo = f'<b>{TERMOS_TITULO}</b>' if html else TERMOS_TITULO
    return f'{titulo}\n\n{itens}'
