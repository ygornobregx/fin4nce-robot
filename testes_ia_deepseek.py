"""Testes do DeepSeek como IA principal do registro, com o Gemini de reserva
(PRO-111).

Roda com `python testes_ia_deepseek.py`. Não usa rede nem a chave de
verdade:
  - o DeepSeek falso é um httpx.MockTransport, instalado pela costura
    registro.definir_transporte_deepseek;
  - o Gemini falso entra por registro.definir_cliente_ia(..., manter_deepseek=True)
    (sem o manter_deepseek, a IA injetada desliga o DeepSeek, como nos
    testes antigos);
  - a DEEPSEEK_API_KEY de cada teste é uma chave falsa, posta no ambiente só
    durante o teste;
  - os transportes de rede de verdade do httpx são trocados por uma armadilha:
    se algum teste chegasse à rede, a chamada é anotada e o teste falha no
    tearDown.
"""
import asyncio
import json
import os
import re
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import httpx

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

import registro  # noqa: E402
from testes_rotas_registro import TesteRegistroBase  # noqa: E402

CHAVE_FALSA = 'sk-chave-falsa-do-teste-0123456789'
PROMPT = 'prompt-secreto texto-do-usuario 4242'
TEXTO_DEEPSEEK = ('{"tipo": "despesa", "valor": 20, "categoria": "alimentação", '
                  '"descricao": "Pão na Padaria", "estabelecimento": null}')
TEXTO_GEMINI = ('{"tipo": "despesa", "valor": 35, "categoria": "roupas", '
                '"descricao": "Camisa da Reserva", "estabelecimento": null}')
USO_DEEPSEEK = {
    'prompt_tokens': 1480, 'prompt_cache_hit_tokens': 1280, 'prompt_cache_miss_tokens': 200,
    'completion_tokens': 96, 'total_tokens': 1576, 'completion_tokens_details': {'reasoning_tokens': 0},
}
USO_GEMINI = SimpleNamespace(prompt_token_count=650, candidates_token_count=100, thoughts_token_count=470,
                             total_token_count=1220)

LINHA_DEEPSEEK = re.compile(r'^INFO:registro:IA: deepseek tempo=\d+\.\d{2}s entrada=1480 cache=1280 '
                            r'saida=96 pensamento=0 total=1576$')
LINHA_GEMINI = re.compile(r'^INFO:registro:IA: gemini tempo=\d+\.\d{2}s entrada=650 cache=None '
                          r'saida=100 pensamento=470 total=1220$')


def linha_reserva(codigo):
    return re.compile(r'^WARNING:registro:IA: deepseek falhou \(' + re.escape(codigo)
                      + r'\) depois de \d+\.\d{2}s, usando o gemini$')


# Item que faz o DeepSeek falso "pendurar" (nunca responde).
PENDURAR = object()

# Chamadas que chegaram aos transportes de rede de verdade do httpx (deve
# ficar sempre vazia).
CHAMADAS_DE_REDE = []


def _armadilha_assincrona(self, request):
    CHAMADAS_DE_REDE.append(str(request.url))
    raise AssertionError('chamada de rede de verdade num teste')


def _armadilha_sincrona(self, request):
    CHAMADAS_DE_REDE.append(str(request.url))
    raise AssertionError('chamada de rede de verdade num teste')


def corpo_ok(conteudo=TEXTO_DEEPSEEK, usage=USO_DEEPSEEK):
    corpo = {'id': 'falso', 'object': 'chat.completion', 'model': 'deepseek-flash',
             'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': conteudo},
                          'finish_reason': 'stop'}]}
    if usage is not None:
        corpo['usage'] = usage
    return corpo


def resposta_ok(conteudo=TEXTO_DEEPSEEK, usage=USO_DEEPSEEK):
    return httpx.Response(200, json=corpo_ok(conteudo, usage))


class ErroFalsoDoGemini(Exception):
    pass


