"""Testes do pagamento do Plano Pro em Telegram Stars (PRO-107): a fatura e o
payload, o pre-checkout, o pagamento (primeira cobrança e renovação), a
regra única de soma (D17) com SuccessfulPayment falso, o reembolso (/reembolsar
e refunded_payment), o cancelamento e a reativação da renovação (/assinatura e
POST /assinaturas/renovacao), os avisos de vencimento (job e /avisos_agora),
os termos (/terms e GET /termos), o /paysupport e a rota POST
/assinaturas/fatura.

Roda com `python testes_pagamento_stars.py`. SEM REDE: a mesma costura do
testes_assinaturas.py (sem .env, libsql proibido, SQLite em arquivo, relógio
fixo). Os eventos do Telegram são montados com as classes do PTB
(PreCheckoutQuery, SuccessfulPayment com is_recurring e is_first_recurring,
RefundedPayment) e passam pelo porteiro e pelos MESMOS handlers registrados
pelo bot.py (registrar_handlers_pagamento). A Bot API é falsa: no bot, um
objeto no lugar do context.bot, que registra as chamadas; na API, um dublê
injetado pela dependência obter_bot_api.

`python testes_pagamento_stars.py --telegram-real` (fora da suíte padrão):
UMA chamada real ao createInvoiceLink no L1d3rTestBot, sem ninguém pagar e sem
banco. Lê o token do ambiente (o .env do worktree, pelo dotenv), sem imprimir;
chama o getMe e aborta se o bot não for o L1d3rTestBot; cria a fatura pela
assinaturas.montar_fatura_pro com o preço padrão e confere que o Telegram
devolveu um link https://t.me/$... Imprime só o nome do bot, "fatura aceita"
e o começo do link.
"""
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

BOT_DE_TESTE = 'L1d3rTestBot'


def rodar_telegram_real():
    """A chamada real ao createInvoiceLink (ver o topo do arquivo). Devolve o
    código de saída: 0 aceita, 1 recusada, 2 abortada."""
    import dotenv
    import libsql

    def _sem_banco(*args, **kwargs):
        raise RuntimeError("Esta checagem não usa banco.")

    libsql.connect = _sem_banco
    dotenv.load_dotenv(os.path.join(RAIZ, '.env'))
    token = (os.getenv('TOKEN') or '').strip()
    if not token:
        print("Abortado: sem TOKEN no ambiente.")
        return 2
    import assinaturas
    from api.main import BotApiTelegram, TelegramIndisponivel, TelegramRecusou
    api = BotApiTelegram(token)
    try:
        eu = api.obter_bot() or {}
    except (TelegramRecusou, TelegramIndisponivel) as erro:
        print(f"Abortado: o getMe falhou ({type(erro).__name__}).")
        return 2
    if eu.get('username') != BOT_DE_TESTE:
        print(f"Abortado: o token não é do {BOT_DE_TESTE}.")
        return 2
    print(f"Bot: {eu['username']}")
    fatura = assinaturas.montar_fatura_pro(int(eu['id']), preco=assinaturas.PRECO_PRO_STARS_PADRAO)
    try:
        link = api.criar_link_fatura(fatura)
    except TelegramRecusou:
        print("Falhou: o Telegram recusou a fatura.")
        return 1
    except TelegramIndisponivel:
        print("Falhou: o Telegram não respondeu.")
        return 1
    if not isinstance(link, str) or not link.startswith('https://t.me/$'):
        print("Falhou: o Telegram não devolveu um link https://t.me/$...")
        return 1
    print("fatura aceita")
    print(f"Começo do link: {link[:16]}...")
    return 0


if __name__ == '__main__' and '--telegram-real' in sys.argv[1:]:
    sys.exit(rodar_telegram_real())

# --- SUÍTE PADRÃO (sem rede) ---
# A costura (sem .env, libsql proibido, ambiente limpo) é a do
# testes_assinaturas.py, feita no import dele, antes do import do bot.
import ast  # noqa: E402
import asyncio  # noqa: E402
import contextlib  # noqa: E402
import io  # noqa: E402
import itertools  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sqlite3  # noqa: E402
import unittest  # noqa: E402
from datetime import datetime, time, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from unittest import mock  # noqa: E402

import testes_assinaturas as base  # noqa: E402  (a costura acontece aqui)
from testes_assinaturas import (  # noqa: E402
    AmbienteLimpo, BotFalso, FilaFalsa, RelogioBot, TesteApiBase, TesteBotBase, br, linhas_resumidas,
)

import httpx  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from telegram import (  # noqa: E402
    Chat, Message, PreCheckoutQuery, RefundedPayment, SuccessfulPayment, Update, User,
)
from telegram.error import BadRequest, NetworkError  # noqa: E402
from telegram.ext import CommandHandler, MessageHandler, PreCheckoutQueryHandler  # noqa: E402

import assinaturas  # noqa: E402
import bot  # noqa: E402
import registro  # noqa: E402
from api import main as api_main  # noqa: E402
from api.main import app  # noqa: E402
from calculos import FUSO_BRASILIA  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_registro import OUTRO_UID, SEM_CADASTRO, UID, IAFalsa  # noqa: E402

logging.getLogger("api").addHandler(logging.NullHandler())

F = FUSO_BRASILIA
LINK = 'https://t.me/$fatura'
SUFIXO = 'abcdef01'
TEXTO_TERMOS_APROVADO = (
    "Termos do L1d3r Pro\n\n"
    "1. O Plano Pro custa 150 Stars a cada 30 dias e renova sozinho, usando as Stars da sua conta.\n"
    "2. Você pode cancelar a renovação quando quiser, em /assinatura, na folha Minha Assinatura do L1d3r ou nas "
    "configurações do Telegram. O Pro vale até o fim do período pago.\n"
    "3. Há um teste grátis de 10 dias, uma vez por pessoa. No teste e no Pro, o limite é de 17 registros com IA "
    "por dia (horário de Brasília).\n"
    "4. Os textos que você manda para registrar gastos e receitas são enviados a serviços de IA de terceiros "
    "(DeepSeek e, quando ele não responde, Google Gemini) só para identificar categoria e valor, e podem ser "
    "processados fora do Brasil.\n"
    "5. Reembolso integral se pedido em até 7 dias da primeira cobrança, por /paysupport.\n"
    "6. Quando a assinatura vence, seus dados ficam guardados por 12 meses. Você pode vê-los no L1d3r, mas não "
    "registrar até assinar de novo.\n"
    "7. O suporte do Telegram não atende compras feitas neste bot. Fale com a gente por /paysupport."
)


def payload_de(user_id=OUTRO_UID, stars=150, sufixo=SUFIXO):
    return f'pro1:{user_id}:{stars}:{sufixo}'


def utc(momento):
    """O subscription_expiration_date como o PTB entrega: datetime em UTC."""
    return momento.astimezone(timezone.utc)


def texto_linha(momento):
    return momento.strftime('%Y-%m-%d %H:%M:%S')


def sem_sobreposicao(caso, conn, user_id):
    """Nenhum par de linhas Pro válidas com períodos que se cruzam (D17)."""
    validas = [l for l in assinaturas.ler_linhas(conn, user_id) if l.plano == 'pro' and l.valida]
    for a, b in itertools.combinations(validas, 2):
        caso.assertFalse(a.inicio < b.fim and b.inicio < a.fim, f"linhas {a.id} e {b.id} se cruzam")


# =============================================================================
# BOT API FALSA (o context.bot do PTB)
# =============================================================================
class BotTelegramFalso(BotFalso):
    """Faz o papel do context.bot: registra cada chamada à Bot API, na ordem.
    'recusar' (BadRequest) e 'cair' (NetworkError) dizem quais métodos falham."""

    def __init__(self, recusar=(), cair=()):
        super().__init__()
        self.chamadas = []
        self.recusar = set(recusar)
        self.cair = set(cair)
        self.mensagens = []
        self.links = 0

    async def _registrar(self, nome, **dados):
        self.chamadas.append((nome, dados))
        if nome in self.recusar:
            raise BadRequest("recusado pelo Telegram")
        if nome in self.cair:
            raise NetworkError("Telegram fora do ar")

    def nomes(self):
        return [nome for nome, _ in self.chamadas]

    async def create_invoice_link(self, **dados):
        await self._registrar('create_invoice_link', **dados)
        self.links += 1
        return f'{LINK}{self.links}'

    async def edit_user_star_subscription(self, user_id, telegram_payment_charge_id, is_canceled, **extra):
        await self._registrar('edit_user_star_subscription', user_id=user_id,
                              telegram_payment_charge_id=telegram_payment_charge_id, is_canceled=is_canceled)
        return True

    async def refund_star_payment(self, user_id, telegram_payment_charge_id, **extra):
        await self._registrar('refund_star_payment', user_id=user_id,
                              telegram_payment_charge_id=telegram_payment_charge_id)
        return True

    async def answer_pre_checkout_query(self, pre_checkout_query_id, ok, error_message=None, **extra):
        await self._registrar('answer_pre_checkout_query', ok=ok, error_message=error_message)
        return True

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None, **extra):
        self.enviadas.append((chat_id, text))
        self.mensagens.append((chat_id, text, TesteBotBase.botoes(reply_markup)))
        self.proximo_id += 1
        return SimpleNamespace(message_id=self.proximo_id)


