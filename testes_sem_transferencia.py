"""Testes da remoção da transferência entre categorias (PRO-84).

Roda com `python testes_sem_transferencia.py`. Não usa .env, banco nem rede:
o bot.py e a home do Mini App são lidos como texto (e o bot.py com `ast`),
sem importar o bot, para não abrir conexão com o Turso nem criar o cliente da IA.
"""
import ast
import os
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))


def ler(*partes):
    with open(os.path.join(RAIZ, *partes), encoding='utf-8') as f:
        return f.read()


FONTE_BOT = ler('bot.py')
ARVORE_BOT = ast.parse(FONTE_BOT)
HOME = ler('mini-apps', 'home', 'index.html')


def nomes_atribuidos(arvore):
    """Todos os nomes que recebem valor em qualquer ponto do módulo (inclui tuplas)."""
    nomes = set()
    for no in ast.walk(arvore):
        alvos = []
        if isinstance(no, ast.Assign):
            alvos = no.targets
        elif isinstance(no, (ast.AnnAssign, ast.AugAssign)):
            alvos = [no.target]
        for alvo in alvos:
            for sub in ast.walk(alvo):
                if isinstance(sub, ast.Name):
                    nomes.add(sub.id)
    return nomes


def funcoes_definidas(arvore):
    return {no.name for no in ast.walk(arvore)
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))}


def valor_da_atribuicao(arvore, nome):
    for no in arvore.body:
        if isinstance(no, ast.Assign) and any(isinstance(a, ast.Name) and a.id == nome for a in no.targets):
            return ast.literal_eval(no.value)
    return None


class TestBotSemTransferencia(unittest.TestCase):
    def test_funcoes_do_fluxo_nao_existem_mais(self):
        funcoes = funcoes_definidas(ARVORE_BOT)
        self.assertNotIn('iniciar_transferencia', funcoes)
        self.assertNotIn('salvar_transferencia_livre', funcoes)

    def test_constante_de_estado_nao_existe_mais(self):
        self.assertNotIn('TRANSFERIR_VALOR', nomes_atribuidos(ARVORE_BOT))

    def test_nenhuma_referencia_restante_ao_fluxo(self):
        for texto in ('TRANSFERIR_VALOR', 'iniciar_transferencia', 'salvar_transferencia_livre',
                      'conv_transferir', 'menu_transferir'):
            with self.subTest(texto=texto):
                self.assertNotIn(texto, FONTE_BOT)

    def test_nao_ha_botao_transferir(self):
        self.assertNotIn('"Transferir"', FONTE_BOT)
        self.assertNotIn("'Transferir'", FONTE_BOT)
        botoes = [no for no in ast.walk(ARVORE_BOT)
                  if isinstance(no, ast.Call) and getattr(no.func, 'id', None) == 'InlineKeyboardButton']
        textos = [b.args[0].value for b in botoes if b.args and isinstance(b.args[0], ast.Constant)]
        self.assertTrue(textos, "nenhum InlineKeyboardButton encontrado: o teste está olhando o lugar errado")
        self.assertNotIn('Transferir', textos)

    def test_bot_compila(self):
        compile(FONTE_BOT, 'bot.py', 'exec')


class TestDadosAntigosFicam(unittest.TestCase):
    def test_renomear_categoria_continua_atualizando_transferencias(self):
        referencias = valor_da_atribuicao(ARVORE_BOT, '_REFERENCIAS_NOME_CATEGORIA')
        self.assertIsNotNone(referencias, "_REFERENCIAS_NOME_CATEGORIA não encontrada no bot.py")
        self.assertIn(('transferencias', 'categoria_origem'), referencias)
        self.assertIn(('transferencias', 'categoria_destino'), referencias)

    def test_tabela_transferencias_continua_em_iniciar_banco(self):
        iniciar_banco = next(no for no in ast.walk(ARVORE_BOT)
                             if isinstance(no, ast.FunctionDef) and no.name == 'iniciar_banco')
        trecho = ast.get_source_segment(FONTE_BOT, iniciar_banco)
        self.assertIn('CREATE TABLE IF NOT EXISTS transferencias', trecho)


class TestHomeComRegistrar(unittest.TestCase):
    def test_atalho_registrar_existe(self):
        self.assertIn('aria-label="Registrar"', HOME)
        self.assertIn('<span class="atalho-rotulo">Registrar</span>', HOME)

    def test_atalho_transferencia_nao_existe_mais(self):
        self.assertNotIn('Transferência', HOME)
        self.assertNotIn('aria-label="Transferência"', HOME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
