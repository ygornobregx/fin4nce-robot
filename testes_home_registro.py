"""Testes leves da tela Registrar do Mini App (PRO-89).

Roda com `python testes_home_registro.py` (ou `python -m unittest testes_home_registro`).
Não usa .env, banco nem rede.

Duas partes:
  1. Estrutura: a home (mini-apps/home/index.html) é lida como texto e conferida
     (blocos, ids, atalho "Registrar" ligado, "Em breve" nos outros atalhos, o
     texto novo do chat vazio da receita, nenhum innerHTML com dado da API).
  2. Funções puras de JavaScript: o bloco <script id="funcoes-puras"> é extraído e
     roda no `node` (subprocess), sem navegador e sem build: arredondamento da
     parcela comparado com o round() do Python, textos do aviso, mensagens de erro,
     regra das novas tentativas (mesmo corpo, mesma chave), barra da Sobra e chave.
Se o `node` não estiver instalado, a parte 2 é pulada (skipTest).

Desde a PRO-92, também a lista "Últimas Movimentações" da home: título, rota
/movimentacoes/recentes, texto da lista vazia, receita sem detalhe ("Em breve"),
--cor-receita e as funções puras do valor e do nome de cada linha.
"""
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
CAMINHO_HOME = os.path.join(RAIZ, 'mini-apps', 'home', 'index.html')
NODE = shutil.which('node')


def ler_home():
    with open(CAMINHO_HOME, encoding='utf-8') as f:
        return f.read()


HOME = ler_home()


def blocos_de_script(html):
    """[(id, codigo)] dos <script> sem src (o do telegram-web-app.js tem src e fica de fora)."""
    return re.findall(r'<script(?: id="([^"]*)")?>(.*?)</script>', html, re.S)


def codigo_puro(html):
    for identificador, codigo in blocos_de_script(html):
        if identificador == 'funcoes-puras':
            return codigo
    raise AssertionError('bloco <script id="funcoes-puras"> não encontrado')


def codigo_principal(html):
    principais = [codigo for identificador, codigo in blocos_de_script(html) if not identificador]
    assert len(principais) == 1, 'esperava um único <script> principal'
    return principais[0]


def corpo_da_funcao(codigo, assinatura):
    """Texto da função que começa em 'assinatura' até a chave que a fecha (conta as chaves)."""
    inicio = codigo.index(assinatura)
    abre = codigo.index('{', inicio)
    nivel = 0
    for i in range(abre, len(codigo)):
        if codigo[i] == '{':
            nivel += 1
        elif codigo[i] == '}':
            nivel -= 1
            if nivel == 0:
                return codigo[inicio:i + 1]
    raise AssertionError('função sem fechamento: ' + assinatura)


def rodar_node(programa, entrada=None):
    """Roda 'programa' (JS) no node e devolve o JSON impresso. 'entrada' vai no arquivo entrada.json."""
    with tempfile.TemporaryDirectory() as pasta:
        with open(os.path.join(pasta, 'programa.js'), 'w', encoding='utf-8') as f:
            f.write(programa)
        with open(os.path.join(pasta, 'entrada.json'), 'w', encoding='utf-8') as f:
            json.dump(entrada, f)
        resultado = subprocess.run(
            [NODE, os.path.join(pasta, 'programa.js'), os.path.join(pasta, 'entrada.json')],
            capture_output=True, text=True, encoding='utf-8', timeout=120
        )
    if resultado.returncode != 0:
        raise AssertionError('node falhou:\n' + resultado.stderr)
    return json.loads(resultado.stdout)


def com_funcoes_puras(corpo_js):
    """Programa JS: as funções puras da página + o 'corpo_js' do teste (que usa console.log(JSON.stringify(...)))."""
    return (
        "const fs = require('fs');\n"
        "const ENTRADA = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));\n"
        + codigo_puro(HOME) + "\n" + corpo_js
    )


# ----------------------------------------------------------------------------
# 1. Estrutura (texto)
# ----------------------------------------------------------------------------