# =============================================================================
# FATURA E PAYLOAD (assinaturas.py)
# =============================================================================
class TestFaturaEPayload(AmbienteLimpo):
    def test_campos_da_fatura(self):
        fatura = assinaturas.montar_fatura_pro(OUTRO_UID, sufixo=SUFIXO)
        self.assertEqual(fatura, {
            'title': 'L1d3r Pro',
            'description': 'Plano Pro do L1d3r por 30 dias. Renova sozinho; cancele quando quiser.',
            'payload': payload_de(),
            'currency': 'XTR',
            'prices': [{'label': 'Plano Pro (30 dias)', 'amount': 150}],
            'subscription_period': 2592000,
        })

    def test_preco_da_variavel_e_sufixo_aleatorio(self):
        self.ambiente(PRECO_PRO_STARS='200')
        a = assinaturas.montar_fatura_pro(UID)
        b = assinaturas.montar_fatura_pro(UID)
        self.assertEqual(a['prices'][0]['amount'], 200)
        self.assertRegex(a['payload'], r'^pro1:4242:200:[0-9a-f]{8}$')
        self.assertNotEqual(a['payload'], b['payload'])
        self.ambiente(PRECO_PRO_STARS='abc')   # inválido: padrão
        self.assertEqual(assinaturas.montar_fatura_pro(UID)['prices'][0]['amount'], 150)

    def test_payload_cabe_nos_128_bytes_no_pior_caso(self):
        fatura = assinaturas.montar_fatura_pro(9_999_999_999_999_999_999 // 10, preco=10000)
        self.assertLessEqual(len(fatura['payload'].encode()), 128)

    def test_user_id_e_preco_invalidos(self):
        for user_id in (0, -1, True, '4242', None):
            with self.subTest(user_id=user_id), self.assertRaises(ValueError):
                assinaturas.montar_fatura_pro(user_id)
        for preco in (0, 10001, 1.5, True):
            with self.subTest(preco=preco), self.assertRaises(ValueError):
                assinaturas.montar_fatura_pro(UID, preco=preco)

    def test_ler_payload(self):
        self.assertEqual(assinaturas.ler_payload(payload_de()), assinaturas.Payload(OUTRO_UID, 150, SUFIXO))
        for errado in ('', None, 150, 'pro2:5151:150:abcdef01', 'pro1:5151:150:ABCDEF01', 'pro1:5151:150:abc',
                       'pro1:0:150:abcdef01', 'pro1:5151:0:abcdef01', 'pro1:5151:150:abcdef01:x',
                       'pro1:-5151:150:abcdef01', ' pro1:5151:150:abcdef01', 'x' * 200):
            with self.subTest(payload=errado):
                self.assertIsNone(assinaturas.ler_payload(errado))

    def test_expiracao_em_brasilia(self):
        esperado = br(2026, 12, 14, 12)
        self.assertEqual(assinaturas.momento_da_expiracao(utc(esperado)), esperado)
        self.assertEqual(assinaturas.momento_da_expiracao(int(esperado.timestamp())), esperado)
        self.assertEqual(assinaturas.momento_da_expiracao(datetime(2026, 12, 14, 15, 0)), esperado)  # sem fuso = UTC
        self.assertIsNone(assinaturas.momento_da_expiracao(None))
        self.assertEqual(assinaturas.momento_da_expiracao(utc(esperado)).tzinfo, F)

    def test_modulo_continua_sem_telegram_httpx_bot_dotenv_nem_libsql(self):
        with open(os.path.join(RAIZ, 'assinaturas.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        importados = set()
        for no in ast.walk(arvore):
            if isinstance(no, ast.Import):
                importados.update(alias.name.split('.')[0] for alias in no.names)
            elif isinstance(no, ast.ImportFrom):
                importados.add(no.module.split('.')[0])
        self.assertFalse(importados & {'telegram', 'httpx', 'bot', 'dotenv', 'libsql'})


# =============================================================================
# BOT: BASE COM OS HANDLERS REGISTRADOS
# =============================================================================
class TesteBotPagamento(TesteBotBase):
    """Chave ligada, UID admin (Ygor), OUTRO_UID usuária comum (Maria), Mini
    App configurado, relógio do bot em 14/11/2026 12:00 e a Bot API falsa. Os
    updates passam pelo porteiro e pelos handlers do registrar_handlers_pagamento."""

    def setUp(self):
        super().setUp()
        self.ambiente(ASSINATURAS_LIGADAS='1', ADMIN_IDS=str(UID), URL_MINI_APP='https://exemplo.ngrok.app/app/')
        anterior = RelogioBot.AGORA
        self.addCleanup(setattr, RelogioBot, 'AGORA', anterior)
        self.relogio(br(2026, 11, 14, 12))
        self.tg = BotTelegramFalso()
        self.handlers = []
        bot.registrar_handlers_pagamento(SimpleNamespace(add_handler=lambda h, group=0: self.handlers.append((h, group))))
        self.ia = IAFalsa()
        registro.definir_cliente_ia(self.ia)
        self.update_ids = itertools.count(1)

    @staticmethod
    def relogio(momento):
        RelogioBot.AGORA = momento

    def ctx(self, args=None, tg=None):
        return SimpleNamespace(user_data={}, application=SimpleNamespace(handlers={}), bot=tg or self.tg,
                               job_queue=FilaFalsa(), args=args)

    # --- eventos do Telegram, com as classes do PTB ---
    def usuario_tg(self, user_id):
        return User(id=user_id, first_name='Maria', is_bot=False)

    def mensagem_tg(self, user_id, **campos):
        mensagem = Message(message_id=next(self.update_ids), date=datetime(2026, 11, 14, 15, tzinfo=timezone.utc),
                           chat=Chat(id=user_id, type='private'), from_user=self.usuario_tg(user_id), **campos)
        mensagem.set_bot(self.tg)
        return Update(update_id=next(self.update_ids), message=mensagem)

    def pre_checkout_tg(self, user_id=OUTRO_UID, payload=None, total=150, moeda='XTR'):
        query = PreCheckoutQuery(id=f'q{next(self.update_ids)}', from_user=self.usuario_tg(user_id), currency=moeda,
                                 total_amount=total, invoice_payload=payload or payload_de())
        query.set_bot(self.tg)
        return Update(update_id=next(self.update_ids), pre_checkout_query=query)

    def pagamento_tg(self, cobranca, expira, user_id=OUTRO_UID, payload=None, total=150, primeira=True, moeda='XTR'):
        pagamento = SuccessfulPayment(currency=moeda, total_amount=total, invoice_payload=payload or payload_de(),
                                      telegram_payment_charge_id=cobranca, provider_payment_charge_id='',
                                      subscription_expiration_date=utc(expira), is_recurring=True,
                                      is_first_recurring=primeira)
        return self.mensagem_tg(user_id, successful_payment=pagamento)

    def reembolso_tg(self, cobranca, user_id=OUTRO_UID, payload=None, total=150, moeda='XTR'):
        reembolsado = RefundedPayment(currency=moeda, total_amount=total, invoice_payload=payload or payload_de(),
                                      telegram_payment_charge_id=cobranca)
        return self.mensagem_tg(user_id, refunded_payment=reembolsado)

    def despachar(self, update, contexto=None):
        """Grupo -1 (porteiro) e depois o primeiro handler do pagamento que
        aceita o update, como o PTB faz. Devolve o handler que rodou."""
        contexto = contexto or self.ctx()

        async def rodar():
            try:
                await bot.porteiro(update, contexto)
            except bot.ApplicationHandlerStop:
                return 'parado pelo porteiro'
            for handler, _ in self.handlers:
                if handler.check_update(update):
                    await handler.callback(update, contexto)
                    return handler
            return None

        return self.rodar(rodar())

    def pagar(self, cobranca, quando, expira=None, primeira=True, user_id=OUTRO_UID, payload=None, total=150):
        """Um successful_payment que chega ao bot no momento 'quando'
        (renova_em = 'expira', padrão: 30 dias depois)."""
        self.relogio(quando)
        handler = self.despachar(self.pagamento_tg(cobranca, expira or quando + timedelta(days=30), user_id=user_id,
                                                   payload=payload, total=total, primeira=primeira))
        self.assertIsInstance(handler, MessageHandler)
        return handler

    def linhas(self, user_id=OUTRO_UID):
        return self.ler("SELECT origem, estado, inicio, fim, renovacao, renova_em, pagamento_id, assinatura_ref, "
                        "valor, moeda, dias FROM assinaturas WHERE user_id = ? AND plano = 'pro' ORDER BY id",
                        (user_id,))

    def situacao(self, user_id=OUTRO_UID, agora=None):
        return self.executar(assinaturas.situacao_acesso, user_id, agora or RelogioBot.AGORA, admin=False)

    def ultima_mensagem(self):
        return self.tg.mensagens[-1]


class TestHandlersRegistrados(TesteBotPagamento):
    def test_handlers_e_filtros(self):
        tipos = [type(h).__name__ for h, _ in self.handlers]
        self.assertTrue(all(grupo == 0 for _, grupo in self.handlers))
        self.assertIsInstance(self.handlers[0][0], PreCheckoutQueryHandler)
        self.assertIs(self.handlers[0][0].callback, bot.pre_checkout)
        pagamento, reembolso = self.handlers[1][0], self.handlers[2][0]
        self.assertIs(pagamento.filters, bot.filters.SUCCESSFUL_PAYMENT)
        self.assertIs(pagamento.callback, bot.pagamento_confirmado)
        self.assertIs(reembolso.filters, bot.filters.StatusUpdate.REFUNDED_PAYMENT)
        self.assertIs(reembolso.callback, bot.pagamento_reembolsado)
        comandos = {next(iter(h.commands)): h for h, _ in self.handlers if isinstance(h, CommandHandler)}
        self.assertEqual(set(comandos), {'assinatura', 'terms', 'paysupport', 'reembolsar', 'avisos_agora'})
        for livre in ('assinatura', 'terms', 'paysupport'):
            self.assertNotIsInstance(comandos[livre].filters, bot.FiltroAdmin)
            self.assertIn(livre, bot.COMANDOS_LIVRES_PORTEIRO)
        for so_admin in ('reembolsar', 'avisos_agora'):
            self.assertIs(comandos[so_admin].filters, bot.FILTRO_ADMIN)
        self.assertEqual(tipos.count('CallbackQueryHandler'), 3)   # renovação, reembolso e reembolso sem Pro

    def test_registro_no_main_job_das_10_e_polling_com_todos_os_tipos(self):
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            fonte = f.read()
        self.assertEqual(bot.HORARIO_AVISOS_VENCIMENTO, time(hour=10, minute=0, tzinfo=FUSO_BRASILIA))
        self.assertIn('registrar_handlers_pagamento(app)', fonte)
        self.assertRegex(fonte, r'run_daily\(\s*job_avisos_vencimento,\s*HORARIO_AVISOS_VENCIMENTO')
        self.assertIn('allowed_updates=Update.ALL_TYPES', fonte)

    def test_comandos_de_admin_nao_existem_para_os_outros(self):
        mensagem = SimpleNamespace(from_user=SimpleNamespace(id=OUTRO_UID))
        self.assertFalse(bot.FILTRO_ADMIN.filter(mensagem))
        self.assertTrue(bot.FILTRO_ADMIN.filter(SimpleNamespace(from_user=SimpleNamespace(id=UID))))

    def test_porteiro_deixa_passar_pagamento_reembolso_e_pre_checkout_de_quem_venceu(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))   # vencida
        for update in (self.pre_checkout_tg(), self.pagamento_tg('c1', br(2026, 12, 14)), self.reembolso_tg('zz')):
            async def rodar():
                await bot.porteiro(update, self.ctx())
            self.rodar(rodar())   # não levanta ApplicationHandlerStop
        # e o "oi" de quem venceu continua parado, agora com o botão de assinar
        update = self.update_texto("oi", OUTRO_UID)
        with self.assertRaises(bot.ApplicationHandlerStop):
            self.rodar(bot.porteiro(update, self.ctx()))
        self.assertEqual(self.telas[-1][1], bot.TEXTO_ASSINATURA_VENCIDA)
        self.assertEqual(self.telas[-1][2], [["Assinar com Stars"], ["Abrir o L1d3r"]])


# =============================================================================
# PRE-CHECKOUT
# =============================================================================
class TestPreCheckout(TesteBotPagamento):
    def responder(self, update):
        handler = self.despachar(update)
        self.assertIsInstance(handler, PreCheckoutQueryHandler)
        nome, dados = self.tg.chamadas[-1]
        self.assertEqual(nome, 'answer_pre_checkout_query')
        return dados

    def assertRecusado(self, update):
        self.assertEqual(self.responder(update),
                         {'ok': False, 'error_message': "O preço mudou ou a fatura não é sua. Abra a assinatura de novo."})

    def test_aceita_a_fatura_certa_com_uma_consulta_so_sem_ia_e_sem_rede(self):
        consultas = []

        def conexao():
            conn = sqlite3.connect(self.caminho)
            conn.set_trace_callback(consultas.append)
            return conn

        with mock.patch.object(bot, 'obter_conexao_db', conexao):
            self.assertEqual(self.responder(self.pre_checkout_tg()), {'ok': True, 'error_message': None})
        self.assertEqual(len(consultas), 1)
        self.assertTrue(consultas[0].lstrip().upper().startswith('SELECT'))
        self.assertEqual(self.tg.nomes(), ['answer_pre_checkout_query'])   # nenhuma outra chamada ao Telegram
        self.assertEqual(self.ia.chamadas, [])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas"), [(0,)])

    def test_quem_esta_no_teste_e_quem_venceu_podem_pagar(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11))
        self.assertTrue(self.responder(self.pre_checkout_tg())['ok'])
        self.relogio(br(2026, 12, 1))
        self.assertTrue(self.responder(self.pre_checkout_tg())['ok'])

    def test_recusas(self):
        casos = {
            'outra pessoa': self.pre_checkout_tg(user_id=UID + 1, payload=payload_de(OUTRO_UID)),
            'outra moeda': self.pre_checkout_tg(moeda='USD'),
            'outro valor': self.pre_checkout_tg(total=100),
            'payload estranho': self.pre_checkout_tg(payload='qualquer-coisa'),
            'fatura de outro usuário paga pela dona do id certo': self.pre_checkout_tg(payload=payload_de(UID)),
        }
        for nome, update in casos.items():
            with self.subTest(caso=nome):
                self.assertRecusado(update)

    def test_preco_mudou(self):
        self.ambiente(PRECO_PRO_STARS='200')
        self.assertRecusado(self.pre_checkout_tg(total=150, payload=payload_de(stars=150)))
        self.assertTrue(self.responder(self.pre_checkout_tg(total=200, payload=payload_de(stars=200)))['ok'])

    def test_admin_nao_paga(self):
        self.assertRecusado(self.pre_checkout_tg(user_id=UID, payload=payload_de(UID)))

    def test_chave_desligada_ninguem_comeca_assinatura(self):
        self.ambiente(ASSINATURAS_LIGADAS=None)
        self.assertRecusado(self.pre_checkout_tg())

    def test_quem_ja_assina_e_renova_nao_paga_outra_assinatura_mas_a_renovacao_passa(self):
        self.pagar('c1', br(2026, 11, 14))
        self.assertRecusado(self.pre_checkout_tg(payload=payload_de(sufixo='00000002')))
        # A renovação (mesmo payload) passa, mesmo com o preço novo e a chave desligada.
        self.ambiente(PRECO_PRO_STARS='200', ASSINATURAS_LIGADAS=None)
        self.assertTrue(self.responder(self.pre_checkout_tg())['ok'])

    def test_banco_fora_recusa_sem_cobrar(self):
        with mock.patch.object(bot, 'obter_conexao_db', side_effect=RuntimeError("banco fora")):
            self.assertRecusado(self.pre_checkout_tg())

    def test_payload_nunca_no_log(self):
        with self.assertLogs('assinaturas', 'DEBUG') as logs:
            logging.getLogger('assinaturas').debug('marca')
            self.responder(self.pre_checkout_tg(total=100))
            self.pagar('c1', br(2026, 11, 14))
        self.assertNotIn(payload_de(), self.saida.getvalue())
        self.assertNotIn(SUFIXO, '\n'.join(logs.output))


# =============================================================================
# PAGAMENTO (successful_payment)
# =============================================================================
class TestPagamento(TesteBotPagamento):
    def test_primeira_cobranca_libera_o_pro_com_renova_em_em_brasilia(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11))   # no teste: o Pro começa agora
        self.pagar('c1', br(2026, 11, 14, 12), expira=br(2026, 12, 14, 12))
        self.assertEqual(self.linhas(), [
            ('stars', 'ativa', '2026-11-14 12:00:00', '2026-12-14 12:00:00', 1, '2026-12-14 12:00:00', 'c1',
             payload_de(), 150.0, 'XTR', 30)])
        self.assertEqual(self.ultima_mensagem(), (OUTRO_UID, "Pagamento confirmado! Seu Plano Pro vale até "
                                                             "<b>14/12/2026</b>.", [["← Voltar ao Menu"]]))
        situacao = self.situacao()
        self.assertEqual((situacao.estado, situacao.vale_ate, situacao.proxima_renovacao, situacao.valor_stars),
                         ('pro', br(2026, 12, 14), br(2026, 12, 14), 150))

    def test_o_mesmo_pagamento_duas_vezes_grava_uma_linha_e_uma_mensagem(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c1', br(2026, 11, 14, 12, 1))
        self.assertEqual(len(self.linhas()), 1)
        self.assertEqual(len(self.tg.mensagens), 1)

    def test_renovacao_soma_30_dias_ao_fim_unico_com_renova_em_novo(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c2', br(2026, 12, 14), primeira=False)
        linhas = self.linhas()
        self.assertEqual([(l[2], l[3], l[5], l[6]) for l in linhas], [
            ('2026-11-14 12:00:00', '2026-12-14 12:00:00', '2026-12-14 12:00:00', 'c1'),
            ('2026-12-14 12:00:00', '2027-01-13 12:00:00', '2027-01-13 12:00:00', 'c2')])
        self.assertEqual(linhas[1][7], payload_de())   # mesmo assinatura_ref
        self.assertEqual(self.ultima_mensagem()[1], "Assinatura renovada. Seu Plano Pro vale até <b>13/01/2027</b>.")
        self.assertEqual(self.situacao().proxima_renovacao, br(2027, 1, 13))

    def test_renovacao_que_nao_chega_vence_depois_da_tolerancia_de_24_horas(self):
        self.pagar('c1', br(2026, 11, 14))
        self.assertEqual(self.situacao(agora=br(2026, 12, 15, 11, 59, 59)).estado, 'pro')
        self.assertEqual(self.situacao(agora=br(2026, 12, 15, 12)).estado, 'pro_vencido')

    def test_conferencia_falha_nao_grava_e_avisa(self):
        self.pagar('c1', br(2026, 11, 14), payload=payload_de(UID))   # fatura de outro id
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.tg.mensagens[0][:2], (UID, "⚠️ Pagamento em Stars de <code>5151</code> não foi gravado "
                                                          "(pagamento_nao_conferido). Cobrança: <code>c1</code>. O "
                                                          "comprovante está guardado no banco: /reembolsar devolve as "
                                                          "Stars. Ou use /dar_pro 5151 30."))
        self.assertEqual(self.ultima_mensagem()[:2], (OUTRO_UID, bot.TEXTO_PAGAMENTO_NAO_GRAVADO))
        self.tg.mensagens.clear()
        self.pagar('c2', br(2026, 11, 14), total=100)   # valor diferente do payload
        self.assertEqual(self.linhas(), [])

    def test_funcao_registrar_confere_moeda_e_id(self):
        for kwargs in ({'moeda': 'USD'}, {'pagamento_id': ''}, {'pagamento_id': None}, {'total': True}):
            dados = dict(pagador_id=OUTRO_UID, moeda='XTR', total=150, payload=payload_de(), pagamento_id='c1',
                         expiracao=br(2026, 12, 14), agora=br(2026, 11, 14))
            dados.update(kwargs)
            with self.subTest(**{k: repr(v) for k, v in kwargs.items()}), self.assertRaises(ErroRegistro) as erro:
                self.executar(assinaturas.registrar_pagamento_stars, **dados)
            self.assertEqual(erro.exception.codigo, 'pagamento_nao_conferido')
        self.assertEqual(self.linhas(), [])

    def test_valor_stars_e_o_pago_mesmo_com_outro_preco(self):
        self.pagar('c1', br(2026, 11, 14))
        self.ambiente(PRECO_PRO_STARS='200')
        bloco = self.executar(assinaturas.montar_bloco_assinatura, OUTRO_UID, br(2026, 11, 15), admin=False)
        self.assertEqual((bloco['valor_stars'], bloco['preco']['stars']), (150, 200))


# =============================================================================
# COMPROVANTE DE CADA PAGAMENTO (pagamentos_recebidos, pedido do Ygor)
# =============================================================================
class TestComprovante(TesteBotPagamento):
    """O comprovante de cada successful_payment (pagamentos_recebidos), gravado
    ANTES de qualquer coisa que possa falhar, e o /reembolsar que usa o
    comprovante quando o Pro não foi gravado (pedido do Ygor, 2026-11-12)."""

    def comprovantes(self):
        return self.ler("SELECT pagamento_id, user_id, valor, moeda, assinatura_ref, recorrente, recebido_em, situacao, "
                        "erro FROM pagamentos_recebidos ORDER BY id")

    def reembolsar(self):
        self.rodar(bot.comando_reembolsar(self.update_texto('/reembolsar', UID), self.ctx(args=[str(OUTRO_UID)])))
        return self.telas[-1]

    def confirmar_comprovante(self, comprovante_id, quem=UID):
        self.rodar(bot.reembolso_comprovante_confirmado(
            self.update_botao(f'reembolsarc_{OUTRO_UID}_{comprovante_id}', quem), self.ctx()))

    def pagar_sem_gravar_o_pro(self, cobranca='c1'):
        with mock.patch.object(bot, 'registrar_pagamento_stars', side_effect=RuntimeError("quebrou")):
            self.pagar(cobranca, br(2026, 11, 14))

    def test_comprovante_e_gravado_com_o_pro_e_a_renovacao(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c2', br(2026, 12, 14), primeira=False)
        self.assertEqual(self.comprovantes(), [
            ('c1', OUTRO_UID, 150, 'XTR', payload_de(), 0, '2026-11-14 12:00:00', 'pro_gravado', None),
            ('c2', OUTRO_UID, 150, 'XTR', payload_de(), 1, '2026-12-14 12:00:00', 'pro_gravado', None)])

    def test_o_mesmo_pagamento_duas_vezes_grava_um_comprovante(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c1', br(2026, 11, 14, 12, 1))
        self.assertEqual(len(self.comprovantes()), 1)

    def test_comprovante_fica_mesmo_se_a_gravacao_do_pro_quebrar(self):
        self.pagar_sem_gravar_o_pro()
        self.assertEqual(self.linhas(), [])
        self.assertEqual(self.comprovantes(), [
            ('c1', OUTRO_UID, 150, 'XTR', payload_de(), 0, '2026-11-14 12:00:00', 'falhou', 'RuntimeError')])
        self.assertEqual(self.tg.mensagens[0][:2], (UID, "⚠️ Pagamento em Stars de <code>5151</code> não foi gravado "
                                                          "(RuntimeError). Cobrança: <code>c1</code>. O comprovante está "
                                                          "guardado no banco: /reembolsar devolve as Stars. Ou use "
                                                          "/dar_pro 5151 30."))
        self.assertEqual(self.ultima_mensagem()[:2], (OUTRO_UID, bot.TEXTO_PAGAMENTO_NAO_GRAVADO))

    def test_conferencia_que_falha_marca_o_codigo_no_comprovante(self):
        self.pagar('c1', br(2026, 11, 14), payload=payload_de(UID))   # fatura de outro id
        self.assertEqual(self.comprovantes()[0][7:], ('falhou', 'pagamento_nao_conferido'))

    def test_banco_fora_avisa_o_admin_com_o_id_da_cobranca(self):
        with mock.patch.object(bot, 'obter_conexao_db', side_effect=RuntimeError("banco fora")):
            self.pagar('c1', br(2026, 11, 14))
        self.assertEqual(self.tg.mensagens[0][:2], (UID, "⚠️ Pagamento em Stars de <code>5151</code> não foi gravado "
                                                          "(RuntimeError). Cobrança: <code>c1</code>. O comprovante NÃO "
                                                          "foi guardado: use o id da cobrança acima. Ou use /dar_pro "
                                                          "5151 30."))
        self.assertEqual(self.comprovantes(), [])

    def test_reembolsar_sem_pro_gravado_usa_o_comprovante(self):
        self.pagar_sem_gravar_o_pro()
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        chat, texto, botoes = self.reembolsar()
        self.assertEqual(chat, UID)
        self.assertEqual(texto, "Pagamento em Stars de Maria <b>sem Pro gravado</b>: <b>150 Stars</b> em 14/11/2026 "
                                "(cobrança <code>c1</code>).\n\nReembolsar? A renovação é cancelada.")
        self.assertEqual(botoes, [["Reembolsar 150 Stars"], ["← Voltar ao Menu"]])
        comprovante_id = self.ler("SELECT id FROM pagamentos_recebidos")[0][0]
        self.confirmar_comprovante(comprovante_id)
        self.assertEqual(self.tg.chamadas, [
            ('refund_star_payment', {'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1'}),
            ('edit_user_star_subscription', {'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1',
                                             'is_canceled': True})])
        self.assertEqual(self.comprovantes()[0][7], 'reembolsado')
        self.assertEqual([m[:2] for m in self.tg.mensagens], [(OUTRO_UID, "Reembolso de <b>150 Stars</b> feito.")])
        self.assertEqual(self.telas[-1][:2], (UID, "Reembolso de <b>150 Stars</b> para Maria feito (pagamento sem Pro "
                                                   "gravado)."))
        # O refunded_payment que chega depois não repete nada; o /reembolsar não acha mais nada.
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual((self.tg.chamadas, self.tg.mensagens), ([], []))
        self.assertEqual(self.reembolsar()[1], "Não há pagamento em Stars de Maria para reembolsar.")

    def test_botao_do_comprovante_de_quem_nao_e_admin_nao_faz_nada(self):
        self.pagar_sem_gravar_o_pro()
        self.tg.chamadas.clear()
        comprovante_id = self.ler("SELECT id FROM pagamentos_recebidos")[0][0]
        self.confirmar_comprovante(comprovante_id, quem=OUTRO_UID)
        self.assertEqual(self.tg.chamadas, [])
        self.assertEqual(self.comprovantes()[0][7], 'falhou')

    def test_estorno_pela_loja_de_cobranca_sem_pro(self):
        self.pagar_sem_gravar_o_pro()
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.comprovantes()[0][7], 'reembolsado')
        self.assertEqual(self.tg.nomes(), ['edit_user_star_subscription'])
        self.assertEqual([m[:2] for m in self.tg.mensagens], [(OUTRO_UID, "Reembolso de <b>150 Stars</b> feito.")])
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('c1'))   # de novo: nada
        self.assertEqual(self.tg.mensagens, [])

    def test_reembolso_normal_marca_o_comprovante(self):
        self.pagar('c1', br(2026, 11, 14))
        self.relogio(br(2026, 11, 16))
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.comprovantes()[0][7], 'reembolsado')
        self.assertEqual(self.linhas()[0][1], 'reembolsada')


