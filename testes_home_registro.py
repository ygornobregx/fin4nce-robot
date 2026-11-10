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

Desde a PRO-97, as telas de boas-vindas (escolha inicial, "Já Sou Amigo do Adm",
cadastro em 3 passos, tour) e o painel vazio: blocos e textos dos quadros, o 404
do /me levando à escolha, nenhum "/start" na home, e no node a comparação de
categorias repetidas contra a normalizar_texto() de verdade do cadastro.py, o
contador, a saudação do tour, a decisão por status do POST /cadastro, a barra
vazia, o gráfico vazio e o cartão do salário.

Desde a PRO-101, as telas dos gastos recorrentes (guia, escolha, lista, formulário Novo e Editar e as
folhas do dia, da categoria e do apagar): blocos e textos dos quadros, o atalho ligado (só "Categorias"
segue em "Em breve"), a chave nascendo só no formulário Novo, a tela da logo com tempo mínimo no criar e
no editar e ausente no apagar, o guia "já visto" sempre em try, e no node a máscara de centavos, a
validação do valor contra o registro.validar_valor, a dica do Salvar, o corpo dos pedidos, os textos da
lista e a decisão por status das três gravações.
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RAIZ)
import cadastro  # noqa: E402  (só a biblioteca padrão e o registro.py: sem .env, rede nem banco)
import recorrentes  # noqa: E402  (idem: só o cadastro.py e o registro.py)
import registro  # noqa: E402
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

    def test_so_categorias_continua_em_breve(self):
        principal = codigo_principal(HOME)
        # Todo .atalho, MENOS o Registrar e (desde a PRO-101) o Recorrentes, segue com o aviso "Em breve".
        self.assertIn('document.querySelectorAll(".atalho:not(#atalho-registrar):not(#atalho-recorrentes)")'
                      '.forEach((b) => b.addEventListener("click", avisarEmBreve))', principal)
        self.assertRegex(HOME, r'<button class="atalho" id="atalho-recorrentes" type="button" aria-label="Recorrentes">')
        self.assertIn('$("atalho-recorrentes").addEventListener("click", abrirRecorrentes);', principal)
        self.assertIn('aria-label="Categorias"', HOME)
        self.assertIn('aria-label="Recorrentes"', HOME)
        # As ações e os lápis do detalhe continuam com o seletor de antes.
        self.assertIn('document.querySelectorAll(".acao, .cartao-icone").forEach((b) => b.addEventListener("click", avisarEmBreve))',
                      principal)
        self.assertIn('function avisarEmBreve()', principal)
        self.assertIn('tg.showAlert("Em breve")', principal)

    def test_texto_do_chat_vazio_da_receita_e_o_do_quadro_v2(self):
        self.assertIn('Registre Aqui as Suas Receitas Avulsas', HOME)
        # O chat da receita não fala em salário. Desde a PRO-97, o tour e o cartão do painel vazio falam
        # ("O seu salário entra como Receita"), então a conferência ficou só no chat (abrirRegistro e o bloco).
        abrir = corpo_da_funcao(codigo_principal(HOME), 'function abrirRegistro')
        bloco_registro = HOME[HOME.index('<main id="registro"'):HOME.index('</main>', HOME.index('<main id="registro"'))]
        for trecho in (abrir, bloco_registro):
            self.assertNotIn('salário', trecho)
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
        # O POST do Registrar, do cadastro e dos recorrentes passa pelo mesmo envio, que desde a PRO-101 também faz PUT e DELETE.
        self.assertIn('return enviarComMetodo("POST", rota, corpo, tempoLimiteMs);', corpo)
        envio = corpo_da_funcao(codigo_principal(HOME), 'async function enviarComMetodo')
        self.assertIn('"Authorization": "tma " + initData', envio)
        self.assertIn('"ngrok-skip-browser-warning": "1"', envio)
        self.assertIn('method: metodo', envio)
        self.assertIn('JSON.stringify(corpo)', envio)
        self.assertIn('return { erro: 0 }', envio)  # nunca lança: falha de rede vira erro 0

    def test_chave_so_nasce_com_a_previa_ou_o_formulario_novo(self):
        principal = codigo_principal(HOME)
        # Duas chamadas só: a prévia do Registrar (interpretar) e o formulário Novo dos recorrentes (PRO-101).
        self.assertEqual(principal.count('gerarChave('), 2)
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'async function interpretar'))
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'function abrirFormNovo'))
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
        # Desde a PRO-97 a lista vazia é a caixa cinza do quadro PAINEL VAZIO (no HTML), que o
        # mostrarExtrato mostra ou esconde, junto com o cartão do salário.
        self.assertRegex(HOME, r'<div class="extrato-vazio" id="extrato-vazio" hidden>\s*<svg[^>]*>.*?</svg>\s*'
                               r'<p>Suas movimentações aparecem aqui</p>')
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarExtrato')
        self.assertIn('$("extrato-vazio").hidden = movimentacoes.length > 0;', corpo)
        self.assertIn('$("cartao-salario").hidden = !mostrarCartaoDoSalario(movimentacoes);', corpo)
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
            [{'erro': 404, 'detalhe': 'usuario_nao_cadastrado'}, 'interpretar', 'gasto'],   # texto novo (PRO-97)
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
            'Não encontramos o seu cadastro. Feche e abra o app de novo.',
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
        # Sem renda e sem receita: sem proporção. Até a PRO-96 a barra sumia; desde a PRO-97 (D5) o trilho
        # aparece vazio com "Sem receitas ainda" (estadoDaBarra, testado em TestPainelVazio).
        self.assertIsNone(obtido['proporcaoSemBase'])
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


# ----------------------------------------------------------------------------
# 3. Boas-vindas, cadastro, tour e painel vazio (PRO-97)
# ----------------------------------------------------------------------------

def bloco_main(identificador):
    """Texto do <main id="..."> até o </main> que o fecha (os blocos não se aninham)."""
    inicio = HOME.index('<main id="%s"' % identificador)
    return HOME[inicio:HOME.index('</main>', inicio)]


class TestEstruturaDasBoasVindas(unittest.TestCase):
    def test_blocos_main_novos_e_ids(self):
        for identificador in ('boas-vindas', 'amigo', 'cadastro', 'tour'):
            with self.subTest(main=identificador):
                self.assertRegex(HOME, r'<main id="%s" class="tela-cheia" hidden>' % identificador)
        for trecho in ('id="escolha-novo"', 'id="escolha-amigo"', 'id="amigo-voltar"', 'id="amigo-carregando"',
                       'id="amigo-nao-encontrado"', 'id="amigo-erro"', 'id="amigo-novo"', 'id="amigo-tentar"',
                       'id="cadastro-voltar"', 'id="cadastro-passo-1"', 'id="cadastro-passo-2"', 'id="cadastro-passo-3"',
                       'id="cadastro-nome"', 'id="cadastro-nome-erro"', 'id="cadastro-dias"', 'id="cadastro-dia-texto"',
                       'id="cadastro-fichas"', 'id="cadastro-contador"', 'id="cadastro-adicionar-outra"',
                       'id="cadastro-nova"', 'id="cadastro-nova-campo"', 'id="cadastro-nova-adicionar"',
                       'id="cadastro-nova-erro"', 'id="cadastro-erro"', 'id="cadastro-continuar"', 'id="cadastro-dica"',
                       'id="tour-saudacao"', 'id="tour-passo-1"', 'id="tour-passo-2"', 'id="tour-proximo"',
                       'id="tour-pular"', 'id="tour-comecar"', 'id="tour-voltar"',
                       'id="grafico-vazio"', 'id="grafico-subtitulo"', 'id="extrato-vazio"', 'id="cartao-salario"'):
            with self.subTest(trecho=trecho):
                self.assertEqual(HOME.count(trecho), 1, trecho)

    def test_textos_dos_quadros(self):
        for texto in ('Boas-vindas ao', '>L1d3r<', 'O seu gerenciador de gastos, direto no Telegram.',
                      'Como Você Chegou Aqui?', 'Sou Novo Por Aqui', 'Configure tudo em 1 minuto',
                      'Já Sou Amigo do Adm', 'Entre com o seu Telegram',
                      'Buscando o Seu Cadastro...', 'Estamos te reconhecendo pelo Telegram. Não precisa digitar nada.',
                      'Não Encontramos o Seu Cadastro', 'Sem problema! Criar um cadastro novo leva menos de 1 minuto.',
                      'Criar Cadastro', 'Como Devemos Chamar Você?', 'É assim que o L1d3r vai te cumprimentar.',
                      'Seu Nome', 'Digite o seu nome', 'Preencha o seu nome para continuar',
                      'Qual o Melhor Dia Para o Seu Mês Virar?', 'Dia do Mês', 'Nos meses mais curtos, usamos o último dia',
                      '"Escolha um dia para continuar"', 'Escolha as Suas Categorias', 'Sugeridas', 'Adicionar Outra',
                      'Nova Categoria', '>Adicionar<', '"Você já tem a categoria "', '"Marque pelo menos uma categoria"',
                      'Como Registro um Gasto ou uma Receita Nova?',
                      'Na Tela Inicial, toque em <strong>Registrar</strong> e escolha entre Gasto ou Receita.',
                      'Escreva do Seu Jeito, o L1d3r Organiza', 'Últimas Movimentações', '+ R$ 3.000,00',
                      'O seu salário entra como Receita, por exemplo: <strong>"Recebi 3000 de salário"</strong>.',
                      '>Próximo<', '>Pular<', '>Começar<', '>Voltar<',
                      '"Ainda sem histórico"', 'O gráfico aparece com o seu primeiro gasto',
                      'Comece registrando o seu salário como <strong>Receita</strong>'):
            with self.subTest(texto=texto):
                self.assertIn(texto, HOME)
        # O nome exibido é L1d3r; "Fin4nce" não aparece em nenhuma tela nova.
        for identificador in ('boas-vindas', 'amigo', 'cadastro', 'tour'):
            self.assertNotIn('Fin4nce', bloco_main(identificador))

    def test_404_do_me_leva_a_escolha_inicial(self):
        carregar = corpo_da_funcao(codigo_principal(HOME), 'async function carregar')
        self.assertIn('const destino = destinoDaCarga(resultados);', carregar)
        self.assertIn('if (destino === "escolha") {\n        abrirEscolhaInicial();',
                      carregar.replace('\r\n', '\n'))
        # 401, 409 (cadastro_incompleto) e o resto continuam com o aviso de hoje.
        self.assertIn('mensagemDeErro(resultados)', carregar)
        self.assertIn('const MENSAGEM_409 = "Seu cadastro está incompleto. Volte ao bot e informe o dia de fechamento da fatura.";',
                      HOME)

    def test_home_nao_fala_mais_em_start(self):
        self.assertNotIn('/start', HOME)
        self.assertNotIn('Mande', HOME)

    def test_nome_digitado_e_dados_da_api_so_com_textcontent(self):
        principal = codigo_principal(HOME)
        self.assertIn('$("tour-saudacao").textContent = saudacaoDoTour(nome, agoraEmBrasilia().hora);',
                      corpo_da_funcao(principal, 'function abrirTour'))
        self.assertIn('ficha.querySelector("span").textContent = c.nome;', corpo_da_funcao(principal, 'function desenharFichas'))
        self.assertIn('$("amigo-erro-texto").textContent = erro.texto;',
                      corpo_da_funcao(principal, 'async function buscarCadastroDoAmigo'))
        self.assertIn('$("cadastro-nova-erro-texto").textContent =', corpo_da_funcao(principal, 'function atualizarCadastro'))
        # A ficha nasce de um <template> clonado (nada de HTML montado em texto).
        self.assertIn('<template id="modelo-ficha-categoria">', HOME)
        for padrao in (r'\.innerHTML\s*[+]?=', r'\.outerHTML\s*[+]?=', r'insertAdjacentHTML', r'document\.write'):
            self.assertIsNone(re.search(padrao, principal + codigo_puro(HOME)), padrao)

    def test_limites_do_d4_iguais_aos_do_cadastro_py(self):
        self.assertRegex(HOME, r'<input class="campo-texto" id="cadastro-nome" type="text" maxlength="%d"' % cadastro.NOME_MAXIMO)
        self.assertRegex(HOME, r'<input class="campo-texto compacto" id="cadastro-nova-campo" type="text" maxlength="%d"'
                         % cadastro.CATEGORIA_MAXIMA)
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify([CADASTRO_NOME_MAXIMO, CADASTRO_CATEGORIA_MAXIMA, CADASTRO_CATEGORIAS_MAXIMAS,"
            " CATEGORIAS_SUGERIDAS]));"
        )) if NODE else None
        if obtido:
            self.assertEqual(obtido[:3], [cadastro.NOME_MAXIMO, cadastro.CATEGORIA_MAXIMA, cadastro.CATEGORIAS_MAXIMAS])
            self.assertEqual(obtido[3], ['Alimentação', 'Casa', 'Lazer', 'Transporte', 'Saúde', 'Roupas', 'Academia', 'Viagens'])

    def test_cadastro_manda_post_com_as_novas_tentativas_do_registrar(self):
        enviar = corpo_da_funcao(codigo_principal(HOME), 'async function enviarCadastro')
        self.assertIn('montarCorpoCadastro(e.nome, e.dia, e.categorias)', enviar)
        self.assertIn('gravarComTentativas((c) => enviarJson("/cadastro", c, 15000), corpo, esperar)', enviar)
        self.assertIn('decisaoDoCadastro(resultado)', enviar)
        self.assertIn('abrirTour(e.nome)', enviar)
        # Desabilita no primeiro toque: 'enviando' liga antes da chamada e o Continuar depende dele.
        self.assertLess(enviar.index('e.enviando = true;'), enviar.index('gravarComTentativas('))
        self.assertIn('if (e.enviando) return false;', corpo_da_funcao(codigo_puro(HOME), 'function podeContinuarCadastro'))
        self.assertEqual(HOME.count('"/cadastro"'), 1)

    def test_backbutton_e_setas_das_telas_novas(self):
        principal = codigo_principal(HOME)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        for trecho in ('voltarNoCadastro()', 'voltarDoAmigo()', 'voltarDoRegistroParaHome()'):
            self.assertIn(trecho, voltar)
        self.assertIn('mostrarBackButton(id === "amigo" || id === "cadastro");',
                      corpo_da_funcao(principal, 'function mostrarTelaBoasVindas'))
        self.assertIn('$("amigo-voltar").addEventListener("click", voltarUmNivel);', principal)
        self.assertIn('$("cadastro-voltar").addEventListener("click", voltarUmNivel);', principal)
        # O tour não tem seta de voltar; a escolha inicial também não.
        self.assertNotIn('botao-menu', bloco_main('tour'))
        self.assertNotIn('botao-menu', bloco_main('boas-vindas'))
        # Do passo 1, a seta volta à escolha inicial.
        self.assertIn('mostrarTelaBoasVindas("boas-vindas");', corpo_da_funcao(principal, 'function voltarNoCadastro'))

    def test_cartao_do_salario_abre_o_registrar_direto_na_receita(self):
        principal = codigo_principal(HOME)
        self.assertIn('$("cartao-salario").addEventListener("click", abrirReceitaDireto);', principal)
        abrir = corpo_da_funcao(principal, 'function abrirReceitaDireto')
        self.assertIn('abrirRegistro("receita");', abrir)
        self.assertIn('registroDireto = true;', abrir)
        self.assertNotIn('registro-opcoes', abrir)  # pula a tela de opções
        # O Voltar do chat aberto pelo cartão volta para a home.
        self.assertIn('if (registroDireto) voltarDoRegistroParaHome();', corpo_da_funcao(principal, 'function voltarUmNivel'))
        self.assertIn('$("tela").hidden = false;', corpo_da_funcao(principal, 'function voltarDoRegistroParaHome'))
        # O cartão é um botão, fora da lista (o clique da lista só abre linhas .gasto).
        self.assertIn('<button class="cartao-salario" id="cartao-salario" type="button" hidden>', HOME)

    def test_saudacao_do_tour_usa_a_hora_de_brasilia(self):
        principal = codigo_principal(HOME)
        self.assertIn('agoraEmBrasilia().hora', corpo_da_funcao(principal, 'function abrirTour'))
        self.assertIn('timeZone: FUSO', corpo_da_funcao(principal, 'function agoraEmBrasilia'))
        self.assertIn('const FUSO = "America/Sao_Paulo";', principal)

    def test_paleta_nova_no_root_e_logo_animada_so_com_css(self):
        raiz = HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))]
        for token in ('--cor-erro: #D93025;', '--cor-erro-fundo: #FFF5F4;', '--cor-receita-fundo: #15803D14;'):
            self.assertIn(token, raiz)
        # 6 = os 3 traços do "Buscando o Seu Cadastro..." + os 3 da tela da logo (PRO-100).
        self.assertEqual(HOME.count('pathLength="1"'), 6)
        for quadro in ('@keyframes tracar-1', '@keyframes tracar-2', '@keyframes tracar-3', 'prefers-reduced-motion'):
            self.assertIn(quadro, HOME)

    def test_fichas_de_categoria_nao_usam_a_classe_das_parcelas(self):
        # O Registrar marca/desmarca todo ".ficha"; as fichas do cadastro têm classe própria.
        self.assertNotRegex(bloco_main('cadastro'), r'class="ficha[ "]')
        self.assertNotRegex(bloco_main('tour'), r'class="atalho[ "]')  # o seletor .atalho liga os atalhos da home

    def test_saudacao_por_hora_movida_sem_duplicar(self):
        self.assertIn('function saudacaoPorHora(hora)', codigo_puro(HOME))
        self.assertNotIn('function saudacaoPorHora', codigo_principal(HOME))