class GeminiFalso:
    """Grava o modelo e o prompt de cada chamada e devolve as respostas
    roteirizadas (texto ou objeto com .text) ou levanta as exceções da lista."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.models = self

    def generate_content(self, model, contents):
        self.chamadas.append((model, contents))
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        if isinstance(resposta, str):
            return SimpleNamespace(text=resposta)
        return resposta


class DeepSeekFalso:
    """Manipulador do httpx.MockTransport: grava cada pedido e devolve o
    próximo item roteirizado (httpx.Response), levanta a exceção da lista ou,
    com PENDURAR, fica esperando para sempre."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.pedidos = []

    async def __call__(self, request):
        self.pedidos.append(request)
        item = self.respostas.pop(0)
        if item is PENDURAR:
            await asyncio.Event().wait()
        if isinstance(item, Exception):
            raise item
        return item


def instalar_armadilhas_de_rede(caso):
    """Troca os transportes de rede do httpx pela armadilha, durante o teste.
    O transporte do TestClient (da API) não é um desses e continua normal."""
    for alvo, nome, funcao in ((httpx.AsyncHTTPTransport, 'handle_async_request', _armadilha_assincrona),
                               (httpx.HTTPTransport, 'handle_request', _armadilha_sincrona)):
        patcher = mock.patch.object(alvo, nome, funcao)
        patcher.start()
        caso.addCleanup(patcher.stop)
    CHAMADAS_DE_REDE.clear()


class ComIAsFalsas(unittest.TestCase):
    """Base: chave falsa no ambiente, armadilha de rede, asyncio.sleep que só
    grava a espera (as novas tentativas do Gemini não dormem) e um DeepSeek
    falso sem respostas já instalado (nenhum teste usa o transporte padrão)."""

    def setUp(self):
        anterior = registro._cliente_ia
        self.addCleanup(registro.definir_cliente_ia, anterior)   # desliga o DeepSeek de novo
        self.addCleanup(registro.definir_transporte_deepseek, None)
        patcher = mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': CHAVE_FALSA})
        patcher.start()
        self.addCleanup(patcher.stop)
        instalar_armadilhas_de_rede(self)

        self.esperas = []

        async def sleep_falso(segundos, result=None):
            self.esperas.append(segundos)
            return result

        patcher = mock.patch.object(registro.asyncio, 'sleep', sleep_falso)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.instalar(gemini=(), deepseek=())

    def tearDown(self):
        self.assertEqual(CHAMADAS_DE_REDE, [])

    def instalar(self, gemini=(), deepseek=()):
        self.gemini = GeminiFalso(*gemini)
        registro.definir_cliente_ia(self.gemini, manter_deepseek=True)
        self.deepseek = DeepSeekFalso(*deepseek)
        registro.definir_transporte_deepseek(httpx.MockTransport(self.deepseek))

    def gerar(self, prompt=PROMPT):
        return asyncio.run(registro.gerar_conteudo_com_retry(prompt))