# =============================================================================
# REGRA ÚNICA DE SOMA (D17) COM SuccessfulPayment FALSO
# =============================================================================
class TestSomaD17ComPagamento(TesteBotPagamento):
    def dar_pro(self, quando, dias=30):
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, dias, UID, quando)

    def resumo(self):
        return linhas_resumidas(sqlite3.connect(self.caminho), OUTRO_UID)

    def tearDown(self):
        conn = sqlite3.connect(self.caminho)
        try:
            sem_sobreposicao(self, conn, OUTRO_UID)
        finally:
            conn.close()
        super().tearDown()

    def test_pro_dado_e_depois_assinatura_o_pago_comeca_no_fim_do_dado(self):
        self.dar_pro(br(2026, 11, 1))
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.assertEqual(self.resumo(), [('manual', 'ativa', '01/11', '01/12'), ('stars', 'ativa', '01/12', '31/12')])
        situacao = self.situacao()
        self.assertEqual((situacao.vale_ate, situacao.proxima_renovacao), (br(2026, 12, 31), br(2026, 12, 11)))
        self.assertEqual(self.ultima_mensagem()[1], "Pagamento confirmado! Seu Plano Pro vale até <b>31/12/2026</b>.")
        # renova em 11/12: soma ao fim único, renova_em novo
        self.pagar('c2', br(2026, 12, 11), expira=br(2027, 1, 10), primeira=False)
        self.assertEqual(self.resumo()[-1], ('stars', 'ativa', '31/12', '30/01'))
        situacao = self.situacao()
        self.assertEqual((situacao.vale_ate, situacao.proxima_renovacao), (br(2027, 1, 30), br(2027, 1, 10)))

    def test_reembolso_com_pro_dado_antes_e_renovacao_depois_o_fim_cai_30_dias(self):
        self.dar_pro(br(2026, 11, 1))
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.pagar('c2', br(2026, 12, 11), expira=br(2027, 1, 10), primeira=False)
        self.assertEqual(self.situacao().vale_ate, br(2027, 1, 30))
        self.relogio(br(2026, 12, 15))
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.resumo(), [('manual', 'ativa', '01/11', '01/12'), ('stars', 'reembolsada', '01/12', '15/12'),
                                         ('stars', 'cancelada', '01/12', '31/12')])
        self.assertEqual(self.situacao().vale_ate, br(2026, 12, 31))   # 30/01 - 30 dias
        self.assertEqual(self.ultima_mensagem()[1], "Reembolso de <b>150 Stars</b> feito. Seu Plano Pro vale até "
                                                    "<b>31/12/2026</b>.")

    def test_reembolso_com_pro_dado_antes_sobra_o_dado(self):
        self.dar_pro(br(2026, 11, 1))
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.relogio(br(2026, 11, 15))
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.resumo(), [('manual', 'ativa', '01/11', '01/12'), ('stars', 'reembolsada', '01/12', '01/12')])
        situacao = self.situacao()
        self.assertEqual((situacao.estado, situacao.vale_ate, situacao.proxima_renovacao, situacao.valor_stars),
                         ('pro', br(2026, 12, 1), None, None))

    def test_reembolso_do_unico_pagamento_acaba_agora(self):
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.relogio(br(2026, 11, 20))
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.resumo(), [('stars', 'reembolsada', '11/11', '20/11')])
        self.assertEqual(self.situacao().estado, 'pro_vencido')
        self.assertEqual(self.ultima_mensagem()[1], "Reembolso de <b>150 Stars</b> feito. O Plano Pro foi encerrado.")

    def test_assinatura_e_depois_pro_dado(self):
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.dar_pro(br(2026, 11, 15))
        self.assertEqual(self.resumo(), [('stars', 'ativa', '11/11', '11/12'), ('manual', 'ativa', '11/12', '10/01')])
        situacao = self.situacao(agora=br(2026, 11, 15))
        self.assertEqual((situacao.vale_ate, situacao.proxima_renovacao), (br(2027, 1, 10), br(2026, 12, 11)))

    def test_vencimento_sem_renovacao_com_a_tolerancia(self):
        self.pagar('c1', br(2026, 11, 11), expira=br(2026, 12, 11))
        self.assertEqual(self.situacao(agora=br(2026, 12, 12, 11, 59)).estado, 'pro')
        self.assertEqual(self.situacao(agora=br(2026, 12, 12, 12)).estado, 'pro_vencido')


