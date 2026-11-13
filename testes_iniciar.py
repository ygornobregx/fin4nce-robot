"""Testes da hospedagem (PRO-94): o iniciador (iniciar.py), a página /teste só
com PAGINA_DIAGNOSTICO, o Menu Button configurado pelo bot (D6) e a linha de
conferência do log (D7).

Roda com `python testes_iniciar.py`. Sem .env e sem internet:
  - antes de qualquer import do projeto, o load_dotenv vira no-op, as
    variáveis do bot ficam vazias e o libsql.connect passa a levantar erro;
  - o supervisor roda com processos filhos FALSOS (`python -c ...`, que só
    dormem ou saem com um código), com tempos curtos; o bot e a API de verdade
    nunca sobem;
  - a API é importada em processos separados, com o ambiente controlado, para
    provar o liga e desliga da /teste na subida;
  - o Menu Button usa um bot falso, que só guarda o pedido.
"""
import ast
import asyncio
import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# --- COSTURA: nada de .env nem conexão real (antes de qualquer import do projeto) ---
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False
os.environ['TOKEN'] = ''
os.environ['TURSO_DATABASE_URL'] = ''
os.environ['TURSO_AUTH_TOKEN'] = ''
for _nome in ('URL_MINI_APP', 'PAGINA_DIAGNOSTICO'):
    os.environ.pop(_nome, None)

import libsql  # noqa: E402


def _libsql_proibido(*args, **kwargs):
    raise RuntimeError("Conexão real com o banco é proibida nos testes.")


libsql.connect = _libsql_proibido

from telegram import MenuButtonWebApp  # noqa: E402
from telegram.error import BadRequest, NetworkError  # noqa: E402

import bot  # noqa: E402
import iniciar  # noqa: E402
import registro  # noqa: E402
from api import main as api_main  # noqa: E402

PY = sys.executable
DORME = [PY, '-c', 'import time; time.sleep(60)']


def sai_depois(segundos, codigo=0):
    return [PY, '-c', f'import sys, time; time.sleep({segundos}); sys.exit({codigo})']


def comandos(migracao=None, bot_=None, api=None):
    return {'migracao': migracao or sai_depois(0.2), 'bot': bot_ or DORME, 'api': api or DORME}


class ProcessoGravado:
    """Um Popen de verdade, com o registro do que o supervisor pediu a ele."""

    def __init__(self, nome, processo):
        self.nome = nome
        self.processo = processo
        self.pedidos = []

    def poll(self):
        return self.processo.poll()

    def wait(self, timeout=None):
        return self.processo.wait(timeout=timeout)

    def terminate(self):
        self.pedidos.append('terminate')
        self.processo.terminate()

    def kill(self):
        self.pedidos.append('kill')
        self.processo.kill()