# --- DEEPSEEK RESPONDE ---
class TestDeepSeekResponde(ComIAsFalsas):
    def test_constantes(self):
        self.assertEqual(registro.MODELO_DEEPSEEK, 'deepseek-flash')
        self.assertEqual(registro.URL_DEEPSEEK, 'https://api.deepseek.com/chat/completions')
        self.assertEqual(registro.TEMPO_LIMITE_DEEPSEEK, 6.0)
        self.assertEqual(registro.MODELO_IA, 'gemini-3.6-flash')   # a reserva não mudou

    def test_deepseek_responde_e_o_gemini_nao_e_chamado(self):
        self.instalar(gemini=(TEXTO_GEMINI,), deepseek=(resposta_ok(),))
        with self.assertLogs('registro', 'INFO') as logs:
            resposta = self.gerar()
        self.assertEqual(resposta.text, TEXTO_DEEPSEEK)
        self.assertEqual(resposta.usage, USO_DEEPSEEK)
        self.assertEqual(self.gemini.chamadas, [])
        self.assertEqual(len(self.deepseek.pedidos), 1)
        self.assertEqual(self.esperas, [])
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], LINHA_DEEPSEEK)

    def test_pedido_enviado_modelo_pensamento_desligado_e_bearer(self):
        self.instalar(deepseek=(resposta_ok(),))
        with self.assertLogs('registro', 'INFO'):
            self.gerar()
        pedido = self.deepseek.pedidos[0]
        self.assertEqual(pedido.method, 'POST')
        self.assertEqual(str(pedido.url), 'https://api.deepseek.com/chat/completions')
        self.assertEqual(pedido.headers['Authorization'], f'Bearer {CHAVE_FALSA}')
        self.assertEqual(pedido.headers['Content-Type'], 'application/json')
        corpo = json.loads(pedido.content)
        self.assertEqual(corpo['model'], 'deepseek-flash')
        self.assertEqual(corpo['thinking'], {'type': 'disabled'})
        self.assertEqual(corpo, {
            'model': 'deepseek-flash',
            'messages': [{'role': 'user', 'content': PROMPT}],
            'thinking': {'type': 'disabled'},
            'stream': False,
            'max_tokens': 2048,
        })
        # Tempo limite do httpx: 3 s para conectar, 6 s para o resto (o
        # asyncio.wait_for cuida do limite da chamada inteira).
        self.assertEqual(pedido.extensions['timeout'], {'connect': 3.0, 'read': 6.0, 'write': 6.0, 'pool': 6.0})

    def test_chave_lida_na_hora_da_chamada(self):
        self.instalar(deepseek=(resposta_ok(), resposta_ok()))
        with self.assertLogs('registro', 'INFO'):
            self.gerar()
            with mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'sk-outra-chave-falsa'}):
                self.gerar()
        self.assertEqual([p.headers['Authorization'] for p in self.deepseek.pedidos],
                         [f'Bearer {CHAVE_FALSA}', 'Bearer sk-outra-chave-falsa'])

    def test_usage_faltando_vira_none_e_nao_muda_o_retorno(self):
        casos = [
            (None, 'entrada=None cache=None saida=None pensamento=None total=None'),
            ({'prompt_tokens': 10, 'total_tokens': 12}, 'entrada=10 cache=None saida=None pensamento=None total=12'),
            ({'prompt_tokens': 'x', 'completion_tokens': 3, 'completion_tokens_details': None, 'total_tokens': True},
             'entrada=None cache=None saida=3 pensamento=None total=None'),
            ('nao-e-dict', 'entrada=None cache=None saida=None pensamento=None total=None'),
        ]
        for usage, esperado in casos:
            with self.subTest(usage=usage):
                self.instalar(deepseek=(resposta_ok(usage=usage),))
                with self.assertLogs('registro', 'INFO') as logs:
                    resposta = self.gerar()
                self.assertEqual(resposta.text, TEXTO_DEEPSEEK)
                self.assertEqual(self.gemini.chamadas, [])
                self.assertEqual(len(logs.output), 1)
                self.assertRegex(logs.output[0], r'^INFO:registro:IA: deepseek tempo=\d+\.\d{2}s '
                                 + re.escape(esperado) + '$')

    def test_resposta_com_espacos_nas_pontas_vem_como_veio(self):
        self.instalar(deepseek=(resposta_ok(conteudo=f'\n```json\n{TEXTO_DEEPSEEK}\n```\n'),))
        with self.assertLogs('registro', 'INFO'):
            resposta = self.gerar()
        self.assertEqual(resposta.text, f'\n```json\n{TEXTO_DEEPSEEK}\n```\n')
        self.assertEqual(registro.limpar_json_ia(resposta.text)['valor'], 20)