# =============================================================================
# REEMBOLSO (/reembolsar E refunded_payment)
# =============================================================================
class TestReembolso(TesteBotPagamento):
    def reembolsar(self, args, admin_id=UID):
        self.rodar(bot.comando_reembolsar(self.update_texto('/reembolsar', admin_id), self.ctx(args=args)))
        return self.telas[-1]

    def confirmar(self, dados, quem=UID, tg=None):
        self.rodar(bot.reembolso_confirmado(self.update_botao(dados, quem), self.ctx(tg=tg)))

    def test_reembolsar_mostra_o_ultimo_pagamento_e_confirma_no_botao(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c2', br(2026, 12, 14), primeira=False)
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        self.relogio(br(2026, 12, 15))
        chat, texto, botoes = self.reembolsar([str(OUTRO_UID)])
        self.assertEqual(chat, UID)
        self.assertEqual(texto, "Último pagamento em Stars de Maria: <b>150 Stars</b> em 14/12/2026.\n\n"
                                "Reembolsar? Sai só o período deste pagamento, e a renovação é cancelada.")
        self.assertEqual(botoes, [["Reembolsar 150 Stars"], ["← Voltar ao Menu"]])
        linha_id = self.ler("SELECT id FROM assinaturas WHERE pagamento_id = 'c2'")[0][0]
        self.confirmar(f'reembolsar_{OUTRO_UID}_{linha_id}')
        # refundStarPayment da cobrança c2 e DEPOIS o cancelamento com o id da PRIMEIRA (c1)
        self.assertEqual(self.tg.chamadas, [
            ('refund_star_payment', {'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c2'}),
            ('edit_user_star_subscription', {'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1',
                                             'is_canceled': True})])
        self.assertEqual([(l[1], l[6]) for l in self.linhas()], [('cancelada', 'c1'), ('reembolsada', 'c2')])
        # 13/01 - 30 dias = 14/12, antes de agora: o Pro acaba agora (a c1 já tinha acabado em 14/12)
        self.assertEqual(self.situacao().estado, 'pro_vencido')
        self.assertEqual(self.ler("SELECT fim FROM assinaturas WHERE pagamento_id = 'c2'"), [('2026-12-15 12:00:00',)])
        self.assertEqual(self.telas[-1], (UID, "Reembolso de <b>150 Stars</b> para Maria feito. O Plano Pro foi "
                                               "encerrado.", [["← Voltar ao Menu"]]))
        # A pessoa é avisada na hora (uma mensagem só, para ela; a tela do botão é só do admin): a
        # documentação não garante o refunded_payment para o reembolso feito pelo próprio bot.
        self.assertEqual([m[:2] for m in self.tg.mensagens],
                         [(OUTRO_UID, "Reembolso de <b>150 Stars</b> feito. O Plano Pro foi encerrado.")])
        # Se depois o Telegram mandar o refunded_payment: nada muda de novo e a mensagem não se repete.
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('c2'))
        self.assertEqual(self.tg.nomes(), [])
        self.assertEqual(self.tg.mensagens, [])

    def test_refunded_payment_repetido_nao_repete_a_mensagem(self):
        self.pagar('c1', br(2026, 11, 14))
        self.relogio(br(2026, 11, 16))
        self.despachar(self.reembolso_tg('c1'))
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('c1'))
        self.assertEqual(self.tg.mensagens, [])
        self.assertEqual(self.linhas()[0][1], 'reembolsada')

    def test_sem_pagamento_em_stars(self):
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, 30, UID, br(2026, 11, 1))
        self.assertEqual(self.reembolsar([str(OUTRO_UID)])[1], "Não há pagamento em Stars de Maria para reembolsar.")
        self.assertEqual(self.reembolsar(['999'])[1], "Não há pagamento em Stars de <code>999</code> para reembolsar.")
        self.assertEqual(self.reembolsar([])[1], bot.USO_REEMBOLSAR)
        self.assertEqual(self.reembolsar(['abc'])[1], bot.USO_REEMBOLSAR)
        self.assertEqual(self.tg.chamadas, [])

    def test_botao_de_quem_nao_e_admin_nao_faz_nada(self):
        self.pagar('c1', br(2026, 11, 14))
        linha_id = self.ler("SELECT id FROM assinaturas")[0][0]
        self.tg.chamadas.clear()
        telas = len(self.telas)
        self.confirmar(f'reembolsar_{OUTRO_UID}_{linha_id}', quem=OUTRO_UID)
        self.assertEqual(self.tg.chamadas, [])
        self.assertEqual(len(self.telas), telas)
        self.assertEqual(self.linhas()[0][1], 'ativa')

    def test_telegram_recusa_ou_cai_nada_muda(self):
        self.pagar('c1', br(2026, 11, 14))
        linha_id = self.ler("SELECT id FROM assinaturas")[0][0]
        antes = self.linhas()
        self.confirmar(f'reembolsar_{OUTRO_UID}_{linha_id}', tg=BotTelegramFalso(recusar={'refund_star_payment'}))
        self.assertEqual(self.telas[-1][1], "O Telegram recusou o reembolso (talvez já tenha sido feito).")
        self.confirmar(f'reembolsar_{OUTRO_UID}_{linha_id}', tg=BotTelegramFalso(cair={'refund_star_payment'}))
        self.assertEqual(self.telas[-1][1], bot.TEXTO_TELEGRAM_INDISPONIVEL)
        self.assertEqual(self.linhas(), antes)

    def test_reembolso_feito_mas_cancelamento_cai(self):
        self.pagar('c1', br(2026, 11, 14))
        linha_id = self.ler("SELECT id FROM assinaturas")[0][0]
        self.confirmar(f'reembolsar_{OUTRO_UID}_{linha_id}', tg=BotTelegramFalso(cair={'edit_user_star_subscription'}))
        self.assertEqual(self.linhas()[0][1], 'reembolsada')
        self.assertTrue(self.telas[-1][1].endswith("Não consegui cancelar a renovação agora: tente de novo com "
                                                   "/reembolsar."))

    def test_refunded_payment_simulado_tira_30_dias_cancela_e_avisa(self):
        self.pagar('c1', br(2026, 11, 14))
        self.tg.chamadas.clear()
        self.relogio(br(2026, 11, 16))
        handler = self.despachar(self.reembolso_tg('c1'))
        self.assertIsInstance(handler, MessageHandler)
        self.assertEqual(self.tg.chamadas, [('edit_user_star_subscription', {
            'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1', 'is_canceled': True})])
        self.assertEqual(self.linhas()[0][:4], ('stars', 'reembolsada', '2026-11-14 12:00:00', '2026-11-16 12:00:00'))
        self.assertEqual(self.ultima_mensagem(), (OUTRO_UID, "Reembolso de <b>150 Stars</b> feito. O Plano Pro foi "
                                                             "encerrado.", [["← Voltar ao Menu"]]))

    def test_refunded_payment_desconhecido_de_outra_pessoa_ou_moeda(self):
        self.pagar('c1', br(2026, 11, 14))
        self.tg.chamadas.clear()
        self.tg.mensagens.clear()
        self.despachar(self.reembolso_tg('nao-existe'))
        self.despachar(self.reembolso_tg('c1', user_id=SEM_CADASTRO))
        self.despachar(self.reembolso_tg('c1', moeda='USD'))
        self.assertEqual(self.linhas()[0][1], 'ativa')
        self.assertEqual((self.tg.chamadas, self.tg.mensagens), ([], []))