def casos_de_normalizacao():
    """Textos para comparar normalizarTexto (JS) com normalizar_texto (Python)."""
    casos = ['Alimentação', 'alimentacao', 'ALIMENTAÇÃO', '  Alimentação ', 'Alimen  tação', 'Saúde', 'saude', 'SAÚDE',
             'Lazer', 'lazer', ' LAZER ', 'Transporte', 'Pão de Açúcar', 'pao de acucar', 'Ônibus', 'ônibus', 'Café',
             'cafe', 'Ação', 'acao', 'Maçã', 'Crédito', 'Débito/Pix', 'Viagens', 'Educação Física', 'educacao  fisica',
             'Ñandú', 'Straße', 'STRASSE', 'Ångström', 'ﬁnanças', 'Ｃａｓａ', 'Casa' + chr(0xA0) + 'Nova',
             'Casa' + chr(0x2003) + 'Nova', 'Casa\tNova', 'Casa\nNova', 'Casa' + chr(0x85) + 'Nova',
             'Casa' + chr(0x1F) + 'Nova', 'Casa' + chr(0xFEFF) + 'Nova', 'Casa' + chr(0x3000) + 'Nova',
             'a' + chr(0x301), 'e' + chr(0x302) + chr(0x323), chr(0x345), 'x' + chr(0x345) + 'y', 'İstanbul', 'ΣΟΦΟΣ',
             'σοφος', 'λόγος', 'Ǆemal', 'Ⅻ', '½ porção', 'Pet 🐶', '', '   ', chr(0x301) + chr(0x302), '1º lugar',
             'Saúde ' + chr(0x200A) + ' e  Bem-Estar']
    # Todos os caracteres latinos de U+0020 a U+024F, os acentos combinantes (U+0300 a U+036F), os latinos
    # estendidos adicionais (U+1E00 a U+1EFF), o grego e o cirílico básicos, sozinhos e no meio de uma palavra.
    for faixa in (range(0x20, 0x250), range(0x300, 0x370), range(0x1E00, 0x1F00), range(0x370, 0x400),
                  range(0x400, 0x500)):
        for codigo in faixa:
            casos.append(chr(codigo))
            casos.append('Ca' + chr(codigo) + 'sa')
    return casos


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestCadastroFuncoesPuras(unittest.TestCase):
    def test_normalizar_texto_igual_ao_do_cadastro_py(self):
        casos = casos_de_normalizacao()
        esperado = [cadastro.normalizar_texto(t) for t in casos]
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.map(normalizarTexto)));"), casos)
        diferentes = [(ascii(t), o, e) for t, o, e in zip(casos, obtido, esperado) if o != e]
        self.assertEqual(diferentes[:10], [], 'diferentes do normalizar_texto(): %d' % len(diferentes))
        self.assertGreater(len(casos), 2000)
        # Os casos que importam na tela, explícitos:
        self.assertEqual(esperado[:4], ['alimentacao'] * 4)
        self.assertEqual(cadastro.normalizar_texto('Alimen  tação'), 'alimen tacao')

    def test_o_teste_tem_forca_um_lowercase_ingenuo_erraria(self):
        # Sem o NFKD, sem tirar as marcas ou só com toLowerCase e \\s, o JavaScript erra casos desta lista.
        casos = casos_de_normalizacao()
        esperado = [cadastro.normalizar_texto(t) for t in casos]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map((t) => String(t).normalize('NFD').replace(/[\\u0300-\\u036f]/g, '')"
            ".toLowerCase().trim().split(/\\s+/).filter(Boolean).join(' '))));"
        ), casos)
        self.assertGreater(sum(1 for o, e in zip(obtido, esperado) if o != e), 10)

    def test_categoria_repetida_pela_mesma_comparacao_do_servidor(self):
        nomes = ['Alimentação', 'Casa', 'Lazer', 'Transporte', 'Saúde', 'Roupas', 'Academia', 'Viagens']
        entradas = ['lazer', ' LAZER ', 'Lazer', 'alimentacao', 'ALIMENTAÇÃO', 'saude', 'Saúde  ', 'Pet', '', '   ',
                    chr(0x301), 'Casa nova', 'Via gens']
        obtido = rodar_node(com_funcoes_puras(
            "const [nomes, entradas] = ENTRADA;"
            " console.log(JSON.stringify(entradas.map((t) => categoriaRepetida(t, nomes))));"
        ), [nomes, entradas])
        self.assertEqual(obtido, ['Lazer', 'Lazer', 'Lazer', 'Alimentação', 'Alimentação', 'Saúde', 'Saúde',
                                  None, None, None, None, None, None])
        # A mesma decisão do servidor: repetida na tela <=> montar_lista_categorias juntaria as duas.
        # (Sem nada que sobre na comparação, como só espaços ou só um acento, o Adicionar fica desabilitado.)
        for entrada, repetida in zip(entradas, obtido):
            if not cadastro.normalizar_texto(entrada):
                continue
            juntou = len(cadastro.montar_lista_categorias(nomes + [entrada])) == len(nomes)
            with self.subTest(entrada=ascii(entrada)):
                self.assertEqual(repetida is not None, juntou)

    def test_nome_da_categoria_igual_ao_strip_capitalize(self):
        casos = ['pet', '  pet  ', 'PET', 'iFood', 'educação física', 'ÁGUA', 'água', 'Ônibus urbano', '1º lugar',
                 'casa  Nova', 'é', 'x']
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.map(nomeDaCategoria)));"), casos)
        self.assertEqual(obtido, [t.strip().capitalize() for t in casos])

    def test_contador_de_marcadas(self):
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify([0, 1, 2, 8, 20].map(textoContadorCategorias)));"))
        self.assertEqual(obtido, ['Nenhuma marcada', '1 marcada', '2 marcadas', '8 marcadas', '20 marcadas'])

    def test_texto_do_dia(self):
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify([textoDiaEscolhido(5), textoDiaEscolhido(31), textoDiaEscolhido(null)]));"))
        self.assertEqual(obtido, [{'antes': 'Todo dia ', 'negrito': '5'}, {'antes': 'Todo dia ', 'negrito': '31'},
                                  {'antes': 'Nenhum dia escolhido', 'negrito': ''}])

    def test_saudacao_do_tour_nas_tres_faixas(self):
        # A hora vem de agoraEmBrasilia() (só a hora cheia): 4h59 -> 4, 11h59 -> 11, 17h59 -> 17.
        horas = [4, 5, 11, 12, 17, 18, 0, 23]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map((h) => saudacaoDoTour('Ygor Nóbrega', h))"
            ".concat([saudacaoDoTour('  Ana  ', 12), saudacaoDoTour('', 9), saudacaoDoTour(null, 20)])));"
        ), horas)
        self.assertEqual(obtido, [
            'Olá, Ygor! Boa Noite', 'Olá, Ygor! Bom Dia', 'Olá, Ygor! Bom Dia', 'Olá, Ygor! Boa Tarde',
            'Olá, Ygor! Boa Tarde', 'Olá, Ygor! Boa Noite', 'Olá, Ygor! Boa Noite', 'Olá, Ygor! Boa Noite',
            'Olá, Ana! Boa Tarde', 'Bom Dia', 'Boa Noite',
        ])

    def test_continuar_de_cada_passo(self):
        sugeridas = [{'nome': n, 'marcada': True} for n in ('Casa', 'Lazer')]
        nenhuma = [{'nome': n, 'marcada': False} for n in ('Casa', 'Lazer')]
        vinte = [{'nome': 'C%d' % i, 'marcada': True} for i in range(20)]
        vinte_e_uma = vinte + [{'nome': 'C20', 'marcada': True}]
        casos = [
            [{'passo': 1, 'nome': 'Ygor'}, True], [{'passo': 1, 'nome': ''}, False], [{'passo': 1, 'nome': '   '}, False],
            [{'passo': 1, 'nome': 'Ygor', 'enviando': True}, False],
            [{'passo': 2, 'dia': 5}, True], [{'passo': 2, 'dia': None}, False], [{'passo': 2, 'dia': 31}, True],
            [{'passo': 2, 'dia': 0}, False], [{'passo': 2, 'dia': 32}, False],
            [{'passo': 3, 'categorias': sugeridas}, True], [{'passo': 3, 'categorias': nenhuma}, False],
            [{'passo': 3, 'categorias': vinte}, True], [{'passo': 3, 'categorias': vinte_e_uma}, False],
            [{'passo': 3, 'categorias': sugeridas, 'enviando': True}, False],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([e]) => podeContinuarCadastro(e))));"), casos)
        self.assertEqual(obtido, [c[1] for c in casos])

    def test_corpo_do_post_cadastro_so_com_as_marcadas_na_ordem(self):
        categorias = [{'nome': 'Alimentação', 'marcada': True}, {'nome': 'Casa', 'marcada': False},
                      {'nome': 'Lazer', 'marcada': True}, {'nome': 'Pet', 'marcada': True}]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(montarCorpoCadastro('  Ygor  ', 5, ENTRADA)));"), categorias)
        self.assertEqual(obtido, {'nome': 'Ygor', 'dia_fechamento': 5, 'categorias': ['Alimentação', 'Lazer', 'Pet']})
        # O formato do contrato da PRO-96: o cadastro.py aceita este corpo como veio.
        self.assertEqual(cadastro.validar_nome(obtido['nome']), 'Ygor')
        self.assertEqual(cadastro.validar_dia(obtido['dia_fechamento']), 5)
        self.assertEqual(cadastro.validar_categorias(obtido['categorias']), ['Alimentação', 'Lazer', 'Pet'])

    def test_decisao_do_cadastro_por_status(self):
        casos = [
            {'dados': {'user_id': 1}, 'status': 201},
            {'erro': 409, 'detalhe': 'usuario_ja_cadastrado'},
            {'erro': 409, 'detalhe': 'outro'},
            {'erro': 422, 'detalhe': 'nome_invalido'},
            {'erro': 422, 'detalhe': 'dia_invalido'},
            {'erro': 422, 'detalhe': 'categorias_invalidas'},
            {'erro': 401, 'detalhe': 'nao_autorizado'},
            {'erro': 500, 'detalhe': 'erro_interno'},
            {'erro': 503, 'detalhe': 'banco_ocupado'},
            {'erro': 0},
            {'erro': 500, 'detalhe': 'SEGREDO sql=SELECT'},
        ]
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.map(decisaoDoCadastro)));"), casos)
        seguir = {'seguir': True, 'mensagem': None}
        dados = {'seguir': False, 'mensagem': 'Confira os dados e tente de novo.'}
        falhou = {'seguir': False, 'mensagem': 'Não foi possível criar o seu cadastro. Tente de novo.'}
        sessao = {'seguir': False, 'mensagem': 'Sua sessão expirou. Feche e abra o app de novo.'}
        self.assertEqual(obtido, [seguir, seguir, falhou, dados, dados, dados, sessao, falhou, falhou, falhou, falhou])

    def test_cadastro_repete_so_em_503_e_rede_com_o_mesmo_corpo(self):
        obtido = rodar_node(com_funcoes_puras("""
            const cenarios = {
              ocupadoDepoisCriado: [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 0 }, { dados: { user_id: 1 }, status: 201 }],
              redeEsgotada: [{ erro: 0 }, { erro: 0 }, { erro: 0 }, { dados: {}, status: 201 }],
              toqueDuplo: [{ erro: 409, detalhe: 'usuario_ja_cadastrado' }],
              recusado: [{ erro: 422, detalhe: 'nome_invalido' }, { dados: {}, status: 201 }],
              interno: [{ erro: 500, detalhe: 'erro_interno' }, { dados: {}, status: 201 }],
            };
            (async () => {
              const saida = {};
              for (const [nome, respostas] of Object.entries(cenarios)) {
                const chamadas = [];
                const esperas = [];
                const corpo = montarCorpoCadastro('Ygor', 5, [{ nome: 'Casa', marcada: true }]);
                const resultado = await gravarComTentativas(
                  async (c) => { chamadas.push(c); return respostas[chamadas.length - 1]; }, corpo,
                  async (ms) => { esperas.push(ms); });
                saida[nome] = { chamadas: chamadas.length, esperas, mesmo: chamadas.every((c) => c === corpo),
                                decisao: decisaoDoCadastro(resultado) };
              }
              console.log(JSON.stringify(saida));
            })();
        """))
        self.assertEqual(obtido['ocupadoDepoisCriado']['chamadas'], 3)
        self.assertEqual(obtido['ocupadoDepoisCriado']['esperas'], [1000, 2000])
        self.assertTrue(obtido['ocupadoDepoisCriado']['decisao']['seguir'])
        self.assertEqual(obtido['redeEsgotada']['chamadas'], 3)
        self.assertEqual(obtido['redeEsgotada']['decisao']['mensagem'], 'Não foi possível criar o seu cadastro. Tente de novo.')
        self.assertEqual(obtido['toqueDuplo']['chamadas'], 1)
        self.assertTrue(obtido['toqueDuplo']['decisao']['seguir'])
        self.assertEqual(obtido['recusado']['chamadas'], 1)
        self.assertEqual(obtido['interno']['chamadas'], 1)
        for nome, dados in obtido.items():
            with self.subTest(cenario=nome):
                self.assertTrue(dados['mesmo'])

    def test_destino_da_primeira_carga(self):
        ok = {'dados': {}}
        nao_cadastrado = {'erro': 404, 'detalhe': 'usuario_nao_cadastrado'}
        casos = [
            [[ok, ok, ok, ok], 'home'],
            [[nao_cadastrado, nao_cadastrado, nao_cadastrado, nao_cadastrado], 'escolha'],
            [[nao_cadastrado, {'erro': 0}, nao_cadastrado, nao_cadastrado], 'escolha'],
            [[{'erro': 401}, {'erro': 401}, {'erro': 401}, {'erro': 401}], 'aviso'],
            [[nao_cadastrado, {'erro': 401}, ok, ok], 'aviso'],
            [[ok, {'erro': 409, 'detalhe': 'cadastro_incompleto'}, {'erro': 409, 'detalhe': 'cadastro_incompleto'}, ok], 'aviso'],
            [[{'erro': 0}, ok, ok, ok], 'aviso'],
            [[ok, {'erro': 404, 'detalhe': 'usuario_nao_cadastrado'}, ok, ok], 'aviso'],   # só o /me decide
            [[{'erro': 404, 'detalhe': 'outro'}, ok, ok, ok], 'aviso'],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([r]) => destinoDaCarga(r))));"), casos)
        self.assertEqual(obtido, [c[1] for c in casos])


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestPainelVazio(unittest.TestCase):
    """D5: barra vazia com "Sem receitas ainda", gráfico vazio e cartão do salário, pelo estado dos dados."""

    def test_estado_da_barra(self):
        obtido = rodar_node(com_funcoes_puras("""
            const caso = (renda, receitas, gasto, temSobra) => {
              const base = baseDaBarra(renda, receitas);
              return estadoDaBarra(temSobra, base, proporcaoDaBarra(gasto, base));
            };
            console.log(JSON.stringify({
              vazio: caso(0, 0, 0, true),
              vazioComGasto: caso(0, 0, 120, true),
              comRenda: caso(3014, 650, 2029.66, true),
              soReceita: caso(0, 650, 100, true),
              semSobra: caso(3014, 0, 10, false),
              semSobraEVazio: caso(0, 0, 0, false),
            }));
        """))
        self.assertEqual(obtido['vazio'], {'trilho': True, 'preenchimento': 0, 'textoDireita': 'Sem receitas ainda'})
        self.assertEqual(obtido['vazioComGasto'], {'trilho': True, 'preenchimento': 0, 'textoDireita': 'Sem receitas ainda'})
        self.assertTrue(obtido['comRenda']['trilho'])
        self.assertAlmostEqual(obtido['comRenda']['preenchimento'], 2029.66 / 3664, places=12)
        self.assertEqual(obtido['comRenda']['textoDireita'], 'de R$ 3.664,00')
        self.assertEqual(obtido['soReceita']['textoDireita'], 'de R$ 650,00')
        self.assertEqual(obtido['semSobra'], {'trilho': False, 'preenchimento': 0, 'textoDireita': None})
        self.assertEqual(obtido['semSobraEVazio'], {'trilho': False, 'preenchimento': 0, 'textoDireita': None})

    def test_mostrar_cabecalho_usa_o_estado_da_barra(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarCabecalho')
        self.assertIn('const barra = estadoDaBarra(temSobra, base, proporcao);', corpo)
        self.assertIn('$("trilho").hidden = !barra.trilho;', corpo)
        self.assertIn('$("renda-texto").hidden = barra.textoDireita === null;', corpo)

    def test_grafico_vazio_e_cartao_do_salario(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              semMeses: graficoVazio([]),
              tudoZero: graficoVazio([{ total_gasto: 0, situacao: 'atual' }, { total_gasto: 0, situacao: 'previsto' }]),
              umGasto: graficoVazio([{ total_gasto: 0, situacao: 'fechado' }, { total_gasto: 12.5, situacao: 'atual' }]),
              soPrevisto: graficoVazio([{ total_gasto: 0, situacao: 'atual' }, { total_gasto: 200, situacao: 'previsto' }]),
              nulo: graficoVazio(undefined),
              listaVazia: mostrarCartaoDoSalario([]),
              listaComReceita: mostrarCartaoDoSalario([{ tipo: 'receita', valor: 3000 }]),
            }));
        """))
        self.assertEqual(obtido, {'semMeses': True, 'tudoZero': True, 'umGasto': False, 'soPrevisto': False,
                                  'nulo': True, 'listaVazia': True, 'listaComReceita': False})

    def test_mostrar_grafico_usa_o_estado_vazio(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarGrafico')
        self.assertIn('const vazio = graficoVazio(meses);', corpo)
        self.assertIn('vazio ? "Ainda sem histórico" : "Arraste para ver outros meses"', corpo)
        self.assertIn('$("grafico-vazio").hidden = !vazio;', corpo)


# ----------------------------------------------------------------------------
# PRO-99: rótulo do gráfico "Gastos por mês"
# ----------------------------------------------------------------------------

CASOS_ROTULO = [
    (0, '0'), (0.004, '0'), (13.5, '13,50'), (820, '820'), (820.05, '820,05'),
    (999.99, '999,99'), (999.995, '1 mil'), (1000, '1 mil'), (1049.99, '1 mil'),
    (1050, '1,1 mil'), (1500, '1,5 mil'), (1949.99, '1,9 mil'), (1950, '2 mil'),
    (2050, '2,1 mil'), (4287.9, '4,3 mil'),
]

# Corpo ANTIGO da função (antes da PRO-99), guardado para provar que o teste tem força.
ROTULO_ANTIGO = """function formatarRotuloValor(total) {
  if (total >= 1000) {
    return (total / 1000).toFixed(1).replace(".", ",").replace(/,0$/, "") + " mil";
  }
  return String(Math.round(total));
}"""


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestRotuloDoGrafico(unittest.TestCase):
    def test_tabela_de_casos_no_node(self):
        entradas = [c[0] for c in CASOS_ROTULO]
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify(ENTRADA.map((v) => formatarRotuloValor(v))));
        """), entradas)
        esperado = [c[1] for c in CASOS_ROTULO]
        for entrada, saida, certo in zip(entradas, obtido, esperado):
            with self.subTest(entrada=entrada):
                self.assertEqual(saida, certo)

    def test_rotulo_nao_tem_simbolo_de_moeda(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify(ENTRADA.map((v) => formatarRotuloValor(v))));
        """), [c[0] for c in CASOS_ROTULO])
        for texto in obtido:
            self.assertNotIn('R$', texto)

    def test_o_teste_tem_forca_a_funcao_antiga_erra(self):
        # A função antiga, nos mesmos casos, erra pelo menos 13,5 ("14") e 999,99 ("1000").
        entradas = [c[0] for c in CASOS_ROTULO]
        programa = (
            "const fs = require('fs');\n"
            "const ENTRADA = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));\n"
            + ROTULO_ANTIGO + "\n"
            "console.log(JSON.stringify(ENTRADA.map((v) => formatarRotuloValor(v))));\n"
        )
        obtido = dict(zip(entradas, rodar_node(programa, entradas)))
        esperado = dict(CASOS_ROTULO)
        self.assertEqual(obtido[13.5], '14')
        self.assertEqual(obtido[999.99], '1000')
        erradas = [e for e in entradas if obtido[e] != esperado[e]]
        self.assertIn(13.5, erradas)
        self.assertIn(999.99, erradas)

    def test_formatar_rotulo_valor_esta_no_bloco_de_funcoes_puras(self):
        self.assertIn('function formatarRotuloValor(', codigo_puro(HOME))
        self.assertNotIn('function formatarRotuloValor(', codigo_principal(HOME))
        self.assertEqual(HOME.count('function formatarRotuloValor('), 1)

    def test_formatador_numero_e_declarado_uma_unica_vez(self):
        self.assertEqual(HOME.count('const formatadorNumero '), 1)
        self.assertIn('const formatadorNumero', codigo_puro(HOME))
        # O valor grande do topo continua usando o mesmo formatador.
        self.assertIn('formatadorNumero.format(Math.abs(valor))', codigo_principal(HOME))

    def test_mostrar_grafico_chama_formatar_rotulo_valor(self):
        corpo = corpo_da_funcao(codigo_principal(HOME), 'function mostrarGrafico')
        self.assertIn('const rotulo = formatarRotuloValor(total);', corpo)
        self.assertIn('criar("div", classeDoRotulo(rotulo), rotulo)', corpo)

    def test_classe_do_rotulo_no_node(self):
        entradas = [c[0] for c in CASOS_ROTULO]
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify(ENTRADA.map((v) => classeDoRotulo(formatarRotuloValor(v)))));
        """), entradas)
        for entrada, classe, (_, texto) in zip(entradas, obtido, CASOS_ROTULO):
            with self.subTest(entrada=entrada):
                esperado = 'coluna-valor' if texto.endswith(' mil') else 'coluna-valor valor-exato'
                self.assertEqual(classe, esperado)
        self.assertEqual(obtido[entradas.index(0)], 'coluna-valor valor-exato')
        self.assertEqual(obtido[entradas.index(0.004)], 'coluna-valor valor-exato')
        self.assertEqual(obtido[entradas.index(999.99)], 'coluna-valor valor-exato')
        self.assertEqual(obtido[entradas.index(999.995)], 'coluna-valor')

    def test_classe_do_rotulo_esta_no_bloco_de_funcoes_puras(self):
        self.assertIn('function classeDoRotulo(', codigo_puro(HOME))
        self.assertEqual(HOME.count('function classeDoRotulo('), 1)

    def test_css_do_rotulo_valor_exato_em_10_5px(self):
        self.assertRegex(HOME, r'\.coluna-valor\.valor-exato\s*\{\s*font-size:\s*10\.5px;\s*\}')
        regra = re.search(r'(?m)^\s*\.coluna-valor\s*\{([^}]*)\}', HOME).group(1)
        self.assertIn('font-size: 12px', regra)
        self.assertIn('line-height: 16px', regra)
        self.assertNotIn('10.5px', regra)


