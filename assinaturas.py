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
ASSINATURAS_LIGADAS, LIMITE_IA_DIA e PRECO_PRO_STARS são lidas na hora, em
cada chamada) e recebe a conexão por parâmetro ('conn'). O "agora" entra por
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
'dias_invalidos', 'sem_teste', 'banco_ocupado'.

A palavra "admin" nunca vai para o JSON (D7.4): o admin aparece como Pro, sem
datas, com selo "pro" e selo_tocavel false (D18).
"""
import logging
import math
import os
import re
from datetime import datetime, timedelta
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
    Até a PRO-107, só informativo (preco.stars do GET /assinatura)."""
    return _ler_inteiro('PRECO_PRO_STARS', 1, PRECO_PRO_STARS_MAXIMO, PRECO_PRO_STARS_PADRAO)


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
        em_vigor = None
        if stars_recente is not None:
            fim_efetivo = stars_recente.fim + (TOLERANCIA_RENOVACAO if _renova(stars_recente, agora) else timedelta(0))
            if agora < fim_efetivo:
                em_vigor = stars_recente
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
    os dados, sem registrar."""
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


def iniciar_teste(conn, user_id, agora=None, admin=None, criado_por=None):
    """POST /assinaturas/teste e o botão "Testar Grátis por 10 Dias" do bot:
    início = agora, fim = agora + 10 dias exatos, uma vez na vida (o índice
    único ux_assinaturas_teste recusa o segundo, mesmo com dois toques
    juntos). Não exige cadastro em 'usuarios' (D9).

    - admin, ou Pro ativo: ErroRegistro('plano_ativo');
    - já existe teste ativo (toque duplo): devolve o dele, repetido=True;
    - o teste já foi usado e venceu: ErroRegistro('teste_ja_usado');
    - banco ocupado em todas as tentativas: ErroRegistro('banco_ocupado')."""
    agora = agora_brasilia(agora)
    if admin is None:
        admin = e_admin(user_id)
    if admin:
        raise ErroRegistro('plano_ativo')

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
        conn.execute(
            'INSERT INTO assinaturas (user_id, plano, origem, inicio, fim, estado, dias, renovacao, criado_em, '
            "criado_por) VALUES (?, 'teste', 'teste', ?, ?, 'ativa', ?, 0, ?, ?)",
            (user_id, _texto(agora), _texto(fim), DIAS_TESTE, _texto(agora), criado_por)
        )
        conn.commit()
        return TesteIniciado(agora, fim, False)

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