# =============================================================================
# /assinatura: CANCELAR E REATIVAR A RENOVAÇÃO
# =============================================================================
class TestAssinaturaNoBot(TesteBotPagamento):
    def assinatura(self, user_id=OUTRO_UID):
        self.rodar(bot.comando_assinatura(self.update_texto('/assinatura', user_id), self.ctx()))
        return self.telas[-1][1:]

    def botao(self, dados, user_id=OUTRO_UID, tg=None):
        self.rodar(bot.renovacao_botao(self.update_botao(dados, user_id), self.ctx(tg=tg)))
        return self.telas[-1][1:]

    def test_teste_mostra_assinar_sem_cancelar(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11, 14))
        texto, botoes = self.assinatura()
        self.assertEqual(texto, "<b>Sua Assinatura</b>\n\nTeste até <b>21/11/2026</b>.\n\n"
                                "Ao assinar, você concorda com os Termos de Uso (/terms).")
        self.assertEqual(botoes, [["Assinar com Stars"], ["Abrir o L1d3r"], ["← Voltar ao Menu"]])
        nome, dados = self.tg.chamadas[-1]
        self.assertEqual(nome, 'create_invoice_link')
        self.assertEqual(dados['currency'], 'XTR')
        self.assertEqual(dados['subscription_period'], 2592000)
        self.assertEqual([(p.label, p.amount) for p in dados['prices']], [('Plano Pro (30 dias)', 150)])
        self.assertRegex(dados['payload'], r'^pro1:5151:150:[0-9a-f]{8}$')
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas"), [(1,)])   # a fatura não grava nada

    def test_sem_plano_vencida_admin_e_chave_desligada(self):
        self.assertEqual(self.assinatura()[1], [["Testar Grátis por 10 Dias"], ["Assinar com Stars"], ["Abrir o L1d3r"]])
        # A mesma escolha no porteiro (quem tem cadastro e nunca escolheu) e no /start.
        with self.assertRaises(bot.ApplicationHandlerStop):
            self.rodar(bot.porteiro(self.update_texto("oi", OUTRO_UID), self.ctx()))
        self.assertEqual(self.telas[-1][1:], (bot.TEXTO_ESCOLHA_PLANO,
                                              [["Testar Grátis por 10 Dias"], ["Assinar com Stars"], ["Abrir o L1d3r"]]))
        with mock.patch.object(bot, 'iniciar_banco', lambda: None):
            self.rodar(bot.start(self.update_texto("/start", OUTRO_UID), self.ctx()))
        self.assertEqual(self.telas[-1][2], [["Testar Grátis por 10 Dias"], ["Assinar com Stars"], ["Abrir o L1d3r"]])
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        texto, botoes = self.assinatura()
        self.assertIn("<b>Assinatura vencida.</b>", texto)
        self.assertEqual(botoes, [["Assinar com Stars"], ["Abrir o L1d3r"]])
        texto, botoes = self.assinatura(UID)
        self.assertEqual(texto, "<b>Sua Assinatura</b>\n\nPlano Pro, sem vencimento.")
        self.assertEqual(botoes, [["Abrir o L1d3r"], ["← Voltar ao Menu"]])
        self.ambiente(ASSINATURAS_LIGADAS=None)
        self.assertEqual(self.assinatura()[0], bot.TEXTO_ASSINATURAS_DESLIGADAS)

    def test_cancelar_e_reativar_sem_cobrar_e_o_pro_vale_ate_o_fim(self):
        self.pagar('c1', br(2026, 11, 14))
        self.pagar('c2', br(2026, 12, 14), primeira=False)
        self.relogio(br(2026, 12, 20))
        texto, botoes = self.assinatura()
        self.assertEqual(texto, "<b>Sua Assinatura</b>\n\nPro até <b>13/01/2027</b>, renova sozinho.")
        self.assertEqual(botoes, [["Cancelar Renovação"], ["Abrir o L1d3r"], ["← Voltar ao Menu"]])
        self.tg.chamadas.clear()
        texto, botoes = self.botao(bot.CALLBACK_CANCELAR_RENOVACAO)
        self.assertEqual(self.tg.chamadas, [('edit_user_star_subscription', {
            'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1', 'is_canceled': True})])
        self.assertEqual([(l[1], l[4]) for l in self.linhas()], [('cancelada', 0), ('cancelada', 0)])
        self.assertEqual(texto, "<b>Sua Assinatura</b>\n\nRenovação cancelada. O Pro continua valendo até o fim.\n\n"
                                "Pro até <b>13/01/2027</b>, não renova.")
        self.assertEqual(botoes, [["Reativar Renovação"], ["Abrir o L1d3r"], ["← Voltar ao Menu"]])
        # Cancelado: vale até o fim exato, sem a tolerância.
        self.assertEqual(self.situacao(agora=br(2027, 1, 13, 11, 59)).estado, 'pro')
        self.assertEqual(self.situacao(agora=br(2027, 1, 13, 12)).estado, 'pro_vencido')
        # Cancelar de novo: nada muda e não chama o Telegram.
        self.tg.chamadas.clear()
        self.botao(bot.CALLBACK_CANCELAR_RENOVACAO)
        self.assertEqual(self.tg.chamadas, [])
        # Reativar: sem cobrança, com o id da primeira cobrança.
        texto, botoes = self.botao(bot.CALLBACK_REATIVAR_RENOVACAO)
        self.assertEqual(self.tg.chamadas, [('edit_user_star_subscription', {
            'user_id': OUTRO_UID, 'telegram_payment_charge_id': 'c1', 'is_canceled': False})])
        self.assertNotIn('refund_star_payment', self.tg.nomes())
        self.assertEqual([(l[1], l[4]) for l in self.linhas()], [('ativa', 1), ('ativa', 1)])
        self.assertTrue(texto.endswith("Pro até <b>13/01/2027</b>, renova sozinho."))
        self.assertEqual(len(self.linhas()), 2)   # nenhuma cobrança nova

    def test_reativacao_recusada_oferece_assinar_sem_mexer_no_banco(self):
        self.pagar('c1', br(2026, 11, 14))
        self.botao(bot.CALLBACK_CANCELAR_RENOVACAO)
        antes = self.linhas()
        texto, botoes = self.botao(bot.CALLBACK_REATIVAR_RENOVACAO, tg=BotTelegramFalso(
            recusar={'edit_user_star_subscription'}))
        self.assertEqual(texto, bot.TEXTO_REATIVACAO_RECUSADA)
        self.assertEqual(botoes, [["Assinar com Stars"], ["Abrir o L1d3r"], ["← Voltar ao Menu"]])
        self.assertEqual(self.linhas(), antes)

    def test_telegram_fora_do_ar_nao_grava(self):
        self.pagar('c1', br(2026, 11, 14))
        antes = self.linhas()
        texto, _ = self.botao(bot.CALLBACK_CANCELAR_RENOVACAO, tg=BotTelegramFalso(cair={'edit_user_star_subscription'}))
        self.assertIn(bot.TEXTO_TELEGRAM_INDISPONIVEL, texto)
        self.assertEqual(self.linhas(), antes)