# --- RESERVA: O DEEPSEEK FALHA E O GEMINI RESPONDE ---
class TestReserva(ComIAsFalsas):
    CASOS = [
        ('timeout de leitura do httpx', httpx.ReadTimeout('tempo esgotado'), 'timeout'),
        ('timeout de conexão do httpx', httpx.ConnectTimeout('tempo esgotado'), 'timeout'),
        ('401 chave errada', httpx.Response(401, json={'error': {'message': 'Authentication Fails, '
                                                                 'Your api key: ****6789 is invalid'}}), 'http_401'),
        ('402 sem saldo', httpx.Response(402, json={'error': {'message': 'Insufficient Balance'}}), 'http_402'),
        ('429 limite', httpx.Response(429, json={'error': {'message': 'Rate Limit Reached'}}), 'http_429'),
        ('500 servidor', httpx.Response(500, text='detalhe-do-servidor'), 'http_500'),
        ('503 servidor', httpx.Response(503, text='detalhe-do-servidor'), 'http_503'),
        ('504 servidor', httpx.Response(504, text='detalhe-do-servidor'), 'http_504'),
        ('erro de rede: conexão recusada', httpx.ConnectError('conexão recusada'), 'erro_rede'),
        ('erro de rede: protocolo', httpx.RemoteProtocolError('servidor fechou'), 'erro_rede'),
        ('erro inesperado no transporte', RuntimeError('detalhe-interno'), 'erro_rede'),
        ('JSON sem choices', httpx.Response(200, json={'error': 'detalhe-do-servidor'}), 'resposta_invalida'),
        ('choices vazio', httpx.Response(200, json={'choices': []}), 'resposta_invalida'),
        ('JSON que é lista', httpx.Response(200, json=['x']), 'resposta_invalida'),
        ('corpo que não é JSON', httpx.Response(200, text='<html>detalhe-do-servidor</html>'), 'resposta_invalida'),
        ('content que não é texto', resposta_ok(conteudo=123), 'resposta_invalida'),
        ('content nulo', resposta_ok(conteudo=None), 'resposta_vazia'),
        ('content vazio', resposta_ok(conteudo=''), 'resposta_vazia'),
        ('content só com espaços', resposta_ok(conteudo='  \n\t '), 'resposta_vazia'),
    ]

    def test_cada_falha_cai_no_gemini_com_o_codigo_certo(self):
        for descricao, item, codigo in self.CASOS:
            with self.subTest(descricao):
                self.instalar(gemini=(TEXTO_GEMINI,), deepseek=(item,))
                with self.assertLogs('registro', 'INFO') as logs:
                    resposta = self.gerar()
                self.assertEqual(resposta.text, TEXTO_GEMINI)
                self.assertEqual(len(self.deepseek.pedidos), 1)            # uma tentativa só
                self.assertEqual(self.gemini.chamadas, [('gemini-3.6-flash', PROMPT)])
                # O Gemini falso não tem usage_metadata: só a linha da reserva.
                self.assertEqual(len(logs.output), 1, logs.output)
                self.assertRegex(logs.output[0], linha_reserva(codigo))
                for proibido in (CHAVE_FALSA, '6789', 'Bearer', 'prompt-secreto', 'texto-do-usuario', '4242',
                                 'detalhe-do-servidor', 'detalhe-interno', 'Insufficient', 'Authentication'):
                    self.assertNotIn(proibido, logs.output[0])

    def test_reserva_seguida_da_linha_do_gemini(self):
        self.instalar(gemini=(SimpleNamespace(text=TEXTO_GEMINI, usage_metadata=USO_GEMINI),),
                      deepseek=(httpx.Response(402, json={}),))
        with self.assertLogs('registro', 'INFO') as logs:
            resposta = self.gerar()
        self.assertEqual(resposta.text, TEXTO_GEMINI)
        self.assertEqual(len(logs.output), 2)
        self.assertRegex(logs.output[0], linha_reserva('http_402'))
        self.assertRegex(logs.output[1], LINHA_GEMINI)

    def test_tempo_limite_vale_para_a_chamada_inteira(self):
        """O DeepSeek não responde nunca: o asyncio.wait_for corta no
        TEMPO_LIMITE_DEEPSEEK (aqui reduzido a 0,05 s) e o Gemini responde."""
        self.instalar(gemini=(TEXTO_GEMINI,), deepseek=(PENDURAR,))
        with mock.patch.object(registro, 'TEMPO_LIMITE_DEEPSEEK', 0.05):
            inicio = time.monotonic()
            with self.assertLogs('registro', 'INFO') as logs:
                resposta = self.gerar()
            duracao = time.monotonic() - inicio
        self.assertEqual(resposta.text, TEXTO_GEMINI)
        self.assertLess(duracao, 2.0)
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], linha_reserva('timeout'))
        segundos = float(re.search(r'depois de (\d+\.\d{2})s', logs.output[0]).group(1))
        self.assertGreaterEqual(segundos, 0.04)

    def test_gemini_com_novas_tentativas_depois_da_reserva(self):
        """O DeepSeek tenta uma vez só; o Gemini mantém as novas tentativas."""
        self.instalar(gemini=(ErroFalsoDoGemini('503 UNAVAILABLE'), TEXTO_GEMINI),
                      deepseek=(httpx.Response(503),))
        with self.assertLogs('registro', 'INFO'):
            resposta = self.gerar()
        self.assertEqual(resposta.text, TEXTO_GEMINI)
        self.assertEqual(len(self.deepseek.pedidos), 1)
        self.assertEqual(len(self.gemini.chamadas), 2)
        self.assertEqual(self.esperas, [2])