class TestEstruturaDaHome(unittest.TestCase):
    def test_blocos_e_ids_do_fluxo_registrar(self):
        for trecho in ('<main id="registro-opcoes"', '<main id="registro"', '<main id="detalhe"', '<main id="tela"',
                       'id="aviso-topo"', 'id="aviso-topo-barra"', 'id="campo"', 'id="botao-enviar"',
                       'id="botao-microfone"', 'id="botao-confirmar"', 'id="dica-confirmar"', 'id="erro-gravacao"',
                       'id="forma-debito"', 'id="forma-credito"', 'id="fichas"', 'id="previa"',
                       'id="chat-entendendo"', 'id="chat-erro"', 'id="chat-vazio-texto"',
                       'id="opcao-gasto"', 'id="opcao-receita"'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, HOME)

    def test_aviso_do_topo_fica_fora_dos_main(self):
        # O aviso é o elemento seguinte ao último </main>: não pode sumir junto com uma tela.
        ultimo_main = HOME.rindex('</main>')
        self.assertGreater(HOME.index('id="aviso-topo"'), ultimo_main)

    def test_atalho_registrar_ligado_ao_fluxo(self):
        self.assertRegex(HOME, r'<button class="atalho" id="atalho-registrar" type="button" aria-label="Registrar">')
        principal = codigo_principal(HOME)
        self.assertIn('$("atalho-registrar").addEventListener("click", abrirOpcoes)', principal)

    def test_categorias_e_recorrentes_continuam_em_breve(self):
        principal = codigo_principal(HOME)
        # Todo .atalho, MENOS o Registrar, segue com o aviso "Em breve".
        self.assertIn('document.querySelectorAll(".atalho:not(#atalho-registrar)")'
                      '.forEach((b) => b.addEventListener("click", avisarEmBreve))', principal)
        self.assertIn('aria-label="Categorias"', HOME)
        self.assertIn('aria-label="Recorrentes"', HOME)
        # As ações e os lápis do detalhe continuam com o seletor de antes.
        self.assertIn('document.querySelectorAll(".acao, .cartao-icone").forEach((b) => b.addEventListener("click", avisarEmBreve))',
                      principal)
        self.assertIn('function avisarEmBreve()', principal)
        self.assertIn('tg.showAlert("Em breve")', principal)

    def test_texto_do_chat_vazio_da_receita_e_o_do_quadro_v2(self):
        self.assertIn('Registre Aqui as Suas Receitas Avulsas', HOME)
        self.assertNotIn('seu salário', HOME)
        self.assertNotIn('valor avulso para somar', HOME)

    def test_texto_do_chat_vazio_do_gasto_e_selo_do_microfone(self):
        self.assertIn('Registre aqui o seu gasto, por exemplo: ', HOME)
        self.assertIn('"Gastei 30 no iFood"', HOME)
        self.assertRegex(HOME, r'id="botao-microfone"[^>]*\bdisabled\b')
        self.assertIn('<span class="selo-em-breve">Em Breve</span>', HOME)

    def test_textos_das_telas_do_paper(self):
        for texto in ('O Que Você Quer Registrar?', 'Subtrai do seu mês', 'Soma ao seu mês', 'Entendi assim:',
                      'Entendendo...', 'Forma de Pagamento', '(Obrigatório)', 'Débito / Pix', 'Escolha a forma de pagamento',
                      'Em Quantas Vezes?', 'De 1x a 12x', 'À Vista', 'Registrar Gasto', 'Registrar Receita',
                      'Escreva o gasto...', 'Escreva a receita...', 'Confirmar'):
            with self.subTest(texto=texto):
                self.assertIn(texto, HOME)

    def test_nenhum_innerhtml_nem_equivalente_recebe_dado(self):
        principal = codigo_principal(HOME) + codigo_puro(HOME)
        for padrao in (r'\.innerHTML\s*[+]?=', r'\.outerHTML\s*[+]?=', r'insertAdjacentHTML', r'document\.write',
                       r'\beval\s*\(', r'new Function'):
            with self.subTest(padrao=padrao):
                self.assertIsNone(re.search(padrao, principal), padrao)

    def test_envio_leva_os_cabecalhos_de_autenticacao(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'async function enviarJson')
        self.assertIn('"Authorization": "tma " + initData', corpo)
        self.assertIn('"ngrok-skip-browser-warning": "1"', corpo)
        self.assertIn('method: "POST"', corpo)
        self.assertIn('JSON.stringify(corpo)', corpo)
        self.assertIn('return { erro: 0 }', corpo)  # nunca lança: falha de rede vira erro 0

    def test_chave_so_nasce_com_a_previa(self):
        principal = codigo_principal(HOME)
        self.assertEqual(principal.count('gerarChave('), 1)
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'async function interpretar'))
        self.assertNotIn('gerarChave', corpo_da_funcao(principal, 'async function confirmar'))
        # A gravação usa o corpo guardado, e as novas tentativas passam por gravarComTentativas.
        confirmar = corpo_da_funcao(principal, 'async function confirmar')
        self.assertIn('gravarComTentativas(', confirmar)
        self.assertIn('e.chave', confirmar)

    def test_backbutton_sobe_um_nivel(self):
        principal = codigo_principal(HOME)
        self.assertIn('tg.BackButton.onClick(voltarUmNivel)', principal)
        corpo = corpo_da_funcao(principal, 'function voltarUmNivel')
        for trecho in ('voltarParaOpcoes()', 'voltarDasOpcoesParaHome()', 'voltarParaHome()'):
            self.assertIn(trecho, corpo)

    def test_viewport_do_telegram_ajusta_o_rodape(self):
        principal = codigo_principal(HOME)
        self.assertIn('tg.viewportHeight', principal)
        self.assertIn('tg.viewportStableHeight', principal)
        self.assertIn('"viewportChanged"', principal)

    def test_aviso_dura_10_segundos(self):
        principal = codigo_principal(HOME)
        self.assertIn('}, 10000)', corpo_da_funcao(principal, 'function mostrarAvisoTopo'))
        self.assertIn('animation: esvaziar-aviso 10s linear', HOME)

    def test_barra_da_sobra_usa_renda_mais_receitas(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarCabecalho')
        self.assertIn('baseDaBarra(resumo.renda, resumo.total_receitas)', corpo)
        self.assertIn('proporcaoDaBarra(resumo.total_gasto, base)', corpo)

    def test_sem_a_palavra_transferencia(self):
        # O testes_sem_transferencia.py exige isso da home.
        self.assertNotIn('Transferência', HOME)

    # --- Últimas Movimentações (PRO-92) ---
    def test_titulo_ultimas_movimentacoes(self):
        self.assertIn('<h2 class="secao-titulo">Últimas Movimentações</h2>', HOME)
        self.assertNotIn('Últimos gastos', HOME)

    def test_home_chama_a_rota_de_movimentacoes(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'async function carregar')
        self.assertIn('chamar("/movimentacoes/recentes")', corpo)
        self.assertIn('mostrarExtrato(recentes.movimentacoes || [])', corpo)
        self.assertNotIn('/gastos/recentes', HOME)

    def test_texto_da_lista_vazia(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarExtrato')
        self.assertIn('"Suas movimentações aparecem aqui"', corpo)
        self.assertNotIn('Seus próximos gastos aparecem aqui', HOME)

    def test_linha_usa_as_funcoes_puras_e_so_o_gasto_guarda_o_id(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarExtrato')
        self.assertIn('textoValorMovimentacao(m)', corpo)
        self.assertIn('nomeMovimentacao(m)', corpo)
        self.assertIn('if (m.tipo === "gasto") linha.dataset.idGasto = m.id;', corpo)
        self.assertIn('linha.dataset.tipo = "receita"', corpo)
        self.assertEqual(corpo.count('dataset.idGasto'), 1)   # o id só é gravado na linha de gasto

    def test_receita_nao_abre_o_detalhe(self):
        principal = codigo_principal(HOME)
        inicio = principal.index('$("extrato-lista").addEventListener("click"')
        ouvinte = principal[inicio:principal.index('});', inicio)]
        # O detalhe só abre com data-id-gasto; sem ele (receita), o toque mostra "Em breve" (D2).
        self.assertIn('if (linha.dataset.idGasto) abrirDetalhe(linha.dataset.idGasto);', ouvinte)
        self.assertIn('else avisarEmBreve();', ouvinte)
        self.assertEqual(principal.count('abrirDetalhe('), 2)  # a definição e esta única chamada

    def test_cor_da_receita_no_root(self):
        raiz = HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))]
        self.assertIn('--cor-receita: #15803D;', raiz)
        self.assertIn('.gasto-valor.receita { color: var(--cor-receita); }', HOME)

    def test_lista_monta_tudo_com_textcontent(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarExtrato')
        self.assertNotIn('innerHTML', corpo)
        self.assertNotIn('insertAdjacentHTML', corpo)
        self.assertIn('criar("span", "gasto-nome", nomeMovimentacao(m))', corpo)

    def test_blocos_de_script_formam_um_so_programa(self):
        # Uma const repetida entre os dois blocos (ex.: formatarDinheiro) é SyntaxError na página.
        nomes = []
        for _, codigo in blocos_de_script(HOME):
            nomes += re.findall(r'^\s{4}(?:const|let|function|async function)\s+([A-Za-z_$][\w$]*)', codigo, re.M)
        repetidos = {n for n in nomes if nomes.count(n) > 1}
        self.assertEqual(repetidos, set())


# ----------------------------------------------------------------------------
# 2. Funções puras no node
# ----------------------------------------------------------------------------

@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestSintaxeDoJavaScript(unittest.TestCase):
    def test_node_check_dos_dois_blocos_juntos(self):
        # Juntos, como o navegador os executa: pega const repetida e erro de sintaxe.
        programa = '\n'.join(codigo for _, codigo in blocos_de_script(HOME))
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, 'pagina.js')
            with open(caminho, 'w', encoding='utf-8') as f:
                f.write(programa)
            resultado = subprocess.run([NODE, '--check', caminho], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(resultado.returncode, 0, resultado.stderr)


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestArredondamentoDaParcela(unittest.TestCase):
    """D6: a parcela da prévia é igual ao round(valor_total / n, 2) do registro.py."""

    @staticmethod
    def casos_de_parcela():
        sorteio = random.Random(89)
        casos = [(10.01, 2), (0.25, 2), (0.75, 2), (600.0, 3), (1000.0, 7), (45.0, 1), (0.01, 12), (999999.99, 12),
                 (1.0, 8), (2.5, 4), (0.5, 4), (0.375, 1), (3.0, 8)]
        for _ in range(12000):                      # valores em centavos, como as prévias reais
            casos.append((sorteio.randint(1, 100_000_000) / 100, sorteio.randint(1, 12)))
        for centavos in range(1, 3001):             # todos os valores de R$ 0,01 a R$ 30,00 em todas as parcelas
            for n in range(1, 13):
                casos.append((centavos / 100, n))
        return casos

    @staticmethod
    def casos_de_x():
        sorteio = random.Random(8989)
        xs = [k / 8 for k in range(0, 20001)]                         # empates exatos (ímpar/8) e inteiros/múltiplos
        xs += [(2 * k + 1) / 200 for k in range(0, 40000)]            # quase empates (x.xx5) que não são exatos
        xs += [sorteio.uniform(0, 1_000_000) for _ in range(20000)]    # doubles quaisquer
        xs += [sorteio.uniform(0, 10) for _ in range(20000)]
        return xs

    def test_valor_da_parcela_igual_ao_round_do_python(self):
        casos = self.casos_de_parcela()
        esperado = [round(total / n, 2) for total, n in casos]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([t, n]) => valorParcela(t, n))));"
        ), [list(c) for c in casos])
        self.assertEqual(len(obtido), len(esperado))
        diferentes = [(c, o, e) for c, o, e in zip(casos, obtido, esperado) if o != e]
        self.assertEqual(diferentes[:10], [], 'parcelas diferentes do round() do Python: %d' % len(diferentes))
        self.assertGreater(len(casos), 30000)

    def test_arredondar2_igual_ao_round_do_python_em_milhares_de_x(self):
        xs = self.casos_de_x()
        esperado = [round(x, 2) for x in xs]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(arredondar2)));"
        ), xs)
        diferentes = [(x, o, e) for x, o, e in zip(xs, obtido, esperado) if o != e]
        self.assertEqual(diferentes[:10], [], 'arredondamentos diferentes do Python: %d' % len(diferentes))
        self.assertGreater(len(xs), 100000)

    def test_casos_conhecidos(self):
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify([valorParcela(10.01, 2), arredondar2(0.125), arredondar2(0.375),"
            " arredondar2(2.675), arredondar2(1.005), valorParcela(600, 3), textoParcela(600, 3), textoParcela(10.01, 2)]));"
        ))
        self.assertEqual(obtido[0], 5.0)                 # 10,01 em 2x: o Math.round dá 5,01; o Python e o servidor, 5,00
        self.assertEqual(obtido[1], 0.12)                # empate exato: vai para o par
        self.assertEqual(obtido[2], 0.38)
        self.assertEqual(obtido[3], round(2.675, 2))     # 2.675 é 2.67499999... no double
        self.assertEqual(obtido[4], round(1.005, 2))
        self.assertEqual(obtido[5], 200.0)
        self.assertEqual(obtido[6], 'em 3x de R$ 200,00')
        self.assertEqual(obtido[7], 'em 2x de R$ 5,00')

    def test_o_teste_tem_forca_o_math_round_ingenuo_erraria(self):
        # Garante que os casos pegam a diferença que motivou a D6 (se não pegassem, o teste não provaria nada).
        casos = self.casos_de_parcela()
        ingenuo = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([t, n]) => Math.round(t / n * 100) / 100)));"
        ), [list(c) for c in casos])
        erros = [c for c, o in zip(casos, ingenuo) if o != round(c[0] / c[1], 2)]
        self.assertGreater(len(erros), 0)
        self.assertIn((10.01, 2), erros)


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestTextosEMensagens(unittest.TestCase):
    def test_formatar_dinheiro(self):
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify([formatarDinheiro(1234.56), formatarDinheiro(0), formatarDinheiro(-120),"
            " formatarDinheiro(3664)]));"
        ))
        self.assertEqual(obtido, ['R$ 1.234,56', 'R$ 0,00', '−R$ 120,00', 'R$ 3.664,00'])

    def test_aviso_do_topo(self):
        casos = [
            ['gasto', {'valor': 20.0, 'parcelas': 1, 'descricao': 'Pão'}],
            ['gasto', {'valor': 600.0, 'parcelas': 3, 'descricao': 'Geladeira'}],
            ['gasto', {'valor': 45.5, 'parcelas': 1, 'descricao': None}],
            ['gasto', {'valor': 45.5, 'parcelas': 1, 'descricao': '   '}],
            ['gasto', {'valor': 1234.56, 'parcelas': 12, 'descricao': ' Sofá '}],
            ['receita', {'valor': 200.0, 'descricao': 'Reembolso'}],
            ['receita', {'valor': 200.0, 'descricao': None}],
            ['receita', {'valor': 1000.0, 'parcelas': 4, 'descricao': 'Bônus'}],   # receita nunca mostra "em Nx"
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([t, r]) => montarAviso(t, r))));"
        ), casos)
        textos = [a['texto'] for a in obtido]
        self.assertEqual(textos, [
            'Gasto registrado: R$ 20,00 · Pão',
            'Gasto registrado: R$ 600,00 em 3x · Geladeira',
            'Gasto registrado: R$ 45,50',
            'Gasto registrado: R$ 45,50',
            'Gasto registrado: R$ 1.234,56 em 12x · Sofá',
            'Receita registrada: R$ 200,00 · Reembolso',
            'Receita registrada: R$ 200,00',
            'Receita registrada: R$ 1.000,00 · Bônus',
        ])
        # O que vai em negrito é o valor (com o "em Nx" no parcelado).
        self.assertEqual(obtido[0]['negrito'], 'R$ 20,00')
        self.assertEqual(obtido[1]['negrito'], 'R$ 600,00 em 3x')
        self.assertEqual(obtido[1]['antes'], 'Gasto registrado: ')
        self.assertEqual(obtido[1]['depois'], ' · Geladeira')

    def test_mensagens_de_erro(self):
        entradas = [
            # [resultado, momento, tipo]
            [{'erro': 0}, 'interpretar', 'gasto'],
            [{'erro': 0}, 'gravar', 'gasto'],
            [{'erro': 401, 'detalhe': 'nao_autorizado'}, 'interpretar', 'gasto'],
            [{'erro': 401, 'detalhe': 'nao_autorizado'}, 'gravar', 'receita'],
            [{'erro': 422, 'detalhe': 'tipo_divergente'}, 'interpretar', 'gasto'],
            [{'erro': 422, 'detalhe': 'tipo_divergente'}, 'interpretar', 'receita'],
            [{'erro': 422, 'detalhe': 'valor_nao_entendido'}, 'interpretar', 'gasto'],
            [{'erro': 422, 'detalhe': 'valor_nao_entendido'}, 'interpretar', 'receita'],
            [{'erro': 422, 'detalhe': 'categoria_nao_encontrada'}, 'interpretar', 'gasto'],
            [{'erro': 429, 'detalhe': 'limite_excedido', 'retryAfter': 23}, 'interpretar', 'gasto'],
            [{'erro': 429, 'detalhe': 'limite_excedido', 'retryAfter': 1}, 'interpretar', 'gasto'],
            [{'erro': 429, 'detalhe': 'limite_excedido'}, 'interpretar', 'gasto'],
            [{'erro': 502, 'detalhe': 'resposta_ia_invalida'}, 'interpretar', 'gasto'],
            [{'erro': 503, 'detalhe': 'ia_indisponivel'}, 'interpretar', 'gasto'],
            [{'erro': 500, 'detalhe': 'erro_interno'}, 'interpretar', 'gasto'],
            [{'erro': 422, 'detalhe': 'texto_invalido'}, 'interpretar', 'gasto'],
            [{'erro': 422, 'detalhe': [{'msg': 'corpo cru do FastAPI'}]}, 'interpretar', 'gasto'],
            [{'erro': 404, 'detalhe': 'usuario_nao_cadastrado'}, 'interpretar', 'gasto'],
            [{'erro': 409, 'detalhe': 'cadastro_incompleto'}, 'gravar', 'gasto'],
            [{'erro': 503, 'detalhe': 'banco_ocupado'}, 'gravar', 'gasto'],
            [{'erro': 500, 'detalhe': 'erro_interno'}, 'gravar', 'receita'],
            [{'erro': 422, 'detalhe': 'chave_invalida'}, 'gravar', 'gasto'],
            [{'erro': 422, 'detalhe': 'categoria_nao_encontrada'}, 'gravar', 'gasto'],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([r, m, t]) => mensagemErroRegistro(r, m, t))));"
        ), entradas)
        sem_conexao = 'Sem conexão. Confira a internet e tente de novo.'
        sessao = 'Sua sessão expirou. Feche e abra o app de novo.'
        nao_entendi = 'Não consegui entender agora. Tente de novo em instantes.'
        nao_registrou = 'Não consegui registrar agora. Toque em Confirmar para tentar de novo.'
        self.assertEqual(obtido, [
            sem_conexao, sem_conexao, sessao, sessao,
            'Isso parece uma receita. Volte e escolha Receita.',
            'Isso parece um gasto. Volte e escolha Gasto.',
            'Não entendi o valor. Escreva de novo com o valor, por exemplo: "Gastei 30 no iFood"',
            'Não entendi o valor. Escreva de novo com o valor, por exemplo: "Recebi 200 de reembolso"',
            'Não achei uma categoria sua para isso. Escreva de outro jeito.',
            'Muitas tentativas seguidas. Espere 23 segundos.',
            'Muitas tentativas seguidas. Espere 1 segundo.',
            'Muitas tentativas seguidas. Espere alguns segundos.',
            nao_entendi, nao_entendi, nao_entendi, nao_entendi, nao_entendi,
            'Você ainda não tem cadastro. Mande /start no bot para começar.',
            'Seu cadastro está incompleto. Volte ao bot e informe o dia de fechamento da fatura.',
            nao_registrou, nao_registrou,
            'Não consegui registrar isso. Volte e escreva de novo.',
            'Não consegui registrar isso. Volte e escreva de novo.',
        ])

    def test_mensagem_nunca_traz_texto_cru_do_servidor(self):
        # Mesmo que o servidor mande um texto qualquer no 'detalhe', ele não vai para a tela.
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(['interpretar', 'gravar'].flatMap((m) =>"
            " [400, 422, 500, 502, 503].map((s) => mensagemErroRegistro({ erro: s, detalhe: 'SEGREDO_DO_SERVIDOR sql=SELECT' }, m, 'gasto')))));"
        ))
        self.assertEqual(len(obtido), 10)
        for texto in obtido:
            self.assertNotIn('SEGREDO', texto)
            self.assertNotIn('SELECT', texto)


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestNovasTentativasEChave(unittest.TestCase):
    def test_esperas_e_quando_tentar_de_novo(self):
        obtido = rodar_node(com_funcoes_puras("""
            const r0 = { erro: 0 };
            const r503 = { erro: 503, detalhe: 'banco_ocupado' };
            console.log(JSON.stringify({
              rede1: esperaAntesDeNovaTentativa(r0, 1),
              rede2: esperaAntesDeNovaTentativa(r0, 2),
              rede3: esperaAntesDeNovaTentativa(r0, 3),
              ocupado1: esperaAntesDeNovaTentativa(r503, 1),
              ocupado2: esperaAntesDeNovaTentativa(r503, 2),
              ocupado3: esperaAntesDeNovaTentativa(r503, 3),
              ia: esperaAntesDeNovaTentativa({ erro: 503, detalhe: 'ia_indisponivel' }, 1),
              s500: esperaAntesDeNovaTentativa({ erro: 500, detalhe: 'erro_interno' }, 1),
              s422: esperaAntesDeNovaTentativa({ erro: 422, detalhe: 'valor_invalido' }, 1),
              s401: esperaAntesDeNovaTentativa({ erro: 401 }, 1),
              s429: esperaAntesDeNovaTentativa({ erro: 429 }, 1),
              sucesso: esperaAntesDeNovaTentativa({ dados: {}, status: 201 }, 1),
              max: MAX_TENTATIVAS,
            }));
        """))
        self.assertEqual(obtido, {
            'rede1': 1000, 'rede2': 2000, 'rede3': None,
            'ocupado1': 1000, 'ocupado2': 2000, 'ocupado3': None,
            'ia': None, 's500': None, 's422': None, 's401': None, 's429': None, 'sucesso': None, 'max': 3,
        })

    def test_gravar_com_tentativas_repete_o_mesmo_corpo_e_a_mesma_chave(self):
        obtido = rodar_node(com_funcoes_puras("""
            const cenarios = {
              ocupadoDuasVezesDepoisOk: [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 0 }, { dados: { id: 1 }, status: 201 }],
              sempreOcupado: [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 503, detalhe: 'banco_ocupado' },
                              { erro: 503, detalhe: 'banco_ocupado' }, { dados: { id: 9 }, status: 201 }],
              redeCaiu: [{ erro: 0 }, { erro: 0 }, { erro: 0 }, { erro: 0 }],
              recusado: [{ erro: 422, detalhe: 'valor_invalido' }, { dados: {}, status: 201 }],
              repetido: [{ dados: { id: 7, repetido: true }, status: 200 }],
              primeiraOk: [{ dados: { id: 3 }, status: 201 }],
            };
            (async () => {
              const saida = {};
              for (const [nome, respostas] of Object.entries(cenarios)) {
                const chamadas = [];
                const esperas = [];
                const corpo = { chave: 'f47ac10b-58cc-4372-a567-0e02b2c3d479', valor: 600, parcelas: 3 };
                const resultado = await gravarComTentativas(
                  async (c) => { chamadas.push(c); return respostas[chamadas.length - 1]; },
                  corpo,
                  async (ms) => { esperas.push(ms); }
                );
                saida[nome] = {
                  chamadas: chamadas.length,
                  esperas,
                  mesmoObjeto: chamadas.every((c) => c === corpo),
                  mesmaChave: chamadas.every((c) => c.chave === 'f47ac10b-58cc-4372-a567-0e02b2c3d479'),
                  resultado,
                };
              }
              console.log(JSON.stringify(saida));
            })();
        """))
        self.assertEqual(obtido['ocupadoDuasVezesDepoisOk']['chamadas'], 3)
        self.assertEqual(obtido['ocupadoDuasVezesDepoisOk']['esperas'], [1000, 2000])
        self.assertEqual(obtido['ocupadoDuasVezesDepoisOk']['resultado'], {'dados': {'id': 1}, 'status': 201})
        # Banco sempre ocupado: 3 tentativas no total (nunca a 4ª) e o último erro volta para a tela.
        self.assertEqual(obtido['sempreOcupado']['chamadas'], 3)
        self.assertEqual(obtido['sempreOcupado']['esperas'], [1000, 2000])
        self.assertEqual(obtido['sempreOcupado']['resultado'], {'erro': 503, 'detalhe': 'banco_ocupado'})
        self.assertEqual(obtido['redeCaiu']['chamadas'], 3)
        self.assertEqual(obtido['redeCaiu']['resultado'], {'erro': 0})
        # Pedido recusado (422) não é repetido; 200 (repetido) e 201 são sucesso na primeira.
        self.assertEqual(obtido['recusado']['chamadas'], 1)
        self.assertEqual(obtido['recusado']['esperas'], [])
        self.assertEqual(obtido['repetido']['chamadas'], 1)
        self.assertEqual(obtido['repetido']['resultado']['status'], 200)
        self.assertEqual(obtido['primeiraOk']['chamadas'], 1)
        for nome, dados in obtido.items():
            with self.subTest(cenario=nome):
                self.assertTrue(dados['mesmoObjeto'], 'o corpo mudou entre tentativas')
                self.assertTrue(dados['mesmaChave'], 'a chave mudou entre tentativas')

    def test_chave_e_uuid_v4_com_e_sem_randomuuid(self):
        obtido = rodar_node(com_funcoes_puras("""
            const falso = { getRandomValues: (b) => { for (let i = 0; i < b.length; i++) b[i] = (i * 37 + 11) & 255; return b; } };
            const reais = Array.from({ length: 200 }, () => gerarChave());
            console.log(JSON.stringify({ reais, falsoA: gerarChave(falso), falsoB: gerarChave(falso) }));
        """))
        formato = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')  # igual ao registro.py
        for chave in obtido['reais'] + [obtido['falsoA']]:
            self.assertRegex(chave, formato)
        self.assertEqual(len(set(obtido['reais'])), 200)

    def test_corpos_da_gravacao(self):
        previa_gasto = {'tipo': 'gasto', 'valor': 600.0, 'descricao': 'Tênis', 'estabelecimento': 'Nike',
                        'categoria': 'Vestuário', 'metodo_pagamento': None}
        previa_receita = {'tipo': 'receita', 'valor': 200.0, 'descricao': 'Reembolso'}
        chave = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
        obtido = rodar_node(com_funcoes_puras(
            "const [g, r, c] = ENTRADA; console.log(JSON.stringify(["
            "montarCorpoGasto(g, 'Crédito', 3, c), montarCorpoGasto(g, 'Débito/Pix', 5, c), montarCorpoReceita(r, c)]));"
        ), [previa_gasto, previa_receita, chave])
        self.assertEqual(obtido[0], {'chave': chave, 'valor': 600.0, 'descricao': 'Tênis', 'estabelecimento': 'Nike',
                                     'categoria': 'Vestuário', 'metodo_pagamento': 'Crédito', 'parcelas': 3})
        # Débito/Pix: parcelas sempre 1, mesmo que sobrasse um 5 do Crédito.
        self.assertEqual(obtido[1]['metodo_pagamento'], 'Débito/Pix')
        self.assertEqual(obtido[1]['parcelas'], 1)
        self.assertEqual(obtido[2], {'chave': chave, 'valor': 200.0, 'descricao': 'Reembolso'})

    def test_confirmar_so_liga_com_previa_e_forma(self):
        casos = [
            [{'tipo': 'gasto', 'previa': {}, 'forma': None}, False],
            [{'tipo': 'gasto', 'previa': {}, 'forma': 'Débito/Pix'}, True],
            [{'tipo': 'gasto', 'previa': {}, 'forma': 'Crédito'}, True],
            [{'tipo': 'gasto', 'previa': {}, 'forma': 'Dinheiro'}, False],
            [{'tipo': 'gasto', 'previa': None, 'forma': 'Crédito'}, False],
            [{'tipo': 'gasto', 'previa': {}, 'forma': 'Crédito', 'gravando': True}, False],
            [{'tipo': 'gasto', 'previa': {}, 'forma': 'Crédito', 'interpretando': True}, False],
            [{'tipo': 'receita', 'previa': {}, 'forma': None}, True],
            [{'tipo': 'receita', 'previa': None, 'forma': None}, False],
            [{'tipo': 'receita', 'previa': {}, 'forma': None, 'gravando': True}, False],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([e]) => podeConfirmar(e))));"
        ), casos)
        self.assertEqual(obtido, [c[1] for c in casos])

    def test_previa_valida(self):
        casos = [
            [{'tipo': 'gasto', 'valor': 10.5, 'categoria': 'Mercado'}, 'gasto', True],
            [{'tipo': 'receita', 'valor': 10.5}, 'receita', True],
            [{'tipo': 'receita', 'valor': 10.5}, 'gasto', False],
            [{'tipo': 'gasto', 'valor': 0, 'categoria': 'Mercado'}, 'gasto', False],
            [{'tipo': 'gasto', 'valor': '10', 'categoria': 'Mercado'}, 'gasto', False],
            [{'tipo': 'gasto', 'valor': 10, 'categoria': None}, 'gasto', False],
            [None, 'gasto', False],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([d, t]) => previaValida(d, t))));"
        ), casos)
        self.assertEqual(obtido, [c[2] for c in casos])


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestBarraDaSobra(unittest.TestCase):
    """D8: o 'de R$' é renda + receitas, e o preenchimento é gasto ÷ (renda + receitas)."""

    def test_base_e_proporcao(self):
        obtido = rodar_node(com_funcoes_puras("""
            const base = baseDaBarra(3014, 650);
            console.log(JSON.stringify({
              base,
              textoDe: 'de ' + formatarDinheiro(base),
              proporcao: proporcaoDaBarra(2029.66, base),
              semReceitas: baseDaBarra(3014, 0),
              receitasAusentes: baseDaBarra(3014, undefined),
              semRenda: baseDaBarra(0, 0),
              soReceita: baseDaBarra(0, 650),
              proporcaoSemBase: proporcaoDaBarra(100, 0),
              estourou: proporcaoDaBarra(5000, 3664),
              negativa: proporcaoDaBarra(-10, 3664),
              soma: baseDaBarra(0.1, 0.2),
            }));
        """))
        self.assertEqual(obtido['base'], 3664)
        self.assertEqual(obtido['textoDe'], 'de R$ 3.664,00')
        self.assertAlmostEqual(obtido['proporcao'], 2029.66 / 3664, places=12)
        self.assertEqual(round(obtido['proporcao'] * 100), 55)     # a nota da PRO-89 fala em 55%
        self.assertEqual(obtido['semReceitas'], 3014)
        self.assertEqual(obtido['receitasAusentes'], 3014)
        self.assertEqual(obtido['semRenda'], 0)
        self.assertEqual(obtido['soReceita'], 650)
        self.assertIsNone(obtido['proporcaoSemBase'])             # sem renda e sem receita: barra escondida
        self.assertEqual(obtido['estourou'], 1)
        self.assertEqual(obtido['negativa'], 0)
        self.assertEqual(obtido['soma'], 0.3)


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestUltimasMovimentacoes(unittest.TestCase):
    """PRO-92: texto, parcelas e classe do valor, e o nome de cada linha da lista."""

    def test_texto_do_valor_e_nome_por_tipo(self):
        casos = [
            {'tipo': 'gasto', 'id': 1, 'descricao': 'Padaria', 'valor': 18.5, 'parcelado': False,
             'total_parcelas': None},
            {'tipo': 'gasto', 'id': 48, 'descricao': 'Magazine Luiza', 'valor': 129.9, 'parcelado': True,
             'total_parcelas': 3},
            {'tipo': 'receita', 'id': 3, 'descricao': 'Reembolso', 'valor': 53.9, 'parcelado': False,
             'total_parcelas': None},
            {'tipo': 'receita', 'id': 4, 'descricao': None, 'valor': 1234.56, 'parcelado': False,
             'total_parcelas': None},
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map((m) => [textoValorMovimentacao(m), nomeMovimentacao(m)])));"
        ), casos)
        self.assertEqual(obtido, [
            [{'texto': 'R$ 18,50', 'parcelas': None, 'classe': 'gasto-valor'}, 'Padaria'],
            [{'texto': 'R$ 129,90', 'parcelas': ' · 3x', 'classe': 'gasto-valor'}, 'Magazine Luiza'],
            [{'texto': '+ R$ 53,90', 'parcelas': None, 'classe': 'gasto-valor receita'}, 'Reembolso'],
            [{'texto': '+ R$ 1.234,56', 'parcelas': None, 'classe': 'gasto-valor receita'}, 'Receita'],
        ])


if __name__ == '__main__':
    unittest.main(verbosity=2)