# ----------------------------------------------------------------------------
# PRO-100: tela da logo com tempo mínimo (abertura e salvamento do cadastro)
# ----------------------------------------------------------------------------

def bloco_tela_logo():
    """Texto do <div id="tela-logo"> até o comentário que abre o aviso de tela cheia (que vem logo depois)."""
    inicio = HOME.index('<div id="tela-logo"')
    return HOME[inicio:HOME.index('<!-- Aviso de tela cheia', inicio)]


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestTempoMinimoNoNode(unittest.TestCase):
    def test_tempo_que_falta_nas_bordas(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              minimo: TEMPO_MINIMO_LOGO_MS,
              falta_total: tempoQueFalta(1000, 1000, 1800),
              um_antes: tempoQueFalta(1000, 1000 + 1799, 1800),
              no_minimo: tempoQueFalta(1000, 1000 + 1800, 1800),
              um_depois: tempoQueFalta(1000, 1000 + 1801, 1800),
              bem_depois: tempoQueFalta(1000, 1000 + 60000, 1800),
              meio: tempoQueFalta(0, 500, 1800),
              relogio_voltando: tempoQueFalta(1000, 400, 1800),
              relogio_voltando_muito: tempoQueFalta(1000, -50000, 1800),
            }));
        """))
        self.assertEqual(obtido, {
            'minimo': 1800,
            'falta_total': 1800,
            'um_antes': 1,
            'no_minimo': 0,
            'um_depois': 0,
            'bem_depois': 0,
            'meio': 1300,
            'relogio_voltando': 1800,        # nunca maior que o mínimo
            'relogio_voltando_muito': 1800,
        })

    def test_com_tempo_minimo_rapido_com_sucesso_espera_o_que_falta_uma_vez(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const saida = {};
              // Relógio de mentira: 1ª leitura (início) = 100 ms; 2ª (resposta) = 100 + 300 ms.
              const leituras = [100, 400];
              const esperas = [];
              const resposta = { dados: { id: 1 }, status: 201 };
              saida.resultado = await comTempoMinimo(
                Promise.resolve(resposta), 1800, (r) => r.erro === undefined,
                () => leituras.shift(), async (ms) => { esperas.push(ms); }
              );
              saida.mesmoObjeto = saida.resultado === resposta;
              saida.esperas = esperas;
              console.log(JSON.stringify(saida));
            })();
        """))
        self.assertEqual(obtido['esperas'], [1500])
        self.assertTrue(obtido['mesmoObjeto'])
        self.assertEqual(obtido['resultado'], {'dados': {'id': 1}, 'status': 201})

    def test_com_tempo_minimo_o_inicio_anotado_antes_de_aguardar_a_promessa(self):
        # A promessa só resolve DEPOIS de o relógio avançar: o início tem de ser a leitura anterior ao await.
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              let agora = 0;
              const esperas = [];
              const promessa = new Promise((resolver) => setTimeout(() => { agora = 700; resolver('ok'); }, 5));
              const r = await comTempoMinimo(promessa, 1800, () => true, () => agora, async (ms) => { esperas.push(ms); });
              console.log(JSON.stringify({ r, esperas }));
            })();
        """))
        self.assertEqual(obtido, {'r': 'ok', 'esperas': [1100]})

    def test_com_tempo_minimo_inicio_de_fora_conta_desde_o_comeco_da_pagina(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const saida = {};
              for (const [nome, agora] of Object.entries({ cedo: 600, tarde: 1800, muito_tarde: 5000 })) {
                const esperas = [];
                await comTempoMinimo(Promise.resolve('ok'), 1800, () => true, () => agora, async (ms) => { esperas.push(ms); }, 0);
                saida[nome] = esperas;
              }
              console.log(JSON.stringify(saida));
            })();
        """))
        self.assertEqual(obtido, {'cedo': [1200], 'tarde': [], 'muito_tarde': []})

    def test_com_tempo_minimo_lento_nao_espera(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const leituras = [0, 2500];
              const esperas = [];
              const r = await comTempoMinimo(Promise.resolve('ok'), 1800, () => true,
                () => leituras.shift(), async (ms) => { esperas.push(ms); });
              console.log(JSON.stringify({ r, esperas }));
            })();
        """))
        self.assertEqual(obtido, {'r': 'ok', 'esperas': []})

    def test_com_tempo_minimo_erro_nao_espera_mesmo_rapido(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const erro = { erro: 422, detalhe: 'dados_invalidos' };
              let leituras = 0;
              const esperas = [];
              const r = await comTempoMinimo(Promise.resolve(erro), 1800, (x) => x.erro === undefined,
                () => { leituras++; return 0; }, async (ms) => { esperas.push(ms); });
              // Pela regra da abertura: o aviso (erro) sai na hora; home e escolha esperam.
              const deve = (x) => destinoDaCarga(x) !== 'aviso';
              const destinos = {
                home: deve([{ dados: {} }, { dados: {} }, { dados: {} }, { dados: {} }]),
                escolha: deve([{ erro: 404, detalhe: 'usuario_nao_cadastrado' }, { erro: 404 }, { erro: 404 }, { erro: 404 }]),
                aviso401: deve([{ erro: 401 }, { erro: 401 }, { erro: 401 }, { erro: 401 }]),
                aviso409: deve([{ erro: 409, detalhe: 'cadastro_incompleto' }, { dados: {} }, { dados: {} }, { dados: {} }]),
                avisoRede: deve([{ erro: 0 }, { erro: 0 }, { erro: 0 }, { erro: 0 }]),
              };
              // E pela regra do cadastro: 201 e 409 usuario_ja_cadastrado esperam; o resto não.
              const cad = (x) => decisaoDoCadastro(x).seguir;
              const cadastro = {
                c201: cad({ dados: {}, status: 201 }),
                c409: cad({ erro: 409, detalhe: 'usuario_ja_cadastrado' }),
                c422: cad({ erro: 422 }),
                c401: cad({ erro: 401 }),
                c500: cad({ erro: 500 }),
                c503: cad({ erro: 503, detalhe: 'banco_ocupado' }),
                rede: cad({ erro: 0 }),
              };
              console.log(JSON.stringify({ r, esperas, leituras, destinos, cadastro }));
            })();
        """))
        self.assertEqual(obtido['r'], {'erro': 422, 'detalhe': 'dados_invalidos'})
        self.assertEqual(obtido['esperas'], [])
        self.assertEqual(obtido['destinos'], {'home': True, 'escolha': True, 'aviso401': False, 'aviso409': False, 'avisoRede': False})
        self.assertEqual(obtido['cadastro'], {'c201': True, 'c409': True, 'c422': False, 'c401': False,
                                              'c500': False, 'c503': False, 'rede': False})