# =============================================================================
# /terms, GET /termos E /paysupport
# =============================================================================
class TestTermosEPaysupport(TesteBotPagamento):
    def test_terms_tem_os_7_itens_aprovados_da_constante_unica(self):
        self.assertEqual(assinaturas.texto_termos(), TEXTO_TERMOS_APROVADO)
        self.rodar(bot.comando_terms(self.update_texto('/terms', OUTRO_UID), self.ctx()))
        texto = self.telas[-1][1]
        self.assertEqual(texto, TEXTO_TERMOS_APROVADO.replace("Termos do L1d3r Pro", "<b>Termos do L1d3r Pro</b>", 1))
        self.assertEqual(re.findall(r'^(\d)\. ', texto, re.M), [str(n) for n in range(1, 8)])
        self.ambiente(PRECO_PRO_STARS='200')
        self.assertIn("1. O Plano Pro custa 200 Stars a cada 30 dias", assinaturas.texto_termos())

    def test_paysupport_repassa_ao_admin_com_id_e_nome_escapados(self):
        update = self.update_texto('/paysupport preciso de <ajuda> & reembolso\nsegunda linha', OUTRO_UID)
        update.effective_user.full_name = 'Maria <b>&</b>'
        self.rodar(bot.comando_paysupport(update, self.ctx()))
        self.assertEqual(self.tg.mensagens, [(UID, "<b>Pedido de suporte (/paysupport)</b>\n"
                                                   "De: Maria &lt;b&gt;&amp;&lt;/b&gt; (<code>5151</code>)\n\n"
                                                   "preciso de &lt;ajuda&gt; &amp; reembolso\nsegunda linha", None)])
        self.assertEqual(self.telas[-1], (OUTRO_UID, "Recebemos o seu pedido. Responderemos por aqui.",
                                          [["← Voltar ao Menu"]]))

    def test_paysupport_sem_mensagem_explica(self):
        self.rodar(bot.comando_paysupport(self.update_texto('/paysupport', OUTRO_UID), self.ctx()))
        self.assertEqual(self.telas[-1][1], bot.USO_PAYSUPPORT)
        self.assertEqual(self.tg.mensagens, [])

    def test_paysupport_sem_admin_ainda_confirma(self):
        self.ambiente(ADMIN_IDS=None)
        self.rodar(bot.comando_paysupport(self.update_texto('/paysupport oi', OUTRO_UID), self.ctx()))
        self.assertEqual(self.telas[-1][1], bot.TEXTO_PAYSUPPORT_RECEBIDO)