class Fabrica:
    """Fábrica de filhos falsos: guarda, a cada criação, o comando, o
    ambiente e quantos filhos anteriores ainda estavam vivos."""

    NOMES = ('migracao', 'bot', 'api')

    def __init__(self, ao_criar_api=None):
        self.criados = []
        self.vivos_antes = []
        self.ambientes = []
        self.ao_criar_api = ao_criar_api

    def __call__(self, comando, ambiente):
        self.vivos_antes.append([c.nome for c in self.criados if c.poll() is None])
        self.ambientes.append(ambiente)
        nome = self.NOMES[len(self.criados)]
        processo = subprocess.Popen(comando, env=ambiente, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        gravado = ProcessoGravado(nome, processo)
        self.criados.append(gravado)
        if nome == 'api' and self.ao_criar_api:
            self.ao_criar_api()
        return gravado

    def por_nome(self, nome):
        return next(c for c in self.criados if c.nome == nome)

    def matar_todos(self):
        for c in self.criados:
            if c.poll() is None:
                c.processo.kill()
                c.processo.wait()


class TesteSupervisorBase(unittest.TestCase):
    def setUp(self):
        self.fabrica = Fabrica()
        self.addCleanup(lambda: self.fabrica.matar_todos())
        self.sigterm_antes = signal.getsignal(signal.SIGTERM)
        self.sigint_antes = signal.getsignal(signal.SIGINT)

    def rodar(self, cmds, parada=None, porta=8000, espera=5, sinais=True):
        saida = io.StringIO()
        inicio = time.monotonic()
        with contextlib.redirect_stdout(saida):
            codigo = iniciar.supervisionar(cmds, porta, criar_processo=self.fabrica, parada=parada,
                                           intervalo=0.05, espera=espera, sinais=sinais)
        self.duracao = time.monotonic() - inicio
        self.log = saida.getvalue()
        return codigo

    def assertTodosParados(self):
        for c in self.fabrica.criados:
            self.assertIsNotNone(c.poll(), f'{c.nome} continuou rodando')

    def assertSinaisRestaurados(self):
        self.assertIs(signal.getsignal(signal.SIGTERM), self.sigterm_antes)
        self.assertIs(signal.getsignal(signal.SIGINT), self.sigint_antes)


# =============================================================================
# COMANDOS, PORTA E AMBIENTE
# =============================================================================
class TestComandosEPorta(unittest.TestCase):
    def test_porta_padrao_e_8080(self):
        self.assertEqual(iniciar.PORTA_PADRAO, 8080)
        self.assertEqual(iniciar.ler_porta([]), 8080)

    def test_porta_pela_linha_de_comando(self):
        self.assertEqual(iniciar.ler_porta(['--porta', '8000']), 8000)

    def test_porta_invalida_e_recusada(self):
        for valor in ('abc', '0', '70000', '-1'):
            with self.subTest(valor=valor), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    iniciar.ler_porta(['--porta', valor])

    def test_comandos_exatos(self):
        self.assertEqual(iniciar.montar_comandos(8080, python='PY'), {
            'migracao': ['PY', '-c', 'import bot; bot.iniciar_banco()'],
            'bot': ['PY', 'bot.py'],
            'api': ['PY', '-m', 'uvicorn', 'api.main:app', '--host', '0.0.0.0', '--port', '8080', '--workers', '1'],
        })
        self.assertEqual(iniciar.comando_api(8000, python='PY')[7], '8000')
        self.assertEqual(iniciar.comando_bot()[0], sys.executable)

    def test_ambiente_dos_filhos_tem_pythonunbuffered_e_nao_muda_a_base(self):
        base = {'A': '1'}
        self.assertEqual(iniciar.ambiente_dos_filhos(base),
                         {'A': '1', 'PYTHONUNBUFFERED': '1', 'PYTHONIOENCODING': 'utf-8'})
        self.assertEqual(base, {'A': '1'})
        # Uma codificação já escolhida no ambiente é mantida.
        self.assertEqual(iniciar.ambiente_dos_filhos({'PYTHONIOENCODING': 'latin-1'})['PYTHONIOENCODING'], 'latin-1')

    def test_import_bot_da_migracao_nao_cria_o_cliente_da_ia_nem_conecta(self):
        # O `import bot` já aconteceu neste processo, com o libsql proibido: se
        # ele conectasse no import, o import teria falhado.
        self.assertIsNone(registro._cliente_ia)
        self.assertNotIn('google.genai', sys.modules)
        self.assertTrue(callable(bot.iniciar_banco))

    def test_main_carrega_o_env_da_raiz_sem_sobrescrever_e_usa_a_porta(self):
        with mock.patch.object(iniciar, 'load_dotenv') as carregar, \
                mock.patch.object(iniciar, 'supervisionar', return_value=7) as supervisionar:
            self.assertEqual(iniciar.main([]), 7)
            carregar.assert_called_once_with(os.path.join(RAIZ, '.env'), override=False)
            args, _ = supervisionar.call_args
            self.assertEqual(args, (iniciar.montar_comandos(8080), 8080))
            iniciar.main(['--porta', '8000'])
            self.assertEqual(supervisionar.call_args[0], (iniciar.montar_comandos(8000), 8000))

    def test_linha_de_comando_com_porta_invalida_sai_sem_subir_nada(self):
        resultado = subprocess.run([PY, 'iniciar.py', '--porta', 'abc'], cwd=RAIZ, capture_output=True,
                                   text=True, timeout=60)
        self.assertEqual(resultado.returncode, 2)
        self.assertNotIn('Migração', resultado.stdout)

    def test_arquivo_tem_handlers_de_sigterm_e_sigint(self):
        self.assertIn(signal.SIGTERM, iniciar.sinais_de_parada())
        self.assertIn(signal.SIGINT, iniciar.sinais_de_parada())


# =============================================================================
# SUPERVISOR
# =============================================================================
class TestMigracao(TesteSupervisorBase):
    def test_migracao_roda_antes_e_sozinha(self):
        codigo = self.rodar(comandos(migracao=sai_depois(0.3), bot_=sai_depois(0.3)))
        self.assertEqual(codigo, 1)   # o bot falso sai: derruba a API
        self.assertEqual([c.nome for c in self.fabrica.criados], ['migracao', 'bot', 'api'])
        self.assertEqual(self.fabrica.vivos_antes[0], [])
        self.assertEqual(self.fabrica.vivos_antes[1], [])          # a migração já tinha terminado
        self.assertLess(self.log.index('Migração concluída'), self.log.index('Bot iniciado'))
        self.assertLess(self.log.index('Bot iniciado'), self.log.index('API iniciada na porta 8000'))

    def test_falha_da_migracao_nao_sobe_nada(self):
        codigo = self.rodar(comandos(migracao=sai_depois(0, codigo=3)))
        self.assertEqual(codigo, 1)
        self.assertEqual([c.nome for c in self.fabrica.criados], ['migracao'])
        self.assertIn('[iniciar] Migração falhou (código 3): o bot e a API não foram iniciados', self.log)
        self.assertNotIn('Migração concluída', self.log)
        self.assertNotIn('Bot iniciado', self.log)
        self.assertSinaisRestaurados()

    def test_parada_durante_a_migracao_encerra_ela_e_nao_sobe_nada(self):
        parada = iniciar.Parada()
        threading.Timer(0.5, parada.set).start()
        codigo = self.rodar(comandos(migracao=DORME), parada=parada)
        self.assertEqual(codigo, 0)
        self.assertEqual([c.nome for c in self.fabrica.criados], ['migracao'])
        self.assertEqual(self.fabrica.por_nome('migracao').pedidos[0], 'terminate')
        self.assertTodosParados()

    def test_filhos_herdam_o_ambiente_com_pythonunbuffered(self):
        with mock.patch.dict(os.environ, {'VARIAVEL_DO_ENV_FALSA': 'x'}):
            self.rodar(comandos(bot_=sai_depois(0.1)))
        self.assertEqual(len(self.fabrica.ambientes), 3)
        for ambiente in self.fabrica.ambientes:
            self.assertEqual(ambiente['PYTHONUNBUFFERED'], '1')
            self.assertEqual(ambiente['VARIAVEL_DO_ENV_FALSA'], 'x')


class TestVigia(TesteSupervisorBase):
    def test_bot_que_cai_derruba_a_api_mesmo_com_codigo_0(self):
        codigo = self.rodar(comandos(bot_=sai_depois(0.3, codigo=0)))
        self.assertEqual(codigo, 1)
        self.assertIn('[iniciar] Processo bot terminou (código 0): encerrando o outro', self.log)
        self.assertEqual(self.fabrica.por_nome('api').pedidos[0], 'terminate')
        self.assertTodosParados()
        self.assertLess(self.duracao, 15)
        self.assertSinaisRestaurados()

    def test_api_que_cai_derruba_o_bot(self):
        codigo = self.rodar(comandos(api=sai_depois(0.3, codigo=2)))
        self.assertEqual(codigo, 1)
        self.assertIn('[iniciar] Processo api terminou (código 2): encerrando o outro', self.log)
        self.assertEqual(self.fabrica.por_nome('bot').pedidos[0], 'terminate')
        self.assertTodosParados()

    def test_log_sem_valores_de_variaveis(self):
        with mock.patch.dict(os.environ, {'TOKEN': 'token-secreto-123', 'TURSO_DATABASE_URL': 'libsql://banco-x.io'}):
            self.rodar(comandos(bot_=sai_depois(0.1)))
        self.assertNotIn('token-secreto-123', self.log)
        self.assertNotIn('banco-x', self.log)
        for linha in self.log.splitlines():
            self.assertTrue(linha.startswith('[iniciar] '), linha)


class TestSinalDeParada(TesteSupervisorBase):
    def esperar_e_sinalizar(self, sinal):
        def disparar():
            time.sleep(0.5)
            signal.raise_signal(sinal)
        self.fabrica.ao_criar_api = lambda: threading.Thread(target=disparar, daemon=True).start()

    def conferir_parada(self, codigo):
        self.assertEqual(codigo, 0)
        self.assertIn('[iniciar] Encerrando o bot e a API', self.log)
        self.assertEqual(self.fabrica.por_nome('bot').pedidos[0], 'terminate')
        self.assertEqual(self.fabrica.por_nome('api').pedidos[0], 'terminate')
        self.assertTodosParados()
        self.assertLess(self.duracao, 15)
        self.assertSinaisRestaurados()

    def test_sigterm_chega_aos_dois(self):
        self.esperar_e_sinalizar(signal.SIGTERM)
        self.conferir_parada(self.rodar(comandos()))

    def test_sigint_ctrl_c_chega_aos_dois(self):
        self.esperar_e_sinalizar(signal.SIGINT)
        self.conferir_parada(self.rodar(comandos()))

    def test_handler_instalado_so_acende_a_parada(self):
        parada = iniciar.Parada()
        anteriores = iniciar.instalar_sinais(parada)
        try:
            self.assertFalse(parada.is_set())
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(parada.is_set())
            parada2 = iniciar.Parada()
            iniciar.restaurar_sinais(anteriores)
            anteriores = iniciar.instalar_sinais(parada2)
            signal.raise_signal(signal.SIGINT)   # sem KeyboardInterrupt
            self.assertTrue(parada2.is_set())
        finally:
            iniciar.restaurar_sinais(anteriores)
        self.assertSinaisRestaurados()


class ProcessoTeimoso:
    """Não sai com o terminate: o encerrar tem de usar o kill."""

    def __init__(self):
        self.pedidos = []
        self.esperas = []

    def poll(self):
        return None if 'kill' not in self.pedidos else -9

    def terminate(self):
        self.pedidos.append('terminate')

    def kill(self):
        self.pedidos.append('kill')

    def wait(self, timeout=None):
        self.esperas.append(timeout)
        if 'kill' not in self.pedidos:
            raise subprocess.TimeoutExpired('falso', timeout)
        return -9


class TestEncerrar(unittest.TestCase):
    def test_kill_depois_da_espera(self):
        teimoso = ProcessoTeimoso()
        iniciar.encerrar([teimoso], espera=0.2)
        self.assertEqual(teimoso.pedidos, ['terminate', 'kill'])
        self.assertLessEqual(teimoso.esperas[0], 0.2)

    def test_espera_padrao_e_10_segundos(self):
        self.assertEqual(iniciar.ESPERA_ENCERRAR, 10)

    def test_processo_ja_parado_nao_recebe_nada(self):
        parado = ProcessoTeimoso()
        parado.pedidos.append('kill')   # poll devolve -9
        iniciar.encerrar([parado], espera=0.1)
        self.assertEqual(parado.pedidos, ['kill'])


# =============================================================================
# /teste SÓ COM PAGINA_DIAGNOSTICO
# =============================================================================
SCRIPT_API = r'''
import json
from fastapi.testclient import TestClient
from api.main import app
cliente = TestClient(app)
teste = cliente.get("/teste/")
print(json.dumps({
    "app": cliente.get("/app/").status_code,
    "teste": teste.status_code,
    "corpo_teste": teste.text,
    "montadas": sorted(r.name for r in app.routes if type(r).__name__ == "Mount"),
}))
'''


def rodar_api(cwd, pagina=None):
    ambiente = {k: v for k, v in os.environ.items() if k not in ('PAGINA_DIAGNOSTICO', 'PYTHONPATH')}
    if pagina is not None:
        ambiente['PAGINA_DIAGNOSTICO'] = pagina
    return subprocess.run([PY, '-c', SCRIPT_API], cwd=cwd, env=ambiente, capture_output=True, text=True,
                          encoding='utf-8', timeout=120)


class TestPaginaDiagnostico(unittest.TestCase):
    def test_valores_que_ligam(self):
        for valor in ('1', 'true', 'TRUE', 'True', 'sim', 'SIM', ' Sim ', '\t1\n'):
            with self.subTest(valor=valor):
                self.assertTrue(api_main.pagina_diagnostico_ligada({'PAGINA_DIAGNOSTICO': valor}))

    def test_valores_que_nao_ligam(self):
        for valor in (None, '', ' ', '0', 'false', 'nao', 'não', 'yes', 'on', '2', 's', 'verdadeiro'):
            with self.subTest(valor=valor):
                ambiente = {} if valor is None else {'PAGINA_DIAGNOSTICO': valor}
                self.assertFalse(api_main.pagina_diagnostico_ligada(ambiente))

    def test_le_o_ambiente_do_processo(self):
        with mock.patch.dict(os.environ, {'PAGINA_DIAGNOSTICO': 'sim'}):
            self.assertTrue(api_main.pagina_diagnostico_ligada())
        with mock.patch.dict(os.environ, {}):
            os.environ.pop('PAGINA_DIAGNOSTICO', None)
            self.assertFalse(api_main.pagina_diagnostico_ligada())

    def test_desligada_teste_da_404_e_app_abre(self):
        resultado = rodar_api(RAIZ)
        self.assertEqual(resultado.returncode, 0, resultado.stderr[-500:])
        dados = json.loads(resultado.stdout.strip().splitlines()[-1])
        self.assertEqual(dados['app'], 200)
        self.assertEqual(dados['teste'], 404)
        self.assertEqual(dados['corpo_teste'], '{"detail":"Not Found"}')
        self.assertEqual(dados['montadas'], ['app'])

    def test_ligada_teste_abre_e_app_tambem(self):
        for valor in ('1', ' TRUE ', 'Sim'):
            with self.subTest(valor=valor):
                resultado = rodar_api(RAIZ, valor)
                self.assertEqual(resultado.returncode, 0, resultado.stderr[-500:])
                dados = json.loads(resultado.stdout.strip().splitlines()[-1])
                self.assertEqual(dados['app'], 200)
                self.assertEqual(dados['teste'], 200)
                self.assertIn('Teste do initData', dados['corpo_teste'])
                self.assertEqual(dados['montadas'], ['app', 'teste'])

    def test_api_sobe_sem_a_pasta_teste_initdata_quando_desligada(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as pasta:
            for nome in os.listdir(RAIZ):
                if nome.endswith('.py') and not nome.startswith('testes_'):
                    shutil.copy2(os.path.join(RAIZ, nome), pasta)
            os.makedirs(os.path.join(pasta, 'api'))
            for nome in os.listdir(os.path.join(RAIZ, 'api')):
                if nome.endswith('.py'):
                    shutil.copy2(os.path.join(RAIZ, 'api', nome), os.path.join(pasta, 'api'))
            shutil.copytree(os.path.join(RAIZ, 'mini-apps', 'home'), os.path.join(pasta, 'mini-apps', 'home'))
            self.assertFalse(os.path.exists(os.path.join(pasta, 'mini-apps', 'teste-initdata')))

            resultado = rodar_api(pasta)
            self.assertEqual(resultado.returncode, 0, resultado.stderr[-500:])
            dados = json.loads(resultado.stdout.strip().splitlines()[-1])
            self.assertEqual((dados['app'], dados['teste'], dados['montadas']), (200, 404, ['app']))

            # Ligada, a pasta é exigida (o StaticFiles recusa pasta que não existe).
            self.assertNotEqual(rodar_api(pasta, '1').returncode, 0)


# =============================================================================
# MENU BUTTON (D6) E LINHA DE CONFERÊNCIA (D7)
# =============================================================================
URL_HTTPS = 'https://l1d3r.exemplo.dev/app/'
URL_BANCO_TESTE = 'libsql://fin4ncetest-l1d3r.aws-us-east-1.turso.io'
URL_BANCO_PRODUCAO = 'libsql://fin4nce-l1d3r.aws-us-east-1.turso.io'
TOKEN_FALSO = '123456:token-falso-do-bot'


class BotFalso:
    def __init__(self, username='L1d3rTestBot', erro=None):
        self.username = username
        self.erro = erro
        self.pedidos = []

    async def set_chat_menu_button(self, **kwargs):
        self.pedidos.append(kwargs)
        if self.erro:
            raise self.erro


class TesteBotBase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('URL_MINI_APP', None)

    def rodar(self, corrotina):
        saida = io.StringIO()
        with contextlib.redirect_stdout(saida):
            resultado = asyncio.run(corrotina)
        self.saida = saida.getvalue()
        return resultado


class TestMenuButton(TesteBotBase):
    def test_com_https_configura_abrir_o_l1d3r(self):
        os.environ['URL_MINI_APP'] = URL_HTTPS
        falso = BotFalso()
        self.assertTrue(self.rodar(bot.configurar_menu_button(falso)))
        self.assertEqual(len(falso.pedidos), 1)
        self.assertEqual(set(falso.pedidos[0]), {'menu_button'})   # sem chat_id: vale para todos
        botao = falso.pedidos[0]['menu_button']
        self.assertIsInstance(botao, MenuButtonWebApp)
        self.assertEqual(botao.text, 'Abrir o L1d3r')
        self.assertEqual(botao.web_app.url, URL_HTTPS)
        self.assertIn('Menu Button configurado: Abrir o L1d3r', self.saida)
        self.assertNotIn(URL_HTTPS, self.saida)

    def test_sem_https_nao_mexe_no_menu_button(self):
        for valor in (None, '', '   ', 'http://l1d3r.exemplo.dev/app/', 'https://', 'l1d3r.exemplo.dev/app/'):
            with self.subTest(valor=valor):
                if valor is None:
                    os.environ.pop('URL_MINI_APP', None)
                else:
                    os.environ['URL_MINI_APP'] = valor
                falso = BotFalso()
                self.assertFalse(self.rodar(bot.configurar_menu_button(falso)))
                self.assertEqual(falso.pedidos, [])
                self.assertIn('Menu Button: sem URL_MINI_APP, não alterado', self.saida)

    def test_falha_vira_aviso_sem_a_url_e_nao_derruba(self):
        os.environ['URL_MINI_APP'] = URL_HTTPS
        for erro in (BadRequest(f'Url {URL_HTTPS} invalida'), NetworkError('rede'), RuntimeError(URL_HTTPS)):
            with self.subTest(erro=type(erro).__name__):
                falso = BotFalso(erro=erro)
                self.assertFalse(self.rodar(bot.configurar_menu_button(falso)))
                self.assertEqual(len(falso.pedidos), 1)
                self.assertIn(f'Aviso: não consegui configurar o Menu Button ({type(erro).__name__}). '
                              'O bot continua normalmente.', self.saida)
                self.assertNotIn('l1d3r.exemplo.dev', self.saida)
                self.assertNotIn('https://', self.saida)

    def test_post_init_comeca_pela_linha_e_pelo_menu_button(self):
        with open(os.path.join(RAIZ, 'bot.py'), encoding='utf-8') as f:
            arvore = ast.parse(f.read())
        post_init = next(no for no in ast.walk(arvore)
                         if isinstance(no, ast.AsyncFunctionDef) and no.name == 'post_init')
        primeiro = post_init.body[0]
        self.assertIsInstance(primeiro, ast.Expr)
        self.assertIsInstance(primeiro.value, ast.Await)
        self.assertEqual(primeiro.value.value.func.id, 'anunciar_subida')


class TestLinhaDeConferencia(TesteBotBase):
    def test_rotulo_do_banco_pelo_host(self):
        casos = (
            (URL_BANCO_TESTE, 'TESTE'),
            ('libsql://FIN4NCETEST-l1d3r.turso.io', 'TESTE'),
            ('https://fin4ncetest-l1d3r.turso.io', 'TESTE'),
            ('fin4ncetest-l1d3r.turso.io', 'TESTE'),
            (URL_BANCO_PRODUCAO, 'PRODUÇÃO'),
            ('libsql://prod-l1d3r.turso.io/fin4ncetest?db=fin4ncetest', 'PRODUÇÃO'),   # só o host conta
            ('', 'PRODUÇÃO'),
            (None, 'PRODUÇÃO'),
            ('libsql://[malformada', 'PRODUÇÃO'),
        )
        for url, esperado in casos:
            with self.subTest(url=url):
                self.assertEqual(bot.rotulo_banco(url), esperado)

    def test_linha_exata(self):
        self.assertEqual(bot.linha_conferencia('L1d3rTestBot', URL_BANCO_TESTE), 'Bot: @L1d3rTestBot | Banco: TESTE')
        self.assertEqual(bot.linha_conferencia('L1d3rBot', URL_BANCO_PRODUCAO), 'Bot: @L1d3rBot | Banco: PRODUÇÃO')

    def test_anunciar_subida_sem_token_nem_url(self):
        os.environ['TOKEN'] = TOKEN_FALSO
        os.environ['URL_MINI_APP'] = URL_HTTPS
        falso = BotFalso(username='L1d3rTestBot')
        aplicacao = SimpleNamespace(bot=falso)
        with mock.patch.object(bot, 'TURSO_DATABASE_URL', URL_BANCO_TESTE), mock.patch.object(bot, 'TOKEN', TOKEN_FALSO):
            self.rodar(bot.anunciar_subida(aplicacao))
        linhas = self.saida.splitlines()
        self.assertEqual(linhas[0], 'Bot: @L1d3rTestBot | Banco: TESTE')
        self.assertEqual(len(falso.pedidos), 1)
        for proibido in (TOKEN_FALSO, 'token-falso', URL_BANCO_TESTE, 'fin4ncetest-l1d3r', 'turso.io', URL_HTTPS):
            self.assertNotIn(proibido, self.saida)

    def test_anunciar_subida_com_menu_button_falhando_termina_normal(self):
        os.environ['URL_MINI_APP'] = URL_HTTPS
        aplicacao = SimpleNamespace(bot=BotFalso(username='L1d3rBot', erro=BadRequest('falhou')))
        with mock.patch.object(bot, 'TURSO_DATABASE_URL', URL_BANCO_PRODUCAO):
            self.rodar(bot.anunciar_subida(aplicacao))
        self.assertEqual(self.saida.splitlines()[0], 'Bot: @L1d3rBot | Banco: PRODUÇÃO')
        self.assertIn('(BadRequest)', self.saida)


if __name__ == '__main__':
    unittest.main(verbosity=2)