class TestEstruturaDaTelaDaLogo(unittest.TestCase):
    def test_nenhum_setinterval_no_arquivo(self):
        self.assertNotIn('setInterval', HOME)

    def test_constante_declarada_uma_vez_no_bloco_puro_e_vale_1800(self):
        self.assertEqual(len(re.findall(r'\bconst\s+TEMPO_MINIMO_LOGO_MS\b', HOME)), 1)
        self.assertIn('const TEMPO_MINIMO_LOGO_MS = 1800;', codigo_puro(HOME))
        self.assertNotIn('TEMPO_MINIMO_LOGO_MS =', codigo_principal(HOME))
        # Nenhum 1800 solto no código principal: quem precisa do mínimo usa a constante.
        self.assertNotRegex(codigo_principal(HOME), r'\b1800\b')
        for funcao in ('function tempoQueFalta', 'async function comTempoMinimo'):
            with self.subTest(funcao=funcao):
                self.assertEqual(HOME.count(funcao), 1)
                self.assertIn(funcao, codigo_puro(HOME))

    def test_tela_da_logo_visivel_no_html_com_a_logo_animada(self):
        self.assertEqual(HOME.count('id="tela-logo"'), 1)
        self.assertRegex(HOME, r'<div id="tela-logo" class="tela-logo">')
        bloco = bloco_tela_logo()
        self.assertNotRegex(bloco, r'(?<!aria-)hidden')
        self.assertEqual(bloco.count('class="logo-animada"'), 1)
        for traco in ('traco-1', 'traco-2', 'traco-3'):
            with self.subTest(traco=traco):
                self.assertEqual(bloco.count('class="traco %s" pathLength="1"' % traco), 1)
        self.assertEqual(bloco.count('class="base"'), 3)
        for classe in ('cabecalho cabecalho-registro', 'cabecalho-linha cabecalho-titulo', 'titulo-tela',
                       'estado-central', 'estado-icone', 'estado-textos', 'estado-titulo', 'estado-texto'):
            with self.subTest(classe=classe):
                self.assertIn('class="%s' % classe, bloco)
        # Sem seta de voltar na tela da logo.
        self.assertNotIn('<button', bloco)
        # Os 3 textos têm id (trocados só por textContent) e começam com os da abertura.
        self.assertIn('id="tela-logo-cabecalho">L1d3r<', bloco)
        self.assertIn('id="tela-logo-titulo">Abrindo o Seu Painel...<', bloco)
        self.assertIn('id="tela-logo-texto">Só um instante.<', bloco)
        # É o primeiro bloco do <body>, para a logo estar no primeiro desenho.
        self.assertLess(HOME.index('<div id="tela-logo"'), HOME.index('<main id="tela"'))

    def test_css_da_tela_da_logo_cobre_tudo_e_usa_so_variaveis(self):
        regra = re.search(r'(?m)^\s*\.tela-logo\s*\{([^}]*)\}', HOME).group(1)
        self.assertIn('position: fixed', regra)
        self.assertIn('background: var(--cor-branco)', regra)
        self.assertNotRegex(regra, r'#[0-9A-Fa-f]{3,8}\b')
        z_logo = int(re.search(r'z-index:\s*(\d+)', regra).group(1))
        regra_aviso = re.search(r'(?m)^\s*\.aviso-topo\s*\{([^}]*)\}', HOME).group(1)
        z_aviso = int(re.search(r'z-index:\s*(\d+)', regra_aviso).group(1))
        self.assertGreater(z_logo, z_aviso)
        # A regra do movimento reduzido já cobre a logo (.logo-animada .traco).
        reduzido = HOME[HOME.index('@media (prefers-reduced-motion: reduce)'):]
        self.assertIn('.logo-animada .traco { animation: none; stroke-dashoffset: 0; opacity: 1; }', reduzido[:200])

    def test_com_tempo_minimo_so_na_abertura_no_cadastro_e_no_salvar_recorrente(self):
        principal = codigo_principal(HOME)
        self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, 'async function carregar'))
        self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, 'async function enviarCadastro'))
        self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, 'async function salvarRecorrente'))  # PRO-101: criar e editar
        for assinatura in ('async function interpretar', 'async function confirmar', 'function concluirRegistro',
                           'async function buscarCadastroDoAmigo', 'async function carregarDetalhe',
                           'function terminarTour', 'async function apagarRecorrente',
                           'async function carregarRecorrentes', 'async function carregarCategoriasRec'):
            with self.subTest(funcao=assinatura):
                corpo = corpo_da_funcao(principal, assinatura)
                self.assertNotIn('comTempoMinimo', corpo)
                self.assertNotIn('mostrarTelaLogo', corpo)
        # Fora dessas três funções (abertura, cadastro e salvar recorrente), ninguém chama o tempo mínimo.
        chamadas = principal.count('comTempoMinimo(')
        self.assertEqual(chamadas, 3)

    def test_abertura_so_na_primeira_carga(self):
        principal = codigo_principal(HOME)
        iniciar = corpo_da_funcao(principal, 'function iniciar()')
        self.assertIn('carregar(true);', iniciar)
        self.assertEqual(principal.count('carregar(true)'), 1)
        # "Tentar de novo" não passa o evento do clique como 'abertura'; as outras chamadas ficam sem argumento.
        self.assertIn('$("aviso-botao").addEventListener("click", () => carregar());', iniciar)
        self.assertNotIn('addEventListener("click", carregar)', principal)
        for assinatura in ('function concluirRegistro', 'async function buscarCadastroDoAmigo', 'function terminarTour'):
            with self.subTest(funcao=assinatura):
                self.assertIn('carregar();', corpo_da_funcao(principal, assinatura))
        carregar = corpo_da_funcao(principal, 'async function carregar')
        self.assertIn('async function carregar(abertura = false)', carregar)
        self.assertIn('(r) => destinoDaCarga(r) !== "aviso"', carregar)
        self.assertIn('TEMPO_MINIMO_LOGO_MS', carregar)
        self.assertIn('() => performance.now(), esperar, 0)', carregar)
        self.assertIn('if (abertura) esconderTelaLogo();', carregar)
        # Fora do Telegram: esconde a logo e mostra o aviso, sem esperar.
        sem_telegram = iniciar[iniciar.index('if (!initData)'):]
        sem_telegram = sem_telegram[:sem_telegram.index('return;')]
        self.assertIn('esconderTelaLogo();', sem_telegram)
        self.assertIn('mostrarAviso(MENSAGEM_FORA_DO_TELEGRAM, false);', sem_telegram)
        self.assertNotIn('comTempoMinimo', sem_telegram)

    def test_cadastro_mostra_a_logo_com_o_post_na_hora(self):
        enviar = corpo_da_funcao(codigo_principal(HOME), 'async function enviarCadastro').replace('\r\n', '\n')
        self.assertIn('mostrarTelaLogo("Criar Cadastro", "Criando o Seu Cadastro...", '
                      '"Guardando o seu nome, o dia e as categorias.");', enviar)
        self.assertIn('gravarComTentativas((c) => enviarJson("/cadastro", c, 15000), corpo, esperar),\n', enviar)
        self.assertIn('TEMPO_MINIMO_LOGO_MS, (r) => decisaoDoCadastro(r).seguir, () => performance.now(), esperar', enviar)
        # A logo entra depois de travar o botão e antes do POST; sai no seguir (junto do tour) e no erro (na hora).
        self.assertLess(enviar.index('e.enviando = true;'), enviar.index('mostrarTelaLogo('))
        self.assertLess(enviar.index('mostrarTelaLogo('), enviar.index('gravarComTentativas('))
        # 'enviando' só volta a falso depois da espera.
        self.assertLess(enviar.index('comTempoMinimo('), enviar.index('e.enviando = false;'))
        self.assertEqual(enviar.count('esconderTelaLogo();'), 2)
        self.assertLess(enviar.index('abrirTour(e.nome);'), enviar.index('esconderTelaLogo();'))

    def test_seta_e_backbutton_nao_fazem_nada_com_a_logo_na_tela(self):
        voltar = corpo_da_funcao(codigo_principal(HOME), 'function voltarUmNivel')
        primeira = voltar.split('\n')[1].strip()
        self.assertEqual(primeira, 'if (!$("tela-logo").hidden) return; // a tela da logo está por cima: nada embaixo recebe comando (PRO-100)')

    def test_textos_da_logo_so_por_textcontent_e_sem_innerhtml_novo(self):
        principal = codigo_principal(HOME)
        mostrar = corpo_da_funcao(principal, 'function mostrarTelaLogo')
        for trecho in ('$("tela-logo-cabecalho").textContent = cabecalho;', '$("tela-logo-titulo").textContent = titulo;',
                       '$("tela-logo-texto").textContent = texto;', '$("tela-logo").hidden = false;'):
            self.assertIn(trecho, mostrar)
        self.assertIn('$("tela-logo").hidden = true;', corpo_da_funcao(principal, 'function esconderTelaLogo'))
        for padrao in (r'\.innerHTML\s*[+]?=', r'\.outerHTML\s*[+]?=', r'insertAdjacentHTML', r'document\.write'):
            self.assertIsNone(re.search(padrao, HOME), padrao)

    def test_textos_da_d5(self):
        for texto in ('Abrindo o Seu Painel...', 'Só um instante.', 'Criar Cadastro', 'Criando o Seu Cadastro...',
                      'Guardando o seu nome, o dia e as categorias.'):
            with self.subTest(texto=texto):
                self.assertIn(texto, HOME)