# =============================================================================
# AVISOS DE VENCIMENTO
# =============================================================================
class TestAvisos(TesteBotPagamento):
    def job(self, quando):
        self.relogio(quando)
        self.rodar(bot.job_avisos_vencimento(SimpleNamespace(bot=self.tg)))
        enviados = [(chat, texto, botoes) for chat, texto, botoes in self.tg.mensagens]
        self.tg.mensagens.clear()
        return enviados

    BOTOES = [["Assinar com Stars"], ["Abrir o L1d3r"]]

    def test_teste_dois_dias_ultimo_dia_e_venceu_uma_vez_cada(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11, 14))   # vence 21/11 14:00
        self.assertEqual(self.job(br(2026, 11, 18, 10)), [])
        self.assertEqual(self.job(br(2026, 11, 19, 10)),
                         [(OUTRO_UID, "Seu teste termina em 2 dias (21/11).", self.BOTOES)])
        self.assertEqual(self.job(br(2026, 11, 19, 10, 5)), [])   # sem repetir
        self.assertEqual(self.job(br(2026, 11, 20, 10)), [])
        self.assertEqual(self.job(br(2026, 11, 21, 10)), [(OUTRO_UID, "Seu teste termina hoje.", self.BOTOES)])
        self.assertEqual(self.job(br(2026, 11, 21, 10, 30)), [])
        self.assertEqual(self.job(br(2026, 11, 22, 10)), [(OUTRO_UID, assinaturas.TEXTO_AVISO_VENCEU, self.BOTOES)])
        self.assertEqual(self.job(br(2026, 11, 23, 10)), [])
        self.assertEqual(self.job(br(2027, 3, 1, 10)), [])
        self.assertEqual(assinaturas.TEXTO_AVISO_VENCEU,
                         "Sua assinatura venceu. Seus dados continuam guardados, e você ainda pode vê-los no L1d3r "
                         "(só para ver, sem registrar).")

    def test_teste_estendido_avisa_de_novo(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11, 14))
        self.assertEqual(len(self.job(br(2026, 11, 19, 10))), 1)
        self.executar(assinaturas.estender_teste, OUTRO_UID, 5, UID, br(2026, 11, 19, 11))   # vence 26/11
        self.assertEqual(self.job(br(2026, 11, 24, 10)),
                         [(OUTRO_UID, "Seu teste termina em 2 dias (26/11).", self.BOTOES)])

    def test_pro_que_nao_renova_avisa_e_o_que_renova_nao(self):
        self.pagar('c1', br(2026, 11, 14))   # renova sozinho: sem aviso
        self.tg.mensagens.clear()
        self.assertEqual(self.job(br(2026, 12, 12, 10)), [])
        self.assertEqual(self.job(br(2026, 12, 14, 10)), [])
        self.rodar(bot.renovacao_botao(self.update_botao(bot.CALLBACK_CANCELAR_RENOVACAO, OUTRO_UID), self.ctx()))
        self.assertEqual(self.job(br(2026, 12, 12, 10)),
                         [(OUTRO_UID, "Seu Plano Pro termina em 2 dias e não vai renovar.", self.BOTOES)])
        self.assertEqual(self.job(br(2026, 12, 14, 10)), [(OUTRO_UID, "Seu Plano Pro termina hoje.", self.BOTOES)])
        self.assertEqual(self.job(br(2026, 12, 14, 12, 1)),
                         [(OUTRO_UID, assinaturas.TEXTO_AVISO_VENCEU, self.BOTOES)])

    def test_pro_dado_avisa_e_admin_nunca(self):
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, 30, UID, br(2026, 11, 1))   # vale até 01/12
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 10, 1), admin=False)      # admin: nunca
        self.assertEqual(self.job(br(2026, 11, 29, 10)),
                         [(OUTRO_UID, "Seu Plano Pro termina em 2 dias e não vai renovar.", self.BOTOES)])
        self.assertEqual(self.job(br(2026, 12, 5, 10)), [(OUTRO_UID, assinaturas.TEXTO_AVISO_VENCEU, self.BOTOES)])

    def test_chave_desligada_nao_consulta_nem_manda(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.ambiente(ASSINATURAS_LIGADAS=None)
        with mock.patch.object(bot, 'obter_conexao_db', side_effect=AssertionError("consultou")):
            self.assertEqual(self.job(br(2026, 11, 22, 10)), [])
        self.assertEqual(self.executar(assinaturas.avisos_devidos, br(2026, 11, 22), ligada=False), [])

    def test_envio_que_falha_sai_na_proxima(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))

        async def falhar(*args, **kwargs):
            raise NetworkError("fora do ar")

        with mock.patch.object(self.tg, 'send_message', falhar):
            self.job(br(2026, 11, 22, 10))
        self.assertEqual(self.ler("SELECT ultimo_aviso FROM assinaturas"), [(None,)])
        self.assertEqual(len(self.job(br(2026, 11, 22, 11))), 1)
        self.assertEqual(self.ler("SELECT ultimo_aviso FROM assinaturas"),
                         [('venceu:2026-10-11 12:00:00',)])

    def test_sem_link_da_fatura_o_aviso_sai_so_com_o_l1d3r(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.tg.cair.add('create_invoice_link')
        self.assertEqual(self.job(br(2026, 11, 22, 10)), [(OUTRO_UID, assinaturas.TEXTO_AVISO_VENCEU, [["Abrir o L1d3r"]])])

    def test_avisos_agora_manda_e_copia_so_ao_admin(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 11, 11, 14))
        self.relogio(br(2026, 11, 19, 10))
        contexto = self.ctx()
        self.rodar(bot.comando_avisos_agora(self.update_texto('/avisos_agora', UID), contexto))
        self.assertEqual(self.tg.mensagens, [
            (OUTRO_UID, "Seu teste termina em 2 dias (21/11).", self.BOTOES),
            (UID, "Prévia do aviso enviado a 5151:\n\nSeu teste termina em 2 dias (21/11).", self.BOTOES)])
        self.assertEqual(self.telas[-1], (UID, "1 aviso(s) enviado(s).", [["← Voltar ao Menu"]]))
        # De novo: nada se repete.
        self.tg.mensagens.clear()
        self.rodar(bot.comando_avisos_agora(self.update_texto('/avisos_agora', UID), self.ctx()))
        self.assertEqual(self.tg.mensagens, [])
        self.assertEqual(self.telas[-1][1], "0 aviso(s) enviado(s).")

    def test_avisos_agora_com_a_chave_desligada(self):
        self.executar(assinaturas.iniciar_teste, OUTRO_UID, br(2026, 10, 1))
        self.ambiente(ASSINATURAS_LIGADAS=None)
        self.rodar(bot.comando_avisos_agora(self.update_texto('/avisos_agora', UID), self.ctx()))
        self.assertEqual(self.telas[-1][1], "A chave ASSINATURAS_LIGADAS está desligada: nenhum aviso enviado.")
        self.assertEqual(self.tg.mensagens, [])


# =============================================================================
# ROTAS DA API (Bot API falsa injetada por dependência, sem rede)
# =============================================================================
class BotApiFalsa:
    def __init__(self, link=LINK, erro_fatura=None, erro_edicao=None):
        self.link = link
        self.erro_fatura = erro_fatura
        self.erro_edicao = erro_edicao
        self.chamadas = []

    def criar_link_fatura(self, fatura):
        self.chamadas.append(('createInvoiceLink', fatura))
        if self.erro_fatura is not None:
            raise self.erro_fatura
        return self.link

    def editar_assinatura(self, user_id, pagamento_id, cancelar):
        self.chamadas.append(('editUserStarSubscription', user_id, pagamento_id, cancelar))
        if self.erro_edicao is not None:
            raise self.erro_edicao


CAMPOS_DA_FOLHA = ('selo', 'selo_tocavel', 'dias_restantes', 'valor_stars', 'proxima_renovacao',
                   'renovacao_cancelada', 'vale_ate')


class TesteRotasPagamento(TesteApiBase):
    def setUp(self):
        super().setUp()
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.usar_bot_api(BotApiFalsa())

    def usar_bot_api(self, falsa):
        self.bot_api = falsa
        app.dependency_overrides[api_main.obter_bot_api] = lambda: self.bot_api

    def pagar(self, cobranca, quando, primeira=True, user_id=UID, sufixo=SUFIXO):
        return self.executar(assinaturas.registrar_pagamento_stars, user_id, 'XTR', 150,
                             payload_de(user_id, sufixo=sufixo), cobranca, utc(quando + timedelta(days=30)),
                             recorrente=True, primeira_recorrente=primeira, agora=quando)

    def linhas(self):
        return self.ler("SELECT * FROM assinaturas ORDER BY id")


class TestRotaFatura(TesteRotasPagamento):
    def fatura(self, user_id=UID):
        return self.post('/assinaturas/fatura', {"user_id": 1, "plano": "pro"}, user_id)

    def test_teste_ativo_recebe_o_link_sem_gravar(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))
        antes = self.linhas()
        resposta = self.fatura()
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json(), {"link": LINK, "stars": 150})
        (nome, fatura), = self.bot_api.chamadas
        self.assertEqual(nome, 'createInvoiceLink')
        self.assertEqual({k: v for k, v in fatura.items() if k != 'payload'},
                         {k: v for k, v in assinaturas.montar_fatura_pro(UID).items() if k != 'payload'})
        self.assertRegex(fatura['payload'], r'^pro1:4242:150:[0-9a-f]{8}$')   # o id vem do initData
        self.assertEqual(self.linhas(), antes)

    def test_preco_da_variavel(self):
        self.ambiente(PRECO_PRO_STARS='200')
        self.assertEqual(self.fatura().json()['stars'], 200)

    def test_vencido_sem_plano_e_pro_dado_recebem_a_fatura(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 10, 1))
        self.assertEqual(self.fatura().status_code, 200)
        self.assertEqual(self.fatura(OUTRO_UID).status_code, 200)
        self.executar(assinaturas.adicionar_pro, SEM_CADASTRO, 30, 'manual', br(2026, 11, 1), criado_por=UID)
        self.assertEqual(self.fatura(SEM_CADASTRO).status_code, 200)

    def test_renovacao_ligada_409_e_cancelada_200(self):
        self.pagar('c1', br(2026, 11, 11))
        self.assertErro(self.fatura(), 409, "plano_ativo")
        self.assertEqual(self.post('/assinaturas/renovacao', {"cancelar": True}).status_code, 200)
        self.assertEqual(self.fatura().status_code, 200)

    def test_admin_409_e_chave_desligada_409(self):
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertErro(self.fatura(), 409, "plano_ativo")
        self.ambiente(ADMIN_IDS=None, ASSINATURAS_LIGADAS=None)
        self.assertErro(self.fatura(), 409, "assinaturas_desligadas")
        self.assertEqual(self.bot_api.chamadas, [])

    def test_bot_api_falha_502_sem_detalhes_nem_token_no_log(self):
        for falsa in (BotApiFalsa(erro_fatura=api_main.TelegramIndisponivel()),
                      BotApiFalsa(erro_fatura=api_main.TelegramRecusou()),
                      BotApiFalsa(erro_fatura=RuntimeError("https://api.telegram.org/bot123:SEGREDO/x")),
                      BotApiFalsa(link="http://outro.site/x"), BotApiFalsa(link=None)):
            self.usar_bot_api(falsa)
            with self.subTest(falsa=falsa.link), self.assertLogs('api', 'WARNING') as logs:
                resposta = self.fatura()
                self.assertErro(resposta, 502, "telegram_indisponivel")
            saida = '\n'.join(logs.output)
            for proibido in ('pro1:', 'SEGREDO', 'api.telegram.org'):
                self.assertNotIn(proibido, saida)
                self.assertNotIn(proibido, resposta.text)

    def test_sem_initdata_401(self):
        self.assertEqual(self.client.post('/assinaturas/fatura').status_code, 401)


