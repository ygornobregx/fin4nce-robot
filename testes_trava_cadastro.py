"""Testes da trava de cadastro (PRO-109): as variáveis CADASTRO_ABERTO e
LIMITE_TESTES_DIA (assinaturas.cadastro_esta_aberto e limite_testes_dia), o
limite diário de testes grátis novos no iniciar_teste (INSERT ... SELECT com a
contagem do dia, a virada do dia em Brasília e dois pedidos juntos no último
lugar), o campo "cadastro_aberto" do montar_bloco_assinatura, o pagamento que
não olha nenhuma das travas, as rotas (POST /cadastro 403 cadastro_fechado,
POST /assinaturas/teste 409 vagas_teste_esgotadas, GET /assinatura e /me) e as
partes do bot (/start de conta nova, fim do cadastro pelo chat e o botão
"Testar Grátis por 10 Dias").

Roda com `python testes_trava_cadastro.py`. SEM REDE: a mesma costura do
testes_assinaturas.py (sem .env, libsql proibido, SQLite em memória ou em
arquivo temporário, relógio fixo, mensagens do Telegram trocadas por versões
que só guardam o texto). Nenhum pagamento real: a fatura é só o dicionário da
assinaturas.montar_fatura_pro, e o pagamento é gravado com um
successful_payment montado à mão.
"""
import asyncio
import contextlib
import io
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# A costura (sem .env, libsql proibido, ambiente limpo) acontece no import do
# testes_assinaturas.py, antes do import do bot.
import testes_assinaturas as base  # noqa: E402
from testes_assinaturas import (  # noqa: E402
    AmbienteLimpo, TesteApiBase, TesteBotBase, br, novo_banco_memoria,
)

import assinaturas  # noqa: E402
import bot  # noqa: E402
import registro  # noqa: E402
from api.main import STATUS_ERRO_REGISTRO  # noqa: E402
from registro import ErroRegistro  # noqa: E402
from testes_rotas_leitura import ESQUEMA  # noqa: E402
from testes_rotas_registro import OUTRO_UID, SEM_CADASTRO, UID  # noqa: E402

logging.getLogger("api").addHandler(logging.NullHandler())

AGORA = br(2026, 11, 14, 12)


def contar_testes(conn):
    return conn.execute("SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste'").fetchone()[0]


def banco_em_arquivo(pasta, nome='trava.db'):
    caminho = os.path.join(pasta, nome)
    conn = sqlite3.connect(caminho)
    conn.executescript(ESQUEMA)
    conn.executescript(assinaturas.SQL_CRIAR_TABELAS)
    conn.commit()
    conn.close()
    return caminho


# =============================================================================
# VARIÁVEIS DE AMBIENTE
# =============================================================================
class TestVariaveisDaTrava(AmbienteLimpo):
    def test_constante_e_variaveis_limpas_pela_costura(self):
        self.assertEqual(assinaturas.LIMITE_TESTES_DIA_PADRAO, 20)
        self.assertIn('CADASTRO_ABERTO', base.VARIAVEIS)
        self.assertIn('LIMITE_TESTES_DIA', base.VARIAVEIS)

    def test_limite_testes_dia_valores_validos_sem_log(self):
        for valor, esperado in ((None, 20), ('', 20), ('   ', 20), ('0', 0), ('5', 5), (' 5 ', 5), ('20', 20),
                                ('1000', 1000)):
            with self.subTest(valor=valor):
                self.ambiente(LIMITE_TESTES_DIA=valor)
                with self.assertNoLogs('assinaturas', 'WARNING'):
                    self.assertEqual(assinaturas.limite_testes_dia(), esperado)

    def test_limite_testes_dia_invalido_vale_20_com_aviso_sem_o_valor(self):
        for valor in ('-1', 'abc', '2.5', '1e3', '١٧', '5 dias'):
            with self.subTest(valor=valor):
                self.ambiente(LIMITE_TESTES_DIA=valor)
                with self.assertLogs('assinaturas', 'WARNING') as logs:
                    self.assertEqual(assinaturas.limite_testes_dia(), 20)
                texto = '\n'.join(logs.output)
                self.assertIn('LIMITE_TESTES_DIA', texto)
                self.assertNotIn(valor, texto)

    def test_cadastro_aberto_valores_conhecidos_sem_log(self):
        casos = ((None, True), ('', True), ('  ', True), ('1', True), ('true', True), ('TRUE', True), (' sim ', True),
                 ('Sim', True), ('0', False), ('false', False), ('False', False), ('nao', False), ('não', False),
                 ('NÃO', False), (' Nao ', False))
        for valor, esperado in casos:
            with self.subTest(valor=valor):
                self.ambiente(CADASTRO_ABERTO=valor)
                with self.assertNoLogs('assinaturas', 'WARNING'):
                    self.assertIs(assinaturas.cadastro_esta_aberto(), esperado)

    def test_cadastro_aberto_lixo_vale_aberto_com_aviso_sem_o_valor(self):
        for valor in ('lixo', 'talvez', '2', 'fechado'):
            with self.subTest(valor=valor):
                self.ambiente(CADASTRO_ABERTO=valor)
                with self.assertLogs('assinaturas', 'WARNING') as logs:
                    self.assertTrue(assinaturas.cadastro_esta_aberto())
                texto = '\n'.join(logs.output)
                self.assertIn('CADASTRO_ABERTO', texto)
                self.assertNotIn(valor, texto)

    def test_cadastro_aberto_lido_na_hora_e_independente_da_chave_das_assinaturas(self):
        self.assertTrue(assinaturas.cadastro_esta_aberto())
        for ligada in (None, '0', '1'):
            with self.subTest(ligada=ligada):
                self.ambiente(ASSINATURAS_LIGADAS=ligada, CADASTRO_ABERTO='0')
                self.assertFalse(assinaturas.cadastro_esta_aberto())
        self.ambiente(CADASTRO_ABERTO=None)
        self.assertTrue(assinaturas.cadastro_esta_aberto())