# --- QUANDO O DEEPSEEK FICA DESLIGADO ---
class TestDeepSeekDesligado(ComIAsFalsas):
    def test_sem_chave_so_o_gemini(self):
        for chave in (None, '', '   '):
            with self.subTest(chave=chave):
                self.instalar(gemini=(TEXTO_GEMINI,), deepseek=(resposta_ok(),))
                ambiente = dict(os.environ)
                ambiente.pop('DEEPSEEK_API_KEY', None)
                if chave is not None:
                    ambiente['DEEPSEEK_API_KEY'] = chave
                with mock.patch.dict(os.environ, ambiente, clear=True):
                    with self.assertNoLogs('registro', 'INFO'):
                        resposta = self.gerar()
                self.assertEqual(resposta.text, TEXTO_GEMINI)
                self.assertEqual(self.deepseek.pedidos, [])
                self.assertEqual(len(self.gemini.chamadas), 1)

    def test_ia_falsa_injetada_desliga_o_deepseek_mesmo_com_a_chave(self):
        self.instalar(deepseek=(resposta_ok(),))
        self.assertEqual(os.environ['DEEPSEEK_API_KEY'], CHAVE_FALSA)
        gemini = GeminiFalso(TEXTO_GEMINI)
        registro.definir_cliente_ia(gemini)            # como os testes antigos fazem
        with self.assertNoLogs('registro', 'INFO'):
            resposta = self.gerar()
        self.assertEqual(resposta.text, TEXTO_GEMINI)
        self.assertEqual(self.deepseek.pedidos, [])
        self.assertEqual(len(gemini.chamadas), 1)

    def test_definir_cliente_none_tambem_desliga(self):
        """Os testes antigos restauram o cliente com definir_cliente_ia(None):
        o DeepSeek continua desligado (o Gemini sem chave dá o erro de sempre)."""
        self.instalar(deepseek=(resposta_ok(),))
        registro.definir_cliente_ia(None)
        self.assertIsNone(registro._chave_deepseek())
        registro.definir_cliente_ia(GeminiFalso(), manter_deepseek=True)
        self.assertEqual(registro._chave_deepseek(), CHAVE_FALSA)


# --- AS DUAS IAS FALHANDO ---
class TestAsDuasFalham(ComIAsFalsas):
    def test_o_mesmo_erro_do_gemini_sobe(self):
        ultimo = ErroFalsoDoGemini('400 INVALID_ARGUMENT')
        self.instalar(gemini=(ultimo,), deepseek=(httpx.Response(402),))
        with self.assertLogs('registro', 'INFO') as logs:
            with self.assertRaises(ErroFalsoDoGemini) as erro:
                self.gerar()
        self.assertIs(erro.exception, ultimo)
        self.assertEqual(len(self.gemini.chamadas), 1)
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], linha_reserva('http_402'))

    def test_tres_503_do_gemini_o_ultimo_sobe(self):
        ultimo = ErroFalsoDoGemini('503 UNAVAILABLE')
        self.instalar(gemini=(ErroFalsoDoGemini('503'), ErroFalsoDoGemini('503'), ultimo),
                      deepseek=(httpx.ConnectError('caiu'),))
        with self.assertLogs('registro', 'INFO'):
            with self.assertRaises(ErroFalsoDoGemini) as erro:
                self.gerar()
        self.assertIs(erro.exception, ultimo)
        self.assertEqual(len(self.deepseek.pedidos), 1)
        self.assertEqual(len(self.gemini.chamadas), 3)
        self.assertEqual(self.esperas, [2, 4])