class TestRotaRenovacao(TesteRotasPagamento):
    def renovacao(self, cancelar, user_id=UID):
        return self.post('/assinaturas/renovacao', {"cancelar": cancelar, "user_id": OUTRO_UID}, user_id)

    def folha(self, corpo):
        return {campo: corpo[campo] for campo in CAMPOS_DA_FOLHA}

    def test_cancelar_e_reativar_com_os_campos_do_selo_e_da_folha(self):
        self.pagar('c1', br(2026, 11, 11))
        self.pagar('c2', br(2026, 12, 11), primeira=False)
        self.agora = br(2026, 12, 15)
        resposta = self.renovacao(True)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(self.bot_api.chamadas, [('editUserStarSubscription', UID, 'c1', True)])
        self.assertEqual(self.folha(resposta.json()), {
            "selo": "pro", "selo_tocavel": True, "dias_restantes": 26, "valor_stars": 150,
            "proxima_renovacao": None, "renovacao_cancelada": True, "vale_ate": "2027-01-10"})
        self.assertEqual(resposta.json(), self.get('/assinatura').json())   # a mesma função do GET /assinatura
        resposta = self.renovacao(False)
        self.assertEqual(self.bot_api.chamadas[-1], ('editUserStarSubscription', UID, 'c1', False))
        self.assertEqual(self.folha(resposta.json()), {
            "selo": "pro", "selo_tocavel": True, "dias_restantes": 26, "valor_stars": 150,
            "proxima_renovacao": "2027-01-10", "renovacao_cancelada": False, "vale_ate": "2027-01-10"})

    def test_repetir_o_pedido_nao_muda_nada_nem_chama_o_telegram(self):
        self.pagar('c1', br(2026, 11, 11))
        self.assertEqual(self.renovacao(False).status_code, 200)   # já renova
        self.assertEqual(self.bot_api.chamadas, [])
        self.renovacao(True)
        antes = self.linhas()
        self.assertEqual(self.renovacao(True).status_code, 200)
        self.assertEqual(len(self.bot_api.chamadas), 1)
        self.assertEqual(self.linhas(), antes)

    def test_reativacao_recusada_409_sem_mexer_no_banco(self):
        self.pagar('c1', br(2026, 11, 11))
        self.renovacao(True)
        antes = self.linhas()
        self.usar_bot_api(BotApiFalsa(erro_edicao=api_main.TelegramRecusou()))
        self.assertErro(self.renovacao(False), 409, "reativacao_recusada")
        self.assertEqual(self.linhas(), antes)
        # A saída é a fatura: liberada com a renovação cancelada.
        self.assertEqual(self.post('/assinaturas/fatura', {}).status_code, 200)

    def test_cancelamento_recusado_grava_o_cancelamento(self):
        self.pagar('c1', br(2026, 11, 11))
        self.usar_bot_api(BotApiFalsa(erro_edicao=api_main.TelegramRecusou()))
        self.assertTrue(self.renovacao(True).json()['renovacao_cancelada'])

    def test_telegram_indisponivel_502_sem_gravar(self):
        self.pagar('c1', br(2026, 11, 11))
        antes = self.linhas()
        self.usar_bot_api(BotApiFalsa(erro_edicao=api_main.TelegramIndisponivel()))
        self.assertErro(self.renovacao(True), 502, "telegram_indisponivel")
        self.assertEqual(self.linhas(), antes)

    def test_sem_assinatura_stars(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))
        self.assertErro(self.renovacao(True), 409, "sem_assinatura_stars")                 # teste
        self.executar(assinaturas.dar_pro_manual, OUTRO_UID, 30, UID, br(2026, 11, 1))
        self.assertErro(self.renovacao(True, OUTRO_UID), 409, "sem_assinatura_stars")       # Pro dado
        self.pagar('c9', br(2026, 9, 1), user_id=SEM_CADASTRO)
        self.assertErro(self.renovacao(False, SEM_CADASTRO), 409, "sem_assinatura_stars")   # vencido
        self.pagar('c1', br(2026, 11, 11), user_id=UID, sufixo='00000002')
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertErro(self.renovacao(True), 409, "sem_assinatura_stars")                 # admin
        self.assertEqual(self.bot_api.chamadas, [])

    def test_corpo_invalido_422(self):
        self.pagar('c1', br(2026, 11, 11))
        for corpo in ({}, {"cancelar": "true"}, {"cancelar": 1}, {"cancelar": None}):
            with self.subTest(corpo=corpo):
                self.assertErro(self.post('/assinaturas/renovacao', corpo), 422, "cancelar_invalido")
        self.assertEqual(self.bot_api.chamadas, [])

    def test_valor_stars_e_o_pago_com_outro_preco(self):
        self.pagar('c1', br(2026, 11, 11))
        self.ambiente(PRECO_PRO_STARS='200')
        corpo = self.renovacao(True).json()
        self.assertEqual((corpo['valor_stars'], corpo['preco']['stars']), (150, 200))


class TestRotaTermosERegras(TesteRotasPagamento):
    def test_termos_publico_com_o_mesmo_texto(self):
        resposta = self.client.get('/termos')
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json(), {"texto": TEXTO_TERMOS_APROVADO})
        self.assertEqual(resposta.json()["texto"], assinaturas.texto_termos())

    def test_rotas_novas_livres_da_checagem_e_termos_sem_autenticacao(self):
        def chamadas(dependente):
            for dep in dependente.dependencies:
                yield dep.call
                yield from chamadas(dep)

        rotas = {rota.path: set(chamadas(rota.dependant)) for rota in app.routes if isinstance(rota, APIRoute)}
        for caminho in ('/assinaturas/fatura', '/assinaturas/renovacao', '/termos'):
            with self.subTest(rota=caminho):
                self.assertNotIn(api_main.acesso_liberado, rotas[caminho])
                self.assertNotIn(api_main.leitura_liberada, rotas[caminho])
        self.assertIn(api_main.usuario_autenticado, rotas['/assinaturas/fatura'])
        self.assertIn(api_main.usuario_autenticado, rotas['/assinaturas/renovacao'])
        self.assertEqual(rotas['/termos'], set())

    def test_vencido_usa_as_rotas_novas(self):
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 10, 1))
        self.assertEqual(self.post('/assinaturas/fatura', {}).status_code, 200)
        self.assertErro(self.post('/assinaturas/renovacao', {"cancelar": True}), 409, "sem_assinatura_stars")

    def test_nenhuma_rota_grava_pagamento(self):
        with open(os.path.join(RAIZ, 'api', 'main.py'), encoding='utf-8') as f:
            fonte = f.read()
        for proibido in ('registrar_pagamento_stars', 'adicionar_pro', 'registrar_reembolso', 'retirar_pro'):
            self.assertNotIn(proibido, fonte)
        self.executar(assinaturas.iniciar_teste, UID, br(2026, 11, 11))
        antes = self.linhas()
        self.post('/assinaturas/fatura', {"pagamento_id": "x", "plano": "pro", "dias": 999})
        self.post('/assinaturas/renovacao', {"cancelar": False, "pagamento_id": "x"})
        self.client.get('/termos')
        self.assertEqual(self.linhas(), antes)


class TestBotApiTelegramHttpx(unittest.TestCase):
    """A classe real da API, com o transporte do HTTPX trocado (sem rede)."""

    def setUp(self):
        self.responder = None
        self.vistos = []
        original = httpx.Client

        def transporte(pedido):
            self.vistos.append(pedido)
            return self.responder(pedido)

        def cliente(*args, **kwargs):
            kwargs['transport'] = httpx.MockTransport(transporte)
            return original(*args, **kwargs)

        patcher = mock.patch.object(api_main.httpx, 'Client', cliente)
        patcher.start()
        self.addCleanup(patcher.stop)

    def usar(self, responder):
        self.responder = responder
        return self.vistos

    def test_link_ok_e_metodo_certo(self):
        vistos = self.usar(lambda pedido: httpx.Response(200, json={"ok": True, "result": LINK}))
        api = api_main.BotApiTelegram('123:SEGREDO')
        fatura = assinaturas.montar_fatura_pro(UID, sufixo=SUFIXO)
        self.assertEqual(api.criar_link_fatura(fatura), LINK)
        self.assertEqual(vistos[0].url.path, '/bot123:SEGREDO/createInvoiceLink')
        import json
        self.assertEqual(json.loads(vistos[0].content), fatura)
        api.editar_assinatura(UID, 'c1', True)
        self.assertEqual(json.loads(vistos[1].content),
                         {"user_id": UID, "telegram_payment_charge_id": "c1", "is_canceled": True})
        self.assertNotIn('SEGREDO', repr(api))

    def test_recusa_e_indisponivel(self):
        self.usar(lambda pedido: httpx.Response(400, json={"ok": False, "error_code": 400, "description": "x"}))
        with self.assertRaises(api_main.TelegramRecusou):
            api_main.BotApiTelegram('t').editar_assinatura(UID, 'c1', False)
        for resposta in (httpx.Response(500, json={"ok": False, "error_code": 500}),
                         httpx.Response(429, json={"ok": False, "error_code": 429}),
                         httpx.Response(502, text="<html>")):
            self.usar(lambda pedido, r=resposta: r)
            with self.subTest(status=resposta.status_code), self.assertRaises(api_main.TelegramIndisponivel):
                api_main.BotApiTelegram('t').criar_link_fatura({})

    def test_erro_de_rede_sem_a_url_encadeada(self):
        def cair(pedido):
            raise httpx.ConnectError("falhou em https://api.telegram.org/bot123:SEGREDO/x")

        self.usar(cair)
        with self.assertRaises(api_main.TelegramIndisponivel) as erro:
            api_main.BotApiTelegram('123:SEGREDO').criar_link_fatura({})
        self.assertIsNone(erro.exception.__cause__)
        self.assertTrue(erro.exception.__suppress_context__)
        self.assertEqual(str(erro.exception), '')

    def test_httpx_nao_loga_a_url_com_o_token(self):
        self.assertGreaterEqual(logging.getLogger('httpx').getEffectiveLevel(), logging.WARNING)


class TestDiscloudignore(unittest.TestCase):
    def test_suite_fora_do_deploy(self):
        with open(os.path.join(RAIZ, '.discloudignore'), encoding='utf-8') as f:
            self.assertIn('testes_pagamento_stars.py', f.read().split())


if __name__ == "__main__":
    unittest.main(verbosity=2)