# =============================================================================
# LIMITE DIÁRIO DE TESTES NOVOS (iniciar_teste e pode_comecar_teste)
# =============================================================================
class TestLimiteDeTestes(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.conn = novo_banco_memoria()
        self.addCleanup(self.conn.close)

    def iniciar(self, user_id, agora=AGORA, **kwargs):
        return assinaturas.iniciar_teste(self.conn, user_id, agora, **kwargs)

    def esgotado(self, user_id, agora=AGORA, **kwargs):
        with self.assertRaises(ErroRegistro) as ctx:
            self.iniciar(user_id, agora, **kwargs)
        return ctx.exception.codigo

    def test_padrao_20_o_vigesimo_passa_e_o_vigesimo_primeiro_nao(self):
        for i in range(19):
            self.assertFalse(self.iniciar(1000 + i, AGORA + timedelta(minutes=i)).repetido)
        self.assertTrue(assinaturas.pode_comecar_teste(self.conn, AGORA))
        self.assertFalse(self.iniciar(1019, AGORA + timedelta(hours=1)).repetido)      # o 20º
        self.assertFalse(assinaturas.pode_comecar_teste(self.conn, AGORA))
        self.assertEqual(self.esgotado(1020, AGORA + timedelta(hours=2)), 'vagas_teste_esgotadas')   # o 21º
        self.assertEqual(contar_testes(self.conn), 20)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM assinaturas WHERE user_id = 1020").fetchone()[0], 0)

    def test_limite_pela_variavel(self):
        self.ambiente(LIMITE_TESTES_DIA='2')
        self.iniciar(UID)
        self.iniciar(OUTRO_UID)
        self.assertEqual(self.esgotado(SEM_CADASTRO), 'vagas_teste_esgotadas')
        self.ambiente(LIMITE_TESTES_DIA='3')                       # lida na hora
        self.assertFalse(self.iniciar(SEM_CADASTRO).repetido)

    def test_limite_0_e_1(self):
        self.ambiente(LIMITE_TESTES_DIA='0')
        self.assertFalse(assinaturas.pode_comecar_teste(self.conn, AGORA))
        self.assertEqual(self.esgotado(UID), 'vagas_teste_esgotadas')
        self.assertEqual(contar_testes(self.conn), 0)
        self.ambiente(LIMITE_TESTES_DIA='1')
        self.assertTrue(assinaturas.pode_comecar_teste(self.conn, AGORA))
        self.assertFalse(self.iniciar(UID).repetido)
        self.assertEqual(self.esgotado(OUTRO_UID), 'vagas_teste_esgotadas')
        self.assertEqual(contar_testes(self.conn), 1)
        # O parâmetro 'limite' vale mais que a variável.
        self.assertFalse(self.iniciar(OUTRO_UID, limite=2).repetido)
        self.assertTrue(assinaturas.pode_comecar_teste(self.conn, AGORA, limite=3))
        self.assertFalse(assinaturas.pode_comecar_teste(self.conn, AGORA, limite=2))

    def test_virada_do_dia_em_brasilia(self):
        ontem_fim = br(2026, 11, 13, 23, 59, 59)
        hoje_inicio = br(2026, 11, 14, 0, 0, 0)
        self.iniciar(1, ontem_fim, limite=2)
        self.iniciar(2, ontem_fim, limite=2)
        self.assertEqual(self.esgotado(3, ontem_fim, limite=2), 'vagas_teste_esgotadas')
        self.assertEqual(assinaturas.testes_do_dia(self.conn, ontem_fim), 2)
        # 00:00:00 já é o dia novo: os das 23:59:59 de ontem não contam hoje.
        self.assertEqual(assinaturas.testes_do_dia(self.conn, hoje_inicio), 0)
        self.assertFalse(self.iniciar(3, hoje_inicio, limite=1).repetido)
        # O das 00:00:00 conta para o dia novo (não para ontem).
        self.assertEqual(assinaturas.testes_do_dia(self.conn, hoje_inicio), 1)
        self.assertEqual(assinaturas.testes_do_dia(self.conn, ontem_fim), 2)
        self.assertEqual(self.esgotado(4, br(2026, 11, 14, 23, 59, 59), limite=1), 'vagas_teste_esgotadas')
        self.assertEqual(assinaturas.intervalo_do_dia(ontem_fim), ('2026-11-13 00:00:00', '2026-11-14 00:00:00'))
        self.assertEqual(assinaturas.intervalo_do_dia(hoje_inicio), ('2026-11-14 00:00:00', '2026-11-15 00:00:00'))
        # Relógio em UTC: 02:59:59 UTC do dia 14 ainda é 23:59:59 do dia 13 em Brasília.
        self.assertEqual(assinaturas.testes_do_dia(self.conn, datetime(2026, 11, 14, 2, 59, 59, tzinfo=timezone.utc)), 2)
        self.assertEqual(assinaturas.testes_do_dia(self.conn, datetime(2026, 11, 14, 3, 0, 0, tzinfo=timezone.utc)), 1)

    def test_virada_do_mes_e_do_ano(self):
        self.iniciar(1, br(2026, 12, 31, 23, 59, 59), limite=1)
        self.assertEqual(assinaturas.intervalo_do_dia(br(2026, 12, 31, 8)), ('2026-12-31 00:00:00', '2027-01-01 00:00:00'))
        self.assertFalse(self.iniciar(2, br(2027, 1, 1, 0, 0, 0), limite=1).repetido)

    def test_so_linhas_de_teste_contam(self):
        assinaturas.dar_pro_manual(self.conn, OUTRO_UID, 30, UID, AGORA)
        base.pagar(self.conn, AGORA, 'cobranca-1', user_id=SEM_CADASTRO)
        self.assertEqual(assinaturas.testes_do_dia(self.conn, AGORA), 0)
        self.assertFalse(self.iniciar(UID, limite=1).repetido)

    def test_toque_duplo_de_quem_tem_teste_ativo_nao_e_barrado(self):
        self.iniciar(UID, br(2026, 11, 14, 9), limite=1)
        repetido = self.iniciar(UID, AGORA, limite=1)            # o dia já está no limite
        self.assertEqual((repetido.inicio, repetido.repetido), (br(2026, 11, 14, 9), True))
        repetido = self.iniciar(UID, AGORA, limite=0)
        self.assertTrue(repetido.repetido)
        self.assertEqual(contar_testes(self.conn), 1)

    def test_teste_ja_usado_continua_teste_ja_usado(self):
        self.iniciar(UID, br(2026, 10, 1))
        self.assertEqual(self.esgotado(UID, AGORA, limite=0), 'teste_ja_usado')
        self.assertEqual(self.esgotado(UID, AGORA, limite=20), 'teste_ja_usado')

    def test_admin_e_pro_ativo_seguem_plano_ativo_e_nao_gastam_vaga(self):
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertEqual(self.esgotado(UID, AGORA, limite=0), 'plano_ativo')
        self.assertEqual(self.esgotado(UID, AGORA, limite=20), 'plano_ativo')
        assinaturas.dar_pro_manual(self.conn, OUTRO_UID, 30, UID, br(2026, 11, 1))
        self.assertEqual(self.esgotado(OUTRO_UID, AGORA, limite=0), 'plano_ativo')
        self.assertEqual(contar_testes(self.conn), 0)

    def test_comandos_de_admin_nao_olham_o_limite(self):
        self.ambiente(LIMITE_TESTES_DIA='0')
        reiniciado = assinaturas.reiniciar_teste(self.conn, UID, 1, AGORA)
        self.assertEqual((reiniciado.inicio, reiniciado.repetido), (AGORA, False))
        self.iniciar(OUTRO_UID, br(2026, 10, 1), limite=5)           # um teste antigo
        self.assertEqual(assinaturas.reiniciar_teste(self.conn, OUTRO_UID, 1, AGORA).fim, AGORA + timedelta(days=10))
        self.assertEqual(assinaturas.estender_teste(self.conn, OUTRO_UID, 3, 1, AGORA), AGORA + timedelta(days=13))
        self.assertEqual(contar_testes(self.conn), 2)

    def test_vagas_esgotadas_sobe_sem_novas_tentativas(self):
        esperas = []
        with mock.patch.object(registro, '_esperar', esperas.append):
            self.assertEqual(self.esgotado(UID, AGORA, limite=0), 'vagas_teste_esgotadas')
        self.assertEqual(esperas, [])

    def test_sql_do_insert_tem_a_contagem_do_dia_no_mesmo_comando(self):
        comandos = []
        self.conn.set_trace_callback(comandos.append)
        self.iniciar(UID, limite=1)
        self.conn.set_trace_callback(None)
        inserts = [c for c in comandos if c.lstrip().upper().startswith('INSERT INTO ASSINATURAS')]
        self.assertEqual(len(inserts), 1)
        self.assertIn("SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste' AND inicio >= "
                      "'2026-11-14 00:00:00' AND inicio < '2026-11-15 00:00:00'", inserts[0])
        self.assertTrue(inserts[0].rstrip().endswith(') < 1'))


# =============================================================================
# DOIS PEDIDOS JUNTOS NO ÚLTIMO LUGAR
# =============================================================================
class TestCorridaNoUltimoLugar(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.pasta = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.pasta.cleanup)
        self.caminho = banco_em_arquivo(self.pasta.name)
        patcher = mock.patch.object(registro, '_esperar', lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def conectar(self):
        return sqlite3.connect(self.caminho, timeout=10, check_same_thread=False)

    def total(self):
        conn = self.conectar()
        try:
            return contar_testes(conn)
        finally:
            conn.close()

    def test_o_outro_grava_o_ultimo_lugar_entre_a_leitura_e_o_insert(self):
        """Determinístico: o pedido A lê (sem teste, vaga livre) e, ANTES do
        INSERT dele, o pedido B grava o último lugar em outra conexão. O
        INSERT ... SELECT de A conta de novo, no mesmo comando, e não grava."""
        conn = self.conectar()
        assinaturas.iniciar_teste(conn, 1, AGORA, limite=2)       # um lugar já usado hoje
        original = assinaturas.ler_linhas
        chamadas = []

        def ler_e_deixar_o_outro_passar(c, user_id):
            linhas = original(c, user_id)
            if user_id == UID and not chamadas:
                chamadas.append(user_id)
                outra = self.conectar()
                try:
                    self.assertFalse(assinaturas.iniciar_teste(outra, OUTRO_UID, AGORA, limite=2).repetido)
                finally:
                    outra.close()
            return linhas

        with mock.patch.object(assinaturas, 'ler_linhas', ler_e_deixar_o_outro_passar):
            with self.assertRaises(ErroRegistro) as ctx:
                assinaturas.iniciar_teste(conn, UID, AGORA, limite=2)
        conn.close()
        self.assertEqual(ctx.exception.codigo, 'vagas_teste_esgotadas')
        self.assertEqual(chamadas, [UID])
        self.assertEqual(self.total(), 2)

    def rodar_em_threads(self, user_ids, limite):
        """Cada pedido numa thread e numa conexão própria; todos leem "sem
        teste" (barreira) antes de qualquer um gravar."""
        barreira = threading.Barrier(len(user_ids), timeout=10)
        original = assinaturas.ler_linhas
        contagem = [0]
        trava = threading.Lock()

        def ler_com_barreira(c, user_id):
            linhas = original(c, user_id)
            with trava:
                contagem[0] += 1
                numero = contagem[0]
            if numero <= len(user_ids):
                barreira.wait()
            return linhas

        resultados = {}

        def pedir(indice, user_id):
            c = self.conectar()
            try:
                resultados[indice] = assinaturas.iniciar_teste(c, user_id, AGORA, limite=limite)
            except Exception as erro:  # pragma: no cover - aparece na asserção
                resultados[indice] = erro
            finally:
                c.close()

        with mock.patch.object(assinaturas, 'ler_linhas', ler_com_barreira):
            threads = [threading.Thread(target=pedir, args=(i, u)) for i, u in enumerate(user_ids)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
        return [resultados[i] for i in range(len(user_ids))]

    def test_seis_pedidos_juntos_com_tres_vagas_so_tres_passam(self):
        resultados = self.rodar_em_threads([101, 102, 103, 104, 105, 106], limite=3)
        gravados = [r for r in resultados if isinstance(r, assinaturas.TesteIniciado)]
        recusados = [r for r in resultados if isinstance(r, ErroRegistro)]
        self.assertEqual(len(gravados), 3, resultados)
        self.assertTrue(all(not r.repetido for r in gravados))
        self.assertEqual([r.codigo for r in recusados], ['vagas_teste_esgotadas'] * 3)
        self.assertEqual(self.total(), 3)

    def test_dois_pedidos_juntos_no_ultimo_lugar_so_um_passa(self):
        resultados = self.rodar_em_threads([UID, OUTRO_UID], limite=1)
        self.assertEqual(sorted(type(r).__name__ for r in resultados), ['ErroRegistro', 'TesteIniciado'])
        self.assertEqual([r.codigo for r in resultados if isinstance(r, ErroRegistro)], ['vagas_teste_esgotadas'])
        self.assertEqual(self.total(), 1)

    def test_toque_duplo_da_mesma_pessoa_no_ultimo_lugar_devolve_o_teste(self):
        # Os dois leem "sem teste"; um grava o último lugar; o outro não grava
        # (limite), mas a pessoa tem o teste: repetido, nunca vagas esgotadas.
        resultados = self.rodar_em_threads([UID, UID], limite=1)
        self.assertTrue(all(isinstance(r, assinaturas.TesteIniciado) for r in resultados), resultados)
        self.assertEqual(sorted(r.repetido for r in resultados), [False, True])
        self.assertEqual(self.total(), 1)


# =============================================================================
# BLOCO DA ASSINATURA: "cadastro_aberto"
# =============================================================================
class TestBlocoCadastroAberto(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.conn = novo_banco_memoria()
        self.addCleanup(self.conn.close)

    def bloco(self, user_id=UID, **kwargs):
        return assinaturas.montar_bloco_assinatura(self.conn, user_id, AGORA, **kwargs)

    def test_campo_novo_no_fim_e_o_resto_igual(self):
        aberto = self.bloco()
        self.assertEqual(list(aberto)[-1], 'cadastro_aberto')
        self.assertIs(aberto['cadastro_aberto'], True)
        self.ambiente(CADASTRO_ABERTO='0')
        fechado = self.bloco()
        self.assertIs(fechado['cadastro_aberto'], False)
        self.assertEqual({k: v for k, v in aberto.items() if k != 'cadastro_aberto'},
                         {k: v for k, v in fechado.items() if k != 'cadastro_aberto'})

    def test_vale_com_a_chave_ligada_e_desligada(self):
        self.ambiente(CADASTRO_ABERTO='nao')
        self.assertIs(self.bloco(ligada=False)['cadastro_aberto'], False)
        self.assertIs(self.bloco(ligada=True)['cadastro_aberto'], False)
        self.ambiente(CADASTRO_ABERTO='sim')
        self.assertIs(self.bloco(ligada=False)['cadastro_aberto'], True)

    def test_admin_sempre_true(self):
        self.ambiente(CADASTRO_ABERTO='0', ADMIN_IDS=str(UID))
        self.assertIs(self.bloco()['cadastro_aberto'], True)
        self.assertIs(self.bloco(OUTRO_UID)['cadastro_aberto'], False)
        self.assertIs(self.bloco(OUTRO_UID, admin=True)['cadastro_aberto'], True)


# =============================================================================
# O PAGAMENTO NUNCA OLHA AS TRAVAS
# =============================================================================
class TestPagamentoIgnoraAsTravas(AmbienteLimpo):
    def setUp(self):
        super().setUp()
        self.conn = novo_banco_memoria()
        self.addCleanup(self.conn.close)
        self.ambiente(ASSINATURAS_LIGADAS='1', CADASTRO_ABERTO='0', LIMITE_TESTES_DIA='0')

    def test_fatura_pre_checkout_pagamento_e_pro_manual_passam_com_as_duas_travas(self):
        self.assertFalse(assinaturas.cadastro_esta_aberto())
        self.assertFalse(assinaturas.pode_comecar_teste(self.conn, AGORA))
        # Até sem cadastro em 'usuarios' (quem não consegue se cadastrar pode assinar).
        for user_id in (UID, SEM_CADASTRO):
            with self.subTest(user_id=user_id):
                fatura = assinaturas.preparar_fatura(self.conn, user_id, AGORA)
                self.assertEqual(fatura['currency'], 'XTR')
                total = fatura['prices'][0]['amount']
                self.assertIsNone(assinaturas.conferir_pre_checkout(self.conn, user_id, 'XTR', total, fatura['payload'],
                                                                    AGORA))
                pago = assinaturas.registrar_pagamento_stars(self.conn, user_id, 'XTR', total, fatura['payload'],
                                                             'cobranca-' + str(user_id), None, agora=AGORA)
                self.assertFalse(pago.repetido)
                self.assertEqual(assinaturas.situacao_acesso(self.conn, user_id, AGORA).estado, 'pro')
        lancado = assinaturas.adicionar_pro(self.conn, OUTRO_UID, 30, 'manual', AGORA, criado_por=1)
        self.assertEqual(lancado.fim_do_pro, AGORA + timedelta(days=30))
        self.assertEqual(contar_testes(self.conn), 0)


# =============================================================================
# ROTAS
# =============================================================================
CORPO_CADASTRO = {"nome": "Ana", "dia_fechamento": 10, "categorias": ["Casa"]}


class TestRotasDaTrava(TesteApiBase):
    def cadastrar(self, user_id=SEM_CADASTRO, corpo=CORPO_CADASTRO):
        return self.post("/cadastro", corpo, user_id)

    def test_status_dos_codigos(self):
        self.assertEqual(STATUS_ERRO_REGISTRO['vagas_teste_esgotadas'], 409)

    def test_cadastro_fechado_sem_conta_recebe_403_sem_gravar_e_log_so_com_o_codigo(self):
        self.ambiente(CADASTRO_ABERTO='0')
        with self.assertLogs('api', 'WARNING') as logs:
            resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 403)
        self.assertEqual(resposta.json(), {"detail": "cadastro_fechado"})
        self.assertEqual(self.ler("SELECT * FROM usuarios WHERE user_id = ?", (SEM_CADASTRO,)), [])
        self.assertEqual(self.ler("SELECT * FROM categorias WHERE user_id = ?", (SEM_CADASTRO,)), [])
        texto = '\n'.join(logs.output)
        self.assertIn('cadastro_fechado', texto)
        for dado in ('Ana', 'Casa', str(SEM_CADASTRO)):
            self.assertNotIn(dado, texto)
        # Vem antes da validação: corpo inválido também recebe 403.
        self.assertEqual(self.cadastrar(corpo={"nome": "", "dia_fechamento": 99}).status_code, 403)

    def test_cadastro_fechado_com_conta_continua_409(self):
        self.ambiente(CADASTRO_ABERTO='0')
        resposta = self.cadastrar(UID)
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json(), {"detail": "usuario_ja_cadastrado"})

    def test_admin_cria_com_cadastro_fechado_e_aberto_cria_como_sempre(self):
        self.ambiente(CADASTRO_ABERTO='0', ADMIN_IDS=str(SEM_CADASTRO))
        resposta = self.cadastrar()
        self.assertEqual(resposta.status_code, 201)
        self.assertEqual(resposta.json()['user_id'], SEM_CADASTRO)
        self.ambiente(CADASTRO_ABERTO=None, ADMIN_IDS=None)
        self.escrever("DELETE FROM usuarios WHERE user_id = ?", (SEM_CADASTRO,))
        self.escrever("DELETE FROM categorias WHERE user_id = ?", (SEM_CADASTRO,))
        self.assertEqual(self.cadastrar().status_code, 201)
        self.escrever("DELETE FROM usuarios WHERE user_id = ?", (SEM_CADASTRO,))
        self.escrever("DELETE FROM categorias WHERE user_id = ?", (SEM_CADASTRO,))
        self.ambiente(CADASTRO_ABERTO='lixo')                  # valor estranho: aberto
        self.assertEqual(self.cadastrar().status_code, 201)

    def test_teste_com_vagas_esgotadas_recebe_409(self):
        self.ambiente(LIMITE_TESTES_DIA='0')
        resposta = self.post("/assinaturas/teste", {}, SEM_CADASTRO)
        self.assertEqual(resposta.status_code, 409)
        self.assertEqual(resposta.json(), {"detail": "vagas_teste_esgotadas"})
        self.ambiente(LIMITE_TESTES_DIA='1')
        self.assertEqual(self.post("/assinaturas/teste", {}, SEM_CADASTRO).status_code, 201)
        self.assertEqual(self.post("/assinaturas/teste", {}, OUTRO_UID).json(), {"detail": "vagas_teste_esgotadas"})
        # Toque duplo de quem já começou: 200 repetido, mesmo no limite.
        repetido = self.post("/assinaturas/teste", {}, SEM_CADASTRO)
        self.assertEqual(repetido.status_code, 200)
        self.assertTrue(repetido.json()['repetido'])
        self.assertEqual(self.ler("SELECT COUNT(*) FROM assinaturas WHERE origem = 'teste'"), [(1,)])

    def test_get_assinatura_e_me_com_cadastro_aberto(self):
        self.assertIs(self.get('/assinatura', SEM_CADASTRO).json()['cadastro_aberto'], True)
        self.ambiente(CADASTRO_ABERTO='0')
        self.assertIs(self.get('/assinatura', SEM_CADASTRO).json()['cadastro_aberto'], False)   # chave desligada
        self.ambiente(ASSINATURAS_LIGADAS='1')
        self.assertIs(self.get('/assinatura', SEM_CADASTRO).json()['cadastro_aberto'], False)
        self.assertIs(self.get('/me').json()['assinatura']['cadastro_aberto'], False)
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertIs(self.get('/assinatura').json()['cadastro_aberto'], True)
        self.assertIs(self.get('/me').json()['assinatura']['cadastro_aberto'], True)
        self.ambiente(ASSINATURAS_LIGADAS=None)
        self.assertEqual(set(self.get('/me').json()), {'user_id', 'nome'})   # /me desligado não muda


# =============================================================================
# BOT
# =============================================================================
class TestBotDaTrava(TesteBotBase):
    def start(self, user_id):
        with mock.patch.object(bot, 'iniciar_banco', lambda: None):
            return self.rodar(bot.start(self.update_texto("/start", user_id), self.contexto()))

    def test_textos(self):
        self.assertEqual(bot.TEXTO_CADASTRO_FECHADO, "Novos cadastros estão fechados no momento.")
        self.assertEqual(bot.TEXTO_VAGAS_TESTE_ESGOTADAS,
                         "As vagas de teste de hoje acabaram. Volte amanhã ou assine o Pro.")

    def test_start_de_conta_nova_com_cadastro_fechado_nos_dois_caminhos(self):
        self.ambiente(CADASTRO_ABERTO='0')
        for url in (None, 'https://exemplo.ngrok.app/app/'):
            with self.subTest(url=url):
                self.ambiente(URL_MINI_APP=url)
                self.assertEqual(self.start(SEM_CADASTRO), bot.ConversationHandler.END)
                self.assertEqual(self.telas[-1], (SEM_CADASTRO, bot.TEXTO_CADASTRO_FECHADO, None))
        self.assertEqual(self.ler("SELECT * FROM usuarios WHERE user_id = ?", (SEM_CADASTRO,)), [])

    def test_start_aberto_admin_e_conta_existente(self):
        self.assertEqual(self.start(SEM_CADASTRO), bot.NOME_USUARIO)          # aberto: o cadastro pelo chat
        self.ambiente(URL_MINI_APP='https://exemplo.ngrok.app/app/')
        self.start(SEM_CADASTRO)
        self.assertEqual(self.telas[-1][1], bot.TEXTO_PORTA_DE_ENTRADA)      # aberto: a porta de entrada
        self.ambiente(CADASTRO_ABERTO='0', ADMIN_IDS=str(SEM_CADASTRO))
        self.start(SEM_CADASTRO)
        self.assertEqual(self.telas[-1][1], bot.TEXTO_PORTA_DE_ENTRADA)      # admin passa
        self.ambiente(URL_MINI_APP=None)
        self.assertEqual(self.start(SEM_CADASTRO), bot.NOME_USUARIO)
        self.ambiente(ADMIN_IDS=None)
        self.start(UID)
        self.assertEqual(self.telas[-1][1], "Menu Principal")                 # quem tem conta segue igual

    def contexto_cadastro(self):
        contexto = self.contexto()
        contexto.user_data.update(nome_temp='Ana', renda_temp=1000.0, categorias_temp=['Casa'], gasto_pendente=1)
        return contexto

    def test_fim_do_cadastro_pelo_chat_com_cadastro_fechado_nao_grava(self):
        self.ambiente(CADASTRO_ABERTO='0')
        contexto = self.contexto_cadastro()
        with mock.patch.object(bot, 'agendar_tarefa_alerta', side_effect=AssertionError("agendou")):
            estado = self.rodar(bot.receber_dia_fechamento(self.update_texto("10", SEM_CADASTRO), contexto))
        self.assertEqual(estado, bot.ConversationHandler.END)
        self.assertEqual(self.telas[-1], (SEM_CADASTRO, bot.TEXTO_CADASTRO_FECHADO, None))
        self.assertEqual(self.ler("SELECT * FROM usuarios WHERE user_id = ?", (SEM_CADASTRO,)), [])
        self.assertEqual(self.ler("SELECT * FROM categorias WHERE user_id = ?", (SEM_CADASTRO,)), [])
        for chave in ('nome_temp', 'renda_temp', 'categorias_temp'):
            self.assertNotIn(chave, contexto.user_data)
        self.assertEqual(contexto.user_data, {'gasto_pendente': 1})          # só os temporários do cadastro

    def test_fim_do_cadastro_pelo_chat_aberto_ou_admin_grava(self):
        for ambiente, user_id in (({}, SEM_CADASTRO), ({'CADASTRO_ABERTO': '0', 'ADMIN_IDS': str(OUTRO_UID + 1)},
                                                        OUTRO_UID + 1)):
            with self.subTest(user_id=user_id):
                self.ambiente(**ambiente)
                agendados = []
                with mock.patch.object(bot, 'agendar_tarefa_alerta', lambda fila, uid, hora: agendados.append(uid)):
                    self.rodar(bot.receber_dia_fechamento(self.update_texto("10", user_id), self.contexto_cadastro()))
                self.assertEqual(self.telas[-1][1], "Configuração Concluída, Ana! Tudo pronto para começarmos.")
                self.assertEqual(self.ler("SELECT nome, dia_fechamento FROM usuarios WHERE user_id = ?", (user_id,)),
                                 [('Ana', 10)])
                self.assertEqual(agendados, [user_id])

    def test_fim_do_cadastro_fechado_de_quem_ja_tem_conta_segue_como_antes(self):
        self.ambiente(CADASTRO_ABERTO='0')
        with mock.patch.object(bot, 'agendar_tarefa_alerta', lambda *a: None):
            self.rodar(bot.receber_dia_fechamento(self.update_texto("10", UID), self.contexto_cadastro()))
        self.assertEqual(self.ler("SELECT nome FROM usuarios WHERE user_id = ?", (UID,)), [('Ana',)])

    def botao_teste(self, user_id=OUTRO_UID):
        self.rodar(bot.testar_gratis_botao(self.update_botao(bot.CALLBACK_TESTAR_GRATIS, user_id), self.contexto()))
        return self.telas[-1]

    def test_botao_do_teste_com_vagas_esgotadas(self):
        self.ambiente(LIMITE_TESTES_DIA='0')
        self.assertEqual(self.botao_teste(), (OUTRO_UID, bot.TEXTO_VAGAS_TESTE_ESGOTADAS, None))
        self.ambiente(URL_MINI_APP='https://exemplo.ngrok.app/app/')
        self.assertEqual(self.botao_teste()[2], [["Abrir o L1d3r"]])

        async def link(bot_tg, user_id):
            return 'https://t.me/$fatura'

        with mock.patch.object(bot, 'criar_link_fatura', link):
            tela = self.botao_teste()
        self.assertEqual(tela[1], bot.TEXTO_VAGAS_TESTE_ESGOTADAS)
        self.assertEqual(tela[2], [["Assinar com Stars"], ["Abrir o L1d3r"]])  # sem o "Testar Grátis" de novo
        self.assertEqual(self.ler("SELECT * FROM assinaturas"), [])

    def test_botao_do_teste_com_vaga_admin_e_toque_duplo(self):
        self.ambiente(LIMITE_TESTES_DIA='1')
        self.assertEqual(self.botao_teste()[1], "Seu teste começou! Vale até <b>24/11/2026</b>.")
        self.assertEqual(self.botao_teste()[1], "Seu teste começou! Vale até <b>24/11/2026</b>.")   # toque duplo
        self.assertEqual(self.botao_teste(UID)[1], bot.TEXTO_VAGAS_TESTE_ESGOTADAS)
        self.ambiente(ADMIN_IDS=str(UID))
        self.assertEqual(self.botao_teste(UID)[1], "Menu Principal")           # admin: plano_ativo, como antes

    def test_porteiro_e_pagamento_nao_mudam(self):
        # O porteiro não conhece as travas; o pagamento também não (assinaturas.py).
        import inspect
        fonte_porteiro = inspect.getsource(bot.porteiro)
        for nome in ('cadastro_esta_aberto', 'cadastro_fechado_para', 'limite_testes_dia', 'pode_comecar_teste'):
            self.assertNotIn(nome, fonte_porteiro)
        for funcao in (assinaturas.preparar_fatura, assinaturas.conferir_pre_checkout,
                       assinaturas.registrar_pagamento_stars, assinaturas.adicionar_pro):
            fonte = inspect.getsource(funcao)
            for nome in ('cadastro_esta_aberto', 'limite_testes_dia', 'pode_comecar_teste', 'testes_do_dia'):
                self.assertNotIn(nome, fonte)


if __name__ == '__main__':
    unittest.main(verbosity=2)