# --- OS CHAMADORES LEEM O .text DO DEEPSEEK SEM MUDANÇA ---
class TestChamadores(ComIAsFalsas):
    CATEGORIAS = ['Alimentação', 'Roupas']

    def test_interpretar_gasto(self):
        self.instalar(deepseek=(resposta_ok(),))
        with self.assertLogs('registro', 'INFO'):
            gasto = asyncio.run(registro.interpretar_gasto('pão na padaria 20', self.CATEGORIAS))
        self.assertEqual(gasto, registro.GastoInterpretado('despesa', 20.0, 'Alimentação', 'Pão na Padaria',
                                                           None, True))
        self.assertEqual(self.gemini.chamadas, [])
        # O prompt é o mesmo de sempre (montar_prompt_gasto), sem mudar nada.
        enviado = json.loads(self.deepseek.pedidos[0].content)['messages'][0]['content']
        self.assertEqual(enviado, registro.montar_prompt_gasto(self.CATEGORIAS, 'pão na padaria 20'))

    def test_interpretar_previa(self):
        self.instalar(deepseek=(resposta_ok(),))
        with self.assertLogs('registro', 'INFO'):
            previa = asyncio.run(registro.interpretar_previa('gasto', 'pão na padaria 20', self.CATEGORIAS))
        self.assertEqual(previa, registro.Previa('gasto', 20.0, 'Pão na Padaria', None, 'Alimentação'))
        self.assertEqual(self.gemini.chamadas, [])

    def test_interpretar_previa_as_duas_falhando_ia_indisponivel(self):
        self.instalar(gemini=(ErroFalsoDoGemini('400 INVALID_ARGUMENT'),), deepseek=(httpx.Response(500),))
        with self.assertLogs('registro', 'INFO'):
            with self.assertRaises(registro.ErroRegistro) as erro:
                asyncio.run(registro.interpretar_previa('gasto', 'pão 20', self.CATEGORIAS))
        self.assertEqual(erro.exception.codigo, 'ia_indisponivel')


# --- POST /registro/interpretar (mesma montagem do testes_rotas_registro.py) ---
class TestRotaInterpretar(TesteRegistroBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(registro.definir_transporte_deepseek, None)
        patcher = mock.patch.dict(os.environ, {'DEEPSEEK_API_KEY': CHAVE_FALSA})
        patcher.start()
        self.addCleanup(patcher.stop)
        instalar_armadilhas_de_rede(self)

        # A base troca o asyncio visto pelo registro.py por um com só o sleep
        # (falso) e o to_thread; o DeepSeek precisa também do wait_for.
        async def sleep_falso(segundos, result=None):
            self.esperas.append(segundos)
            return result

        patcher = mock.patch.object(registro, 'asyncio', SimpleNamespace(
            sleep=sleep_falso, to_thread=asyncio.to_thread, wait_for=asyncio.wait_for,
            TimeoutError=asyncio.TimeoutError))
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        super().tearDown()
        self.assertEqual(CHAMADAS_DE_REDE, [])

    def instalar_ias(self, gemini=(), deepseek=()):
        self.gemini = GeminiFalso(*gemini)
        registro.definir_cliente_ia(self.gemini, manter_deepseek=True)
        self.deepseek = DeepSeekFalso(*deepseek)
        registro.definir_transporte_deepseek(httpx.MockTransport(self.deepseek))

    def interpretar(self, texto='pão na padaria 20'):
        return self.post('/registro/interpretar', {'tipo': 'gasto', 'texto': texto})

    def test_previa_pelo_deepseek(self):
        self.instalar_ias(deepseek=(resposta_ok(),))
        with self.assertLogs('registro', 'INFO') as logs:
            resposta = self.interpretar()
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual({campo: corpo[campo] for campo in ('tipo', 'valor', 'descricao', 'estabelecimento',
                                                             'categoria')},
                         {'tipo': 'gasto', 'valor': 20.0, 'descricao': 'Pão na Padaria',
                          'estabelecimento': None, 'categoria': 'Alimentação'})
        self.assertEqual(self.gemini.chamadas, [])
        self.assertEqual(len(self.deepseek.pedidos), 1)
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], LINHA_DEEPSEEK)

    def test_as_duas_falhando_503_ia_indisponivel(self):
        self.instalar_ias(gemini=(ErroFalsoDoGemini('400 INVALID_ARGUMENT detalhe-interno'),),
                          deepseek=(httpx.Response(503, text='detalhe-do-servidor'),))
        with self.assertLogs('registro', 'INFO') as logs:
            resposta = self.interpretar()
        self.assertErro(resposta, 503, 'ia_indisponivel')
        for proibido in ('detalhe-interno', 'detalhe-do-servidor', CHAVE_FALSA):
            self.assertNotIn(proibido, resposta.text)
            self.assertNotIn(proibido, '\n'.join(logs.output))
        self.assertEqual(len(self.deepseek.pedidos), 1)
        self.assertEqual(len(self.gemini.chamadas), 1)
        self.assertRegex(logs.output[0], linha_reserva('http_503'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