# ----------------------------------------------------------------------------
# 5. Gastos recorrentes no Mini App (PRO-101)
# ----------------------------------------------------------------------------

def centavos_validos_no_servidor(centavos):
    """O que o servidor (registro.validar_valor) aceita para este valor em centavos."""
    return registro.validar_valor(centavos / 100) is not None


class TestEstruturaDosRecorrentes(unittest.TestCase):
    TELAS = ('recorrentes-guia', 'recorrentes-escolha', 'recorrentes-lista', 'recorrente-form')
    FOLHAS = ('folha-dia', 'folha-categoria', 'folha-apagar')

    def test_blocos_main_e_folhas(self):
        for identificador in self.TELAS:
            with self.subTest(main=identificador):
                self.assertRegex(HOME, r'<main id="%s" class="tela-cheia" hidden>' % identificador)
        # As folhas de baixo ficam fora dos <main> (depois do último), escondidas, como o aviso do topo.
        ultimo_main = HOME.rindex('</main>')
        for identificador in self.FOLHAS:
            with self.subTest(folha=identificador):
                self.assertRegex(HOME, r'<div class="folha-camada" id="%s" role="dialog" aria-modal="true" aria-labelledby="[^"]+" hidden>' % identificador)
                self.assertGreater(HOME.index('id="%s"' % identificador), ultimo_main)

    def test_ids_unicos(self):
        ids = ('atalho-recorrentes', 'guia-voltar', 'guia-comecar', 'escolha-rec-voltar', 'escolha-rec-titulo',
               'escolha-rec-gasto', 'escolha-rec-receita', 'lista-rec-voltar', 'rec-carregando', 'rec-erro',
               'rec-erro-texto', 'rec-erro-tentar', 'rec-conteudo', 'rec-total', 'rec-contagem', 'rec-itens',
               'rec-vazio', 'rec-rodape', 'rec-adicionar', 'rf-cabecalho', 'rf-voltar', 'rf-titulo', 'rf-corpo',
               'rf-intro', 'rf-dia', 'rf-dia-texto', 'rf-dia-erro', 'rf-valor', 'rf-valor-caixa', 'rf-valor-erro',
               'rf-descricao', 'rf-descricao-erro', 'rf-estabelecimento', 'rf-estabelecimento-erro', 'rf-categoria',
               'rf-categoria-texto', 'rf-categoria-erro', 'rf-erro', 'rf-salvar', 'rf-dica', 'rf-apagar',
               'folha-dia-grade', 'folha-dia-texto', 'folha-dia-confirmar', 'folha-categoria-lista',
               'folha-categoria-estado', 'folha-categoria-tentar', 'folha-apagar-texto', 'folha-apagar-erro',
               'folha-apagar-confirmar', 'folha-apagar-cancelar')
        for identificador in ids:
            with self.subTest(id=identificador):
                self.assertEqual(HOME.count('id="%s"' % identificador), 1)

    def test_textos_dos_quadros(self):
        for texto in ('Como Funciona?', 'Use Essa Aba Para Assinaturas e Pagamentos Recorrentes no Cartão.', 'Exemplo:',
                      'Abre um calendário para selecionar o dia', 'Abre uma seleção das categorias existentes',
                      'Começar', 'Assinaturas do cartão', 'Salário e entradas', 'Em Breve',
                      'Total por Mês', 'Adicionar Recorrente', 'Seus recorrentes aparecem aqui',
                      'Comece pelas assinaturas do cartão, como <strong>Streaming</strong> e <strong>Academia</strong>',
                      'Dia da Cobrança', 'Valor da Cobrança', 'Descrição', 'Estabelecimento', '(Opcional)', 'Categoria',
                      'Escolha o dia', 'Ex.: Assinatura Spotify', 'Ex.: Spotify', 'Escolha a categoria',
                      'Registre Seu Gasto e Espere Chegar a Data, na Data Exata o L1d3r Registra.',
                      'Veja na Sua Fatura do Cartão o Dia Exato em Que Costuma Cair Essa Assinatura Para um Melhor Controle.',
                      'As mudanças valem a partir da próxima cobrança. O que já entrou nos meses anteriores não muda.',
                      'Salvar Alterações', 'Apagar Recorrente', 'Escolha o dia do mês em que essa cobrança cai.',
                      'Nos meses mais curtos, usamos o último dia', 'Escolha uma das suas categorias.',
                      'Apagar Este Recorrente?', 'Sim, Apagar', 'Cancelar', 'Salvando...', 'Apagando...'):
            with self.subTest(texto=texto):
                self.assertIn(texto, HOME)

    def test_campos_do_formulario(self):
        self.assertRegex(HOME, r'<input id="rf-valor" type="text" inputmode="numeric"')
        self.assertRegex(HOME, r'<input class="campo-form" id="rf-descricao" type="text" maxlength="30"')
        self.assertRegex(HOME, r'<input class="campo-form" id="rf-estabelecimento" type="text" maxlength="60"')
        # Dia e categoria abrem folhas: botões, não campos digitáveis.
        self.assertRegex(HOME, r'<button class="campo-form" id="rf-dia" type="button"')
        self.assertRegex(HOME, r'<button class="campo-form" id="rf-categoria" type="button"')
        # O Salvar começa desabilitado e o Apagar começa escondido (só aparece no Editar).
        self.assertRegex(HOME, r'id="rf-salvar" type="button" disabled>Salvar<')
        self.assertRegex(HOME, r'id="rf-apagar" type="button" hidden>')

    def test_guia_sem_campos_tocaveis(self):
        guia = bloco_main('recorrentes-guia')
        exemplo = guia[guia.index('class="guia-exemplo"'):guia.index('class="rodape-tela"')]
        self.assertIn('aria-hidden="true"', exemplo[:60])
        for proibido in ('<input', '<button', '<textarea', '<select', 'tabindex'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, exemplo)
        self.assertIn('pointer-events: none', HOME[HOME.index('.guia-exemplo .campo-form'):][:120])
        for valor in ('>12<', '>117,80<', '>Assinatura Spotify<', '>Spotify<', '>Lazer<'):
            self.assertIn(valor, exemplo)

    def test_receita_so_avisa_em_breve_e_gasto_abre_o_formulario(self):
        principal = codigo_principal(HOME)
        self.assertIn('$("escolha-rec-receita").addEventListener("click", avisarEmBreve);', principal)
        self.assertIn('$("escolha-rec-gasto").addEventListener("click", abrirFormNovo);', principal)
        escolha = bloco_main('recorrentes-escolha')
        self.assertIn('<span class="selo-opcao">Em Breve</span>', escolha)
        self.assertIn('id="escolha-rec-titulo"', escolha)
        self.assertIn('$("escolha-rec-titulo").textContent = tituloDaEscolha(nomeDoUsuario);',
                      corpo_da_funcao(principal, 'function abrirEscolha(origem)'))
        # O nome vem do /me, guardado no cabeçalho da home.
        self.assertIn('nomeDoUsuario = me.nome || "";', corpo_da_funcao(principal, 'function mostrarCabecalho'))

    def test_lista_usa_o_total_do_servidor_e_so_textcontent(self):
        principal = codigo_principal(HOME)
        desenhar = corpo_da_funcao(principal, 'function desenharLista')
        self.assertIn('dados.total_mensal', desenhar)
        self.assertIn('formatarDinheiro(', desenhar)
        # Nenhum cálculo no front: nada de somar valores.
        for proibido in ('reduce(', '+=', '.valor +', 'sum('):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, desenhar)
        linha = corpo_da_funcao(principal, 'function criarLinhaRecorrente')
        for trecho in ('criar("span", "rec-linha-nome", r.descricao)', 'subtituloRecorrente(r)',
                       'formatarDinheiro(r.valor)', 'textoDiaCobranca(r.dia_cobranca)'):
            self.assertIn(trecho, linha)
        # Categorias e fichas também só por textContent (criar).
        self.assertIn('criar("span", "", nome)', corpo_da_funcao(principal, 'function criarLinhaCategoria'))
        self.assertIn('criar("strong", "", texto.negrito)', corpo_da_funcao(principal, 'function abrirFolhaApagar'))
        for padrao in (r'\.innerHTML\s*[+]?=', r'\.outerHTML\s*[+]?=', r'insertAdjacentHTML', r'document\.write'):
            self.assertIsNone(re.search(padrao, HOME), padrao)

    def test_lista_tem_carregando_erros_e_tentar_de_novo(self):
        principal = codigo_principal(HOME)
        self.assertIn('Carregando...', bloco_main('recorrentes-lista'))
        self.assertIn('mensagemDeErro([resultado]).texto', corpo_da_funcao(principal, 'function aplicarResultadoLista'))
        self.assertIn('$("rec-erro-tentar").addEventListener("click", () => carregarRecorrentes());', principal)
        # Carregar a lista não usa a tela da logo (só os salvamentos).
        carregar = corpo_da_funcao(principal, 'async function carregarRecorrentes')
        self.assertNotIn('mostrarTelaLogo', carregar)
        self.assertIn('chamar("/recorrentes")', carregar)

    def test_categorias_buscadas_ao_abrir_o_formulario(self):
        principal = codigo_principal(HOME)
        self.assertIn('carregarCategoriasRec();', corpo_da_funcao(principal, 'function abrirForm()'))
        self.assertIn('chamar("/categorias")', corpo_da_funcao(principal, 'async function carregarCategoriasRec'))
        self.assertIn('categoriaExistente(formRec.categoria, categoriasRec.lista)',
                      corpo_da_funcao(principal, 'async function carregarCategoriasRec'))

    def test_chave_nasce_so_no_formulario_novo_e_se_repete(self):
        principal = codigo_principal(HOME)
        self.assertIn('formRec.chave = gerarChave();', corpo_da_funcao(principal, 'function abrirFormNovo'))
        self.assertNotIn('gerarChave', corpo_da_funcao(principal, 'async function salvarRecorrente'))
        self.assertNotIn('gerarChave', corpo_da_funcao(principal, 'function abrirFormEditar'))
        # O editar não leva chave; o novo leva a do formulário, a mesma em todas as tentativas.
        salvar = corpo_da_funcao(principal, 'async function salvarRecorrente')
        self.assertIn('montarCorpoRecorrente(f, novo ? f.chave : undefined)', salvar)
        self.assertIn('gravarComTentativas((c) => enviarComMetodo(metodo, rota, c, 15000), corpo, esperar)', salvar)
        self.assertEqual(salvar.count('montarCorpoRecorrente('), 1)  # o corpo é montado uma vez só, antes das tentativas
        self.assertIn('f.chave = null;', corpo_da_funcao(principal, 'function zerarForm'))

    def test_salvar_usa_a_tela_da_logo_com_tempo_minimo(self):
        salvar = corpo_da_funcao(codigo_principal(HOME), 'async function salvarRecorrente').replace('\r\n', '\n')
        self.assertIn('TEMPO_MINIMO_LOGO_MS, (x) => decisaoDaGravacaoRecorrente(operacao, x.r).acao === "sucesso", () => performance.now(), esperar', salvar)
        self.assertIn('mostrarTelaLogo(logo.cabecalho, logo.titulo, logo.texto);', salvar)
        self.assertIn('textosTelaLogoRecorrente(operacao, f.dia)', salvar)
        # Trava no primeiro toque (antes de qualquer chamada), logo antes do POST/PUT, sai no erro na hora.
        self.assertLess(salvar.index('f.salvando = true;'), salvar.index('mostrarTelaLogo('))
        self.assertLess(salvar.index('mostrarTelaLogo('), salvar.index('gravarComTentativas('))
        self.assertLess(salvar.index('comTempoMinimo('), salvar.index('f.salvando = false;'))
        self.assertIn('if (!podeSalvarRecorrente(f)) return;', salvar)
        self.assertEqual(salvar.count('esconderTelaLogo();'), 2)
        # Sucesso: a lista e o aviso entram na mesma tarefa em que a logo sai.
        sucesso = salvar[salvar.index('if (decisao.acao === "sucesso")'):]
        self.assertLess(sucesso.index('mostrarTelaRecorrentes("recorrentes-lista");'), sucesso.index('esconderTelaLogo();'))
        self.assertIn('mostrarAvisoTopo(avisoDeRecorrente(operacao, resultado.r.dados));', sucesso)
        # PUT no editar e POST no criar.
        self.assertIn('const metodo = novo ? "POST" : "PUT";', salvar)
        self.assertIn('"/recorrentes/" + encodeURIComponent(f.id)', salvar)

    def test_apagar_nao_usa_a_logo_e_trata_o_404_como_sucesso(self):
        principal = codigo_principal(HOME)
        apagar = corpo_da_funcao(principal, 'async function apagarRecorrente')
        for proibido in ('comTempoMinimo', 'mostrarTelaLogo', 'esconderTelaLogo'):
            self.assertNotIn(proibido, apagar)
        self.assertIn('"Apagando..."', apagar)
        self.assertIn('gravarComTentativas(() => enviarComMetodo("DELETE", rota, undefined, 15000), null, esperar)', apagar)
        self.assertIn('decisaoDaGravacaoRecorrente("apagar", resultado)', apagar)
        self.assertIn('mostrarAvisoTopo(avisoDeRecorrente("apagar", resultado.dados));', apagar)
        # O primeiro toque desabilita o botão.
        self.assertLess(apagar.index('$("folha-apagar-confirmar").disabled = true;'), apagar.index('gravarComTentativas('))
        self.assertIn('if (apagarRec.apagando) return;', apagar)
        # A folha pede a confirmação antes: "Apagar Recorrente" só abre a folha.
        self.assertIn('$("rf-apagar").addEventListener("click", abrirFolhaApagar);', principal)
        self.assertNotIn('DELETE', corpo_da_funcao(principal, 'function abrirFolhaApagar'))
        self.assertEqual(principal.count('"DELETE"'), 1)

    def test_comTempoMinimo_so_nos_tres_lugares(self):
        principal = codigo_principal(HOME)
        for assinatura in ('async function carregar(', 'async function enviarCadastro', 'async function salvarRecorrente'):
            with self.subTest(funcao=assinatura):
                self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, assinatura))
        # Chamadas à tela da logo (fora a definição): o cadastro e o salvar recorrente. A abertura já vem visível no HTML.
        sem_comentarios = re.sub(r'//[^\n]*', '', principal)
        chamadas = [m for m in re.finditer(r'\bmostrarTelaLogo\(', sem_comentarios)
                    if not sem_comentarios[:m.start()].endswith('function ')]
        self.assertEqual(len(chamadas), 2)

    def test_voltar_sobe_um_nivel_e_a_logo_bloqueia(self):
        principal = codigo_principal(HOME)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        self.assertLess(voltar.index('if (!$("tela-logo").hidden) return;'), voltar.index('folhaAberta()'))
        self.assertLess(voltar.index('folhaAberta()'), voltar.index('telaRecorrentesAtual()'))
        for trecho in ('fecharFolhaAtual();', 'voltarNasRecorrentes(telaRecorrentes);'):
            self.assertIn(trecho, voltar)
        nas_recorrentes = corpo_da_funcao(principal, 'function voltarNasRecorrentes')
        self.assertIn('telaAoVoltarRecorrentes(atual, origemEscolha)', nas_recorrentes)
        self.assertIn('if (formRec.salvando || apagarRec.apagando) return;', nas_recorrentes)
        for identificador in ('guia-voltar', 'escolha-rec-voltar', 'lista-rec-voltar', 'rf-voltar'):
            self.assertIn('"%s"' % identificador, corpo_da_funcao(principal, 'function iniciarRecorrentes'))
        # A seta de voltar vai para a home no fim: BackButton escondido.
        self.assertIn('mostrarBackButton(false);', corpo_da_funcao(principal, 'function voltarRecorrentesParaHome'))
        self.assertIn('mostrarBackButton(true);', corpo_da_funcao(principal, 'function mostrarTelaRecorrentes'))

    def test_guia_visto_no_cloudstorage_com_reserva_no_localstorage_sempre_em_try(self):
        principal = codigo_principal(HOME)
        self.assertIn('const CHAVE_GUIA_RECORRENTES = "guia_recorrentes_visto";', principal)
        for assinatura, marca in (('function lerGuiaLocal', 'localStorage'), ('function lerGuiaNuvem', 'tg.CloudStorage'),
                                  ('function marcarGuiaVisto', 'localStorage'), ('function marcarGuiaVisto', 'tg.CloudStorage')):
            with self.subTest(funcao=assinatura, marca=marca):
                corpo = corpo_da_funcao(principal, assinatura)
                self.assertIn(marca, corpo)
                self.assertIn('try {', corpo)
                self.assertIn('catch (e)', corpo)
        # localStorage e CloudStorage só aparecem dentro dessas três funções (nenhum acesso solto).
        def sem_comentarios(texto):
            return re.sub(r'//[^\n]*', '', texto)
        for marca in ('localStorage', 'CloudStorage'):
            dentro = sum(sem_comentarios(corpo_da_funcao(principal, f)).count(marca)
                         for f in ('function lerGuiaLocal', 'function lerGuiaNuvem', 'function marcarGuiaVisto'))
            self.assertEqual(sem_comentarios(principal).count(marca), dentro, marca)
        self.assertNotIn('setInterval', HOME)

    def test_css_das_folhas_fica_entre_o_aviso_e_a_logo(self):
        regra = re.search(r'(?m)^\s*\.folha-camada\s*\{([^}]*)\}', HOME).group(1)
        z_folha = int(re.search(r'z-index:\s*(\d+)', regra).group(1))
        z_aviso = int(re.search(r'z-index:\s*(\d+)', re.search(r'(?m)^\s*\.aviso-topo\s*\{([^}]*)\}', HOME).group(1)).group(1))
        z_logo = int(re.search(r'z-index:\s*(\d+)', re.search(r'(?m)^\s*\.tela-logo\s*\{([^}]*)\}', HOME).group(1)).group(1))
        self.assertGreater(z_folha, z_aviso)
        self.assertLess(z_folha, z_logo)
        self.assertIn('#12121266', HOME)  # o fundo escuro do Paper

    def test_novas_cores_vem_de_variaveis_ou_do_paper(self):
        # Só as cores do Paper (#12121266 e #FAFAFA) aparecem soltas nas regras novas.
        inicio = HOME.index('/* ---------- Gastos recorrentes (PRO-101')
        css = HOME[inicio:HOME.index('</style>')]
        self.assertEqual(sorted(set(re.findall(r'#[0-9A-Fa-f]{3,8}\b', css))), ['#12121266', '#FAFAFA'])


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestRecorrentesFuncoesPuras(unittest.TestCase):
    def test_mascara_de_centavos_digito_a_digito(self):
        obtido = rodar_node(com_funcoes_puras("""
            const texto_apos = (digitos) => textoDoValor(digitos);
            // Digita 1, 1, 7, 8, 0: cada dígito entra pela direita do texto que o campo mostra.
            let digitos = '';
            const digitando = [];
            for (const d of '11780') {
              digitos = digitosDoValor(texto_apos(digitos) + d);
              digitando.push([digitos, texto_apos(digitos)]);
            }
            // Apaga um por um (o campo tira o último caractere do texto) até esvaziar.
            const apagando = [];
            for (let i = 0; i < 6; i++) {
              const texto = texto_apos(digitos);
              digitos = digitosDoValor(texto.slice(0, -1));
              apagando.push([digitos, texto_apos(digitos)]);
            }
            // Digitar 0 no campo vazio, e mais um 0 em cima.
            const zero = digitosDoValor('0');
            const doisZeros = digitosDoValor(textoDoValor(zero) + '0');
            console.log(JSON.stringify({
              digitando, apagando, zero, textoZero: textoDoValor(zero), doisZeros,
              casos: ['', '1', '0,01', '0,015', '117,800', '0005', '00', '000', 'a1b2', '99999999999', '999999999999', '1.234,56'].map((t) => digitosDoValor(t)),
            }));
        """))
        self.assertEqual(obtido['digitando'], [['1', '0,01'], ['11', '0,11'], ['117', '1,17'], ['1178', '11,78'], ['11780', '117,80']])
        self.assertEqual(obtido['apagando'], [['1178', '11,78'], ['117', '1,17'], ['11', '0,11'], ['1', '0,01'], ['', ''], ['', '']])
        self.assertEqual(obtido['zero'], '0')
        self.assertEqual(obtido['textoZero'], '0,00')
        self.assertEqual(obtido['doisZeros'], '0')
        self.assertEqual(obtido['casos'], ['', '1', '1', '15', '117800', '5', '', '0', '12', '99999999999', '99999999999', '123456'])

    def test_valor_enviado_e_numero_com_duas_casas(self):
        obtido = rodar_node(com_funcoes_puras("""
            const exemplos = { um: valorDosDigitos('1'), cento: valorDosDigitos('11780'), vazio: valorDosDigitos(''),
                               texto_cento: textoDoValor('11780'), milhar: textoDoValor('123456'), teto: textoDoValor('100000000') };
            // Todos os valores de 0,00 a 20.000,00: o número tem no máximo 2 casas e volta aos mesmos dígitos.
            let ruins = 0;
            for (let c = 0; c <= 2000000; c++) {
              const v = valorDosDigitos(String(c));
              const casas = (JSON.stringify(v).split('.')[1] || '').length;
              if (casas > 2 || Math.round(v * 100) !== c || digitosDoValorGravado(v) !== String(c)) ruins++;
            }
            console.log(JSON.stringify({ exemplos, ruins, gravado: [digitosDoValorGravado(117.8), digitosDoValorGravado(0.01), digitosDoValorGravado(1000000)] }));
        """))
        self.assertEqual(obtido['exemplos'], {'um': 0.01, 'cento': 117.8, 'vazio': None, 'texto_cento': '117,80',
                                              'milhar': '1.234,56', 'teto': '1.000.000,00'})
        self.assertEqual(obtido['ruins'], 0)
        self.assertEqual(obtido['gravado'], ['11780', '1', '100000000'])

    def test_erro_do_valor_igual_ao_do_servidor(self):
        centavos = [0, 1, 2, 99, 100, 117_80, 99_999_999, 100_000_000, 100_000_001, 100_000_100, 999_999_999, 9_999_999_999]
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify(ENTRADA.map((c) => erroDoValor(String(c)))));
        """), centavos)
        mensagem = 'Valor inválido. Digite um valor maior que zero e de no máximo R$ 1.000.000,00'
        for c, erro in zip(centavos, obtido):
            with self.subTest(centavos=c):
                # A tela só avisa o que o servidor recusaria (validar_valor), com a mesma mensagem do bot.
                self.assertEqual(erro is None, centavos_validos_no_servidor(c))
                if erro is not None:
                    self.assertEqual(erro, mensagem)
        vazio = rodar_node(com_funcoes_puras("console.log(JSON.stringify([erroDoValor(''), erroDaDescricao(''), erroDaDescricao('   '), erroDaDescricao(' a ')]));"))
        self.assertEqual(vazio, [None, 'Preencha a descrição para continuar', 'Preencha a descrição para continuar', None])

    def test_limites_iguais_aos_do_servidor(self):
        puro = codigo_puro(HOME)
        def constante(nome):
            return int(re.search(r'const %s = (\d+);' % nome, puro).group(1))
        self.assertEqual(constante('RECORRENTE_DESCRICAO_MAXIMA'), recorrentes.DESCRICAO_MAXIMA_RECORRENTE)
        self.assertEqual(constante('RECORRENTE_ESTABELECIMENTO_MAXIMO'), registro.ESTABELECIMENTO_MAXIMO)
        self.assertEqual(constante('RECORRENTE_VALOR_MAXIMO_CENTAVOS'), round(registro.VALOR_MAXIMO * 100))
        # Os maxlength do HTML são os mesmos limites.
        self.assertIn('id="rf-descricao" type="text" maxlength="%d"' % recorrentes.DESCRICAO_MAXIMA_RECORRENTE, HOME)
        self.assertIn('id="rf-estabelecimento" type="text" maxlength="%d"' % registro.ESTABELECIMENTO_MAXIMO, HOME)

    def test_dica_do_salvar(self):
        base = {'dia': 12, 'valorDigitos': '11780', 'descricao': 'Assinatura Spotify', 'categoria': 'Lazer'}
        casos = [
            ({'dia': None, 'valorDigitos': '', 'descricao': '', 'categoria': None}, 'Preencha o dia, o valor, a descrição e a categoria'),
            ({'dia': 12, 'valorDigitos': '0', 'descricao': '', 'categoria': 'Lazer'}, 'Corrija o valor e preencha a descrição'),
            ({'dia': 12, 'valorDigitos': '100000001', 'descricao': 'x', 'categoria': 'Lazer'}, 'Corrija o valor'),
            (base, ''),
            (dict(base, dia=None), 'Preencha o dia'),
            (dict(base, dia=None, categoria=None), 'Preencha o dia e a categoria'),
            (dict(base, valorDigitos='', descricao='   '), 'Preencha o valor e a descrição'),
            (dict(base, valorDigitos='', descricao='', categoria=None), 'Preencha o valor, a descrição e a categoria'),
            (dict(base, valorDigitos='0', dia=None, descricao='', categoria=None), 'Corrija o valor e preencha o dia, a descrição e a categoria'),
            (dict(base, categoria=''), 'Preencha a categoria'),
        ]
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.map((f) => dicaDoSalvar(f))));"), [c for c, _ in casos])
        for (entrada, esperado), recebido in zip(casos, obtido):
            with self.subTest(entrada=entrada):
                self.assertEqual(recebido, esperado)

    def test_salvar_so_com_os_quatro_obrigatorios_validos(self):
        base = {'dia': 12, 'valorDigitos': '11780', 'descricao': 'Assinatura Spotify', 'categoria': 'Lazer', 'salvando': False}
        casos = [
            (base, True),
            (dict(base, estabelecimento=''), True),                 # o estabelecimento é opcional
            (dict(base, salvando=True), False),                     # o primeiro toque trava
            (dict(base, dia=None), False), (dict(base, dia=0), False), (dict(base, dia=32), False),
            (dict(base, dia='12'), False), (dict(base, dia=12.5), False), (dict(base, dia=1), True), (dict(base, dia=31), True),
            (dict(base, valorDigitos=''), False), (dict(base, valorDigitos='0'), False),
            (dict(base, valorDigitos='100000000'), True), (dict(base, valorDigitos='100000001'), False),
            (dict(base, descricao=''), False), (dict(base, descricao='   '), False),
            (dict(base, categoria=None), False), (dict(base, categoria=''), False),
        ]
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.map((f) => podeSalvarRecorrente(f))));"), [c for c, _ in casos])
        for (entrada, esperado), recebido in zip(casos, obtido):
            with self.subTest(entrada=entrada):
                self.assertEqual(recebido, esperado)

    def test_corpo_do_pedido(self):
        obtido = rodar_node(com_funcoes_puras("""
            const f = { dia: 12, valorDigitos: '11780', descricao: '  Assinatura Spotify ', estabelecimento: ' Spotify ', categoria: 'Lazer' };
            const sem_estab = Object.assign({}, f, { estabelecimento: '   ' });
            console.log(JSON.stringify({
              put: montarCorpoRecorrente(f), post: montarCorpoRecorrente(f, 'abc'), sem: montarCorpoRecorrente(sem_estab),
              ordem_post: Object.keys(montarCorpoRecorrente(f, 'abc')), json: JSON.stringify(montarCorpoRecorrente(f)),
            }));
        """))
        corpo = {'dia_cobranca': 12, 'valor': 117.8, 'descricao': 'Assinatura Spotify', 'estabelecimento': 'Spotify', 'categoria': 'Lazer'}
        self.assertEqual(obtido['put'], corpo)
        self.assertEqual(obtido['post'], dict(corpo, chave='abc'))
        self.assertIsNone(obtido['sem']['estabelecimento'])
        self.assertEqual(obtido['ordem_post'][0], 'chave')
        self.assertIn('"valor":117.8,', obtido['json'])

    def test_textos_da_lista(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              contagem: [0, 1, 2, 4, 12].map(textoContagemRecorrentes),
              subtitulos: [
                subtituloRecorrente({ estabelecimento: 'Smart Fit', categoria: 'Academia' }),
                subtituloRecorrente({ estabelecimento: null, categoria: 'Lazer' }),
                subtituloRecorrente({ estabelecimento: '   ', categoria: 'Casa' }),
                subtituloRecorrente({ categoria: 'Casa' }),
              ],
              dia: [1, 5, 28].map(textoDiaCobranca),
              escolha: [tituloDaEscolha('Ygor Nobrega'), tituloDaEscolha(''), tituloDaEscolha(null), tituloDaEscolha('  Ana  ')],
              apagar: textoConfirmarApagar({ descricao: 'Assinatura Spotify', valor: 117.8, dia_cobranca: 12 }),
              confirmarDia: [textoConfirmarDia(null), textoConfirmarDia(12), textoConfirmarDia(1)],
              existente: [categoriaExistente('Lazer', ['Casa', 'Lazer']), categoriaExistente('Lazer', ['Casa']),
                          categoriaExistente(null, ['Lazer']), categoriaExistente('Lazer', undefined)],
            }));
        """))
        self.assertEqual(obtido['contagem'], ['Nenhum gasto recorrente ainda', '1 gasto, lançado sozinho todo mês',
                                              '2 gastos, lançados sozinhos todo mês', '4 gastos, lançados sozinhos todo mês',
                                              '12 gastos, lançados sozinhos todo mês'])
        self.assertEqual(obtido['subtitulos'], ['Smart Fit · Academia', 'Lazer', 'Casa', 'Casa'])
        self.assertEqual(obtido['dia'], ['Todo dia 1', 'Todo dia 5', 'Todo dia 28'])
        self.assertEqual(obtido['escolha'], ['Olá, Ygor! O Que Deseja Adicionar Como Recorrente?',
                                             'O Que Deseja Adicionar Como Recorrente?', 'O Que Deseja Adicionar Como Recorrente?',
                                             'Olá, Ana! O Que Deseja Adicionar Como Recorrente?'])
        self.assertEqual(obtido['apagar']['negrito'], 'Assinatura Spotify · R$ 117,80')
        self.assertEqual(obtido['apagar']['depois'], ' deixa de entrar todo dia 12. Os gastos que já entraram nos meses '
                                                     'anteriores continuam no histórico.')
        self.assertEqual(obtido['confirmarDia'], ['Escolha um Dia', 'Confirmar Dia 12', 'Confirmar Dia 1'])
        self.assertEqual(obtido['existente'], ['Lazer', None, None, None])

    def test_navegacao(self):
        obtido = rodar_node(com_funcoes_puras("""
            const voltar = {};
            for (const tela of ['guia', 'escolha', 'lista', 'form-novo', 'form-editar', 'outra']) {
              voltar[tela] = [telaAoVoltarRecorrentes(tela, 'home'), telaAoVoltarRecorrentes(tela, 'lista')];
            }
            console.log(JSON.stringify({
              atalho: [proximaTelaDoAtalho(false), proximaTelaDoAtalho(true), proximaTelaDoAtalho(undefined)],
              guia: [guiaJaVisto('1', null), guiaJaVisto(null, '1'), guiaJaVisto('1', '1'), guiaJaVisto(null, null),
                     guiaJaVisto('', ''), guiaJaVisto(undefined, undefined), guiaJaVisto('0', '0'), guiaJaVisto('true', 'sim')],
              voltar,
            }));
        """))
        self.assertEqual(obtido['atalho'], ['guia', 'lista', 'guia'])
        self.assertEqual(obtido['guia'], [True, True, True, False, False, False, False, False])
        self.assertEqual(obtido['voltar'], {
            'guia': ['home', 'home'],
            'escolha': ['home', 'lista'],        # a escolha volta de onde veio
            'lista': ['home', 'home'],
            'form-novo': ['escolha', 'escolha'],  # descarta o que foi digitado
            'form-editar': ['lista', 'lista'],
            'outra': [None, None],
        })

    def test_textos_da_logo_e_dos_avisos(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              criar: textosTelaLogoRecorrente('criar', 12), editar: textosTelaLogoRecorrente('editar', 12),
              avisoCriar: avisoDeRecorrente('criar', { categoria: 'Lazer', valor: 1 }),
              avisoEditar: avisoDeRecorrente('editar', {}), avisoApagar: avisoDeRecorrente('apagar', undefined),
              avisoSemCategoria: avisoDeRecorrente('criar', {}),
            }));
        """))
        self.assertEqual(obtido['criar'], {'cabecalho': 'Novo Recorrente', 'titulo': 'Salvando o Recorrente...',
                                           'texto': 'Ele entra sozinho todo dia 12.'})
        self.assertEqual(obtido['editar'], {'cabecalho': 'Editar Recorrente', 'titulo': 'Salvando as Alterações...',
                                            'texto': 'Elas valem a partir da próxima cobrança.'})
        self.assertEqual(obtido['avisoCriar']['texto'], 'Cobrança Recorrente Adicionada a Lazer')
        self.assertEqual((obtido['avisoCriar']['antes'], obtido['avisoCriar']['negrito'], obtido['avisoCriar']['depois']),
                         ('Cobrança Recorrente Adicionada a ', 'Lazer', ''))
        self.assertEqual(obtido['avisoEditar']['texto'], 'Cobrança Recorrente Atualizada')
        self.assertEqual(obtido['avisoApagar']['texto'], 'Cobrança Recorrente Apagada')
        self.assertEqual(obtido['avisoSemCategoria']['texto'], 'Cobrança Recorrente Adicionada a ')

    def test_decisao_por_status_das_tres_gravacoes(self):
        sessao = 'Sua sessão expirou. Feche e abra o app de novo.'
        salvar = 'Não foi possível salvar. Tente de novo.'
        apagar = 'Não foi possível apagar. Tente de novo.'
        casos = [
            # (operação, resultado, esperado)
            ('criar', {'dados': {}, 'status': 201}, {'acao': 'sucesso'}),
            ('criar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),    # repetido: já estava gravado
            ('editar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),
            ('apagar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),
            ('apagar', {'erro': 404, 'detalhe': 'recorrente_nao_encontrado'}, {'acao': 'sucesso'}),  # já apagado
            ('editar', {'erro': 404, 'detalhe': 'recorrente_nao_encontrado'},
             {'acao': 'erro', 'mensagem': 'Este recorrente não existe mais.', 'voltarLista': True}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_repetida'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'Já existe uma cobrança com esse nome nesta categoria.'}),
            ('editar', {'erro': 422, 'detalhe': 'descricao_repetida'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'Já existe uma cobrança com esse nome nesta categoria.'}),
            ('criar', {'erro': 422, 'detalhe': 'categoria_nao_encontrada'},
             {'acao': 'erro', 'campo': 'categoria', 'mensagem': 'Essa categoria não existe mais. Escolha outra.',
              'recarregarCategorias': True}),
            ('criar', {'erro': 422, 'detalhe': 'valor_invalido'},
             {'acao': 'erro', 'campo': 'valor', 'mensagem': 'Valor inválido. Digite um valor maior que zero e de no máximo R$ 1.000.000,00'}),
            ('criar', {'erro': 422, 'detalhe': 'dia_invalido'}, {'acao': 'erro', 'campo': 'dia', 'mensagem': 'Escolha o dia da cobrança.'}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_invalida'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'Preencha a descrição para continuar'}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_longa'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'A descrição pode ter no máximo 30 caracteres.'}),
            ('criar', {'erro': 422, 'detalhe': 'estabelecimento_longo'},
             {'acao': 'erro', 'campo': 'estabelecimento', 'mensagem': 'O estabelecimento pode ter no máximo 60 caracteres.'}),
            ('criar', {'erro': 422, 'detalhe': 'chave_invalida'}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 422, 'detalhe': 'qualquer_coisa'}, {'acao': 'erro', 'mensagem': salvar}),
            ('apagar', {'erro': 422, 'detalhe': 'descricao_repetida'}, {'acao': 'erro', 'mensagem': apagar}),
            ('criar', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
            ('editar', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
            ('apagar', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
            ('criar', {'erro': 404, 'detalhe': 'usuario_nao_cadastrado'},
             {'acao': 'erro', 'mensagem': 'Não encontramos o seu cadastro. Feche e abra o app de novo.'}),
            ('criar', {'erro': 409, 'detalhe': 'cadastro_incompleto'},
             {'acao': 'erro', 'mensagem': 'Seu cadastro está incompleto. Volte ao bot e informe o dia de fechamento da fatura.'}),
            ('criar', {'erro': 503, 'detalhe': 'banco_ocupado'}, {'acao': 'erro', 'mensagem': salvar}),
            ('editar', {'erro': 0}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 500, 'detalhe': 'erro_interno'}, {'acao': 'erro', 'mensagem': salvar}),
            ('apagar', {'erro': 503, 'detalhe': 'banco_ocupado'}, {'acao': 'erro', 'mensagem': apagar}),
            ('apagar', {'erro': 0}, {'acao': 'erro', 'mensagem': apagar}),
            ('apagar', {'erro': 500}, {'acao': 'erro', 'mensagem': apagar}),
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([op, r]) => decisaoDaGravacaoRecorrente(op, r))));"),
            [[op, r] for op, r, _ in casos])
        for (operacao, resultado, esperado), recebido in zip(casos, obtido):
            with self.subTest(operacao=operacao, resultado=resultado):
                self.assertEqual(recebido, esperado)
        # O texto cru do servidor nunca aparece: a mensagem não contém o código do erro.
        for recebido in obtido:
            self.assertNotIn('descricao_repetida', recebido.get('mensagem', ''))

    def test_codigos_422_tratados_sao_os_do_recorrentes_py(self):
        tratados = set(re.findall(r'^      (\w+): \{ campo:', codigo_puro(HOME), re.M))
        esperados = {'dia_invalido', 'valor_invalido', 'descricao_invalida', 'descricao_longa', 'descricao_repetida',
                     'estabelecimento_longo', 'categoria_nao_encontrada'}
        self.assertEqual(tratados, esperados)
        fonte = open(os.path.join(RAIZ, 'recorrentes.py'), encoding='utf-8').read() + open(
            os.path.join(RAIZ, 'api', 'main.py'), encoding='utf-8').read()
        for codigo in esperados:
            with self.subTest(codigo=codigo):
                self.assertIn("'%s'" % codigo, fonte)

    def test_novas_tentativas_do_salvar_repetem_corpo_e_chave(self):
        # O salvar usa o gravarComTentativas de sempre: 503 banco_ocupado e rede repetem o MESMO corpo (com a mesma chave).
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const f = { dia: 12, valorDigitos: '11780', descricao: 'Assinatura Spotify', estabelecimento: '', categoria: 'Lazer' };
              const corpo = montarCorpoRecorrente(f, 'chave-do-formulario');
              const enviados = [];
              const respostas = [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 0 }, { dados: { id: 1 }, status: 201 }];
              const resultado = await gravarComTentativas(async (c) => { enviados.push(JSON.stringify(c)); return respostas.shift(); }, corpo, async () => {});
              const rejeitado = [];
              await gravarComTentativas(async (c) => { rejeitado.push(1); return { erro: 422, detalhe: 'descricao_repetida' }; }, corpo, async () => {});
              console.log(JSON.stringify({ iguais: enviados.every((x) => x === enviados[0]), n: enviados.length,
                                           status: resultado.status, rejeitado: rejeitado.length }));
            })();
        """))
        self.assertEqual(obtido, {'iguais': True, 'n': 3, 'status': 201, 'rejeitado': 1})  # 422 não é repetido sozinho


if __name__ == '__main__':
    unittest.main(verbosity=2)
