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

Desde a PRO-103, a receita recorrente no Mini App: o guia em 2 passos (Próximo, voltar ao passo 1 e Começar),
a Receita ativa na escolha (sem o "Em Breve"), o formulário da receita como MODO do mesmo bloco (só dia, valor
e descrição, sem aviso de renda), a folha do dia com os textos da receita, os botões Gasto/Receita da lista
(com o total em verde e o vazio da receita), a gravação pelas rotas /receitas-recorrentes (chave só no Novo,
tela da logo no criar e no editar, nenhuma no apagar), o cartão do salário abrindo direto a Nova Receita e,
no node, as funções puras novas (dica e corpo sem categoria, textos, rota, aba e decisão por status da receita).

Desde a PRO-105, a tela Categorias no Mini App: o atalho ligado (nenhum atalho da home segue em "Em breve"), o ranking do
mês (título pela mesma MESES_COMPLETOS[indiceMes(ano_mes)] da home, sem new Date), as barras (largura mínima de 8px, a maior
em grafite, a função pura da proporção), a linha excluída sem toque, a animação FLIP só com transform e transition (e nada se
move com prefers-reduced-motion), as folhas Nova (sem descrição, sem "Em breve" visível, a Temporária só mostra o alerta
nativo), Editar e Excluir (bloqueado: só o Cancelar), a chave só ao abrir a folha Nova, a tela da logo com o tempo mínimo
no criar e no renomear e fora do excluir, o corpo do POST só com nome e chave, os códigos 422 e os limites comparados com o
categorias.py e a api/main.py, e a decisão por status de cada gravação no node.

Desde a PRO-108, as telas das assinaturas no Mini App. Três partes novas:
  - O <script> principal RODANDO de verdade num navegador de mentira (HARNESS_PAGINA: elementos por id, fetch, Telegram.WebApp,
    CloudStorage e localStorage falsos, relógio de mentira): abertura sem a chave (as mesmas 4 chamadas, nada muda), selo só do campo
    "selo", toque no selo só com selo_tocavel, folha Minha Assinatura nos 4 estados, cancelar e reativar só depois do showConfirm,
    409 reativacao_recusada levando à folha Plano Pro, tela Expirada uma vez por dia (já vista, não vista, armazenamento quebrado ou
    ausente, ?tela=assinatura e start_param sempre mostram), Mais Tarde e BackButton levando à home em modo leitura, bloqueios do modo
    leitura sem chamar a API, tratamento central do 403, cadastro pelas Assinaturas (teste, vagas esgotadas, erro, cadastros
    fechados, chave desligada), pagamento (paid/pending, cancelled, failed, 409 assinaturas_desligadas, 30 s sem chegar).
  - Estrutura e medidas dos quadros, a ordem de decisão da abertura, nenhum cálculo de data de plano no cliente, nenhum "Free",
    "Teste" ou "Admin" como selo, rotas e códigos comparados com a api/main.py e o assinaturas.py.
  - As funções puras novas no node (selo, destino da abertura, datas, dias, textos, decisão por status, espera do pagamento).
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
import categorias  # noqa: E402  (PRO-105: idem; os limites, os códigos e o título são comparados com o servidor)
import assinaturas  # noqa: E402  (PRO-108: idem, só a biblioteca padrão; as constantes dos textos são comparadas com o servidor)
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

    def test_categorias_ligado_nenhum_atalho_segue_em_breve(self):
        # PRO-105: era "Categorias ainda Em breve" (o seletor .atalho:not(#atalho-registrar):not(#atalho-recorrentes) ligava o
        # aviso só nele). Agora o "Categorias" abre a tela e o seletor saiu: nenhum atalho da home segue com o "Em breve".
        principal = codigo_principal(HOME)
        self.assertNotIn('document.querySelectorAll(".atalho', principal)
        self.assertRegex(HOME, r'<button class="atalho" id="atalho-categorias" type="button" aria-label="Categorias">')
        self.assertIn('$("atalho-categorias").addEventListener("click", abrirCategorias);', principal)
        self.assertRegex(HOME, r'<button class="atalho" id="atalho-recorrentes" type="button" aria-label="Recorrentes">')
        self.assertIn('$("atalho-recorrentes").addEventListener("click", abrirRecorrentes);', principal)
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
        # Três chamadas só: a prévia do Registrar (interpretar), o formulário Novo dos recorrentes (PRO-101) e, desde a
        # PRO-105, a abertura da folha Nova Categoria (eram 2; a terceira é a da folha, nunca o Salvar).
        self.assertEqual(principal.count('gerarChave('), 3)
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'async function interpretar'))
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'function abrirFormNovo'))
        self.assertIn('gerarChave()', corpo_da_funcao(principal, 'function abrirFolhaNova'))
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

    def test_limite_diario_ia_vem_antes_do_429_por_minuto(self):
        # PRO-106 (D5): o 429 limite_diario_ia tem mensagem própria, conferida antes da do 429 por minuto
        # (mesmo com Retry-After). O número vem do 4º argumento (limite_ia_dia do GET /assinatura, PRO-108);
        # sem ele, ou inválido, 17. Na gravação, o código não existe e nada muda.
        entradas = [
            [{'erro': 429, 'detalhe': 'limite_diario_ia', 'retryAfter': 3600}, 'interpretar', 'gasto', None],
            [{'erro': 429, 'detalhe': 'limite_diario_ia'}, 'interpretar', 'receita', None],
            [{'erro': 429, 'detalhe': 'limite_diario_ia', 'retryAfter': 30}, 'interpretar', 'gasto', 25],
            [{'erro': 429, 'detalhe': 'limite_diario_ia'}, 'interpretar', 'gasto', 0],
            [{'erro': 429, 'detalhe': 'limite_diario_ia'}, 'interpretar', 'gasto', '20'],
            [{'erro': 429, 'detalhe': 'limite_diario_ia'}, 'interpretar', 'gasto', 2.5],
            [{'erro': 429, 'detalhe': 'limite_excedido', 'retryAfter': 23}, 'interpretar', 'gasto', 25],
            [{'erro': 429, 'detalhe': 'limite_diario_ia'}, 'gravar', 'gasto', None],
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([r, m, t, l]) => mensagemErroRegistro(r, m, t, l === null ? undefined : l))));"
        ), entradas)
        dezessete = 'Você usou os 17 registros com IA de hoje. Amanhã o limite volta a zero.'
        self.assertEqual(obtido, [
            dezessete, dezessete,
            'Você usou os 25 registros com IA de hoje. Amanhã o limite volta a zero.',
            dezessete, dezessete, dezessete,
            'Muitas tentativas seguidas. Espere 23 segundos.',
            'Não consegui registrar agora. Toque em Confirmar para tentar de novo.',
        ])
        funcao = corpo_da_funcao(codigo_puro(HOME), 'function mensagemErroRegistro')
        self.assertLess(funcao.index('"limite_diario_ia"'), funcao.index('Muitas tentativas seguidas'))

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
                      'Cadastre o seu salário em <strong>Recorrentes</strong>, como Receita, e ele entra sozinho todo mês.',
                      '>Próximo<', '>Pular<', '>Começar<', '>Voltar<',
                      '"Ainda sem histórico"', 'O gráfico aparece com o seu primeiro gasto',
                      'Cadastre o seu salário como <strong>Receita Recorrente</strong>'):
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
        for trecho in ('voltarNoCadastro()', 'voltarDoAmigo()'):
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

    def test_cartao_do_salario_abre_direto_a_nova_receita_recorrente(self):
        # PRO-103 (D14): o cartão deixa de abrir o chat da Receita avulsa e abre o formulário Nova Receita recorrente.
        principal = codigo_principal(HOME)
        self.assertIn('$("cartao-salario").addEventListener("click", abrirSalarioRecorrente);', principal)
        self.assertNotIn('abrirReceitaDireto', HOME)
        self.assertNotIn('registroDireto', HOME)
        self.assertNotIn('voltarDoRegistroParaHome', HOME)
        abrir = corpo_da_funcao(principal, 'function abrirSalarioRecorrente')
        self.assertIn('abrirFormNovo("receita", "salario");', abrir)   # sem guia nem escolha
        for proibido in ('abrirEscolha', 'mostrarPassoGuia', 'abrirRecorrentes', 'abrirRegistro'):
            self.assertNotIn(proibido, abrir)
        # A posição da home é guardada, e o voltar do formulário aberto pelo cartão vai à home.
        self.assertIn('rolagemHome = window.scrollY;', abrir)
        self.assertIn('origemFormNovo = origem === "salario" ? "salario" : "escolha";',
                      corpo_da_funcao(principal, 'function abrirFormNovo'))
        # O chat vazio da Receita avulsa não muda.
        self.assertIn('Registre Aqui as Suas Receitas Avulsas', corpo_da_funcao(principal, 'function abrirRegistro'))
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
        # 9 = os 3 traços do "Buscando o Seu Cadastro..." + os 3 da tela da logo (PRO-100) + os 3 do "Carregando..." das
        # Categorias (PRO-105, a logo pequena do quadro CARREGANDO; eram 6).
        # PRO-108: +6 = o "Carregando..." da tela Assinaturas e a tela Confirmando o Pagamento (15 no total).
        self.assertEqual(HOME.count('pathLength="1"'), 15)
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
        self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, 'async function salvarCategoria'))   # PRO-105: criar e renomear
        for assinatura in ('async function interpretar', 'async function confirmar', 'function concluirRegistro',
                           'async function buscarCadastroDoAmigo', 'async function carregarDetalhe',
                           'function terminarTour', 'async function apagarRecorrente',
                           'async function carregarRecorrentes', 'async function carregarCategoriasRec'):
            with self.subTest(funcao=assinatura):
                corpo = corpo_da_funcao(principal, assinatura)
                self.assertNotIn('comTempoMinimo', corpo)
                self.assertNotIn('mostrarTelaLogo', corpo)
        # Fora dessas quatro funções (abertura, cadastro, salvar recorrente e, desde a PRO-105, salvar categoria; eram
        # três), ninguém chama o tempo mínimo.
        # PRO-108: o quinto é o "Testar Gratuitamente" (testarGratuitamente), com a tela da logo.
        chamadas = principal.count('comTempoMinimo(')
        self.assertEqual(chamadas, 5)

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
        # PRO-109: o 403 cadastro_fechado também tira a logo na hora (volta à escolha inicial): 3 saídas.
        self.assertEqual(enviar.count('esconderTelaLogo();'), 3)
        self.assertLess(enviar.index('abrirTour(e.nome);'), enviar.index('esconderTelaLogo();'))
        self.assertLess(enviar.index('if (decisao.fechado) {'), enviar.index('fecharCadastroNaEscolha();'))

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
        ids = ('atalho-recorrentes', 'guia-voltar', 'guia-comecar', 'guia-proximo', 'guia-passo-1', 'guia-passo-2',
               'rec-aba-gasto', 'rec-aba-receita', 'rec-vazio-receita', 'rf-rotulo-dia', 'rf-rotulo-valor',
               'rf-bloco-estabelecimento', 'rf-bloco-categoria', 'folha-dia-titulo', 'folha-dia-apoio',
               'escolha-rec-voltar', 'escolha-rec-titulo',
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
                      'Começar', 'Assinaturas do cartão', 'Salário e entradas',
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
        for valor in ('>12<', '>117,80<', '>Assinatura Spotify<', '>Spotify<', '>Lazer<',
                      '>5<', '>4.500,00<', '>Salário<'):   # os dois passos (PRO-103)
            self.assertIn(valor, exemplo)

    def test_escolha_com_a_receita_ativa_e_cada_cartao_abre_o_seu_formulario(self):
        principal = codigo_principal(HOME)
        # PRO-103: a Receita deixa de ser "Em Breve" (sem selo, sem o aviso nativo) e abre a Nova Receita.
        self.assertIn('$("escolha-rec-receita").addEventListener("click", () => abrirFormNovo("receita"));', principal)
        self.assertIn('$("escolha-rec-gasto").addEventListener("click", () => abrirFormNovo("gasto"));', principal)
        escolha = bloco_main('recorrentes-escolha')
        self.assertNotIn('Em Breve', escolha)
        self.assertNotIn('selo-opcao', HOME)
        self.assertNotIn('opcao apagada', HOME)
        self.assertNotIn('.opcao.apagada', HOME)
        self.assertIn('<button class="opcao" id="escolha-rec-receita" type="button">', escolha)
        self.assertEqual(escolha.count('class="opcao-seta"'), 2)   # os dois cartões têm a seta
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
                       'formatarDinheiro(r.valor)', 'textoDiaCobranca(diaDoRecorrente(r, tipo))'):
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
        self.assertIn('chamar(rotaRecorrentes(listaRecorrentes.aba))', carregar)

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
        self.assertIn('TEMPO_MINIMO_LOGO_MS, (x) => decisaoDaGravacaoRecorrente(operacao, x.r, tipo).acao === "sucesso", () => performance.now(), esperar', salvar)
        self.assertIn('mostrarTelaLogo(logo.cabecalho, logo.titulo, logo.texto);', salvar)
        self.assertIn('textosTelaLogoRecorrente(operacao, f.dia, tipo)', salvar)
        # Trava no primeiro toque (antes de qualquer chamada), logo antes do POST/PUT, sai no erro na hora.
        self.assertLess(salvar.index('f.salvando = true;'), salvar.index('mostrarTelaLogo('))
        self.assertLess(salvar.index('mostrarTelaLogo('), salvar.index('gravarComTentativas('))
        self.assertLess(salvar.index('comTempoMinimo('), salvar.index('f.salvando = false;'))
        self.assertIn('if (!podeSalvarRecorrente(f)) return;', salvar)
        self.assertEqual(salvar.count('esconderTelaLogo();'), 2)
        # Sucesso: a lista e o aviso entram na mesma tarefa em que a logo sai.
        sucesso = salvar[salvar.index('if (decisao.acao === "sucesso")'):]
        self.assertLess(sucesso.index('mostrarTelaRecorrentes("recorrentes-lista");'), sucesso.index('esconderTelaLogo();'))
        self.assertIn('mostrarAvisoTopo(avisoDeRecorrente(operacao, resultado.r.dados, tipo));', sucesso)
        # PUT no editar e POST no criar.
        self.assertIn('const metodo = novo ? "POST" : "PUT";', salvar)
        self.assertIn('rotaRecorrentes(tipo) + "/" + encodeURIComponent(f.id)', salvar)

    def test_apagar_nao_usa_a_logo_e_trata_o_404_como_sucesso(self):
        principal = codigo_principal(HOME)
        apagar = corpo_da_funcao(principal, 'async function apagarRecorrente')
        for proibido in ('comTempoMinimo', 'mostrarTelaLogo', 'esconderTelaLogo'):
            self.assertNotIn(proibido, apagar)
        self.assertIn('"Apagando..."', apagar)
        self.assertIn('gravarComTentativas(() => enviarComMetodo("DELETE", rota, undefined, 15000), null, esperar)', apagar)
        self.assertIn('decisaoDaGravacaoRecorrente("apagar", resultado, tipo)', apagar)
        self.assertIn('mostrarAvisoTopo(avisoDeRecorrente("apagar", resultado.dados, tipo));', apagar)
        # O primeiro toque desabilita o botão.
        self.assertLess(apagar.index('$("folha-apagar-confirmar").disabled = true;'), apagar.index('gravarComTentativas('))
        self.assertIn('if (apagarRec.apagando) return;', apagar)
        # A folha pede a confirmação antes: "Apagar Recorrente" só abre a folha.
        self.assertIn('$("rf-apagar").addEventListener("click", abrirFolhaApagar);', principal)
        self.assertNotIn('DELETE', corpo_da_funcao(principal, 'function abrirFolhaApagar'))
        # Dois DELETE: o do recorrente (aqui) e, desde a PRO-105, o da categoria (eram 1; o outro é o excluirCategoria).
        self.assertEqual(principal.count('"DELETE"'), 2)
        self.assertEqual(corpo_da_funcao(principal, 'async function excluirCategoria').count('"DELETE"'), 1)

    def test_comTempoMinimo_so_nos_quatro_lugares(self):
        # Eram três (abertura, cadastro e salvar recorrente); a PRO-105 acrescenta o salvar categoria (criar e renomear).
        principal = codigo_principal(HOME)
        for assinatura in ('async function carregar(', 'async function enviarCadastro', 'async function salvarRecorrente',
                           'async function salvarCategoria'):
            with self.subTest(funcao=assinatura):
                self.assertIn('comTempoMinimo(', corpo_da_funcao(principal, assinatura))
        # Chamadas à tela da logo (fora a definição): o cadastro, o salvar recorrente e (PRO-105) o salvar categoria.
        # A abertura já vem visível no HTML.
        sem_comentarios = re.sub(r'//[^\n]*', '', principal)
        chamadas = [m for m in re.finditer(r'\bmostrarTelaLogo\(', sem_comentarios)
                    if not sem_comentarios[:m.start()].endswith('function ')]
        self.assertEqual(len(chamadas), 4)   # PRO-108: mais o "Testar Gratuitamente"

    def test_voltar_sobe_um_nivel_e_a_logo_bloqueia(self):
        principal = codigo_principal(HOME)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        self.assertLess(voltar.index('if (!$("tela-logo").hidden) return;'), voltar.index('folhaAberta()'))
        self.assertLess(voltar.index('folhaAberta()'), voltar.index('telaRecorrentesAtual()'))
        for trecho in ('fecharFolhaAtual();', 'voltarNasRecorrentes(telaRecorrentes);'):
            self.assertIn(trecho, voltar)
        nas_recorrentes = corpo_da_funcao(principal, 'function voltarNasRecorrentes')
        self.assertIn('telaAoVoltarRecorrentes(atual, origemEscolha, origemFormNovo)', nas_recorrentes)
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
        # localStorage e CloudStorage só aparecem dentro dessas funções (nenhum acesso solto): as três do guia, desde a
        # PRO-108 as três do "dia em que a tela Expirada foi vista" (mesmo molde, chave própria) e, desde a PRO-112, as duas
        # da marca do atalho (só localStorage).
        def sem_comentarios(texto):
            return re.sub(r'//[^\n]*', '', texto)
        for marca in ('localStorage', 'CloudStorage'):
            dentro = sum(sem_comentarios(corpo_da_funcao(principal, f)).count(marca)
                         for f in ('function lerGuiaLocal', 'function lerGuiaNuvem', 'function marcarGuiaVisto',
                                   'function lerExpiradaLocal', 'function lerExpiradaNuvem', 'function marcarExpiradaVista',
                                   'function atalhoJaPerguntado', 'function gravarAtalhoPerguntado'))
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
        # Só a cor do fundo escuro do Paper (#12121266) aparece solta nas regras novas. O #FAFAFA do cartão "Em Breve"
        # saiu na PRO-103 (a Receita ficou ativa), e a sombra dos botões Gasto/Receita (#1212121A) é variável no :root.
        inicio = HOME.index('/* ---------- Gastos recorrentes (PRO-101')
        css = HOME[inicio:HOME.index('</style>')]
        self.assertEqual(sorted(set(re.findall(r'#[0-9A-Fa-f]{3,8}\b', css))), ['#12121266'])
        self.assertIn('--cor-sombra-abas: #1212121A;', HOME)
        self.assertIn('box-shadow: 0 4px 14px var(--cor-sombra-abas);', css)

    # ---------------------------- PRO-103: receita recorrente ----------------------------

    def test_guia_tem_dois_passos_com_proximo_voltar_e_comecar(self):
        guia = bloco_main('recorrentes-guia')
        for texto in ('PASSO 1 DE 2 · GASTO', 'PASSO 2 DE 2 · RECEITA', 'Como Funciona?', 'E a Receita?',
                      'Use Para Valores Que Entram Todo Mês, Como Salário ou Aluguel Recebido.',
                      'Dia do Recebimento', 'Valor do Recebimento', '<span>Próximo</span>', '>Começar<'):
            with self.subTest(texto=texto):
                self.assertIn(texto, guia)
        # Só o passo 1 e o "Próximo" aparecem de início; o passo 2 e o "Começar" começam escondidos.
        self.assertIn('<div class="rec-corpo" id="guia-passo-1">', guia)
        self.assertIn('<div class="rec-corpo" id="guia-passo-2" hidden>', guia)
        self.assertIn('id="guia-comecar" type="button" hidden>', guia)
        self.assertNotIn('id="guia-proximo" type="button" hidden', guia)
        # Indicador: a barra comprida marca o passo atual (passo 1 à esquerda, passo 2 à direita).
        self.assertIn('<div class="guia-barras" aria-hidden="true"><span class="atual"></span><span></span></div>', guia)
        self.assertIn('<div class="guia-barras" aria-hidden="true"><span></span><span class="atual"></span></div>', guia)
        # O "Próximo" tem a seta e não tem a logo (a logo fica só no "Começar").
        proximo = guia[guia.index('id="guia-proximo"'):guia.index('id="guia-comecar"')]
        self.assertIn('M12 6l5 5-5 5', proximo)
        self.assertNotIn('M11.5 5V22.5', proximo)
        self.assertIn('M11.5 5V22.5', guia[guia.index('id="guia-comecar"'):])
        principal = codigo_principal(HOME)
        self.assertIn('$("guia-proximo").addEventListener("click", () => mostrarPassoGuia(2));', principal)
        self.assertIn('$("guia-comecar").addEventListener("click", () => { marcarGuiaVisto(); abrirEscolha("home"); });', principal)
        # O "já visto" só é gravado no "Começar" (D11: mesma marca de antes), nunca ao trocar de passo.
        self.assertNotIn('marcarGuiaVisto', corpo_da_funcao(principal, 'function mostrarPassoGuia'))
        self.assertIn('const CHAVE_GUIA_RECORRENTES = "guia_recorrentes_visto";', principal)
        self.assertNotIn('guia_recorrentes_v2', HOME)
        # O voltar do passo 2 volta ao passo 1; o atalho sempre abre o guia no passo 1.
        self.assertIn('else if (destino === "guia-passo-1") mostrarPassoGuia(1);', corpo_da_funcao(principal, 'function voltarNasRecorrentes'))
        self.assertIn('guiaPasso === 2 ? "guia-passo-2" : "guia"', corpo_da_funcao(principal, 'function telaRecorrentesAtual'))
        abrir = corpo_da_funcao(principal, 'async function abrirRecorrentes')
        self.assertLess(abrir.index('mostrarPassoGuia(1);'), abrir.index('mostrarTelaRecorrentes("recorrentes-guia");'))
        self.assertIn('abrirLista("gasto");', abrir)   # D12: o atalho abre a lista na aba Gasto

    def test_formulario_da_receita_e_o_mesmo_bloco_em_outro_modo(self):
        principal = codigo_principal(HOME)
        for identificador in ('recorrente-form', 'rf-salvar', 'rf-dia', 'rf-valor', 'rf-descricao'):
            with self.subTest(id=identificador):
                self.assertEqual(HOME.count('id="%s"' % identificador), 1)   # nada de bloco duplicado
        desenhar = corpo_da_funcao(principal, 'function desenharForm')
        for trecho in ('textosDoFormulario(f.tipo, f.modo)', '$("rf-rotulo-dia").textContent = textos.rotuloDia;',
                       '$("rf-rotulo-valor").textContent = textos.rotuloValor;', '$("rf-descricao").placeholder = textos.exemplo;',
                       '$("rf-bloco-estabelecimento").hidden = f.tipo === "receita";',
                       '$("rf-bloco-categoria").hidden = f.tipo === "receita";'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, desenhar)
        # A receita não tem categoria: nem busca a lista de categorias.
        abrir_form = corpo_da_funcao(principal, 'function abrirForm()')
        self.assertIn('if (formRec.tipo !== "receita") carregarCategoriasRec();', abrir_form)
        # D5 = C da PRO-102: a tela não lê nem mostra a renda.
        for nome, corpo in (('bloco', bloco_main('recorrente-form')), ('desenharForm', desenhar), ('abrirForm', abrir_form),
                            ('salvar', corpo_da_funcao(principal, 'async function salvarRecorrente'))):
            with self.subTest(sem_renda=nome):
                self.assertNotIn('renda', corpo.lower())
        # Mesmo limite de 30 caracteres, a mesma máscara e os mesmos erros ao sair do campo.
        self.assertIn('id="rf-descricao" type="text" maxlength="30"', HOME)
        self.assertIn('formRec.valorDigitos = digitosDoValor($("rf-valor").value);', principal)
        self.assertIn('$("rf-valor").addEventListener("blur", () => { formRec.valorTocado = true; desenharForm(); });', principal)
        self.assertIn('$("rf-descricao").addEventListener("blur", () => { formRec.descricaoTocada = true; desenharForm(); });', principal)
        # O Editar abre no modo da aba da lista, com o dia certo de cada tipo.
        editar_ = corpo_da_funcao(principal, 'function abrirFormEditar')
        self.assertIn('const tipo = listaRecorrentes.aba;', editar_)
        self.assertIn('zerarForm("editar", tipo);', editar_)
        self.assertIn('diaDoRecorrente(r, tipo)', editar_)
        self.assertNotIn('gerarChave', editar_)
        # A chave nasce só ao abrir o Novo (de gasto ou de receita) e vale para todas as tentativas.
        self.assertEqual(corpo_da_funcao(principal, 'function abrirFormNovo').count('gerarChave()'), 1)
        self.assertNotIn('gerarChave', corpo_da_funcao(principal, 'function abrirSalarioRecorrente'))

    def test_folha_do_dia_troca_os_textos_pelo_modo(self):
        abrir = corpo_da_funcao(codigo_principal(HOME), 'function abrirFolhaDia')
        for trecho in ('textosFolhaDia(formRec.tipo)', '$("folha-dia-titulo").textContent = textos.titulo;',
                       '$("folha-dia-apoio").textContent = textos.apoio;'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, abrir)

    def test_lista_tem_os_botoes_gasto_e_receita(self):
        principal = codigo_principal(HOME)
        lista = bloco_main('recorrentes-lista')
        for trecho in ('role="tablist"', 'id="rec-aba-gasto" type="button" role="tab" aria-selected="true">Gasto</button>',
                       'id="rec-aba-receita" type="button" role="tab" aria-selected="false">Receita</button>',
                       'Suas receitas recorrentes aparecem aqui',
                       'Comece pelo seu <strong>Salário</strong> e outras entradas fixas, como <strong>Aluguel Recebido</strong>'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, lista)
        # Quadro LISTA - V3: 154px de largura, a 12px da faixa, à direita (24px), com 3px de folga e botões de 30px.
        css = HOME[:HOME.index('</style>')]
        regra = re.search(r'(?m)^\s*\.rec-abas\s*\{([^}]*)\}', css).group(1)
        for trecho in ('position: absolute;', 'top: 12px;', 'right: 24px;', 'width: 154px;', 'padding: 3px;', 'gap: 2px;',
                       'border-radius: 12px;'):
            with self.subTest(css=trecho):
                self.assertIn(trecho, regra)
        self.assertIn('.rec-aba { flex: 1 1 0; height: 30px;', css)
        self.assertIn('.rec-total-valor.receita { color: var(--cor-receita); }', css)
        # Cada aba carrega a sua rota; os botões não guardam a escolha (D12).
        self.assertIn('$("rec-aba-gasto").addEventListener("click", () => trocarAba("gasto"));', principal)
        self.assertIn('$("rec-aba-receita").addEventListener("click", () => trocarAba("receita"));', principal)
        trocar = corpo_da_funcao(principal, 'function trocarAba')
        self.assertIn('carregarRecorrentes();', trocar)
        self.assertNotIn('localStorage', trocar)
        self.assertNotIn('CloudStorage', trocar)
        desenhar = corpo_da_funcao(principal, 'function desenharLista')
        for trecho in ('itensDaResposta(dados, tipo)', 'textoContagemReceitas(itens.length)', 'dados.total_mensal',
                       '$("rec-total").classList.toggle("receita", receita);'):
            with self.subTest(desenhar=trecho):
                self.assertIn(trecho, desenhar)
        # A linha da receita não tem a linha de estabelecimento e categoria.
        linha = corpo_da_funcao(principal, 'function criarLinhaRecorrente')
        self.assertIn('if (tipo !== "receita") textos.append(criar("span", "rec-linha-sub", subtituloRecorrente(r)));', linha)
        # "Adicionar Recorrente" abre a escolha nas duas abas (um único botão, sem condição de aba).
        self.assertIn('$("rec-adicionar").addEventListener("click", () => abrirEscolha("lista"));', principal)

    def test_gravacao_da_receita_usa_as_rotas_novas_e_nao_muda_a_do_gasto(self):
        principal = codigo_principal(HOME)
        self.assertEqual(HOME.count('"/receitas-recorrentes"'), 1)   # a rota só existe na função pura rotaRecorrentes
        self.assertEqual(HOME.count('"/recorrentes"'), 1)
        salvar = corpo_da_funcao(principal, 'async function salvarRecorrente')
        self.assertIn('const tipo = f.tipo;', salvar)
        self.assertIn('chamar(rotaRecorrentes(tipo))', salvar)
        self.assertIn('listaRecorrentes.aba = abaDepoisDeSalvar(tipo);', salvar)   # D12
        self.assertIn('abrirLista(tipo);', salvar)                                 # receita sumiu: volta à lista do tipo
        self.assertIn('const rota = novo ? rotaRecorrentes(tipo) :', salvar)
        apagar = corpo_da_funcao(principal, 'async function apagarRecorrente')
        self.assertIn('rotaRecorrentes(tipo) + "/" + encodeURIComponent(formRec.id)', apagar)
        self.assertIn('listaRecorrentes.aba = abaDepoisDeSalvar(tipo);', apagar)
        self.assertIn('chamar(rotaRecorrentes(tipo))', apagar)
        # A tela da logo e o tempo mínimo seguem só no criar/editar (um lugar), nunca no apagar nem na lista.
        self.assertEqual(principal.count('comTempoMinimo('), 5)   # eram 3; o quarto é o salvarCategoria (PRO-105), o quinto o teste grátis (PRO-108)
        self.assertEqual(salvar.count('comTempoMinimo('), 1)
        # O texto do apagar da receita vem da função pura, por textContent (criar).
        self.assertIn('textoConfirmarApagar(r, formRec.tipo)', corpo_da_funcao(principal, 'function abrirFolhaApagar'))

    def test_rotas_campos_e_codigos_da_receita_sao_os_da_api(self):
        api = open(os.path.join(RAIZ, 'api', 'main.py'), encoding='utf-8').read()
        servidor = api + open(os.path.join(RAIZ, 'recorrentes.py'), encoding='utf-8').read()
        for rota in ('@app.get("/receitas-recorrentes")', '@app.post("/receitas-recorrentes", status_code=201)',
                     '@app.put("/receitas-recorrentes/{receita_recorrente_id}")',
                     '@app.delete("/receitas-recorrentes/{receita_recorrente_id}")'):
            with self.subTest(rota=rota):
                self.assertIn(rota, api)
        for campo in ('"receitas_recorrentes"', '"total_mensal"', '"dia_recebimento"', '"descricao"', '"valor"'):
            with self.subTest(campo=campo):
                self.assertIn(campo, api)
        puro = codigo_puro(HOME)
        for campo in ('receitas_recorrentes', 'dia_recebimento', 'total_mensal'):
            self.assertIn(campo, puro + codigo_principal(HOME))
        for codigo in ('dia_invalido', 'valor_invalido', 'descricao_invalida', 'descricao_longa', 'descricao_repetida',
                       'receita_recorrente_nao_encontrada'):
            with self.subTest(codigo=codigo):
                self.assertIn("'%s'" % codigo, servidor)
                self.assertIn(codigo, puro)
        # A receita não tem estabelecimento nem categoria: o corpo e os 422 da receita não os citam.
        corpo = corpo_da_funcao(puro, 'function montarCorpoReceitaRecorrente')
        for proibido in ('estabelecimento', 'categoria'):
            self.assertNotIn(proibido, corpo)
        erros = puro[puro.index('const ERROS_422_RECEITA_RECORRENTE'):]
        erros = erros[:erros.index('};')]
        for proibido in ('estabelecimento_longo', 'categoria_nao_encontrada'):
            self.assertNotIn(proibido, erros)

    def test_voltar_da_receita_sobe_um_nivel_em_toda_tela_e_folha(self):
        principal = codigo_principal(HOME)
        # A seta de cada tela e o BackButton passam pelo mesmo voltarUmNivel (as folhas fecham primeiro).
        for identificador in ('guia-voltar', 'escolha-rec-voltar', 'lista-rec-voltar', 'rf-voltar'):
            self.assertIn('"%s"' % identificador, corpo_da_funcao(principal, 'function iniciarRecorrentes'))
        self.assertIn('tg.BackButton.onClick(voltarUmNivel)', principal)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        self.assertLess(voltar.index('folhaAberta()'), voltar.index('telaRecorrentesAtual()'))
        # O formulário aberto pelo cartão do salário volta direto à home.
        self.assertIn('origemFormNovo', corpo_da_funcao(principal, 'function voltarNasRecorrentes'))


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

    # ---------------------------- PRO-103: receita recorrente (funções puras) ----------------------------

    def test_receita_dica_e_salvar_sem_categoria(self):
        base = {'tipo': 'receita', 'dia': 5, 'valorDigitos': '450000', 'descricao': 'Salário', 'categoria': None, 'salvando': False}
        casos = [
            ({'tipo': 'receita', 'dia': None, 'valorDigitos': '', 'descricao': '', 'categoria': None}, 'Preencha o dia, o valor e a descrição', False),
            (base, '', True),
            (dict(base, dia=None), 'Preencha o dia', False),
            (dict(base, valorDigitos='0'), 'Corrija o valor', False),
            (dict(base, valorDigitos='100000001'), 'Corrija o valor', False),
            (dict(base, valorDigitos='', descricao='  '), 'Preencha o valor e a descrição', False),
            (dict(base, valorDigitos='0', dia=None, descricao=''), 'Corrija o valor e preencha o dia e a descrição', False),
            (dict(base, salvando=True), '', False),                       # o primeiro toque trava
            (dict(base, categoria='Lazer'), '', True),                    # categoria sobrando não atrapalha
            # O mesmo formulário no modo gasto (sem tipo) continua exigindo a categoria.
            (dict(base, tipo='gasto'), 'Preencha a categoria', False),
            ({k: v for k, v in base.items() if k != 'tipo'}, 'Preencha a categoria', False),
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map((f) => [dicaDoSalvar(f), podeSalvarRecorrente(f), formularioRecorrenteValido(f)])));"),
            [c for c, _, _ in casos])
        for (entrada, dica, pode), recebido in zip(casos, obtido):
            with self.subTest(entrada=entrada):
                self.assertEqual(recebido[0], dica)
                self.assertEqual(recebido[1], pode)

    def test_corpo_da_receita_so_tem_dia_valor_descricao_e_chave(self):
        obtido = rodar_node(com_funcoes_puras("""
            const f = { tipo: 'receita', dia: 5, valorDigitos: '450000', descricao: '  Salário ', estabelecimento: 'x', categoria: 'Lazer' };
            console.log(JSON.stringify({
              put: montarCorpoRecorrente(f), post: montarCorpoRecorrente(f, 'abc'), direto: montarCorpoReceitaRecorrente(f),
              ordem: Object.keys(montarCorpoRecorrente(f, 'abc')), json: JSON.stringify(montarCorpoRecorrente(f)),
              gasto: Object.keys(montarCorpoRecorrente(Object.assign({}, f, { tipo: 'gasto' }))),
            }));
        """))
        corpo = {'dia_recebimento': 5, 'valor': 4500.0, 'descricao': 'Salário'}
        self.assertEqual(obtido['put'], corpo)
        self.assertEqual(obtido['post'], dict(corpo, chave='abc'))
        self.assertEqual(obtido['direto'], corpo)
        self.assertEqual(obtido['ordem'][0], 'chave')
        self.assertIn('"valor":4500,', obtido['json'])
        # O gasto continua com o corpo de sempre (estabelecimento e categoria).
        self.assertEqual(obtido['gasto'], ['dia_cobranca', 'valor', 'descricao', 'estabelecimento', 'categoria'])

    def test_textos_da_receita_na_lista_no_formulario_e_na_folha(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              contagem: [0, 1, 2, 3, 12].map(textoContagemReceitas),
              formulario: [textosDoFormulario('receita', 'novo'), textosDoFormulario('receita', 'editar'),
                           textosDoFormulario('gasto', 'novo'), textosDoFormulario('gasto', 'editar'), textosDoFormulario(undefined, 'novo')],
              folha: [textosFolhaDia('receita'), textosFolhaDia('gasto'), textosFolhaDia(undefined)],
              apagar: [textoConfirmarApagar({ descricao: 'Salário', valor: 4500, dia_recebimento: 5 }, 'receita'),
                       textoConfirmarApagar({ descricao: 'Assinatura Spotify', valor: 117.8, dia_cobranca: 12 }, 'gasto'),
                       textoConfirmarApagar({ descricao: 'Assinatura Spotify', valor: 117.8, dia_cobranca: 12 })],
              dia: [diaDoRecorrente({ dia_recebimento: 5, dia_cobranca: 9 }, 'receita'), diaDoRecorrente({ dia_recebimento: 5, dia_cobranca: 9 }, 'gasto'),
                    diaDoRecorrente({ dia_cobranca: 9 })],
              rotas: [rotaRecorrentes('receita'), rotaRecorrentes('gasto'), rotaRecorrentes(), rotaRecorrentes('outro')],
              itens: [itensDaResposta({ receitas_recorrentes: [{ id: 1 }], recorrentes: [{ id: 2 }] }, 'receita'),
                      itensDaResposta({ receitas_recorrentes: [{ id: 1 }], recorrentes: [{ id: 2 }] }, 'gasto'),
                      itensDaResposta({ recorrentes: [{ id: 2 }] }, 'receita'), itensDaResposta(undefined, 'gasto'),
                      itensDaResposta({ recorrentes: 'x' }, 'gasto')],
              aba: [abaDepoisDeSalvar('receita'), abaDepoisDeSalvar('gasto'), abaDepoisDeSalvar(undefined)],
              dia_texto: [textoDiaEscolhido(5), textoConfirmarDia(5)],
            }));
        """))
        self.assertEqual(obtido['contagem'], ['Nenhuma receita recorrente ainda', '1 receita, lançada sozinha todo mês',
                                              '2 receitas, lançadas sozinhas todo mês', '3 receitas, lançadas sozinhas todo mês',
                                              '12 receitas, lançadas sozinhas todo mês'])
        titulo_novo = 'Registre Sua Receita e Espere Chegar a Data, na Data Exata o L1d3r Registra.'
        apoio_novo = 'Escolha o Dia em Que Esse Valor Costuma Entrar na Sua Conta, Como o Dia do Salário.'
        self.assertEqual(obtido['formulario'][0], {
            'titulo': titulo_novo, 'intro': apoio_novo, 'rotuloDia': 'Dia do Recebimento',
            'rotuloValor': 'Valor do Recebimento', 'exemplo': 'Ex.: Salário'})
        self.assertEqual(obtido['formulario'][1], {
            'titulo': 'Editar Recorrente',
            'intro': 'As mudanças valem a partir do próximo recebimento. O que já entrou nos meses anteriores não muda.',
            'rotuloDia': 'Dia do Recebimento', 'rotuloValor': 'Valor do Recebimento', 'exemplo': 'Ex.: Salário'})
        self.assertEqual(obtido['formulario'][2], {
            'titulo': 'Registre Seu Gasto e Espere Chegar a Data, na Data Exata o L1d3r Registra.',
            'intro': 'Veja na Sua Fatura do Cartão o Dia Exato em Que Costuma Cair Essa Assinatura Para um Melhor Controle.',
            'rotuloDia': 'Dia da Cobrança', 'rotuloValor': 'Valor da Cobrança', 'exemplo': 'Ex.: Assinatura Spotify'})
        self.assertEqual(obtido['formulario'][3]['intro'],
                         'As mudanças valem a partir da próxima cobrança. O que já entrou nos meses anteriores não muda.')
        self.assertEqual(obtido['formulario'][4], obtido['formulario'][2])   # sem tipo, vale o gasto
        self.assertEqual(obtido['folha'], [
            {'titulo': 'Dia do Recebimento', 'apoio': 'Escolha o dia do mês em que esse valor entra.'},
            {'titulo': 'Dia da Cobrança', 'apoio': 'Escolha o dia do mês em que essa cobrança cai.'},
            {'titulo': 'Dia da Cobrança', 'apoio': 'Escolha o dia do mês em que essa cobrança cai.'}])
        self.assertEqual(obtido['apagar'][0], {
            'negrito': 'Salário · R$ 4.500,00',
            'depois': ' deixa de entrar todo dia 5. As receitas que já entraram nos meses anteriores continuam no histórico.'})
        self.assertEqual(obtido['apagar'][1], obtido['apagar'][2])
        self.assertEqual(obtido['apagar'][1]['depois'],
                         ' deixa de entrar todo dia 12. Os gastos que já entraram nos meses anteriores continuam no histórico.')
        self.assertEqual(obtido['dia'], [5, 9, 9])
        self.assertEqual(obtido['rotas'], ['/receitas-recorrentes', '/recorrentes', '/recorrentes', '/recorrentes'])
        self.assertEqual(obtido['itens'], [[{'id': 1}], [{'id': 2}], [], [], []])
        self.assertEqual(obtido['aba'], ['receita', 'gasto', 'gasto'])
        self.assertEqual(obtido['dia_texto'], [{'antes': 'Todo dia ', 'negrito': '5'}, 'Confirmar Dia 5'])   # "Todo dia N" e "Confirmar Dia N"

    def test_navegacao_do_guia_em_dois_passos_e_do_cartao_do_salario(self):
        obtido = rodar_node(com_funcoes_puras("""
            const voltar = {};
            for (const tela of ['guia', 'guia-passo-2', 'escolha', 'lista', 'form-novo', 'form-editar', 'outra']) {
              voltar[tela] = [telaAoVoltarRecorrentes(tela, 'home', 'escolha'), telaAoVoltarRecorrentes(tela, 'lista', 'escolha'),
                              telaAoVoltarRecorrentes(tela, 'home', 'salario'), telaAoVoltarRecorrentes(tela, 'lista'), telaAoVoltarRecorrentes(tela)];
            }
            console.log(JSON.stringify(voltar));
        """))
        self.assertEqual(obtido['guia'], ['home'] * 5)
        self.assertEqual(obtido['guia-passo-2'], ['guia-passo-1'] * 5)       # o passo 2 volta ao passo 1
        self.assertEqual(obtido['escolha'], ['home', 'lista', 'home', 'lista', 'home'])
        self.assertEqual(obtido['lista'], ['home'] * 5)
        # Formulário Novo: da escolha volta à escolha; do cartão do salário (D14) volta à home.
        self.assertEqual(obtido['form-novo'], ['escolha', 'escolha', 'home', 'escolha', 'escolha'])
        self.assertEqual(obtido['form-editar'], ['lista'] * 5)
        self.assertEqual(obtido['outra'], [None] * 5)

    def test_logo_e_avisos_da_receita(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              criar: textosTelaLogoRecorrente('criar', 5, 'receita'), editar: textosTelaLogoRecorrente('editar', 5, 'receita'),
              gastoCriar: textosTelaLogoRecorrente('criar', 12, 'gasto'), gastoSem: textosTelaLogoRecorrente('criar', 12),
              avisos: ['criar', 'editar', 'apagar'].map((op) => avisoDeRecorrente(op, { categoria: 'Lazer', valor: 1 }, 'receita')),
              avisoGasto: avisoDeRecorrente('criar', { categoria: 'Lazer' }, 'gasto'),
              avisoApagarSemCorpo: avisoDeRecorrente('apagar', undefined, 'receita'),
            }));
        """))
        self.assertEqual(obtido['criar'], {'cabecalho': 'Nova Receita', 'titulo': 'Salvando a Receita...',
                                           'texto': 'Ela entra sozinha todo dia 5.'})
        self.assertEqual(obtido['editar'], {'cabecalho': 'Editar Recorrente', 'titulo': 'Salvando as Alterações...',
                                            'texto': 'Elas valem a partir do próximo recebimento.'})
        self.assertEqual(obtido['gastoCriar'], {'cabecalho': 'Novo Recorrente', 'titulo': 'Salvando o Recorrente...',
                                                'texto': 'Ele entra sozinho todo dia 12.'})
        self.assertEqual(obtido['gastoSem'], obtido['gastoCriar'])
        self.assertEqual([a['texto'] for a in obtido['avisos']],
                         ['Receita Recorrente Adicionada', 'Receita Recorrente Atualizada', 'Receita Recorrente Apagada'])
        for aviso in obtido['avisos']:
            self.assertEqual((aviso['negrito'], aviso['depois']), ('', ''))   # sem categoria em negrito
        self.assertEqual(obtido['avisoGasto']['texto'], 'Cobrança Recorrente Adicionada a Lazer')
        self.assertEqual(obtido['avisoApagarSemCorpo']['texto'], 'Receita Recorrente Apagada')

    def test_decisao_por_status_das_tres_gravacoes_da_receita(self):
        sessao = 'Sua sessão expirou. Feche e abra o app de novo.'
        salvar = 'Não foi possível salvar. Tente de novo.'
        apagar = 'Não foi possível apagar. Tente de novo.'
        repetida = 'Já existe uma receita recorrente com esse nome.'
        nao_achou = 'receita_recorrente_nao_encontrada'
        casos = [
            ('criar', {'dados': {}, 'status': 201}, {'acao': 'sucesso'}),
            ('criar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),         # repetido: já estava gravado
            ('editar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),
            ('apagar', {'dados': {}, 'status': 200}, {'acao': 'sucesso'}),
            ('apagar', {'erro': 404, 'detalhe': nao_achou}, {'acao': 'sucesso'}),  # já apagada
            ('editar', {'erro': 404, 'detalhe': nao_achou},
             {'acao': 'erro', 'mensagem': 'Este recorrente não existe mais.', 'voltarLista': True}),
            # O código do gasto não vale na receita (vira o 404 de cadastro), e o da receita não vale no gasto.
            ('editar', {'erro': 404, 'detalhe': 'recorrente_nao_encontrado'},
             {'acao': 'erro', 'mensagem': 'Não encontramos o seu cadastro. Feche e abra o app de novo.'}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_repetida'}, {'acao': 'erro', 'campo': 'descricao', 'mensagem': repetida}),
            ('editar', {'erro': 422, 'detalhe': 'descricao_repetida'}, {'acao': 'erro', 'campo': 'descricao', 'mensagem': repetida}),
            ('criar', {'erro': 422, 'detalhe': 'dia_invalido'}, {'acao': 'erro', 'campo': 'dia', 'mensagem': 'Escolha o dia do recebimento.'}),
            ('criar', {'erro': 422, 'detalhe': 'valor_invalido'},
             {'acao': 'erro', 'campo': 'valor', 'mensagem': 'Valor inválido. Digite um valor maior que zero e de no máximo R$ 1.000.000,00'}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_invalida'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'Preencha a descrição para continuar'}),
            ('criar', {'erro': 422, 'detalhe': 'descricao_longa'},
             {'acao': 'erro', 'campo': 'descricao', 'mensagem': 'A descrição pode ter no máximo 30 caracteres.'}),
            # Não existe estabelecimento nem categoria na receita: esses códigos caem na mensagem geral.
            ('criar', {'erro': 422, 'detalhe': 'categoria_nao_encontrada'}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 422, 'detalhe': 'estabelecimento_longo'}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 422, 'detalhe': 'chave_invalida'}, {'acao': 'erro', 'mensagem': salvar}),
            ('apagar', {'erro': 422, 'detalhe': 'descricao_repetida'}, {'acao': 'erro', 'mensagem': apagar}),
            ('criar', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
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
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([op, r]) => decisaoDaGravacaoRecorrente(op, r, 'receita'))));"),
            [[op, r] for op, r, _ in casos])
        for (operacao, resultado, esperado), recebido in zip(casos, obtido):
            with self.subTest(operacao=operacao, resultado=resultado):
                self.assertEqual(recebido, esperado)
        for recebido in obtido:
            self.assertNotIn('descricao_repetida', recebido.get('mensagem', ''))
        # O gasto (sem o tipo) segue como na PRO-101: o 404 da receita não vale e o 422 repetido tem a mensagem antiga.
        gasto = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify([
              decisaoDaGravacaoRecorrente('editar', { erro: 404, detalhe: 'receita_recorrente_nao_encontrada' }),
              decisaoDaGravacaoRecorrente('criar', { erro: 422, detalhe: 'descricao_repetida' }),
              decisaoDaGravacaoRecorrente('criar', { erro: 422, detalhe: 'descricao_repetida' }, 'gasto'),
            ]));
        """))
        self.assertEqual(gasto[0], {'acao': 'erro', 'mensagem': 'Não encontramos o seu cadastro. Feche e abra o app de novo.'})
        self.assertEqual(gasto[1]['mensagem'], 'Já existe uma cobrança com esse nome nesta categoria.')
        self.assertEqual(gasto[1], gasto[2])

    def test_novas_tentativas_da_receita_repetem_o_mesmo_corpo_e_a_mesma_chave(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const f = { tipo: 'receita', dia: 5, valorDigitos: '450000', descricao: 'Salário' };
              const corpo = montarCorpoRecorrente(f, 'chave-do-formulario');
              const enviados = [];
              const respostas = [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 0 }, { dados: { id: 1 }, status: 201 }];
              const resultado = await gravarComTentativas(async (c) => { enviados.push(JSON.stringify(c)); return respostas.shift(); }, corpo, async () => {});
              console.log(JSON.stringify({ iguais: enviados.every((x) => x === enviados[0]), n: enviados.length,
                                           status: resultado.status, tem_chave: JSON.parse(enviados[0]).chave }));
            })();
        """))
        self.assertEqual(obtido, {'iguais': True, 'n': 3, 'status': 201, 'tem_chave': 'chave-do-formulario'})


# ----------------------------------------------------------------------------
# 6. Categorias no Mini App (PRO-105)
# ----------------------------------------------------------------------------

def bloco_folha(identificador):
    """Texto de uma folha de baixo (<div class="folha-camada" id="...">) até a próxima folha ou o bloco de funções puras."""
    inicio = HOME.index('<div class="folha-camada" id="%s"' % identificador)
    fim = len(HOME)
    for marca in ('<div class="folha-camada"', '<!-- INICIO FUNCOES PURAS'):
        achou = HOME.find(marca, inicio + 10)
        if achou != -1:
            fim = min(fim, achou)
    return HOME[inicio:fim]


def css_da_home():
    return HOME[HOME.index('<style>'):HOME.index('</style>')]


def regra_css(seletor):
    """Corpo da primeira regra '<seletor> { ... }' que começa uma linha do CSS."""
    return re.search(r'(?m)^\s*' + re.escape(seletor) + r'\s*\{([^}]*)\}', css_da_home()).group(1)


class TestEstruturaDasCategorias(unittest.TestCase):
    FOLHAS = ('folha-cat-editar', 'folha-cat-excluir', 'folha-cat-nova')

    def test_bloco_main_e_folhas_fora_dos_main(self):
        self.assertRegex(HOME, r'<main id="categorias" class="tela-cheia" hidden>')
        ultimo_main = HOME.rindex('</main>')
        for identificador in self.FOLHAS:
            with self.subTest(folha=identificador):
                self.assertRegex(HOME, r'<div class="folha-camada" id="%s" role="dialog" aria-modal="true" aria-labelledby="[^"]+" hidden>' % identificador)
                self.assertGreater(HOME.index('id="%s"' % identificador), ultimo_main)
                # O toque no fundo escuro fecha a folha (o [data-fechar] do iniciarRecorrentes liga todos).
                self.assertIn('<div class="folha-fundo" data-fechar="%s"></div>' % identificador, bloco_folha(identificador))
        # A tela entra antes do aviso do topo (que continua depois do último </main>).
        self.assertLess(HOME.index('<main id="categorias"'), HOME.index('id="aviso-topo"'))

    def test_ids_unicos(self):
        ids = ('atalho-categorias', 'cat-voltar', 'cat-nova', 'cat-carregando', 'cat-erro', 'cat-erro-texto', 'cat-erro-tentar',
               'cat-conteudo', 'cat-titulo', 'cat-total', 'cat-lista',
               'cat-editar-campo', 'cat-editar-erro', 'cat-editar-erro-geral', 'cat-editar-salvar', 'cat-editar-excluir',
               'folha-cat-excluir-texto', 'folha-cat-excluir-erro', 'folha-cat-excluir-confirmar', 'folha-cat-excluir-cancelar',
               'cat-tipo-fixa', 'cat-tipo-temporaria', 'cat-nova-campo', 'cat-nova-erro', 'cat-nova-erro-geral',
               'cat-nova-salvar', 'cat-nova-cancelar') + self.FOLHAS
        for identificador in ids:
            with self.subTest(id=identificador):
                self.assertEqual(HOME.count('id="%s"' % identificador), 1)

    def test_textos_dos_quadros(self):
        tela = bloco_main('categorias')
        for texto in ('>Categorias<', 'Nova Categoria', 'Clique Nas Barras Para Visualizar e Editar as Categorias',
                      'Carregando...', 'Tentar de novo', 'Total Gasto:'):
            with self.subTest(texto=texto):
                self.assertIn(texto, tela)
        self.assertIn('Editar Categoria', bloco_folha('folha-cat-editar'))
        for texto in ('Nome:', 'Salvar Alterações', 'Excluir Categoria'):
            self.assertIn(texto, bloco_folha('folha-cat-editar'))
        for texto in ('Excluir Esta Categoria?', 'Sim, Excluir', 'Cancelar'):
            self.assertIn(texto, bloco_folha('folha-cat-excluir'))
        for texto in ('Nova Categoria', 'Fixa', 'Temporária', 'Nome da Nova Categoria:', 'Ex.: Pets', 'Salvar', 'Cancelar'):
            self.assertIn(texto, bloco_folha('folha-cat-nova'))
        # O nome exibido é L1d3r; "Fin4nce" não aparece na tela nova.
        self.assertNotIn('Fin4nce', tela)

    def test_atalho_ligado_e_nenhum_atalho_da_home_segue_em_breve(self):
        principal = codigo_principal(HOME)
        self.assertRegex(HOME, r'<button class="atalho" id="atalho-categorias" type="button" aria-label="Categorias">')
        self.assertIn('$("atalho-categorias").addEventListener("click", abrirCategorias);', corpo_da_funcao(principal, 'function iniciarCategorias'))
        self.assertIn('iniciarCategorias();', corpo_da_funcao(principal, 'function iniciar()'))
        # Os três atalhos abrem telas: nenhum seletor de ".atalho" liga o aviso "Em breve" (o atalho Categorias saiu dele).
        self.assertNotIn('document.querySelectorAll(".atalho', principal)
        abrir = corpo_da_funcao(principal, 'function abrirCategorias')
        self.assertIn('mostrarTelaCategorias();', abrir)
        self.assertIn('carregarCategorias();', abrir)
        self.assertIn('chamar(ROTA_RANKING_CATEGORIAS)', corpo_da_funcao(principal, 'async function carregarCategorias('))

    def test_chave_so_nasce_ao_abrir_a_folha_nova(self):
        principal = codigo_principal(HOME)
        self.assertEqual(corpo_da_funcao(principal, 'function abrirFolhaNova').count('gerarChave()'), 1)
        self.assertIn('catNova.chave = gerarChave();', corpo_da_funcao(principal, 'function abrirFolhaNova'))
        for assinatura in ('async function salvarCategoria', 'function abrirFolhaEditar', 'async function excluirCategoria',
                           'function aoDigitarNome', 'function desenharFolhaNome'):
            with self.subTest(funcao=assinatura):
                self.assertNotIn('gerarChave', corpo_da_funcao(principal, assinatura))
        # A chave vale para todas as tentativas do mesmo Salvar: o corpo é montado uma vez, antes de gravarComTentativas.
        salvar = corpo_da_funcao(principal, 'async function salvarCategoria')
        self.assertEqual(salvar.count('montarCorpoNovaCategoria('), 1)
        self.assertIn('montarCorpoNovaCategoria(f.nome, f.chave)', salvar)
        self.assertLess(salvar.index('const corpo ='), salvar.index('gravarComTentativas('))
        self.assertIn('gravarComTentativas((c) => enviarComMetodo(metodo, rota, c, 15000), corpo, esperar)', salvar)

    def test_logo_com_tempo_minimo_no_criar_e_no_renomear_e_fora_do_excluir(self):
        principal = codigo_principal(HOME)
        salvar = corpo_da_funcao(principal, 'async function salvarCategoria').replace('\r\n', '\n')
        # Uma função só para criar (POST) e renomear (PUT): os dois passam pela tela da logo com o tempo mínimo.
        self.assertIn('const metodo = criarNova ? "POST" : "PUT";', salvar)
        self.assertIn('TEMPO_MINIMO_LOGO_MS, (x) => decisaoDaGravacaoCategoria(operacao, x.r, limite).acao === "sucesso", () => performance.now(), esperar', salvar)
        self.assertIn('mostrarTelaLogo(logo.cabecalho, logo.titulo, logo.texto);', salvar)
        self.assertIn('textosTelaLogoCategoria(operacao)', salvar)
        self.assertEqual(salvar.count('comTempoMinimo('), 1)
        # Trava no primeiro toque, antes de qualquer chamada; a logo entra antes do POST/PUT; sai no erro na hora.
        self.assertLess(salvar.index('f.salvando = true;'), salvar.index('mostrarTelaLogo('))
        self.assertLess(salvar.index('mostrarTelaLogo('), salvar.index('gravarComTentativas('))
        self.assertLess(salvar.index('comTempoMinimo('), salvar.index('f.salvando = false;'))
        self.assertIn('if (!podeSalvarCategoria(f)) return;', salvar)
        self.assertEqual(salvar.count('esconderTelaLogo();'), 2)
        # Sucesso: o ranking novo é desenhado na mesma tarefa em que a logo sai.
        sucesso = salvar[salvar.index('if (decisao.acao === "sucesso")'):]
        self.assertLess(sucesso.index('aplicarResultadoCategorias(resultado.ranking);'), sucesso.index('esconderTelaLogo();'))
        self.assertIn('chamar(ROTA_RANKING_CATEGORIAS)', salvar)
        # Excluir: sem logo e sem tempo mínimo; "Excluindo..." no botão.
        excluir = corpo_da_funcao(principal, 'async function excluirCategoria')
        for proibido in ('comTempoMinimo', 'mostrarTelaLogo', 'esconderTelaLogo'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, excluir)
        self.assertIn('"Excluindo..."', corpo_da_funcao(principal, 'function mostrarErroExcluir'))
        self.assertIn('gravarComTentativas(() => enviarComMetodo("DELETE", rota, undefined, 15000), null, esperar)', excluir)
        self.assertIn('if (catEstado.excluindo) return;', excluir)
        self.assertLess(excluir.index('catEstado.excluindo = true;'), excluir.index('gravarComTentativas('))
        # A abertura da folha de exclusão só abre a folha (o DELETE está só no excluirCategoria).
        self.assertNotIn('DELETE', corpo_da_funcao(principal, 'function abrirFolhaExcluir'))
        # Quem mais chama o tempo mínimo e a logo: a abertura, o cadastro, o salvar recorrente e este (nada no resto).
        for assinatura in ('function abrirCategorias', 'async function carregarCategorias(', 'function abrirFolhaNova',
                           'function abrirFolhaEditar', 'function aplicarResultadoCategorias', 'function desenharRanking'):
            with self.subTest(funcao=assinatura):
                corpo = corpo_da_funcao(principal, assinatura)
                self.assertNotIn('comTempoMinimo', corpo)
                self.assertNotIn('mostrarTelaLogo', corpo)

    def test_rotas_campos_codigos_e_limites_sao_os_do_servidor(self):
        api = open(os.path.join(RAIZ, 'api', 'main.py'), encoding='utf-8').read()
        fonte = open(os.path.join(RAIZ, 'categorias.py'), encoding='utf-8').read()
        for rota in ('@app.get("/categorias/ranking")', '@app.post("/categorias", status_code=201)',
                     '@app.put("/categorias/{categoria_id}")', '@app.delete("/categorias/{categoria_id}")'):
            with self.subTest(rota=rota):
                self.assertIn(rota, api)
        for campo in ('"ano_mes"', '"total_gasto"', '"categorias"', '"limite"', '"ativa"'):
            with self.subTest(campo=campo):
                self.assertIn(campo, api)
        puro = codigo_puro(HOME)
        self.assertIn('const ROTA_RANKING_CATEGORIAS = "/categorias/ranking";', puro)
        # Os códigos de erro tratados pela tela existem no categorias.py; o 404 está em STATUS_ERRO_REGISTRO.
        for codigo in ('nome_invalido', 'nome_repetido', 'limite_categorias', 'categoria_com_recorrentes', 'ultima_categoria',
                       'categoria_inexistente'):
            with self.subTest(codigo=codigo):
                self.assertIn("ErroRegistro('%s')" % codigo, fonte)
                self.assertIn('"%s"' % codigo, puro)
        self.assertIn('"categoria_inexistente": 404', api)
        # O limite de 30 caracteres do campo é o NOME_MAXIMO do servidor, nas duas folhas.
        self.assertEqual(categorias.NOME_MAXIMO, 30)
        for campo in ('cat-editar-campo', 'cat-nova-campo'):
            with self.subTest(campo=campo):
                self.assertRegex(HOME, r'<input class="campo-form" id="%s" type="text" maxlength="%d"' % (campo, categorias.NOME_MAXIMO))
        # O limite de categorias vem do ranking (o campo "limite"): o número não é escrito na tela.
        principal = codigo_principal(HOME)
        self.assertIn('catEstado.dados.limite', corpo_da_funcao(principal, 'async function salvarCategoria'))
        self.assertNotIn('Você já tem 20', HOME)
        self.assertNotRegex(puro[puro.index('// ---------- Categorias: ranking'):], r'\b20\b')

    def test_barra_minima_de_8px_e_a_maior_em_grafite(self):
        barra = regra_css('.cat-barra')
        self.assertIn('min-width: 8px;', barra)            # vale também para R$ 0,00
        self.assertIn('transition: width', barra)          # a barra estica com transition própria
        self.assertIn('.cat-barra.maior { background: var(--cor-grafite); }', css_da_home())
        self.assertIn('background: var(--cor-barra-categoria);', barra)
        self.assertIn('--cor-barra-categoria: #D4D4D6;', HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))])
        self.assertIn('background: var(--cor-fundo-suave);', regra_css('.cat-trilho'))
        self.assertIn('color: var(--cor-erro);', regra_css('.cat-linha-valor'))
        self.assertIn('color: var(--cor-erro);', regra_css('.cat-total'))
        # O desenho usa a função pura para a largura e para a barra grafite.
        criar_linha = corpo_da_funcao(codigo_principal(HOME), 'function criarLinhaRanking')
        self.assertIn('proporcaoDaBarraCategoria(c.gasto, maior)', criar_linha)
        self.assertIn('barraEhMaior(c.gasto, maior)', criar_linha)

    def test_animacao_so_com_transform_e_transition_e_com_movimento_reduzido_nada_se_move(self):
        css = css_da_home()
        self.assertIn('transition: transform 0.35s ease;', regra_css('.cat-linha'))
        # A regra do movimento reduzido da lista (depois da regra geral da logo, que continua a primeira).
        inicio = css.index('/* ---------- Categorias (PRO-105')
        reduzido = css[inicio:]
        reduzido = reduzido[reduzido.index('@media (prefers-reduced-motion: reduce)'):]
        reduzido = reduzido[:160]
        self.assertIn('.cat-linha, .cat-barra { transition: none; }', reduzido)
        # E no JS: a animação é calculada só se NÃO houver movimento reduzido e se já havia uma lista antes.
        principal = codigo_principal(HOME)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)").matches', corpo_da_funcao(principal, 'function movimentoReduzido'))
        desenhar = corpo_da_funcao(principal, 'function desenharRanking')
        self.assertIn('const mover = animar && !movimentoReduzido() && !$("cat-conteudo").hidden;', desenhar)
        self.assertIn('if (!mover) return;', desenhar)
        # FLIP: First (antes de redesenhar), Last (depois), Invert (transform sem transição), Play (transição de volta).
        self.assertLess(desenhar.index('getBoundingClientRect().top'), desenhar.index('lista.replaceChildren('))
        self.assertLess(desenhar.index('lista.replaceChildren('), desenhar.index('translateY('))
        self.assertIn('el.style.transition = "none";', desenhar)
        self.assertIn('el.style.transition = "";', desenhar)
        self.assertIn('el.style.transform = "";', desenhar)
        # Só transform e a largura se movem: nenhuma outra propriedade de layout mexida pela animação (top/left/height/margin).
        for proibido in ('style.top', 'style.left', 'style.height', 'style.margin', 'requestAnimationFrame'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, desenhar)
        self.assertNotIn('setInterval', HOME)
        # Primeira carga: sem lista antes, nada se move (animar só quando catEstado.dados já existia).
        aplicar = corpo_da_funcao(principal, 'function aplicarResultadoCategorias')
        self.assertIn('const animar = catEstado.dados !== null;', aplicar)
        self.assertIn('desenharRanking(resultado.dados, animar);', aplicar)
        # Erro: a lista antiga deixa de valer (a próxima carga volta a ser "primeira").
        self.assertIn('catEstado.dados = null;', aplicar)

    def test_titulo_do_ranking_vem_da_mesma_funcao_e_do_mesmo_ano_mes_da_home(self):
        principal = codigo_principal(HOME)
        desenhar = corpo_da_funcao(principal, 'function desenharRanking')
        home = corpo_da_funcao(principal, 'function mostrarCabecalho')
        self.assertIn('"Gastos em " + MESES_COMPLETOS[indiceMes(resumo.ano_mes)]', home)
        self.assertIn('mostrarTituloCategorias(dados.ano_mes);', desenhar)
        titulo = corpo_da_funcao(principal, 'function mostrarTituloCategorias')
        self.assertIn('tituloDoRanking(MESES_COMPLETOS[indiceMes(anoMes)])', titulo)
        # Nenhum relógio do aparelho nem mês escrito no código no rótulo.
        for nome, corpo in (('desenharRanking', desenhar), ('mostrarTituloCategorias', titulo),
                            ('mostrarEstadoCategorias', corpo_da_funcao(principal, 'function mostrarEstadoCategorias')),
                            ('tituloDoRanking', corpo_da_funcao(codigo_puro(HOME), 'function tituloDoRanking')),
                            ('carregarCategorias', corpo_da_funcao(principal, 'async function carregarCategorias(')),
                            ('aplicarResultadoCategorias', corpo_da_funcao(principal, 'function aplicarResultadoCategorias'))):
            with self.subTest(funcao=nome):
                self.assertNotIn('new Date', corpo)
                self.assertNotIn('Date.now', corpo)
                self.assertNotIn('agoraEmBrasilia', corpo)
        for mes in ('janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro',
                    'novembro', 'dezembro'):
            with self.subTest(mes=mes):
                self.assertNotIn('"' + mes + '"', corpo_da_funcao(principal, 'function desenharRanking'))
        # O total é o do servidor, por formatarDinheiro; nenhuma soma na tela.
        self.assertIn('formatarDinheiro(Number(dados.total_gasto) || 0)', desenhar)
        for proibido in ('+=', '.reduce('):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, desenhar)
        self.assertNotIn('.sort(', desenhar)   # a ordem vem pronta do servidor

    def test_linha_excluida_sem_toque_e_nome_por_textcontent(self):
        principal = codigo_principal(HOME)
        linha = corpo_da_funcao(principal, 'function criarLinhaRanking')
        self.assertIn('criar(tocavel ? "button" : "div", "cat-linha" + (tocavel ? "" : " excluida"))', linha)
        self.assertIn('criar("span", "cat-nome-texto", c.nome)', linha)
        self.assertIn('if (!tocavel) trilho.append(criar("span", "cat-selo", "Excluída"));', linha)
        self.assertIn('formatarDinheiro(Number(c.gasto) || 0)', linha)
        self.assertNotIn('innerHTML', linha)
        # Só a linha ativa (um <button> com data-id) abre a folha; a delegação procura "button.cat-linha".
        self.assertIn('evento.target.closest("button.cat-linha")', corpo_da_funcao(principal, 'function iniciarCategorias'))
        self.assertIn('.cat-linha.excluida .cat-nome-texto { color: var(--cor-texto-3); }', css_da_home())

    def test_folha_nova_sem_em_breve_sem_descricao_e_a_temporaria_so_mostra_o_alerta(self):
        nova = bloco_folha('folha-cat-nova')
        # Nenhum texto "Em breve" visível (nem em atributo), e nenhum campo de descrição.
        self.assertNotRegex(nova, r'(?i)em\s+breve')
        self.assertNotRegex(nova, r'(?i)descri')
        self.assertNotIn('<textarea', nova)
        self.assertEqual(nova.count('<input'), 1)            # só o campo do nome
        self.assertNotIn('selo', nova)
        # A "Fixa" começa marcada e a "Temporária" fica apagada, sem selo, e continua tocável (nada de atributo disabled).
        self.assertIn('id="cat-tipo-fixa" type="button" role="tab" aria-selected="true">Fixa</button>', nova)
        self.assertIn('id="cat-tipo-temporaria" type="button" role="tab" aria-selected="false" aria-disabled="true">Temporária</button>', nova)
        self.assertNotRegex(nova, r'id="cat-tipo-temporaria"[^>]*\sdisabled')
        self.assertIn('.cat-tipo.apagada {', css_da_home())
        # O toque só chama o alerta nativo (o mesmo avisarEmBreve: showAlert("Em breve"), com window.alert de reserva).
        principal = codigo_principal(HOME)
        iniciar = corpo_da_funcao(principal, 'function iniciarCategorias')
        self.assertIn('$("cat-tipo-temporaria").addEventListener("click", avisarEmBreve);', iniciar)
        self.assertIn('tg.showAlert("Em breve")', corpo_da_funcao(principal, 'function avisarEmBreve'))
        self.assertIn('window.alert("Em breve")', corpo_da_funcao(principal, 'function avisarEmBreve'))
        # Nada da aba é guardado nem enviado, e a aba escolhida não muda.
        self.assertNotIn('$("cat-tipo-fixa")', iniciar)   # a "Fixa" já vem marcada e não faz nada
        for guardar in ('localStorage', 'CloudStorage', 'temporaria', 'temporária'):
            self.assertNotIn(guardar, corpo_da_funcao(principal, 'async function salvarCategoria'))
        self.assertNotIn('aria-selected', corpo_da_funcao(principal, 'function iniciarCategorias'))
        # A folha Editar também não tem descrição.
        self.assertNotRegex(bloco_folha('folha-cat-editar'), r'(?i)descri')

    def test_corpo_do_post_so_com_nome_e_chave(self):
        puro = codigo_puro(HOME)
        corpo = corpo_da_funcao(puro, 'function montarCorpoNovaCategoria')
        self.assertIn('return { nome: String(nome).trim(), chave: chave };', corpo)
        for proibido in ('descricao', 'tipo', 'temporaria', 'fixa', 'user_id'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, corpo)
        self.assertIn('return { nome: String(nome).trim() };', corpo_da_funcao(puro, 'function montarCorpoRenomearCategoria'))

    def test_voltar_das_categorias(self):
        principal = codigo_principal(HOME)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        # Com a logo na frente, nada; folha aberta, fecha a folha; depois a tela das Categorias; senão as outras.
        self.assertLess(voltar.index('if (!$("tela-logo").hidden) return;'), voltar.index('folhaAberta()'))
        self.assertLess(voltar.index('folhaAberta()'), voltar.index('if (!$("categorias").hidden)'))
        self.assertIn('voltarNasCategorias();', voltar)
        self.assertIn('FOLHAS_RECORRENTES.concat(FOLHAS_CATEGORIAS)', corpo_da_funcao(principal, 'function folhaAberta'))
        # Salvando ou excluindo, nada: nem a seta, nem o fundo escuro, nem o Cancelar.
        self.assertIn('if (categoriasOcupadas()) return;', corpo_da_funcao(principal, 'function voltarNasCategorias'))
        self.assertIn('categoriasOcupadas()', corpo_da_funcao(principal, 'function fecharFolhaAtual'))
        ocupadas = corpo_da_funcao(principal, 'function categoriasOcupadas')
        for trecho in ('catNova.salvando', 'catEditar.salvando', 'catEstado.excluindo'):
            self.assertIn(trecho, ocupadas)
        # A volta esconde o BackButton e devolve a rolagem da home; a seta usa o mesmo voltarUmNivel.
        home = corpo_da_funcao(principal, 'function voltarCategoriasParaHome')
        self.assertIn('mostrarBackButton(false);', home)
        self.assertIn('window.scrollTo(0, rolagemHome);', home)
        self.assertIn('catEstado.versao++;', home)
        self.assertIn('mostrarBackButton(true);', corpo_da_funcao(principal, 'function mostrarTelaCategorias'))
        self.assertIn('$("cat-voltar").addEventListener("click", voltarUmNivel);', corpo_da_funcao(principal, 'function iniciarCategorias'))

    def test_estados_carregando_erro_e_tentar_de_novo(self):
        principal = codigo_principal(HOME)
        tela = bloco_main('categorias')
        carregando = tela[tela.index('id="cat-carregando"'):tela.index('id="cat-erro"')]
        self.assertIn('class="logo-animada"', carregando)     # a logo pequena do quadro CARREGANDO
        self.assertIn('<p>Carregando...</p>', carregando)
        self.assertIn('mensagemDeErro([resultado])', corpo_da_funcao(principal, 'function aplicarResultadoCategorias'))
        self.assertIn('$("cat-erro-tentar").addEventListener("click", () => carregarCategorias());', corpo_da_funcao(principal, 'function iniciarCategorias'))
        # Reabrindo, a lista antiga continua visível enquanto a nova chega; sem lista antes, "Carregando...".
        self.assertIn('mostrarEstadoCategorias(catEstado.dados ? "ok" : "carregando");', corpo_da_funcao(principal, 'async function carregarCategorias('))
        self.assertIn('if (versao !== catEstado.versao) return;', corpo_da_funcao(principal, 'async function carregarCategorias('))
        self.assertNotIn('mostrarTelaLogo', corpo_da_funcao(principal, 'async function carregarCategorias('))
        # Quadro CARREGANDO: caixa de 320px, o quadrado de 64px (.estado-icone) com a logo de 40px (.logo-animada) e o texto.
        self.assertIn('class="estado-icone"', carregando)
        self.assertIn('height: 320px;', regra_css('.cat-carregando'))
        self.assertIn('width: 64px;', regra_css('.estado-icone'))
        self.assertIn('width: 40px; height: 40px;', regra_css('.logo-animada'))

    def test_excluir_bloqueado_deixa_so_o_cancelar(self):
        principal = codigo_principal(HOME)
        mostrar = corpo_da_funcao(principal, 'function mostrarErroExcluir')
        self.assertIn('$("folha-cat-excluir-confirmar").disabled = !!bloqueado || catEstado.excluindo;', mostrar)
        excluir = corpo_da_funcao(principal, 'async function excluirCategoria')
        self.assertIn('mostrarErroExcluir(decisao.mensagem, decisao.bloqueado);', excluir)
        self.assertIn('textoConfirmarExcluirCategoria(catExcluir.nome)', corpo_da_funcao(principal, 'function abrirFolhaExcluir'))
        self.assertIn('criar("strong", "", texto.negrito)', corpo_da_funcao(principal, 'function abrirFolhaExcluir'))
        # O erro é vermelho (campo-erro), fica entre o texto e os botões.
        folha = bloco_folha('folha-cat-excluir')
        self.assertLess(folha.index('id="folha-cat-excluir-texto"'), folha.index('id="folha-cat-excluir-erro"'))
        self.assertLess(folha.index('id="folha-cat-excluir-erro"'), folha.index('id="folha-cat-excluir-confirmar"'))
        self.assertIn('class="campo-erro" id="folha-cat-excluir-erro"', folha)


    def test_cabecalho_so_com_voltar_e_titulo_e_o_topo_do_conteudo(self):
        tela = bloco_main('categorias')
        cabecalho = tela[:tela.index('</header>')]
        # Como as outras telas cheias: só a seta e o título. O botão e a dica NÃO ficam no cabeçalho.
        self.assertIn('id="cat-voltar"', cabecalho)
        self.assertIn('<h1 class="titulo-tela">Categorias</h1>', cabecalho)
        self.assertNotIn('Nova Categoria', cabecalho)
        self.assertNotIn('cat-nova', cabecalho)
        self.assertNotIn('Clique Nas Barras', cabecalho)
        self.assertEqual(cabecalho.count('<button'), 1)
        # No conteúdo, nesta ordem: botão escuro à direita, dica, divisória e só depois o ranking.
        corpo = tela[tela.index('</header>'):]
        ordem = [corpo.index(x) for x in ('id="cat-nova"', 'Clique Nas Barras Para Visualizar e Editar as Categorias',
                                          'class="cat-divisoria"', 'class="cat-ranking"')]
        self.assertEqual(ordem, sorted(ordem))
        self.assertIn('justify-content: flex-end;', regra_css('.cat-nova-linha'))
        botao = regra_css('.cat-nova')
        for trecho in ('height: 36px;', 'border-radius: 12px;', 'background: var(--cor-grafite);'):
            with self.subTest(trecho=trecho):
                self.assertIn(trecho, botao)
        self.assertIn('color: var(--cor-texto-2);', regra_css('.cat-dica'))
        self.assertIn('font-size: 15px;', regra_css('.cat-dica'))
        divisoria = regra_css('.cat-divisoria')
        self.assertIn('height: 1px;', divisoria)
        self.assertIn('background: var(--cor-divisoria);', divisoria)
        # O botão, a dica e a divisória não somem em nenhum estado: nenhum deles é escondido pelo código.
        principal = codigo_principal(HOME)
        estado = corpo_da_funcao(principal, 'function mostrarEstadoCategorias')
        for identificador in ('cat-nova', 'cat-divisoria'):
            self.assertNotIn(identificador, estado)
        self.assertNotRegex(HOME, r'id="cat-nova"[^>]*\bhidden\b')
        self.assertNotIn('$("cat-nova").hidden', principal)

    def test_linha_do_ranking_tem_o_nome_no_trilho_e_o_valor_fora(self):
        principal = codigo_principal(HOME)
        linha = corpo_da_funcao(principal, 'function criarLinhaRanking')
        # O trilho leva a barra e o nome; o valor é irmão do trilho (fora dele), na ordem trilho, valor.
        self.assertIn('trilho.append(barra, criar("span", "cat-nome-texto", c.nome));', linha)
        self.assertIn('linha.append(trilho, criar("span", "cat-linha-valor", formatarDinheiro(Number(c.gasto) || 0)));', linha)
        self.assertNotIn('trilho.append(criar("span", "cat-linha-valor"', linha)
        trilho = regra_css('.cat-trilho')
        for trecho in ('position: relative;', 'flex: 1 1 0;', 'height: 44px;', 'padding: 0 14px;', 'border-radius: 12px;',
                       'background: var(--cor-fundo-suave);', 'overflow: hidden;'):
            with self.subTest(trilho=trecho):
                self.assertIn(trecho, trilho)
        barra = regra_css('.cat-barra')
        for trecho in ('position: absolute;', 'left: 0;', 'top: 0;', 'height: 44px;', 'min-width: 8px;'):
            with self.subTest(barra=trecho):
                self.assertIn(trecho, barra)
        valor = regra_css('.cat-linha-valor')
        for trecho in ('flex-shrink: 0;', 'width: 96px;', 'text-align: right;', 'color: var(--cor-erro);'):
            with self.subTest(valor=trecho):
                self.assertIn(trecho, valor)
        self.assertIn('gap: 12px;', regra_css('.cat-linha'))       # entre o trilho e o valor
        self.assertIn('gap: 10px;', regra_css('.cat-lista'))       # entre as linhas
        # A maior: barra grafite e o nome em branco. Nenhuma barrinha embaixo do nome.
        self.assertIn('.cat-trilho.maior .cat-nome-texto { color: var(--cor-branco); }', css_da_home())
        self.assertNotIn('height: 12px', regra_css('.cat-trilho'))
        # Excluída: trilho #F6F6F7, selo branco com borda #E0E2E2 dentro do trilho (o selo é filho do trilho).
        raiz = HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))]
        self.assertIn('--cor-categoria-excluida: #F6F6F7;', raiz)
        self.assertIn('--cor-borda-selo: #E0E0E2;', raiz)
        self.assertIn('.cat-linha.excluida .cat-trilho { background: var(--cor-categoria-excluida); }', css_da_home())
        selo = regra_css('.cat-selo')
        for trecho in ('background: var(--cor-branco);', 'border: 1px solid var(--cor-borda-selo);', 'height: 22px;'):
            with self.subTest(selo=trecho):
                self.assertIn(trecho, selo)
        self.assertLess(linha.index('trilho.append(criar("span", "cat-selo"'), linha.index('linha.append(trilho'))

    def test_titulo_e_total_do_bloco_e_o_que_aparece_em_cada_estado(self):
        principal = codigo_principal(HOME)
        self.assertIn('font-size: 18px;', regra_css('.cat-titulo'))
        self.assertIn('font-weight: 800;', regra_css('.cat-titulo'))
        self.assertIn('gap: 6px;', regra_css('.cat-resumo'))
        total = regra_css('.cat-total-linha')
        self.assertIn('font-size: 15px;', total)
        self.assertIn('font-weight: 700;', total)
        self.assertIn('font-weight: 800;', regra_css('.cat-total'))
        self.assertIn('color: var(--cor-erro);', regra_css('.cat-total'))
        # O mês conhecido vem do ano_mes do último /resumo da home (mostrarCabecalho), pela mesma MESES_COMPLETOS.
        self.assertIn('anoMesDaHome = resumo.ano_mes;', corpo_da_funcao(principal, 'function mostrarCabecalho'))
        estado = corpo_da_funcao(principal, 'function mostrarEstadoCategorias')
        self.assertIn('if (estado !== "ok") mostrarTituloCategorias(anoMesDaHome);', estado)
        self.assertIn('$("cat-total-linha").hidden = estado !== "ok";', estado)   # o "Total Gasto" só aparece com o ranking
        titulo = corpo_da_funcao(principal, 'function mostrarTituloCategorias')
        self.assertIn('$("cat-titulo").hidden = !anoMes;', titulo)                 # sem mês conhecido, esconde só o título
        self.assertRegex(HOME, r'id="cat-titulo" hidden>')
        self.assertRegex(HOME, r'id="cat-total-linha" hidden>Total Gasto: ')

    def test_carregando_e_erro_de_rede_seguem_os_quadros(self):
        principal = codigo_principal(HOME)
        tela = bloco_main('categorias')
        erro = tela[tela.index('id="cat-erro"'):tela.index('id="cat-conteudo"')]
        self.assertIn('class="cat-erro-caixa"', erro)
        self.assertIn('id="cat-erro-titulo"', erro)
        self.assertIn('id="cat-erro-texto"', erro)
        self.assertIn('M4 4l16 16', erro)                                    # o wi-fi cortado
        self.assertIn('<button class="botao-principal" id="cat-erro-tentar" type="button">Tentar de novo</button>', erro)
        self.assertLess(erro.index('cat-erro-caixa'), erro.index('cat-erro-tentar'))
        caixa = regra_css('.cat-erro-caixa')
        for trecho in ('height: 180px;', 'border: 1.5px dashed var(--cor-barra-fechado);', 'border-radius: 18px;'):
            with self.subTest(caixa=trecho):
                self.assertIn(trecho, caixa)
        self.assertIn('font-weight: 700;', regra_css('.cat-erro-titulo'))
        self.assertIn('color: var(--cor-texto-3);', regra_css('.cat-erro-texto'))
        # Os textos do quadro estão nas constantes puras e são os escritos no aplicarResultadoCategorias.
        puro = codigo_puro(HOME)
        self.assertIn('const MSG_CATEGORIAS_ERRO_TITULO = "Não Conseguimos Carregar Suas Categorias";', puro)
        self.assertIn('const MSG_CATEGORIAS_ERRO_TEXTO = "Confira a sua conexão e tente de novo.";', puro)
        aplicar = corpo_da_funcao(principal, 'function aplicarResultadoCategorias')
        self.assertIn('erro.tentarDeNovo ? MSG_CATEGORIAS_ERRO_TITULO : erro.texto', aplicar)   # 401, 404, 409: as mensagens de sempre
        self.assertIn('$("cat-erro-texto").hidden = !erro.tentarDeNovo;', aplicar)

    def test_folha_editar_tem_subtitulo_e_placeholder(self):
        editar = bloco_folha('folha-cat-editar')
        self.assertRegex(editar, r'<h2 class="folha-titulo" id="folha-cat-editar-titulo">Editar Categoria</h2>\s*'
                                 r'<p class="folha-texto">Escolha um novo nome para essa categoria.</p>')
        self.assertIn('<div class="folha-titulos compacto">', editar)
        self.assertIn('.folha-titulos.compacto { gap: 4px; }', css_da_home())
        self.assertIn('placeholder="Nome da categoria"', editar)
        self.assertIn('Ex.: Pets', bloco_folha('folha-cat-nova'))

    def test_folhas_nova_e_excluir_seguem_os_quadros(self):
        nova = bloco_folha('folha-cat-nova')
        # Título e seletor segmentado no mesmo cabeçalho (gap 14px); abas de 40px dentro da caixa de 4px de folga.
        self.assertLess(nova.index('folha-cat-nova-titulo'), nova.index('class="cat-tipos"'))
        self.assertIn('gap: 14px;', regra_css('.cat-folha-cabecalho'))
        caixa = regra_css('.cat-tipos')
        for trecho in ('padding: 4px;', 'gap: 4px;', 'border: 1px solid var(--cor-divisoria);', 'border-radius: 16px;'):
            with self.subTest(tipos=trecho):
                self.assertIn(trecho, caixa)
        tipo = regra_css('.cat-tipo')
        self.assertIn('height: 40px;', tipo)
        self.assertIn('border-radius: 12px;', tipo)
        self.assertIn('.cat-tipo.apagada { color: var(--cor-desabilitado-texto); }', css_da_home())
        # Salvar desativado: #E8E8E8 e #A3A3A3 (o botão principal desativado); ativo: grafite e branco.
        raiz = HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))]
        self.assertIn('--cor-desabilitado-fundo: #E8E8E8;', raiz)
        self.assertIn('--cor-desabilitado-texto: #A3A3A3;', raiz)
        self.assertIn('.botao-confirmar:disabled, .botao-principal:disabled { background: var(--cor-desabilitado-fundo); color: var(--cor-desabilitado-texto);',
                      css_da_home())
        self.assertIn('<button class="botao-principal" id="cat-nova-salvar" type="button" disabled>Salvar</button>', nova)
        # Excluir: ícone, o erro do bloqueio DENTRO do bloco de texto e os botões depois.
        excluir = bloco_folha('folha-cat-excluir')
        bloco = excluir[excluir.index('<div class="folha-titulos medio">'):excluir.index('<div class="folha-botoes">')]
        self.assertIn('id="folha-cat-excluir-texto"', bloco)
        self.assertIn('id="folha-cat-excluir-erro"', bloco)
        self.assertLess(excluir.index('folha-icone-apagar'), excluir.index('folha-titulos medio'))
        self.assertIn('width: 56px; height: 56px;', regra_css('.folha-icone-apagar'))

    def test_css_novo_so_com_variaveis(self):
        css = css_da_home()
        novo = css[css.index('/* ---------- Categorias (PRO-105'):]
        self.assertEqual(sorted(set(re.findall(r'#[0-9A-Fa-f]{3,8}\b', novo))), [])


@unittest.skipUnless(NODE, 'node não encontrado no PATH')
class TestCategoriasFuncoesPuras(unittest.TestCase):
    def test_proporcao_da_barra_e_a_maior(self):
        obtido = rodar_node(com_funcoes_puras("""
            const lista = [{ gasto: 612.4 }, { gasto: 117.8 }, { gasto: 20 }, { gasto: 0 }];
            const maior = maiorGastoCategorias(lista);
            console.log(JSON.stringify({
              maior,
              proporcoes: lista.map((c) => proporcaoDaBarraCategoria(c.gasto, maior)),
              grafite: lista.map((c) => barraEhMaior(c.gasto, maior)),
              semGasto: [proporcaoDaBarraCategoria(0, 0), proporcaoDaBarraCategoria(0, 100), barraEhMaior(0, 0), maiorGastoCategorias([]),
                         maiorGastoCategorias(undefined), maiorGastoCategorias([{ gasto: 0 }, { gasto: 0 }])],
              empate: [barraEhMaior(50, 50), barraEhMaior(50.004, 50), barraEhMaior(49.99, 50)],
              limites: [proporcaoDaBarraCategoria(80, 100), proporcaoDaBarraCategoria(100, 100), proporcaoDaBarraCategoria(120, 100),
                        proporcaoDaBarraCategoria(-5, 100), proporcaoDaBarraCategoria('x', 100), proporcaoDaBarraCategoria(5, null)],
            }));
        """))
        self.assertEqual(obtido['maior'], 612.4)
        self.assertEqual(obtido['proporcoes'][0], 1)                  # a maior ocupa o trilho todo
        self.assertAlmostEqual(obtido['proporcoes'][1], 117.8 / 612.4)
        self.assertAlmostEqual(obtido['proporcoes'][2], 20 / 612.4)
        self.assertEqual(obtido['proporcoes'][3], 0)                  # R$ 0,00: 0 (o CSS garante os 8px)
        self.assertEqual(obtido['grafite'], [True, False, False, False])
        self.assertEqual(obtido['semGasto'], [0, 0, False, 0, 0, 0])
        self.assertEqual(obtido['empate'], [True, True, False])
        self.assertEqual(obtido['limites'], [0.8, 1, 1, 0, 0, 0])

    def test_linha_tocavel_e_chave_da_linha(self):
        obtido = rodar_node(com_funcoes_puras("""
            const ativa = { id: 70, nome: 'Alimentação', gasto: 1, ativa: true };
            const excluida = { id: null, nome: 'Pets', gasto: 20, ativa: false };
            console.log(JSON.stringify({
              tocavel: [linhaTocavelCategoria(ativa), linhaTocavelCategoria(excluida), linhaTocavelCategoria({ id: 5, ativa: false }),
                        linhaTocavelCategoria({ id: null, ativa: true })],
              chaves: [chaveDaLinhaCategoria(ativa), chaveDaLinhaCategoria(excluida)],
            }));
        """))
        self.assertEqual(obtido['tocavel'], [True, False, False, False])
        self.assertEqual(obtido['chaves'], ['id:70', 'excluida:Pets'])

    def test_titulo_e_mensagem_do_limite(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              titulo: [tituloDoRanking('novembro'), tituloDoRanking('março'), tituloDoRanking('Dezembro')],
              limite: [mensagemLimiteCategorias(20), mensagemLimiteCategorias(15), mensagemLimiteCategorias(undefined),
                       mensagemLimiteCategorias(null), mensagemLimiteCategorias('x'), mensagemLimiteCategorias(0)],
            }));
        """))
        self.assertEqual(obtido['titulo'], ['Gastos Por Categoria em Novembro:', 'Gastos Por Categoria em Março:',
                                            'Gastos Por Categoria em Dezembro:'])
        self.assertEqual(obtido['limite'][0], 'Você já tem %d categorias. Exclua uma para criar outra.' % categorias.LIMITE_CATEGORIAS)
        self.assertEqual(obtido['limite'][1], 'Você já tem 15 categorias. Exclua uma para criar outra.')
        for texto in obtido['limite'][2:]:
            self.assertEqual(texto, 'Você já atingiu o limite de categorias. Exclua uma para criar outra.')

    def test_mes_do_titulo_igual_ao_da_home_com_o_ciclo_do_servidor(self):
        # Dia 11 de novembro com o fechamento no dia 5: a fatura vigente é a de dezembro (D4 da PRO-104). A home e o título
        # do ranking recebem o MESMO ano_mes do servidor e mostram o mesmo mês, sem olhar o calendário do aparelho.
        import calculos
        from datetime import datetime
        ano_mes = calculos.obter_ciclo_atual(5, datetime(2026, 11, 11, 12, 0, tzinfo=calculos.FUSO_BRASILIA))
        self.assertEqual(ano_mes, '2026-12')
        principal = codigo_principal(HOME)
        constantes = re.search(r'const MESES_COMPLETOS = \[[^\]]*\];', principal).group(0)
        indice = corpo_da_funcao(principal, 'function indiceMes')
        obtido = rodar_node(com_funcoes_puras(constantes + "\n" + indice + """
            const anoMes = ENTRADA.ano_mes;
            // A expressão da home (mostrarCabecalho) e a do ranking (desenharRanking), iguais às do código da página.
            console.log(JSON.stringify({
              home: 'Gastos em ' + MESES_COMPLETOS[indiceMes(anoMes)],
              ranking: tituloDoRanking(MESES_COMPLETOS[indiceMes(anoMes)]),
            }));
        """), {'ano_mes': ano_mes})
        self.assertEqual(obtido['home'], 'Gastos em dezembro')
        self.assertEqual(obtido['ranking'], 'Gastos Por Categoria em Dezembro:')

    def test_nome_vazio_e_salvar(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              erro: [erroDoNomeCategoria('', false), erroDoNomeCategoria('', true), erroDoNomeCategoria('   ', true),
                     erroDoNomeCategoria('Pets', true), erroDoNomeCategoria(' a ', true)],
              pode: [podeSalvarCategoria({ nome: '', salvando: false }), podeSalvarCategoria({ nome: '   ', salvando: false }),
                     podeSalvarCategoria({ nome: 'Pets', salvando: false }), podeSalvarCategoria({ nome: 'Pets', salvando: true }),
                     podeSalvarCategoria({ nome: ' x ', salvando: false })],
            }));
        """))
        self.assertEqual(obtido['erro'], [None, 'Preencha o nome para continuar', 'Preencha o nome para continuar', None, None])
        self.assertEqual(obtido['pode'], [False, False, True, False, True])

    def test_corpos_rotas_e_textos(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              post: montarCorpoNovaCategoria('  pets ', 'chave-1'), put: montarCorpoRenomearCategoria(' Bichos '),
              rotas: [rotaCategorias(), rotaCategorias(null), rotaCategorias(79), rotaCategorias('7/9')],
              logoCriar: textosTelaLogoCategoria('criar'), logoRenomear: textosTelaLogoCategoria('renomear'),
              excluir: textoConfirmarExcluirCategoria('Pets'),
            }));
        """))
        self.assertEqual(obtido['post'], {'nome': 'pets', 'chave': 'chave-1'})   # só nome e chave
        self.assertEqual(list(obtido['post'].keys()), ['nome', 'chave'])
        self.assertEqual(obtido['put'], {'nome': 'Bichos'})
        self.assertEqual(obtido['rotas'], ['/categorias', '/categorias', '/categorias/79', '/categorias/7%2F9'])
        self.assertEqual(obtido['logoCriar'], {'cabecalho': 'Nova Categoria', 'titulo': 'Criando a Categoria...',
                                               'texto': 'Ela já pode ser usada nos seus gastos.'})
        self.assertEqual(obtido['logoRenomear'], {'cabecalho': 'Editar Categoria', 'titulo': 'Salvando as Alterações...',
                                                  'texto': 'Os gastos antigos também mudam de nome.'})
        self.assertEqual(obtido['excluir'], {'negrito': 'Pets',
                                             'depois': ' deixa de existir. Os gastos que já entraram continuam no histórico.'})

    def test_decisao_por_status_das_tres_gravacoes(self):
        sessao = 'Sua sessão expirou. Feche e abra o app de novo.'
        c404 = 'Não encontramos o seu cadastro. Feche e abra o app de novo.'
        c409 = 'Seu cadastro está incompleto. Volte ao bot e informe o dia de fechamento da fatura.'
        salvar = 'Não foi possível salvar. Tente de novo.'
        excluir = 'Não foi possível excluir. Tente de novo.'
        casos = [
            # (operacao, resultado, esperado)
            ('criar', {'dados': {'id': 79}, 'status': 201}, {'acao': 'sucesso'}),
            ('criar', {'dados': {'id': 79, 'repetido': True}, 'status': 200}, {'acao': 'sucesso'}),
            ('renomear', {'dados': {'id': 79}, 'status': 200}, {'acao': 'sucesso'}),
            ('excluir', {'dados': {'id': 79, 'excluida': True}, 'status': 200}, {'acao': 'sucesso'}),
            # 404 no excluir conta como "já excluída"
            ('excluir', {'erro': 404, 'detalhe': 'categoria_inexistente'}, {'acao': 'sucesso'}),
            ('renomear', {'erro': 404, 'detalhe': 'categoria_inexistente'},
             {'acao': 'erro', 'mensagem': 'Esta categoria não existe mais.', 'voltarRanking': True}),
            ('criar', {'erro': 404, 'detalhe': 'categoria_inexistente'}, {'acao': 'erro', 'mensagem': c404}),
            # 422 de nome: embaixo do campo, em vermelho
            ('criar', {'erro': 422, 'detalhe': 'nome_repetido'},
             {'acao': 'erro', 'mensagem': 'Já existe uma categoria com esse nome.', 'noCampo': True, 'campoVermelho': True}),
            ('renomear', {'erro': 422, 'detalhe': 'nome_repetido'},
             {'acao': 'erro', 'mensagem': 'Já existe uma categoria com esse nome.', 'noCampo': True, 'campoVermelho': True}),
            ('criar', {'erro': 422, 'detalhe': 'nome_invalido'},
             {'acao': 'erro', 'mensagem': 'Preencha o nome para continuar', 'noCampo': True, 'campoVermelho': True}),
            ('renomear', {'erro': 422, 'detalhe': 'nome_invalido'},
             {'acao': 'erro', 'mensagem': 'Preencha o nome para continuar', 'noCampo': True, 'campoVermelho': True}),
            # limite: o número vem do ranking; o campo fica normal (o problema não é o nome)
            ('criar', {'erro': 422, 'detalhe': 'limite_categorias'},
             {'acao': 'erro', 'mensagem': 'Você já tem 20 categorias. Exclua uma para criar outra.', 'noCampo': True, 'campoVermelho': False}),
            ('renomear', {'erro': 422, 'detalhe': 'limite_categorias'}, {'acao': 'erro', 'mensagem': salvar}),
            # excluir bloqueado: erro embaixo do texto, só o Cancelar
            ('excluir', {'erro': 422, 'detalhe': 'categoria_com_recorrentes'},
             {'acao': 'erro', 'bloqueado': True,
              'mensagem': 'Essa categoria tem gastos recorrentes. Mude a categoria deles em Recorrentes antes de excluir.'}),
            ('excluir', {'erro': 422, 'detalhe': 'ultima_categoria'},
             {'acao': 'erro', 'bloqueado': True, 'mensagem': 'Você precisa de pelo menos uma categoria.'}),
            ('excluir', {'erro': 422, 'detalhe': 'nome_repetido'}, {'acao': 'erro', 'mensagem': excluir}),
            # o resto: as mensagens de sempre
            ('criar', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
            ('excluir', {'erro': 401}, {'acao': 'erro', 'mensagem': sessao}),
            ('renomear', {'erro': 404, 'detalhe': 'usuario_nao_cadastrado'}, {'acao': 'erro', 'mensagem': c404}),
            ('criar', {'erro': 409, 'detalhe': 'cadastro_incompleto'}, {'acao': 'erro', 'mensagem': c409}),
            ('criar', {'erro': 422, 'detalhe': 'chave_invalida'}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 503, 'detalhe': 'banco_ocupado'}, {'acao': 'erro', 'mensagem': salvar}),
            ('renomear', {'erro': 0}, {'acao': 'erro', 'mensagem': salvar}),
            ('criar', {'erro': 500, 'detalhe': 'erro_interno'}, {'acao': 'erro', 'mensagem': salvar}),
            ('excluir', {'erro': 503, 'detalhe': 'banco_ocupado'}, {'acao': 'erro', 'mensagem': excluir}),
            ('excluir', {'erro': 0}, {'acao': 'erro', 'mensagem': excluir}),
            ('excluir', {'erro': 500}, {'acao': 'erro', 'mensagem': excluir}),
        ]
        obtido = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(ENTRADA.map(([op, r]) => decisaoDaGravacaoCategoria(op, r, 20))));"
        ), [[operacao, resultado] for operacao, resultado, _ in casos])
        for (operacao, resultado, esperado), recebido in zip(casos, obtido):
            with self.subTest(operacao=operacao, resultado=resultado):
                self.assertEqual(recebido, esperado)
        # O texto cru do servidor nunca aparece na mensagem.
        for recebido in obtido:
            for codigo in ('nome_repetido', 'categoria_inexistente', 'banco_ocupado', 'limite_categorias', 'ultima_categoria'):
                self.assertNotIn(codigo, recebido.get('mensagem', ''))
        # O limite da mensagem é o do ranking (não um 20 fixo): com 15, a mensagem diz 15.
        quinze = rodar_node(com_funcoes_puras(
            "console.log(JSON.stringify(decisaoDaGravacaoCategoria('criar', { erro: 422, detalhe: 'limite_categorias' }, 15)));"
        ))
        self.assertEqual(quinze['mensagem'], 'Você já tem 15 categorias. Exclua uma para criar outra.')

    def test_codigos_e_mensagens_tratados_sao_os_do_servidor(self):
        # Todo código que a decisão trata existe no categorias.py/api; as constantes do servidor valem o que a tela usa.
        puro = codigo_puro(HOME)
        tratados = set(re.findall(r'resultado\.detalhe === "(\w+)"', corpo_da_funcao(puro, 'function decisaoDaGravacaoCategoria')))
        self.assertEqual(tratados, {'categoria_inexistente', 'categoria_com_recorrentes', 'ultima_categoria', 'nome_invalido',
                                    'nome_repetido', 'limite_categorias'})
        fonte = open(os.path.join(RAIZ, 'categorias.py'), encoding='utf-8').read()
        for codigo in tratados:
            with self.subTest(codigo=codigo):
                self.assertIn("ErroRegistro('%s')" % codigo, fonte)
        self.assertEqual(categorias.LIMITE_CATEGORIAS, 20)    # o valor que a nota cita; a tela lê o do ranking

    def test_novas_tentativas_do_criar_repetem_o_mesmo_corpo_e_a_mesma_chave(self):
        obtido = rodar_node(com_funcoes_puras("""
            (async () => {
              const corpo = montarCorpoNovaCategoria('Pets', 'chave-da-folha');
              const enviados = [];
              const respostas = [{ erro: 503, detalhe: 'banco_ocupado' }, { erro: 0 }, { dados: { id: 79, repetido: false }, status: 201 }];
              const resultado = await gravarComTentativas(async (c) => { enviados.push(JSON.stringify(c)); return respostas.shift(); }, corpo, async () => {});
              // 422 não é repetido sozinho.
              const outros = [];
              await gravarComTentativas(async (c) => { outros.push(1); return { erro: 422, detalhe: 'nome_repetido' }; }, corpo, async () => {});
              console.log(JSON.stringify({ iguais: enviados.every((x) => x === enviados[0]), n: enviados.length,
                                           status: resultado.status, corpo: JSON.parse(enviados[0]), tentativas422: outros.length }));
            })();
        """))
        self.assertEqual(obtido, {'iguais': True, 'n': 3, 'status': 201, 'corpo': {'nome': 'Pets', 'chave': 'chave-da-folha'},
                                  'tentativas422': 1})

# ----------------------------------------------------------------------------
# PRO-108: telas das assinaturas, selo da home, folha Minha Assinatura e modo somente leitura
# ----------------------------------------------------------------------------

# O "navegador de mentira": uma página mínima (elementos por id, fetch, Telegram.WebApp, armazenamento) para rodar o
# <script> principal de verdade no node, sem rede e sem .env. O relógio é de mentira: setTimeout dispara na hora e soma a
# espera pedida ao performance.now(), então "30 segundos" de espera passam em milissegundos.
HARNESS_PAGINA = r"""
const __log = { fetch: [], alertas: [], confirms: [], faturas: [], ligacoes: [], telaCheia: 0, status: 0, atalhos: [] };
let __virtual = 0;
const __stReal = setTimeout;
// Esperas curtas disparam na hora (e somam ao relógio); as de 10 s ou mais (o aviso do topo, o tempo limite do fetch) nunca disparam.
globalThis.setTimeout = (f, ms) => { if (ms < 10000) setImmediate(() => { __virtual += (ms || 0); f(); }); return { falso: true }; };
globalThis.clearTimeout = () => {};
Object.defineProperty(globalThis, 'performance', { value: { now: () => __virtual }, configurable: true });
// Espera a página ficar parada: nenhuma chamada nova ao fetch por 40 voltas seguidas do laço de eventos.
const __ocioso = async () => {
  let estavel = 0; let ultimo = -1;
  for (let i = 0; i < 6000 && estavel < 40; i++) {
    await new Promise((r) => setImmediate(r));
    if (__log.fetch.length === ultimo) estavel++; else { estavel = 0; ultimo = __log.fetch.length; }
  }
};

function __texto(t) { const n = __criarEl('#texto', {}); n.textContent = t; return n; }
function __criarEl(tag, info) {
  info = info || {};
  const el = { tagName: String(tag).toUpperCase(), id: info.id || '', _classe: info.classe || '', _hidden: !!info.hidden, _texto: '',
               children: [], dataset: {}, style: { setProperty() {} }, listeners: {}, disabled: false, value: '', attrs: {},
               scrollTop: 0, scrollLeft: 0, offsetWidth: 0, scrollHeight: 0, tabIndex: 0, type: '', placeholder: '' };
  Object.defineProperty(el, 'hidden', { get() { return this._hidden; }, set(v) { this._hidden = !!v; } });
  Object.defineProperty(el, 'className', { get() { return this._classe; }, set(v) { this._classe = String(v); } });
  Object.defineProperty(el, 'textContent', {
    get() { return this.children.length ? this.children.map((c) => c.textContent).join('') : this._texto; },
    set(v) { this._texto = String(v); this.children = []; },
  });
  el.classList = {
    contains: (c) => el._classe.split(/\s+/).includes(c),
    add: (...cs) => cs.forEach((c) => { if (!el.classList.contains(c)) el._classe = (el._classe + ' ' + c).trim(); }),
    remove: (...cs) => { el._classe = el._classe.split(/\s+/).filter((x) => x && !cs.includes(x)).join(' '); },
    toggle: (c, forca) => { const quer = forca === undefined ? !el.classList.contains(c) : !!forca;
                            if (quer) el.classList.add(c); else el.classList.remove(c); return quer; },
  };
  el.addEventListener = (tipo, f) => { (el.listeners[tipo] = el.listeners[tipo] || []).push(f); };
  el.click = () => { (el.listeners.click || []).forEach((f) => f({ target: el, preventDefault() {}, key: '' })); };
  el.setAttribute = (n, v) => { el.attrs[n] = String(v); };
  el.getAttribute = (n) => el.attrs[n];
  el.append = (...ns) => { ns.forEach((n) => el.children.push(typeof n === 'string' ? __texto(n) : n)); };
  el.replaceChildren = (...ns) => { el.children = []; el._texto = ''; el.append(...ns); };
  el.querySelector = () => { el._q = el._q || __criarEl('span', {}); return el._q; };
  el.querySelectorAll = () => [];
  el.closest = () => el;
  el.focus = () => {}; el.blur = () => {};
  el.getBoundingClientRect = () => ({ top: 0 });
  el.cloneNode = () => __criarEl(tag, {});
  if (tag === 'template') el.content = { firstElementChild: __criarEl('button', {}) };
  return el;
}

const __elementos = {};
for (const [id, info] of Object.entries(ENTRADA.ids)) __elementos[id] = __criarEl(info.tag, Object.assign({ id }, info));
const __mains = Object.values(__elementos).filter((e) => e.tagName === 'MAIN');
const __folhas = Object.values(__elementos).filter((e) => e.classList.contains('folha-camada'));
const __corpo = __criarEl('body', {});
globalThis.document = {
  getElementById: (id) => { if (!__elementos[id]) throw new Error('id inexistente: ' + id); return __elementos[id]; },
  querySelectorAll: (seletor) => (seletor === 'main' ? __mains : seletor === '.folha-camada' ? __folhas : []),
  createElement: (tag) => __criarEl(tag, {}), createElementNS: (ns, tag) => __criarEl(tag, {}),
  createTextNode: (t) => __texto(t),
  body: __corpo, documentElement: __criarEl('html', {}), activeElement: null,
};

// Armazenamento: 'ok' (guarda de verdade), 'quebrado' (toda leitura e escrita lança), 'sem-escrita' (lê, mas a escrita lança) ou 'sem' (não existe).
const __hoje = diaDeBrasilia(Date.now(), 'America/Sao_Paulo');
function __armazenamento(config, comCallback) {
  const modo = (config && config.modo) || 'ok';
  if (modo === 'sem') return undefined;
  if (modo === 'quebrado') return { getItem() { throw new Error('sem acesso'); }, setItem() { throw new Error('sem acesso'); } };
  if (modo === 'sem-escrita') return { dados: {}, getItem() { return null; }, setItem() { throw new Error('sem escrita'); } };
  const dados = Object.assign({}, config && config.hoje ? { expirada_vista_em: __hoje } : {}, (config && config.dados) || {});
  if (comCallback) return { dados, getItem: (k, cb) => cb(null, k in dados ? dados[k] : null), setItem: (k, v, cb) => { dados[k] = String(v); cb(null, true); } };
  return { dados, getItem: (k) => (k in dados ? dados[k] : null), setItem: (k, v) => { dados[k] = String(v); } };
}
const __local = __armazenamento(ENTRADA.local, false);
const __nuvem = __armazenamento(ENTRADA.nuvem, true);

// Rotas de mentira: {"GET /me": {status, body, headers} | [respostas em sequência (a última se repete)]}
const __rotas = ENTRADA.rotas || {};
globalThis.fetch = async (rota, opcoes) => {
  const metodo = (opcoes && opcoes.method) || 'GET';
  __log.fetch.push({ metodo, rota, corpo: opcoes && opcoes.body ? JSON.parse(opcoes.body) : null });
  let r = __rotas[metodo + ' ' + rota];
  if (Array.isArray(r)) r = r.length > 1 ? r.shift() : r[0];
  if (typeof r === 'function') r = r(opcoes);
  if (!r) r = { status: 599, body: { detail: 'rota_de_mentira_nao_definida' } };
  return { ok: r.status < 400, status: r.status, headers: { get: (n) => (r.headers && r.headers[n]) || null }, json: async () => r.body };
};
const __chamadas = () => __log.fetch.map((c) => c.metodo + ' ' + c.rota);

let __voltar = null;
let __ultimoConfirm = null;
let __ultimoAlerta = null;
let __ultimaFatura = null;
const __webapp = {
  initData: 'dados-de-mentira', initDataUnsafe: { start_param: ENTRADA.startParam || undefined },
  ready() {}, expand() {}, setHeaderColor() {}, setBackgroundColor() {}, onEvent(nome, f) { (__eventos[nome] = __eventos[nome] || []).push(f); },
  viewportHeight: 800, viewportStableHeight: 800,
  BackButton: { show() { __log.ligacoes.push('show'); }, hide() { __log.ligacoes.push('hide'); }, onClick(f) { __voltar = f; } },
  showAlert(msg, cb) { __log.alertas.push(msg); __ultimoAlerta = { msg, cb }; },
  showConfirm(msg, cb) { __log.confirms.push(msg); __ultimoConfirm = { msg, cb }; },
  openInvoice(link, cb) { __log.faturas.push(link); __ultimaFatura = { link, cb }; },
};
if (__nuvem) __webapp.CloudStorage = __nuvem;
// PRO-112: tela cheia e atalho. ENTRADA.telegram = { platform, versao, emTelaCheia, tela: [largura, altura], atalho: { status, abrir } }.
// Sem ENTRADA.telegram, o Telegram de mentira não tem plataforma, versão, tela cheia nem atalho (como os testes antigos esperam).
// atalho.status: o texto que o callback devolve (sem a chave: nunca responde); atalho.abrir: abre uma "folha" ou "tela" durante a espera.
const __eventos = {};
const __tgInfo = ENTRADA.telegram || null;
if (__tgInfo) {
  __webapp.platform = __tgInfo.platform;
  __webapp.isFullscreen = !!__tgInfo.emTelaCheia;
  if (__tgInfo.versao !== undefined) {
    __webapp.isVersionAtLeast = (v) => __tgInfo.versao >= parseFloat(v);
    if (__tgInfo.semTelaCheia !== true) __webapp.requestFullscreen = () => { __log.telaCheia++; };
    if (__tgInfo.atalho) {
      __webapp.checkHomeScreenStatus = (cb) => {
        __log.status++;
        if (ENTRADA.telegram.atalho.status !== undefined) cb(ENTRADA.telegram.atalho.status);
        if (ENTRADA.telegram.atalho.abrir === 'folha') __elementos['folha-minha'].hidden = false;
        if (ENTRADA.telegram.atalho.abrir === 'tela') __elementos['tela'].hidden = true;
      };
      __webapp.addToHomeScreen = () => { __log.atalhos.push(__local && __local.dados ? (__local.dados.atalho_tela_inicial_perguntado || null) : 'sem-local'); };
    }
  }
}
globalThis.window = { Telegram: { WebApp: __webapp }, location: { search: ENTRADA.busca || '' }, scrollY: 0, innerHeight: 800,
                      scrollTo() {}, addEventListener() {}, matchMedia: () => ({ matches: false }),
                      screen: (__tgInfo && __tgInfo.tela) ? { width: __tgInfo.tela[0], height: __tgInfo.tela[1] } : undefined,
                      alert() {}, confirm: () => true };
if (__local) globalThis.window.localStorage = __local;
else Object.defineProperty(globalThis.window, 'localStorage', { get() { throw new Error('sem localStorage'); } });
const __visivel = (id) => !document.getElementById(id).hidden;
"""


def ids_da_home():
    """{id: {tag, classe, hidden}} de todos os elementos com id do HTML (antes do bloco de funções puras)."""
    html = HOME[:HOME.index('INICIO FUNCOES PURAS')]
    ids = {}
    for m in re.finditer(r'<(\w+)\b([^>]*?)>', html):
        atributos = m.group(2)
        achou = re.search(r'\bid="([^"]+)"', atributos)
        if not achou:
            continue
        sem_aspas = re.sub(r'"[^"]*"', '""', atributos)
        classe = re.search(r'\bclass="([^"]*)"', atributos)
        ids[achou.group(1)] = {'tag': m.group(1), 'classe': classe.group(1) if classe else '',
                               'hidden': bool(re.search(r'(?<![\w-])hidden(?![\w-])', sem_aspas))}
    return ids


def rodar_pagina(entrada, cenario):
    """Roda o <script> principal da home num navegador de mentira e depois o 'cenario' (JS, async, com console.log do JSON)."""
    entrada = dict(entrada)
    entrada['ids'] = ids_da_home()
    programa = (
        "const fs = require('fs');\n"
        "const ENTRADA = JSON.parse(fs.readFileSync(process.argv[2], 'utf-8'));\n"
        + codigo_puro(HOME) + "\n" + HARNESS_PAGINA + "\n" + codigo_principal(HOME) + "\n"
        + "(async () => {\n" + cenario + "\n})().catch((e) => { console.error(e && e.stack || e); process.exit(1); });\n"
    )
    return rodar_node(programa, entrada)


# --- Corpos de mentira do servidor (o formato do montar_bloco_assinatura do assinaturas.py) ---
def bloco_assinatura(**campos):
    base = {
        'ligada': True, 'estado': 'teste', 'inicio': '2026-11-10', 'fim': '2026-11-20', 'renovacao': False,
        'teste_disponivel': False, 'teste_previsto': None, 'preco': {'stars': 150, 'reais_aproximado': 19},
        'limite_ia_dia': 17, 'uso_ia_hoje': 0, 'selo': 'gratis', 'selo_tocavel': True, 'dias_restantes': 7,
        'valor_stars': None, 'proxima_renovacao': None, 'renovacao_cancelada': False, 'vale_ate': None, 'modo_leitura': False,
    }
    base.update(campos)
    return base


BLOCO_TESTE = bloco_assinatura()
BLOCO_PRO = bloco_assinatura(estado='pro', inicio='2026-11-12', fim='2026-12-12', renovacao=True, selo='pro', dias_restantes=30,
                             valor_stars=150, proxima_renovacao='2026-12-12', vale_ate='2026-12-12')
BLOCO_CANCELADO = dict(BLOCO_PRO, proxima_renovacao=None, renovacao_cancelada=True)
BLOCO_DADO = dict(BLOCO_PRO, valor_stars=None, proxima_renovacao=None, renovacao=False, vale_ate='2026-12-12')
BLOCO_ADMIN = bloco_assinatura(estado='pro', inicio=None, fim=None, selo='pro', selo_tocavel=False, dias_restantes=None,
                               limite_ia_dia=None, vale_ate=None)
BLOCO_TESTE_VENCIDO = bloco_assinatura(estado='teste_vencido', inicio='2026-11-01', fim='2026-11-11', selo='assinar', dias_restantes=None,
                                       modo_leitura=True, teste_disponivel=False)
BLOCO_PRO_VENCIDO = bloco_assinatura(estado='pro_vencido', inicio='2026-10-01', fim='2026-11-01', selo='assinar', dias_restantes=None,
                                     modo_leitura=True)
BLOCO_SEM_PLANO = bloco_assinatura(estado='sem_plano', inicio=None, fim=None, selo=None, selo_tocavel=False, dias_restantes=None,
                                   teste_disponivel=True, teste_previsto={'inicio': '2026-11-12', 'fim': '2026-11-22'})


def rotas_da_home(assinatura='ausente', extras=None):
    """As 4 rotas da abertura. assinatura='ausente' = o /me sem a chave (a chave desligada); None = "assinatura": null."""
    me = {'user_id': 1, 'nome': 'Ygor Lima'}
    if assinatura != 'ausente':
        me['assinatura'] = assinatura
    rotas = {
        'GET /me': {'status': 200, 'body': me},
        'GET /resumo': {'status': 200, 'body': {'ano_mes': '2026-11', 'total_gasto': 100.0, 'sobra_mes': 900.0, 'renda': 1000.0,
                                                 'total_receitas': 0}},
        'GET /historico': {'status': 200, 'body': {'meses': []}},
        'GET /movimentacoes/recentes': {'status': 200, 'body': {'movimentacoes': []}},
    }
    rotas.update(extras or {})
    return rotas


ABERTURA = ['GET /me', 'GET /resumo', 'GET /historico', 'GET /movimentacoes/recentes']


@unittest.skipUnless(NODE, 'node não encontrado')
class TestAssinaturasNaPagina(unittest.TestCase):
    """O <script> principal rodando de verdade (navegador de mentira): abertura, selo, folhas, modo leitura e fluxos."""

    # ---------------------------- abertura ----------------------------
    def test_sem_a_chave_ou_desligada_o_app_fica_como_hoje(self):
        desligada = bloco_assinatura(ligada=False, selo='pro', modo_leitura=True, estado='teste_vencido')
        for nome, bloco in (('ausente', 'ausente'), ('nulo', None), ('ligada false', desligada)):
            with self.subTest(bloco=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco), 'busca': '?tela=assinatura'}, """
                    await __ocioso();
                    console.log(JSON.stringify({ chamadas: __chamadas(), selo: __visivel('selo-linha'), tela: __visivel('tela'),
                      logo: __visivel('tela-logo'), aviso: __visivel('aviso'), expirada: __visivel('expirada'),
                      assinaturas: __visivel('assinaturas'), pausadoHome: __visivel('pausado-home'), pausadoRec: __visivel('rec-pausado'),
                      dica: $('cat-dica').textContent, leitura: assEstado.leitura }));
                """)
                self.assertEqual(obtido['chamadas'], ABERTURA)   # as mesmas 4 chamadas, nenhuma nova
                self.assertTrue(obtido['tela'])
                self.assertFalse(obtido['logo'])
                self.assertFalse(obtido['aviso'])
                for chave in ('selo', 'expirada', 'assinaturas', 'pausadoHome', 'pausadoRec', 'leitura'):
                    self.assertFalse(obtido[chave], chave)
                self.assertEqual(obtido['dica'], 'Clique Nas Barras Para Visualizar e Editar as Categorias')

    def test_selo_sai_so_do_campo_selo(self):
        casos = [
            ('gratis', bloco_assinatura(), 'Grátis', True, 'gratis'),
            ('pro', BLOCO_PRO, 'Pro', True, 'pro'),
            ('assinar', BLOCO_PRO_VENCIDO, 'Assinar', True, 'assinar'),
            ('admin', BLOCO_ADMIN, 'Pro', False, 'pro'),
        ]
        for nome, bloco, texto, tocavel, variante in casos:
            with self.subTest(selo=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco), 'local': {'hoje': True}}, """
                    await __ocioso();
                    console.log(JSON.stringify({ linha: __visivel('selo-linha'), texto: $('selo-texto').textContent,
                      classe: $('selo').className, seta: __visivel('selo-seta') }));
                """)
                self.assertTrue(obtido['linha'])
                self.assertEqual(obtido['texto'], texto)
                self.assertEqual(obtido['classe'].split(), ['selo', variante] + (['tocavel'] if tocavel else []))
                self.assertEqual(obtido['seta'], nome == 'assinar')
        # Nulo, ausente ou qualquer outro valor: sem selo (a linha some), mesmo com o estado dizendo outra coisa.
        for nome, bloco in (('nulo', bloco_assinatura(selo=None)), ('free', bloco_assinatura(selo='free')),
                            ('admin', bloco_assinatura(selo='admin')), ('teste', bloco_assinatura(selo='teste')),
                            ('maiúsculo', bloco_assinatura(selo='PRO'))):
            with self.subTest(sem_selo=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco)}, """
                    await __ocioso();
                    console.log(JSON.stringify({ linha: __visivel('selo-linha'), tela: __visivel('tela') }));
                """)
                self.assertFalse(obtido['linha'])
                self.assertTrue(obtido['tela'])   # nunca vira aviso de erro
        sem_chave = bloco_assinatura()
        del sem_chave['selo']
        obtido = rodar_pagina({'rotas': rotas_da_home(sem_chave)}, "await __ocioso(); console.log(JSON.stringify({ linha: __visivel('selo-linha') }));")
        self.assertFalse(obtido['linha'])

    def test_toque_no_selo_abre_a_folha_sem_chamada_nova_e_so_se_for_tocavel(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_TESTE)}, """
            await __ocioso();
            const antes = __chamadas().length;
            $('selo').click();
            await __ocioso();
            console.log(JSON.stringify({ antes, depois: __chamadas().length, minha: __visivel('folha-minha'), plano: __visivel('folha-plano'),
              logo: __visivel('tela-logo'), situacao: $('min-situacao').textContent, tela: __visivel('tela') }));
        """)
        self.assertEqual(obtido['antes'], 4)
        self.assertEqual(obtido['depois'], 4)   # a folha abre com o que a home já tem
        self.assertTrue(obtido['minha'])
        self.assertFalse(obtido['plano'])
        self.assertFalse(obtido['logo'])
        self.assertTrue(obtido['tela'])         # por cima da home
        self.assertEqual(obtido['situacao'], 'Seu Teste Gratuito Está Ativo. Faltam 7 Dias.')
        # Admin: selo "Pro", e o toque não faz nada (sem estado pressionado, sem folha, sem aviso).
        for nome, bloco in (('admin', BLOCO_ADMIN), ('sem selo_tocavel', {k: v for k, v in BLOCO_PRO.items() if k != 'selo_tocavel'}),
                            ('selo_tocavel nulo', dict(BLOCO_PRO, selo_tocavel=None)), ('selo_tocavel "true"', dict(BLOCO_PRO, selo_tocavel='true'))):
            with self.subTest(sem_toque=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco)}, """
                    await __ocioso();
                    $('selo').click();
                    await __ocioso();
                    console.log(JSON.stringify({ minha: __visivel('folha-minha'), plano: __visivel('folha-plano'), aviso: __visivel('aviso-topo'),
                      classe: $('selo').className, chamadas: __chamadas().length }));
                """)
                self.assertFalse(obtido['minha'])
                self.assertFalse(obtido['plano'])
                self.assertFalse(obtido['aviso'])
                self.assertNotIn('tocavel', obtido['classe'].split())
                self.assertNotIn('tocado', obtido['classe'].split())
                self.assertEqual(obtido['chamadas'], 4)

    def test_folha_minha_assinatura_nos_quatro_estados(self):
        casos = [
            ('gratis', BLOCO_TESTE, 'Seu Teste Gratuito Está Ativo. Faltam 7 Dias.',
             [['Plano:', 'Grátis'], ['Início do Teste:', '10/11/2026'], ['Fim do Teste:', '20/11/2026']],
             'Assinar o Plano Pro', False, True, False),
            ('pro-ativo', BLOCO_PRO, 'Obrigado Por Assinar o L1d3r! Seu Plano Pro Está Ativo.',
             [['Plano:', 'Pro'], ['Valor:', '150 Stars por mês'], ['Próxima Renovação:', '12/12/2026']],
             'Cancelar Renovação', True, True, True),
            ('renovacao-cancelada', BLOCO_CANCELADO, 'Seu Plano Pro Continua Ativo Até o Fim do Período Pago. A Renovação Foi Cancelada.',
             [['Plano:', 'Pro'], ['Vale Até:', '12/12/2026']], 'Reativar Renovação', False, True, False),
            ('pro-dado', BLOCO_DADO, 'Seu Plano Pro Está Ativo Até a Data Abaixo.',
             [['Plano:', 'Pro'], ['Vale Até:', '12/12/2026']], 'Fechar', False, False, False),
        ]
        for nome, bloco, situacao, linhas, botao, claro, fechar, termos in casos:
            with self.subTest(estado=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco)}, """
                    await __ocioso();
                    $('selo').click();
                    const linhas = []; const caixa = $('min-linhas').children.filter((c) => c.className === 'ass-linha');
                    caixa.forEach((l) => linhas.push(l.children.map((x) => x.textContent)));
                    console.log(JSON.stringify({ situacao: $('min-situacao').textContent, linhas, botao: $('min-principal').textContent,
                      claro: $('min-principal').classList.contains('claro'), fechar: __visivel('min-fechar'), termos: __visivel('min-termos-bloco'),
                      apoio: __visivel('min-apoio') ? $('min-apoio').textContent : '' }));
                """)
                self.assertEqual(obtido['situacao'], situacao)
                self.assertEqual(obtido['linhas'], linhas)
                self.assertEqual(obtido['botao'], botao)
                self.assertEqual(obtido['claro'], claro)
                self.assertEqual(obtido['fechar'], fechar)
                self.assertEqual(obtido['termos'], termos)
                if nome == 'pro-ativo':
                    self.assertEqual(obtido['apoio'], 'A Renovação Usa as Stars Que Estão na Sua Conta. Você Pode Cancelar Quando Quiser.')

    def test_textos_dos_dias_na_folha(self):
        for dias, esperado in ((7, 'Faltam 7 Dias.'), (2, 'Faltam 2 Dias.'), (1, 'Falta 1 Dia.'), (0, 'Termina Hoje.')):
            with self.subTest(dias=dias):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco_assinatura(dias_restantes=dias))}, """
                    await __ocioso(); $('selo').click();
                    console.log(JSON.stringify({ s: $('min-situacao').textContent }));
                """)
                self.assertEqual(obtido['s'], 'Seu Teste Gratuito Está Ativo. ' + esperado)

    def test_o_valor_da_folha_e_o_valor_stars_nunca_o_preco(self):
        bloco = dict(BLOCO_PRO, valor_stars=100, preco={'stars': 150, 'reais_aproximado': 19})
        obtido = rodar_pagina({'rotas': rotas_da_home(bloco)}, """
            await __ocioso(); $('selo').click();
            console.log(JSON.stringify({ v: $('min-linhas').children.filter((c) => c.className === 'ass-linha').map((l) => l.children[1].textContent) }));
        """)
        self.assertIn('100 Stars por mês', obtido['v'])
        self.assertNotIn('150 Stars por mês', obtido['v'])

    # ---------------------------- cancelar e reativar ----------------------------
    def test_cancelar_so_depois_do_showconfirm(self):
        rotas = rotas_da_home(BLOCO_PRO, {'POST /assinaturas/renovacao': {'status': 200, 'body': BLOCO_CANCELADO}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso(); $('selo').click();
            $('min-principal').click();
            const pedido = { confirms: __log.confirms.slice(), posts: __chamadas().filter((c) => c.startsWith('POST')).length };
            __ultimoConfirm.cb(false);                      // cancelou no alerta: nada acontece
            await __ocioso();
            const aposNao = { posts: __chamadas().filter((c) => c.startsWith('POST')).length, botao: $('min-principal').textContent };
            $('min-principal').click();
            __ultimoConfirm.cb(true);
            await __ocioso();
            const posts = __log.fetch.filter((c) => c.metodo === 'POST');
            console.log(JSON.stringify({ pedido, aposNao, posts, botao: $('min-principal').textContent, situacao: $('min-situacao').textContent,
              selo: $('selo-texto').textContent, logo: __visivel('tela-logo'), minha: __visivel('folha-minha') }));
        """)
        self.assertEqual(obtido['pedido']['confirms'], ['Cancelar a renovação? Seu Plano Pro continua ativo até 12/12/2026.'])
        self.assertEqual(obtido['pedido']['posts'], 0)        # nada foi enviado antes da confirmação
        self.assertEqual(obtido['aposNao']['posts'], 0)
        self.assertEqual(obtido['aposNao']['botao'], 'Cancelar Renovação')
        self.assertEqual(obtido['posts'], [{'metodo': 'POST', 'rota': '/assinaturas/renovacao', 'corpo': {'cancelar': True}}])
        self.assertEqual(obtido['botao'], 'Reativar Renovação')   # a folha passa para a Renovação Cancelada com a resposta
        self.assertTrue(obtido['situacao'].endswith('A Renovação Foi Cancelada.'))
        self.assertEqual(obtido['selo'], 'Pro')
        self.assertFalse(obtido['logo'])                      # sem a tela da logo
        self.assertTrue(obtido['minha'])

    def test_erro_ao_cancelar_mostra_o_alerta_e_a_folha_fica(self):
        for status, corpo in ((502, {'detail': 'telegram_indisponivel'}), (0, None)):
            with self.subTest(status=status):
                rota = {'status': status, 'body': corpo} if status else {'status': 599, 'body': {}}
                obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_PRO, {'POST /assinaturas/renovacao': rota})}, """
                    await __ocioso(); $('selo').click(); $('min-principal').click(); __ultimoConfirm.cb(true);
                    await __ocioso();
                    console.log(JSON.stringify({ alertas: __log.alertas, botao: $('min-principal').textContent, minha: __visivel('folha-minha') }));
                """)
                self.assertEqual(obtido['alertas'], ['Não foi possível cancelar agora. Tente de novo.'])
                self.assertEqual(obtido['botao'], 'Cancelar Renovação')
                self.assertTrue(obtido['minha'])

    def test_reativar_confirma_e_volta_ao_pro_ativo(self):
        rotas = rotas_da_home(BLOCO_CANCELADO, {'POST /assinaturas/renovacao': {'status': 200, 'body': BLOCO_PRO}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso(); $('selo').click();
            $('min-principal').click();
            const confirms = __log.confirms.slice();
            const antes = __log.fetch.filter((c) => c.metodo === 'POST').length;
            __ultimoConfirm.cb(true);
            await __ocioso();
            console.log(JSON.stringify({ confirms, antes, posts: __log.fetch.filter((c) => c.metodo === 'POST'),
              botao: $('min-principal').textContent, linhas: $('min-linhas').children.filter((c) => c.className === 'ass-linha').map((l) => l.children[0].textContent) }));
        """)
        self.assertEqual(obtido['confirms'], ['Reativar a renovação? Nada é cobrado agora; a próxima cobrança segue a data de sempre.'])
        self.assertEqual(obtido['antes'], 0)
        self.assertEqual(obtido['posts'], [{'metodo': 'POST', 'rota': '/assinaturas/renovacao', 'corpo': {'cancelar': False}}])
        self.assertEqual(obtido['botao'], 'Cancelar Renovação')
        self.assertIn('Próxima Renovação:', obtido['linhas'])

    def test_reativacao_recusada_leva_a_folha_plano_depois_do_alerta(self):
        rotas = rotas_da_home(BLOCO_CANCELADO, {'POST /assinaturas/renovacao': {'status': 409, 'body': {'detail': 'reativacao_recusada'}}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso(); $('selo').click(); $('min-principal').click(); __ultimoConfirm.cb(true);
            await __ocioso();
            const antes = { alertas: __log.alertas.slice(), plano: __visivel('folha-plano'), minha: __visivel('folha-minha') };
            __ultimoAlerta.cb();                             // tocou em OK no alerta
            console.log(JSON.stringify({ antes, plano: __visivel('folha-plano'), minha: __visivel('folha-minha'),
              preco: $('plano-preco-stars').textContent }));
        """)
        self.assertEqual(obtido['antes']['alertas'], ['Não foi possível reativar por aqui. Você pode assinar de novo sem perder nenhum dia.'])
        self.assertFalse(obtido['antes']['plano'])           # só depois do OK
        self.assertTrue(obtido['plano'])
        self.assertFalse(obtido['minha'])
        self.assertEqual(obtido['preco'], '150 Stars')

    # ---------------------------- tela Expirada (uma vez por dia) ----------------------------
    def test_expirada_uma_vez_por_dia_com_o_armazenamento_falso(self):
        cenario = """
            await __ocioso();
            console.log(JSON.stringify({ chamadas: __chamadas(), expirada: __visivel('expirada'), tela: __visivel('tela'),
              logo: __visivel('tela-logo'), pausado: __visivel('pausado-home'), selo: $('selo-texto').textContent,
              hoje: __hoje, nuvem: __nuvem && __nuvem.dados ? __nuvem.dados.expirada_vista_em || null : null,
              leitura: assEstado.leitura }));
        """
        base = {'rotas': rotas_da_home(BLOCO_TESTE_VENCIDO)}
        # Não vista: a Expirada aparece, a home fica por baixo, e o dia de hoje é guardado.
        obtido = rodar_pagina(base, cenario)
        self.assertTrue(obtido['expirada'])
        self.assertFalse(obtido['tela'])
        self.assertFalse(obtido['logo'])
        self.assertEqual(obtido['chamadas'], ABERTURA)
        self.assertEqual(obtido['nuvem'], obtido['hoje'])
        # Já vista hoje (no aparelho OU na nuvem): direto para a home em modo leitura.
        for nome, extra in (('localStorage', {'local': {'hoje': True}}), ('CloudStorage', {'nuvem': {'hoje': True}}),
                            ('ontem', None)):
            with self.subTest(vista_em=nome):
                entrada = dict(base, **(extra or {'local': {'dados': {'expirada_vista_em': '2000-01-01'}}}))
                obtido = rodar_pagina(entrada, cenario)
                if extra is None:
                    self.assertTrue(obtido['expirada'])      # outro dia: aparece de novo
                else:
                    self.assertFalse(obtido['expirada'])
                    self.assertTrue(obtido['tela'])
                    self.assertTrue(obtido['pausado'])
                    self.assertEqual(obtido['selo'], 'Assinar')
                    self.assertTrue(obtido['leitura'])
        # Armazenamento quebrado, ausente ou só a nuvem quebrada: mostra a tela (o lado seguro).
        for nome, extra in (('os dois quebrados', {'local': {'modo': 'quebrado'}, 'nuvem': {'modo': 'quebrado'}}),
                            ('sem nenhum', {'local': {'modo': 'sem'}, 'nuvem': {'modo': 'sem'}}),
                            ('nuvem quebrada', {'nuvem': {'modo': 'quebrado'}})):
            with self.subTest(armazenamento=nome):
                obtido = rodar_pagina(dict(base, **extra), cenario)
                self.assertTrue(obtido['expirada'], nome)
                self.assertFalse(obtido['tela'])
        # Dia guardado só no CloudStorage com o localStorage quebrado: vale a nuvem.
        obtido = rodar_pagina(dict(base, local={'modo': 'quebrado'}, nuvem={'hoje': True}), cenario)
        self.assertFalse(obtido['expirada'])

    def test_link_assinatura_sempre_abre_a_expirada_para_quem_venceu(self):
        for nome, extra in (('?tela=assinatura', {'busca': '?tela=assinatura'}), ('start_param', {'startParam': 'assinatura'})):
            with self.subTest(link=nome):
                entrada = dict({'rotas': rotas_da_home(BLOCO_PRO_VENCIDO), 'local': {'hoje': True}, 'nuvem': {'hoje': True}}, **extra)
                obtido = rodar_pagina(entrada, """
                    await __ocioso();
                    console.log(JSON.stringify({ expirada: __visivel('expirada'), assinaturas: __visivel('assinaturas'), chamadas: __chamadas() }));
                """)
                self.assertTrue(obtido['expirada'])       # mesmo já vista hoje
                self.assertFalse(obtido['assinaturas'])
                self.assertEqual(obtido['chamadas'], ABERTURA)
        # Outro link qualquer não conta.
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_PRO_VENCIDO), 'local': {'hoje': True}, 'busca': '?tela=outra', 'startParam': 'x'},
                              "await __ocioso(); console.log(JSON.stringify({ expirada: __visivel('expirada') }));")
        self.assertFalse(obtido['expirada'])

    def test_textos_da_expirada(self):
        casos = [(BLOCO_TESTE_VENCIDO, 'Seu Teste de 10 Dias Terminou. Para Continuar Usando o L1d3r, Assine o Plano Pro.',
                  'Seu Teste Terminou em:', '11/11/2026', 'Assinar'),
                 (BLOCO_PRO_VENCIDO, 'Sua Assinatura Pro Venceu. Renove Para Continuar Usando o L1d3r.',
                  'Venceu em:', '01/11/2026', 'Renovar Assinatura')]
        for bloco, apoio, rotulo, data, botao in casos:
            with self.subTest(estado=bloco['estado']):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco)}, """
                    await __ocioso();
                    console.log(JSON.stringify({ apoio: $('exp-apoio').textContent, rotulo: $('exp-data-rotulo').textContent,
                      data: $('exp-data-valor').textContent, botao: $('exp-botao').textContent, stars: $('exp-preco-stars').textContent,
                      reais: $('exp-preco-reais').textContent, back: __log.ligacoes[__log.ligacoes.length - 1] }));
                """)
                self.assertEqual((obtido['apoio'], obtido['rotulo'], obtido['data'], obtido['botao']), (apoio, rotulo, data, botao))
                self.assertEqual(obtido['stars'], '150 Stars')
                self.assertEqual(obtido['reais'], '≈ R$ 19 (varia conforme a loja)')
                self.assertEqual(obtido['back'], 'show')

    def test_mais_tarde_e_o_backbutton_levam_a_home_em_modo_leitura(self):
        for nome, acao in (('Mais Tarde', "$('exp-mais-tarde').click();"), ('BackButton', '__voltar();')):
            with self.subTest(saida=nome):
                obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_TESTE_VENCIDO)}, """
                    await __ocioso();
                    const antes = __chamadas().length;
                    %s
                    await __ocioso();
                    console.log(JSON.stringify({ antes, depois: __chamadas().length, tela: __visivel('tela'), expirada: __visivel('expirada'),
                      pausado: __visivel('pausado-home'), selo: $('selo-texto').textContent, leitura: assEstado.leitura,
                      classe: document.body.className, back: __log.ligacoes[__log.ligacoes.length - 1] }));
                """ % acao)
                self.assertTrue(obtido['tela'])
                self.assertFalse(obtido['expirada'])
                self.assertEqual(obtido['antes'], obtido['depois'])    # a home já estava pronta: sem recarregar
                self.assertTrue(obtido['pausado'])
                self.assertEqual(obtido['selo'], 'Assinar')
                self.assertTrue(obtido['leitura'])
                self.assertNotIn('modo-assinaturas', obtido['classe'])
                self.assertEqual(obtido['back'], 'hide')

    # ---------------------------- abertura com plano: link e sem_plano ----------------------------
    def test_link_para_quem_tem_plano_abre_a_tela_assinaturas_sem_chamada_nova(self):
        casos = [
            (BLOCO_TESTE, 'Seu Teste Gratuito Está Ativo. Assine Quando Quiser Para Continuar Usando o L1d3r Depois Dele.',
             'Seu Teste Está Ativo. Faltam 7 Dias.', 'Assinar', False, True),
            (BLOCO_PRO, 'Obrigado Por Assinar o L1d3r! Seu Plano Pro Está Ativo.', 'Seu Plano Pro', 'Cancelar Renovação', True, True),
            (BLOCO_CANCELADO, 'Você Cancelou a Renovação. Seu Plano Pro Continua Ativo Até o Fim do Período Pago.', 'Seu Plano Pro',
             'Reativar Renovação', False, True),
            (BLOCO_DADO, 'Seu Plano Pro Está Ativo Até a Data Abaixo.', 'Seu Plano Pro', None, False, False),
        ]
        for bloco, apoio, titulo, botao, claro, com_preco in casos:
            with self.subTest(estado=bloco['estado'], renovacao_cancelada=bloco['renovacao_cancelada']):
                obtido = rodar_pagina({'rotas': rotas_da_home(bloco), 'busca': '?tela=assinatura'}, """
                    await __ocioso();
                    console.log(JSON.stringify({ chamadas: __chamadas(), assinaturas: __visivel('assinaturas'), tela: __visivel('tela'),
                      apoio: $('ass-apoio').textContent, titulo: $('ass-atual-titulo').textContent, preco: __visivel('ass-cartao-preco'),
                      botao: $('ass-preco-botao').textContent, botaoVisivel: __visivel('ass-preco-botao'), claro: $('ass-preco-botao').classList.contains('claro'),
                      testeBotao: __visivel('ass-teste-botao'), atualPrimeiro: $('ass-conteudo').classList.contains('atual-primeiro') }));
                """)
                self.assertEqual(obtido['chamadas'], ABERTURA)
                self.assertTrue(obtido['assinaturas'])
                self.assertFalse(obtido['tela'])
                self.assertEqual(obtido['apoio'], apoio)
                self.assertEqual(obtido['titulo'], titulo)
                self.assertEqual(obtido['preco'], com_preco)
                self.assertTrue(obtido['atualPrimeiro'])      # o cartão do plano atual vem antes do de preço
                self.assertFalse(obtido['testeBotao'])
                if botao:
                    self.assertEqual((obtido['botao'], obtido['claro']), (botao, claro))
                    self.assertTrue(obtido['botaoVisivel'])

    def test_sem_plano_cadastrado_abre_a_tela_assinaturas_principal(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_SEM_PLANO)}, """
            await __ocioso();
            console.log(JSON.stringify({ chamadas: __chamadas(), assinaturas: __visivel('assinaturas'), tela: __visivel('tela'),
              apoio: $('ass-apoio').textContent, titulo: $('ass-atual-titulo').textContent,
              datas: $('ass-atual-caixa').children.filter((c) => c.className === 'ass-linha').map((l) => l.children.map((x) => x.textContent)),
              stars: $('ass-preco-stars').textContent, reais: $('ass-preco-reais').textContent, testeBotao: __visivel('ass-teste-botao'),
              preco: $('ass-preco-botao').textContent, atualPrimeiro: $('ass-conteudo').classList.contains('atual-primeiro') }));
        """)
        self.assertEqual(obtido['chamadas'], ABERTURA)
        self.assertTrue(obtido['assinaturas'])
        self.assertFalse(obtido['tela'])
        self.assertEqual(obtido['apoio'], 'Nossa Aplicação Conta Com Uma Assinatura Simbólica de 150 Stars Por Mês. Mas Não Se Preocupe: '
                                         'Liberamos 10 Dias Gratuitos Para Novos Usuários Testarem.')
        self.assertEqual(obtido['titulo'], 'Teste Gratuitamente Por 10 Dias Corridos!')
        self.assertEqual(obtido['datas'], [['Início do Teste:', '12/11/2026'], ['Fim do Teste:', '22/11/2026']])   # de teste_previsto
        self.assertEqual((obtido['stars'], obtido['reais']), ('150 Stars', '≈ R$ 19 (varia conforme a loja)'))
        self.assertTrue(obtido['testeBotao'])
        self.assertEqual(obtido['preco'], 'Assinar')
        self.assertFalse(obtido['atualPrimeiro'])             # na principal, o cartão de preço vem primeiro

    # ---------------------------- modo somente leitura ----------------------------
    def test_bloqueios_do_modo_leitura_mostram_o_aviso_sem_chamar_a_api(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_TESTE_VENCIDO), 'local': {'hoje': True}}, """
            await __ocioso();
            const fotos = [];
            const foto = (nome) => fotos.push({ nome, aviso: __visivel('aviso-topo'), texto: $('aviso-topo-texto').textContent,
              cadeado: __visivel('aviso-topo-cadeado'), check: __visivel('aviso-topo-check'), chamadas: __chamadas().length,
              opcoes: __visivel('registro-opcoes'), escolha: __visivel('recorrentes-escolha'), form: __visivel('recorrente-form'),
              catNova: __visivel('folha-cat-nova'), catEditar: __visivel('folha-cat-editar'), tela: __visivel('tela') });
            const limpar = () => { $('aviso-topo').hidden = true; };
            $('atalho-registrar').click(); foto('registrar'); limpar();
            $('rec-adicionar').click(); foto('adicionar recorrente'); limpar();
            abrirFormEditar({ id: 1, descricao: 'x', valor: 1, categoria: 'Casa', dia_cobranca: 3 }); foto('item recorrente'); limpar();
            abrirSalarioRecorrente(); foto('cartão do salário'); limpar();
            $('cat-nova').click(); foto('nova categoria'); limpar();
            abrirFolhaEditar({ id: 1, nome: 'Casa' }); foto('barra'); limpar();
            console.log(JSON.stringify({ fotos, dica: $('cat-dica').textContent, pausadoRec: __visivel('rec-pausado') }));
        """)
        for foto in obtido['fotos']:
            with self.subTest(toque=foto['nome']):
                self.assertTrue(foto['aviso'])
                self.assertEqual(foto['texto'], 'Assine o Pro Para Continuar se Organizando')
                self.assertTrue(foto['cadeado'])
                self.assertFalse(foto['check'])
                self.assertEqual(foto['chamadas'], 4)           # nenhuma chamada à API
                for aberto in ('opcoes', 'escolha', 'form', 'catNova', 'catEditar'):
                    self.assertFalse(foto[aberto], aberto)       # nada de formulário nem folha
                self.assertTrue(foto['tela'])
        self.assertEqual(obtido['dica'], 'Veja Quanto Cada Categoria Gastou. Para Criar ou Editar, Assine o Plano Pro.')
        self.assertTrue(obtido['pausadoRec'])

    def test_fora_do_modo_leitura_os_toques_funcionam_como_antes(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_PRO)}, """
            await __ocioso();
            $('atalho-registrar').click();
            console.log(JSON.stringify({ opcoes: __visivel('registro-opcoes'), aviso: __visivel('aviso-topo'), leitura: assEstado.leitura }));
        """)
        self.assertTrue(obtido['opcoes'])
        self.assertFalse(obtido['aviso'])
        self.assertFalse(obtido['leitura'])

    def test_403_assinatura_vencida_liga_o_modo_leitura_sem_trocar_de_tela(self):
        rotas = rotas_da_home(BLOCO_TESTE, {'POST /gastos': {'status': 403, 'body': {'detail': 'assinatura_vencida'}},
                                            'GET /assinatura': {'status': 200, 'body': BLOCO_TESTE_VENCIDO}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('atalho-registrar').click();                        // a pessoa está no meio do fluxo de registrar
            const r = await enviarJson('/gastos', { chave: 'x' });
            await __ocioso();
            console.log(JSON.stringify({ erro: r.erro, detalhe: r.detalhe, aviso: __visivel('aviso-topo'), texto: $('aviso-topo-texto').textContent,
              cadeado: __visivel('aviso-topo-cadeado'), leitura: assEstado.leitura, opcoes: __visivel('registro-opcoes'),
              pausado: __visivel('pausado-home'), selo: $('selo-texto').textContent, mensagem: mensagemErroRegistro(r, 'gravar', 'gasto'),
              mensagemChat: mensagemErroRegistro(r, 'interpretar', 'gasto'), chamadas: __chamadas().slice(4), expirada: __visivel('expirada'),
              assinaturas: __visivel('assinaturas') }));
        """)
        self.assertEqual((obtido['erro'], obtido['detalhe']), (403, 'assinatura_vencida'))
        self.assertTrue(obtido['aviso'])
        self.assertEqual(obtido['texto'], 'Assine o Pro Para Continuar se Organizando')
        self.assertTrue(obtido['cadeado'])
        self.assertTrue(obtido['leitura'])
        self.assertTrue(obtido['opcoes'])                         # a tela atual continua aberta, sem trocar de tela
        self.assertFalse(obtido['expirada'])
        self.assertFalse(obtido['assinaturas'])
        self.assertTrue(obtido['pausado'])
        self.assertEqual(obtido['selo'], 'Assinar')               # o JSON completo vem do GET /assinatura em segundo plano
        self.assertEqual(obtido['mensagem'], '')                  # nunca a mensagem de erro genérica
        self.assertEqual(obtido['mensagemChat'], '')
        self.assertEqual(obtido['chamadas'], ['POST /gastos', 'GET /assinatura'])

    def test_403_nas_gravacoes_nao_vira_mensagem_generica(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_TESTE)}, """
            const r403 = { erro: 403, detalhe: 'assinatura_vencida' };
            console.log(JSON.stringify({
              recGasto: decisaoDaGravacaoRecorrente('criar', r403, 'gasto'), recReceita: decisaoDaGravacaoRecorrente('editar', r403, 'receita'),
              recApagar: decisaoDaGravacaoRecorrente('apagar', r403, 'gasto'),
              catCriar: decisaoDaGravacaoCategoria('criar', r403, 20), catExcluir: decisaoDaGravacaoCategoria('excluir', r403, 20),
              outro403: mensagemErroRegistro({ erro: 403, detalhe: 'outra_coisa' }, 'gravar', 'gasto'),
            }));
        """)
        for chave in ('recGasto', 'recReceita', 'recApagar', 'catCriar', 'catExcluir'):
            self.assertEqual(obtido[chave], {'acao': 'erro', 'mensagem': ''}, chave)
        self.assertEqual(obtido['outro403'], '')

    def test_403_assinatura_ausente_abre_a_tela_assinaturas(self):
        rotas = rotas_da_home(BLOCO_TESTE, {'POST /gastos': {'status': 403, 'body': {'detail': 'assinatura_ausente'}},
                                            'GET /assinatura': {'status': 200, 'body': BLOCO_SEM_PLANO}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            await enviarJson('/gastos', { chave: 'x' });
            await __ocioso();
            console.log(JSON.stringify({ assinaturas: __visivel('assinaturas'), tela: __visivel('tela'), aviso: __visivel('aviso-topo'),
              leitura: assEstado.leitura, teste: __visivel('ass-teste-botao'), chamadas: __chamadas().slice(4) }));
        """)
        self.assertTrue(obtido['assinaturas'])
        self.assertFalse(obtido['tela'])
        self.assertFalse(obtido['aviso'])
        self.assertFalse(obtido['leitura'])
        self.assertTrue(obtido['teste'])
        self.assertEqual(obtido['chamadas'], ['POST /gastos', 'GET /assinatura'])

    def test_403_de_outro_motivo_nao_liga_o_modo_leitura(self):
        rotas = rotas_da_home(BLOCO_TESTE, {'POST /gastos': {'status': 403, 'body': {'detail': 'proibido'}}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            await enviarJson('/gastos', {});
            await __ocioso();
            console.log(JSON.stringify({ aviso: __visivel('aviso-topo'), leitura: assEstado.leitura, chamadas: __chamadas().slice(4) }));
        """)
        self.assertFalse(obtido['aviso'])
        self.assertFalse(obtido['leitura'])
        self.assertEqual(obtido['chamadas'], ['POST /gastos'])

    def test_pagou_o_json_novo_sem_modo_leitura_desliga_na_hora(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_TESTE_VENCIDO), 'local': {'hoje': True}}, """
            await __ocioso();
            const antes = { leitura: assEstado.leitura, pausado: __visivel('pausado-home'), dica: $('cat-dica').textContent };
            aplicarAssinatura(%s);
            console.log(JSON.stringify({ antes, leitura: assEstado.leitura, pausado: __visivel('pausado-home'), pausadoRec: __visivel('rec-pausado'),
              selo: $('selo-texto').textContent, dica: $('cat-dica').textContent }));
        """ % json.dumps(BLOCO_PRO))
        self.assertTrue(obtido['antes']['leitura'])
        self.assertTrue(obtido['antes']['pausado'])
        self.assertFalse(obtido['leitura'])
        self.assertFalse(obtido['pausado'])
        self.assertFalse(obtido['pausadoRec'])
        self.assertEqual(obtido['selo'], 'Pro')
        self.assertEqual(obtido['dica'], 'Clique Nas Barras Para Visualizar e Editar as Categorias')

    def test_selo_assinar_abre_a_folha_plano_com_a_linha_dos_termos(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(BLOCO_PRO_VENCIDO), 'local': {'hoje': True}}, """
            await __ocioso();
            $('selo').click();
            console.log(JSON.stringify({ plano: __visivel('folha-plano'), minha: __visivel('folha-minha'), stars: $('plano-preco-stars').textContent,
              reais: $('plano-preco-reais').textContent, ia: $('plano-ia-texto').textContent, iaVisivel: __visivel('plano-item-ia'),
              chamadas: __chamadas().length }));
        """)
        self.assertTrue(obtido['plano'])
        self.assertFalse(obtido['minha'])
        self.assertEqual(obtido['stars'], '150 Stars')
        self.assertEqual(obtido['reais'], '≈ R$ 19 (varia conforme a loja)')
        self.assertEqual(obtido['ia'], 'Até 17 Registros Por IA Por Dia')
        self.assertEqual(obtido['chamadas'], 4)

    def test_termos_de_uso_mostram_o_texto_do_get_termos(self):
        rotas = rotas_da_home(BLOCO_PRO_VENCIDO, {'GET /termos': [{'status': 500, 'body': {'detail': 'erro_interno'}},
                                                                  {'status': 200, 'body': {'texto': 'Termos linha 1\nlinha 2'}}]})
        obtido = rodar_pagina({'rotas': rotas, 'local': {'hoje': True}}, """
            await __ocioso();
            $('selo').click(); $('plano-termos').click();
            await __ocioso();
            const erro = { termos: __visivel('folha-termos'), texto: __visivel('termos-texto'), estado: $('termos-estado-texto').textContent,
                           tentar: __visivel('termos-tentar') };
            $('termos-tentar').click();
            await __ocioso();
            const ok = { texto: __visivel('termos-texto'), conteudo: $('termos-texto').textContent, estado: __visivel('termos-estado') };
            __voltar();                                          // fecha só os Termos; a folha Plano Pro continua
            console.log(JSON.stringify({ erro, ok, termos: __visivel('folha-termos'), plano: __visivel('folha-plano'),
              gets: __chamadas().filter((c) => c === 'GET /termos').length }));
        """)
        self.assertTrue(obtido['erro']['termos'])
        self.assertFalse(obtido['erro']['texto'])
        self.assertEqual(obtido['erro']['estado'], 'Não foi possível carregar os termos. Tente de novo.')
        self.assertTrue(obtido['erro']['tentar'])
        self.assertTrue(obtido['ok']['texto'])
        self.assertEqual(obtido['ok']['conteudo'], 'Termos linha 1\nlinha 2')
        self.assertFalse(obtido['ok']['estado'])
        self.assertFalse(obtido['termos'])
        self.assertTrue(obtido['plano'])
        self.assertEqual(obtido['gets'], 2)

    # ---------------------------- cadastro ----------------------------
    def rotas_do_cadastro(self, assinatura, extras=None):
        rotas = {'GET /me': {'status': 404, 'body': {'detail': 'usuario_nao_cadastrado'}},
                 'GET /resumo': {'status': 404, 'body': {'detail': 'usuario_nao_cadastrado'}},
                 'GET /historico': {'status': 404, 'body': {'detail': 'usuario_nao_cadastrado'}},
                 'GET /movimentacoes/recentes': {'status': 404, 'body': {'detail': 'usuario_nao_cadastrado'}},
                 'GET /assinatura': {'status': 200, 'body': assinatura}}
        rotas.update(extras or {})
        return rotas

    def test_cadastro_chave_desligada_vai_direto_ao_passo_3(self):
        obtido = rodar_pagina({'rotas': self.rotas_do_cadastro(bloco_assinatura(ligada=False))}, """
            await __ocioso();
            const escolha = { visivel: __visivel('boas-vindas'), desabilitado: $('escolha-novo').disabled, fechado: __visivel('escolha-fechado'),
                              sub: $('escolha-novo-sub').textContent };
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            const antes = __chamadas().length;
            continuarCadastro();
            await __ocioso();
            console.log(JSON.stringify({ escolha, passo: cadastroEstado.passo, cadastro: __visivel('cadastro'), assinaturas: __visivel('assinaturas'),
              novas: __chamadas().length - antes, chamadas: __chamadas().slice(4) }));
        """)
        self.assertTrue(obtido['escolha']['visivel'])
        self.assertFalse(obtido['escolha']['desabilitado'])
        self.assertFalse(obtido['escolha']['fechado'])
        self.assertEqual(obtido['escolha']['sub'], 'Configure tudo em 1 minuto')
        self.assertEqual(obtido['passo'], 3)                     # como sempre foi: direto para as categorias
        self.assertTrue(obtido['cadastro'])
        self.assertFalse(obtido['assinaturas'])
        self.assertEqual(obtido['novas'], 0)
        self.assertEqual(obtido['chamadas'], ['GET /assinatura'])    # só a do cadastro (antes do passo 2), nenhuma na abertura da home

    def test_cadastro_passa_pelas_assinaturas_e_o_teste_segue_para_as_categorias(self):
        rotas = self.rotas_do_cadastro(BLOCO_SEM_PLANO, {
            'POST /assinaturas/teste': {'status': 201, 'body': {'inicio': '2026-11-12', 'fim': '2026-11-22', 'repetido': False}}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            continuarCadastro();
            await __ocioso();
            const tela = { assinaturas: __visivel('assinaturas'), cadastro: __visivel('cadastro'), conteudo: __visivel('ass-conteudo'),
              datas: $('ass-atual-caixa').children.filter((c) => c.className === 'ass-linha').map((l) => l.children.map((x) => x.textContent)),
              back: __log.ligacoes[__log.ligacoes.length - 1] };
            __voltar();                                           // a seta volta ao passo 2
            const volta = { cadastro: __visivel('cadastro'), assinaturas: __visivel('assinaturas'), passo: cadastroEstado.passo };
            continuarCadastro();
            await __ocioso();
            $('ass-teste-botao').click();
            const durante = { logo: __visivel('tela-logo'), titulo: $('tela-logo-titulo').textContent };
            await __ocioso();
            const feito = { resultado: __visivel('assinatura-resultado'), titulo: $('res-titulo').textContent, texto: $('res-texto').textContent,
              logo: __visivel('tela-logo'), back: __log.ligacoes[__log.ligacoes.length - 1] };
            $('res-continuar').click();
            console.log(JSON.stringify({ tela, volta, durante, feito, passo: cadastroEstado.passo, cadastro: __visivel('cadastro'),
              posts: __log.fetch.filter((c) => c.metodo === 'POST').map((c) => c.rota) }));
        """)
        self.assertTrue(obtido['tela']['assinaturas'])
        self.assertFalse(obtido['tela']['cadastro'])
        self.assertTrue(obtido['tela']['conteudo'])
        self.assertEqual(obtido['tela']['datas'], [['Início do Teste:', '12/11/2026'], ['Fim do Teste:', '22/11/2026']])
        self.assertEqual(obtido['volta'], {'cadastro': True, 'assinaturas': False, 'passo': 2})
        self.assertTrue(obtido['durante']['logo'])
        self.assertEqual(obtido['feito']['titulo'], 'Seu Teste Começou!')
        self.assertEqual(obtido['feito']['texto'], 'Vale Até 22/11/2026')
        self.assertTrue(obtido['feito']['resultado'])
        self.assertFalse(obtido['feito']['logo'])
        self.assertEqual(obtido['feito']['back'], 'hide')
        self.assertEqual(obtido['passo'], 3)
        self.assertTrue(obtido['cadastro'])
        self.assertEqual(obtido['posts'], ['/assinaturas/teste'])

    def test_erro_no_teste_e_vagas_esgotadas(self):
        casos = [
            ('banco_ocupado', {'status': 503, 'body': {'detail': 'banco_ocupado'}}, 'erro'),
            ('sem resposta', {'status': 599, 'body': {}}, 'erro'),
            ('vagas', {'status': 409, 'body': {'detail': 'vagas_teste_esgotadas'}}, 'esgotado'),
        ]
        for nome, resposta, esperado in casos:
            with self.subTest(caso=nome):
                rotas = self.rotas_do_cadastro(BLOCO_SEM_PLANO, {'POST /assinaturas/teste': resposta})
                obtido = rodar_pagina({'rotas': rotas}, """
                    await __ocioso();
                    $('escolha-novo').click();
                    cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
                    continuarCadastro();
                    await __ocioso();
                    $('ass-teste-botao').click();
                    await __ocioso();
                    console.log(JSON.stringify({ erro: __visivel('ass-teste-erro'), textoErro: $('ass-teste-erro').querySelector('span').textContent,
                      vagas: __visivel('ass-vagas'), caixa: __visivel('ass-atual-caixa'), botaoDesabilitado: $('ass-teste-botao').disabled,
                      resultado: __visivel('assinatura-resultado'), logo: __visivel('tela-logo'), assinaturas: __visivel('assinaturas') }));
                """)
                self.assertTrue(obtido['assinaturas'])
                self.assertFalse(obtido['resultado'])
                self.assertFalse(obtido['logo'])
                if esperado == 'erro':
                    self.assertTrue(obtido['erro'])
                    self.assertFalse(obtido['vagas'])
                    self.assertTrue(obtido['caixa'])             # a caixa das datas continua
                    self.assertFalse(obtido['botaoDesabilitado'])  # tenta de novo
                else:
                    self.assertFalse(obtido['erro'])
                    self.assertTrue(obtido['vagas'])
                    self.assertFalse(obtido['caixa'])            # a caixa de aviso no lugar das datas
                    self.assertTrue(obtido['botaoDesabilitado'])  # apagado e sem toque

    def test_erro_no_teste_tenta_de_novo_no_segundo_toque(self):
        rotas = self.rotas_do_cadastro(BLOCO_SEM_PLANO, {'POST /assinaturas/teste': [
            {'status': 503, 'body': {'detail': 'banco_ocupado'}},
            {'status': 201, 'body': {'inicio': '2026-11-12', 'fim': '2026-11-22', 'repetido': False}}]})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            continuarCadastro(); await __ocioso();
            $('ass-teste-botao').click(); await __ocioso();
            const erro = __visivel('ass-teste-erro');
            $('ass-teste-botao').click(); await __ocioso();
            console.log(JSON.stringify({ erro, resultado: __visivel('assinatura-resultado'), titulo: $('res-titulo').textContent }));
        """)
        self.assertTrue(obtido['erro'])
        self.assertTrue(obtido['resultado'])
        self.assertEqual(obtido['titulo'], 'Seu Teste Começou!')

    def test_cadastros_fechados_apagam_o_sou_novo_por_aqui(self):
        obtido = rodar_pagina({'rotas': self.rotas_do_cadastro(bloco_assinatura(estado='sem_plano', selo=None, cadastro_aberto=False))}, """
            await __ocioso();
            $('escolha-novo').click();                            // o toque não faz nada
            console.log(JSON.stringify({ desabilitado: $('escolha-novo').disabled, sub: $('escolha-novo-sub').textContent,
              caixa: __visivel('escolha-fechado'), amigo: $('escolha-amigo').disabled, boasVindas: __visivel('boas-vindas'),
              cadastro: __visivel('cadastro') }));
        """)
        self.assertTrue(obtido['desabilitado'])
        self.assertEqual(obtido['sub'], 'Cadastros fechados')
        self.assertTrue(obtido['caixa'])
        self.assertFalse(obtido['amigo'])                         # "Já Sou Amigo do Adm" continua normal
        self.assertTrue(obtido['boasVindas'])
        self.assertFalse(obtido['cadastro'])

    def test_assinaturas_sem_rede_mostra_o_erro_com_tentar_de_novo(self):
        rotas = self.rotas_do_cadastro(BLOCO_SEM_PLANO, {'GET /assinatura': [
            {'status': 200, 'body': BLOCO_SEM_PLANO}, {'status': 599, 'body': {}}, {'status': 200, 'body': BLOCO_SEM_PLANO}]})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            continuarCadastro(); await __ocioso();
            const erro = { erro: __visivel('ass-erro'), conteudo: __visivel('ass-conteudo'), titulo: $('ass-erro-titulo').textContent,
                           texto: $('ass-erro-texto').textContent, seta: __visivel('ass-voltar') };
            $('ass-erro-tentar').click(); await __ocioso();
            console.log(JSON.stringify({ erro, conteudo: __visivel('ass-conteudo'), erroDepois: __visivel('ass-erro'), seta: __visivel('ass-voltar') }));
        """)
        self.assertTrue(obtido['erro']['erro'])
        self.assertFalse(obtido['erro']['conteudo'])
        self.assertEqual(obtido['erro']['titulo'], 'Não Conseguimos Verificar Sua Assinatura')
        self.assertEqual(obtido['erro']['texto'], 'Confira a sua conexão e tente de novo.')
        self.assertFalse(obtido['erro']['seta'])
        self.assertTrue(obtido['conteudo'])
        self.assertFalse(obtido['erroDepois'])
        self.assertTrue(obtido['seta'])

    # ---------------------------- pagamento ----------------------------
    def rotas_de_pagamento(self, bloco_inicial, sequencia, fatura=None):
        return rotas_da_home(bloco_inicial, {
            'POST /assinaturas/fatura': fatura or {'status': 200, 'body': {'link': 'https://t.me/$abc', 'stars': 150}},
            'GET /assinatura': sequencia,
        })

    def test_pagamento_a_partir_da_pro_venceu_confirma_e_mostra_o_obrigado_renovacao(self):
        sequencia = [{'status': 200, 'body': BLOCO_PRO_VENCIDO}, {'status': 200, 'body': BLOCO_PRO_VENCIDO},
                     {'status': 200, 'body': BLOCO_PRO}]
        obtido = rodar_pagina({'rotas': self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, sequencia)}, """
            await __ocioso();
            $('exp-botao').click();
            const folha = __visivel('folha-plano');
            $('plano-pagar').click();
            await __ocioso();
            const aberta = { faturas: __log.faturas.slice(), pagando: $('plano-pagar').disabled };
            __ultimaFatura.cb('paid');
            const conf = { confirmando: __visivel('assinatura-confirmando'), folha: __visivel('folha-plano'), texto: $('conf-texto').textContent };
            await __ocioso();
            const gets = __chamadas().filter((c) => c === 'GET /assinatura').length;
            const fim = { resultado: __visivel('assinatura-resultado'), titulo: $('res-titulo').textContent, texto: $('res-texto').textContent,
                          leitura: assEstado.leitura, selo: $('selo-texto').textContent };
            __rotas['GET /me'] = { status: 200, body: { user_id: 1, nome: 'Ygor Lima', assinatura: %s } };   // o servidor já sabe do Pro
            $('res-continuar').click();
            await __ocioso();
            console.log(JSON.stringify({ folha, aberta, conf, gets, fim, tela: __visivel('tela'), resultadoDepois: __visivel('assinatura-resultado'),
              pausado: __visivel('pausado-home'), abertura: __chamadas().filter((c) => c === 'GET /me').length }));
        """ % json.dumps(BLOCO_PRO))
        self.assertTrue(obtido['folha'])
        self.assertEqual(obtido['aberta']['faturas'], ['https://t.me/$abc'])
        self.assertTrue(obtido['aberta']['pagando'])
        self.assertTrue(obtido['conf']['confirmando'])
        self.assertFalse(obtido['conf']['folha'])
        self.assertEqual(obtido['conf']['texto'], 'Isso Leva Só Alguns Segundos.')
        self.assertEqual(obtido['gets'], 3)                       # consultou até o Pro chegar
        self.assertTrue(obtido['fim']['resultado'])
        self.assertEqual(obtido['fim']['titulo'], 'Assinatura Renovada!')
        self.assertEqual(obtido['fim']['texto'], 'Obrigado Por Continuar Com a Gente, Ygor Lima. Agradecemos a Sua Confiança no L1d3r. '
                                                'Seu Plano Pro Renova em 12/12/2026 Usando as Stars Que Estão na Sua Conta.')
        self.assertFalse(obtido['fim']['leitura'])                # pagou: o modo leitura desliga na hora
        self.assertEqual(obtido['fim']['selo'], 'Pro')
        self.assertTrue(obtido['tela'])                           # "Continuar" leva à home (recarregada)
        self.assertFalse(obtido['resultadoDepois'])
        self.assertFalse(obtido['pausado'])
        self.assertEqual(obtido['abertura'], 2)

    def test_obrigado_nova_para_quem_assina_durante_o_teste(self):
        sequencia = [{'status': 200, 'body': BLOCO_PRO}]
        obtido = rodar_pagina({'rotas': self.rotas_de_pagamento(BLOCO_TESTE, sequencia)}, """
            await __ocioso();
            $('selo').click(); $('min-principal').click();       // Grátis -> Assinar o Plano Pro
            const folhas = { plano: __visivel('folha-plano'), minha: __visivel('folha-minha') };
            $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('pending');                        // "pending" também vai para o Confirmando
            await __ocioso();
            console.log(JSON.stringify({ folhas, titulo: $('res-titulo').textContent, texto: $('res-texto').textContent }));
        """)
        self.assertEqual(obtido['folhas'], {'plano': True, 'minha': False})
        self.assertEqual(obtido['titulo'], 'Obrigado Pela Assinatura, Ygor Lima!')
        self.assertEqual(obtido['texto'], 'Seu Plano Pro Está Ativo. Ele Renova em 12/12/2026 Usando as Stars Que Estão na Sua Conta.')

    def test_pagamento_cancelado_volta_a_folha_e_recusado_mostra_a_linha_vermelha(self):
        sequencia = [{'status': 200, 'body': BLOCO_PRO_VENCIDO}]
        rotas = self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, sequencia)
        # cancelled: a folha continua aberta, nada mais acontece
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('exp-botao').click(); $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('cancelled');
            console.log(JSON.stringify({ folha: __visivel('folha-plano'), erro: __visivel('plano-erro'), erroExp: __visivel('exp-erro'),
              confirmando: __visivel('assinatura-confirmando'), pagarAtivo: !$('plano-pagar').disabled, gets: __chamadas().filter((c) => c === 'GET /assinatura').length }));
        """)
        self.assertTrue(obtido['folha'])
        self.assertFalse(obtido['erro'])
        self.assertFalse(obtido['erroExp'])
        self.assertFalse(obtido['confirmando'])
        self.assertTrue(obtido['pagarAtivo'])
        self.assertEqual(obtido['gets'], 0)
        # failed estando na tela Expirada: a linha vermelha no cartão, acima do botão; a folha fecha
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            $('exp-botao').click(); $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('failed');
            console.log(JSON.stringify({ folha: __visivel('folha-plano'), erroExp: __visivel('exp-erro'), texto: $('exp-erro').querySelector('span').textContent,
              expirada: __visivel('expirada'), botaoAtivo: !$('exp-botao').disabled }));
        """)
        self.assertFalse(obtido['folha'])
        self.assertTrue(obtido['erroExp'])
        self.assertEqual(obtido['texto'], 'Não Conseguimos Concluir o Pagamento. Tente de Novo.')
        self.assertTrue(obtido['expirada'])
        self.assertTrue(obtido['botaoAtivo'])
        # failed fora da Expirada (selo "Assinar"): a linha vermelha dentro da folha Plano Pro
        obtido = rodar_pagina({'rotas': rotas, 'local': {'hoje': True}}, """
            await __ocioso();
            $('selo').click(); $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('failed');
            console.log(JSON.stringify({ folha: __visivel('folha-plano'), erro: __visivel('plano-erro'), texto: $('plano-erro').querySelector('span').textContent,
              erroExp: __visivel('exp-erro') }));
        """)
        self.assertTrue(obtido['folha'])
        self.assertTrue(obtido['erro'])
        self.assertEqual(obtido['texto'], 'Não Conseguimos Concluir o Pagamento. Tente de Novo.')
        self.assertFalse(obtido['erroExp'])

    def test_fatura_com_erro_do_servidor(self):
        casos = [
            ('assinaturas_desligadas', {'status': 409, 'body': {'detail': 'assinaturas_desligadas'}},
             'As assinaturas ainda não estão abertas. Volte em breve.'),
            ('telegram_indisponivel', {'status': 502, 'body': {'detail': 'telegram_indisponivel'}}, 'Não Conseguimos Abrir o Pagamento. Tente de Novo.'),
            ('erro_interno', {'status': 500, 'body': {'detail': 'erro_interno'}}, 'Não Conseguimos Abrir o Pagamento. Tente de Novo.'),
            ('sem resposta', {'status': 599, 'body': {}}, 'Não Conseguimos Abrir o Pagamento. Tente de Novo.'),
        ]
        for nome, fatura, texto in casos:
            with self.subTest(erro=nome):
                rotas = self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, [{'status': 200, 'body': BLOCO_PRO_VENCIDO}], fatura)
                obtido = rodar_pagina({'rotas': rotas, 'local': {'hoje': True}}, """
                    await __ocioso();
                    $('selo').click(); $('plano-pagar').click(); await __ocioso();
                    console.log(JSON.stringify({ erro: __visivel('plano-erro'), texto: $('plano-erro').querySelector('span').textContent, faturas: __log.faturas.length,
                      pagarAtivo: !$('plano-pagar').disabled, confirmando: __visivel('assinatura-confirmando') }));
                """)
                self.assertTrue(obtido['erro'])
                self.assertEqual(obtido['texto'], texto)
                self.assertEqual(obtido['faturas'], 0)           # openInvoice nem foi chamado
                self.assertTrue(obtido['pagarAtivo'])
                self.assertFalse(obtido['confirmando'])

    def test_fora_do_telegram_o_pagamento_mostra_o_aviso_padrao_sem_chamar_a_api(self):
        obtido = rodar_pagina({'rotas': self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, [{'status': 200, 'body': BLOCO_PRO_VENCIDO}]),
                               'local': {'hoje': True}}, """
            await __ocioso();
            delete tg.openInvoice;
            $('selo').click(); $('plano-pagar').click(); await __ocioso();
            console.log(JSON.stringify({ texto: $('plano-erro').querySelector('span').textContent, posts: __chamadas().filter((c) => c.startsWith('POST')).length }));
        """)
        self.assertEqual(obtido['texto'], 'Abra esta tela pelo botão do bot no Telegram.')
        self.assertEqual(obtido['posts'], 0)

    def test_confirmando_depois_de_30_segundos_nunca_libera_sozinho_e_o_toque_confere_de_novo(self):
        rotas = self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, [{'status': 200, 'body': BLOCO_PRO_VENCIDO}])
        obtido = rodar_pagina({'rotas': rotas, 'local': {'hoje': True}}, """
            await __ocioso();
            $('selo').click(); $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('paid');
            await __ocioso();
            const gets1 = __chamadas().filter((c) => c === 'GET /assinatura').length;
            const demorou = { confirmando: __visivel('assinatura-confirmando'), resultado: __visivel('assinatura-resultado'),
              texto: $('conf-texto').textContent, tocavel: $('conf-area').classList.contains('tocavel'), virtual: __virtual };
            __rotas['GET /assinatura'] = [{ status: 200, body: %s }];     // agora o pagamento chegou
            $('conf-area').click();
            await __ocioso();
            console.log(JSON.stringify({ gets1, demorou, resultado: __visivel('assinatura-resultado'), titulo: $('res-titulo').textContent }));
        """ % json.dumps(BLOCO_PRO))
        self.assertEqual(obtido['gets1'], 16)                     # a cada 2 s, por 30 s (0, 2, ..., 30)
        self.assertTrue(obtido['demorou']['confirmando'])
        self.assertFalse(obtido['demorou']['resultado'])
        self.assertEqual(obtido['demorou']['texto'], 'O pagamento foi feito, mas ainda não chegou. Toque para conferir de novo.')
        self.assertTrue(obtido['demorou']['tocavel'])
        self.assertTrue(obtido['resultado'])
        self.assertEqual(obtido['titulo'], 'Assinatura Renovada!')     # a tela de onde veio era a Pro Venceu

    def test_back_na_tela_confirmando_e_no_resultado_nao_faz_nada(self):
        rotas = self.rotas_de_pagamento(BLOCO_PRO_VENCIDO, [{'status': 200, 'body': BLOCO_PRO_VENCIDO}])
        obtido = rodar_pagina({'rotas': rotas, 'local': {'hoje': True}}, """
            await __ocioso();
            $('selo').click(); $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('paid'); await __ocioso();
            __voltar();
            console.log(JSON.stringify({ confirmando: __visivel('assinatura-confirmando'), tela: __visivel('tela'), back: __log.ligacoes[__log.ligacoes.length - 1] }));
        """)
        self.assertTrue(obtido['confirmando'])
        self.assertFalse(obtido['tela'])
        self.assertEqual(obtido['back'], 'hide')

    def test_cancelar_e_assinar_pela_propria_tela_assinaturas(self):
        # Aberta pelo link: o botão do cartão de preço é o mesmo da folha (Cancelar Renovação, com o showConfirm antes).
        rotas = rotas_da_home(BLOCO_PRO, {'POST /assinaturas/renovacao': {'status': 200, 'body': BLOCO_CANCELADO}})
        obtido = rodar_pagina({'rotas': rotas, 'busca': '?tela=assinatura'}, """
            await __ocioso();
            $('ass-preco-botao').click();
            const pedido = { confirms: __log.confirms.slice(), posts: __log.fetch.filter((c) => c.metodo === 'POST').length };
            __ultimoConfirm.cb(true);
            await __ocioso();
            console.log(JSON.stringify({ pedido, posts: __log.fetch.filter((c) => c.metodo === 'POST').map((c) => c.corpo),
              apoio: $('ass-apoio').textContent, botao: $('ass-preco-botao').textContent, claro: $('ass-preco-botao').classList.contains('claro'),
              linhas: $('ass-atual-caixa').children.filter((c) => c.className === 'ass-linha').map((l) => l.children[1].textContent),
              precoApoio: $('ass-preco-apoio').textContent, selo: $('selo-texto').textContent }));
        """)
        self.assertEqual(obtido['pedido'], {'confirms': ['Cancelar a renovação? Seu Plano Pro continua ativo até 12/12/2026.'], 'posts': 0})
        self.assertEqual(obtido['posts'], [{'cancelar': True}])
        self.assertEqual(obtido['apoio'], 'Você Cancelou a Renovação. Seu Plano Pro Continua Ativo Até o Fim do Período Pago.')
        self.assertEqual((obtido['botao'], obtido['claro']), ('Reativar Renovação', False))
        self.assertEqual(obtido['linhas'], ['12/12/2026', 'Cancelada'])
        self.assertEqual(obtido['precoApoio'], 'Não Haverá Nova Cobrança. Reative Para Continuar Depois de 12/12/2026.')
        self.assertEqual(obtido['selo'], 'Pro')
        # No teste em andamento, "Assinar" abre a folha Plano Pro; o pagamento recusado dentro dela mostra a linha vermelha.
        rotas = self.rotas_de_pagamento(BLOCO_TESTE, [{'status': 200, 'body': BLOCO_TESTE}])
        obtido = rodar_pagina({'rotas': rotas, 'busca': '?tela=assinatura'}, """
            await __ocioso();
            $('ass-preco-botao').click();
            const folha = __visivel('folha-plano');
            $('plano-pagar').click(); await __ocioso();
            __ultimaFatura.cb('failed');
            const recusado = __visivel('plano-erro');
            $('plano-voltar').click();
            console.log(JSON.stringify({ folha, recusado, fechou: !__visivel('folha-plano'), assinaturas: __visivel('assinaturas') }));
        """)
        self.assertEqual(obtido, {'folha': True, 'recusado': True, 'fechou': True, 'assinaturas': True})


class TestEstruturaDasAssinaturas(unittest.TestCase):
    """Texto do index.html: blocos, textos e medidas dos quadros do Paper, ordem da abertura e regras da nota da PRO-108."""

    # ---------------------------- blocos e textos ----------------------------
    def test_blocos_das_assinaturas_existem_escondidos_e_em_tela_cheia(self):
        for identificador in ('assinaturas', 'expirada', 'assinatura-resultado', 'assinatura-confirmando'):
            with self.subTest(tela=identificador):
                self.assertIn('<main id="%s" class="tela-cheia" hidden>' % identificador, HOME)
        # Os 4 ficam depois da tela Categorias e antes do aviso do topo (que fica fora dos <main>).
        self.assertLess(HOME.index('<main id="categorias"'), HOME.index('<main id="assinaturas"'))
        self.assertLess(HOME.index('<main id="assinatura-confirmando"'), HOME.index('id="aviso-topo"'))
        for identificador in ('folha-plano', 'folha-minha', 'folha-termos'):
            with self.subTest(folha=identificador):
                self.assertIn('<div class="folha-camada" id="%s" role="dialog" aria-modal="true"' % identificador, bloco_folha(identificador))
                self.assertIn('data-fechar="%s"' % identificador, bloco_folha(identificador))
        # Os Termos vêm por último: ficam por cima da Plano Pro e da Minha Assinatura (mesmo z-index).
        self.assertLess(HOME.index('id="folha-plano"'), HOME.index('id="folha-termos"'))
        self.assertLess(HOME.index('id="folha-minha"'), HOME.index('id="folha-termos"'))

    def test_cabecalhos_com_e_sem_seta(self):
        # Só a tela Assinaturas tem a seta (no cadastro volta ao passo 2); Expirada, Resultado e Confirmando não.
        self.assertIn('id="ass-voltar"', bloco_main('assinaturas'))
        for identificador in ('expirada', 'assinatura-resultado', 'assinatura-confirmando'):
            with self.subTest(tela=identificador):
                bloco = bloco_main(identificador)
                self.assertIn('class="cabecalho cabecalho-registro cabecalho-sem-seta"', bloco)
                self.assertNotIn('<button class="botao-menu"', bloco)
                self.assertIn('<h1 class="titulo-tela">Assinaturas</h1>', bloco)
        # O sem-seta tem a altura do cabeçalho com a seta (o botão tem 44px).
        self.assertIn('min-height: 44px', regra_css('.cabecalho-sem-seta .cabecalho-linha'))

    def test_textos_fixos_dos_quadros(self):
        textos = {
            'assinaturas': ('Plano Pro', 'por mês', 'Testar Gratuitamente', 'As Vagas de Teste de Hoje Acabaram.',
                            'Volte amanhã para testar, ou assine o Plano Pro agora.', 'Tentar de novo', 'Carregando...'),
            'expirada': ('Mais Tarde', 'Com Telegram Stars Você Paga com Segurança Pela App Store ou Play Store.',
                         'Seus Dados Continuam Guardados. Em Mais Tarde, Você Ainda Pode Ver Tudo, Só Não Registra.', 'Assinar'),
            'assinatura-resultado': ('Continuar',),
            'assinatura-confirmando': ('Confirmando o Pagamento...', 'Isso Leva Só Alguns Segundos.'),
        }
        for identificador, lista in textos.items():
            for texto in lista:
                with self.subTest(tela=identificador, texto=texto):
                    self.assertIn(texto, bloco_main(identificador))
        plano = bloco_folha('folha-plano')
        for texto in ('Plano Pro', 'Até 20 Categorias', 'Ao assinar, você concorda com os', 'Termos de Uso', 'Pagar com Stars', 'Voltar',
                      'A Assinatura Renova a Cada 30 Dias Usando as Stars Que Estão na Sua Conta, e Você Pode Cancelar Quando Quiser.'):
            with self.subTest(folha='plano', texto=texto):
                self.assertIn(texto, plano)
        minha = bloco_folha('folha-minha')
        for texto in ('Minha Assinatura', 'Fechar', 'Termos de Uso'):
            with self.subTest(folha='minha', texto=texto):
                self.assertIn(texto, minha)
        self.assertIn('Novos Cadastros Estão Fechados no Momento.', HOME)
        self.assertIn('Volte em breve. Quem já tem conta entra normalmente.', HOME)
        self.assertEqual(HOME.count('<span>Registros Pausados Até Você Assinar o Pro</span>'), 2)    # home e Recorrentes
        # "Até 20 Categorias" é o limite do servidor; 30 dias, o período do Pro; 10 dias, o teste (só os textos).
        self.assertEqual(categorias.LIMITE_CATEGORIAS, 20)
        self.assertEqual(assinaturas.DIAS_PRO_PADRAO, 30)
        self.assertEqual(assinaturas.DIAS_TESTE, 10)
        self.assertIn('const DIAS_TESTE = 10;', codigo_puro(HOME))
        # O nome do app nas telas é "L1d3r": o novo HTML nunca escreve "Fin4nce".
        for identificador in ('assinaturas', 'expirada', 'assinatura-resultado', 'assinatura-confirmando'):
            self.assertNotIn('Fin4nce', bloco_main(identificador))
        for identificador in ('folha-plano', 'folha-minha', 'folha-termos'):
            self.assertNotIn('Fin4nce', bloco_folha(identificador))

    def test_nenhum_numero_de_preco_escrito_na_tela(self):
        # O preço ("150 Stars" e "≈ R$ 19") e o limite ("17") vêm do servidor: nenhum desses números no HTML novo.
        novo = ''.join(bloco_main(i) for i in ('assinaturas', 'expirada', 'assinatura-resultado', 'assinatura-confirmando'))
        novo += ''.join(bloco_folha(i) for i in ('folha-plano', 'folha-minha', 'folha-termos'))
        for proibido in ('150', '≈ R$', 'R$ 19', ' 17 ', 'Stars por mês'):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, novo)
        puro = codigo_puro(HOME)
        for funcao in ('function textosDaTelaAssinaturas', 'function textosDaFolha', 'function textosDaExpirada', 'function textoPrecoStars'):
            corpo = corpo_da_funcao(puro, funcao)
            self.assertNotIn('150', corpo)
            self.assertNotIn('"17"', corpo)

    def test_selo_so_tem_tres_textos_e_nenhum_free_teste_ou_admin(self):
        puro = codigo_puro(HOME)
        self.assertIn('const SELOS = { gratis: "Grátis", pro: "Pro", assinar: "Assinar" };', puro)
        self.assertNotIn('Free', HOME)
        self.assertNotIn('Admin', HOME)
        # O texto do selo vem só do SELOS, pela função seloDaHome, que só lê o campo "selo".
        selo = corpo_da_funcao(puro, 'function seloDaHome')
        self.assertIn('b.selo', selo)
        for proibido in ('b.estado', 'dias_restantes', 'inicio', 'fim', 'modo_leitura', 'valor_stars'):
            self.assertNotIn(proibido, selo)
        principal = codigo_principal(HOME)
        desenho = corpo_da_funcao(principal, 'function desenharSelo')
        self.assertIn('$("selo-texto").textContent = selo.texto;', desenho)
        self.assertIn('$("selo-linha").hidden = !selo;', desenho)
        self.assertEqual(principal.count('"selo-texto"'), 1)
        # Só o selo tocável ganha a classe .tocavel; o estado pressionado (CSS) só existe com ela.
        self.assertIn('(selo.tocavel ? " tocavel" : "")', desenho)
        css = css_da_home()
        pressionados = re.findall(r'(?m)^\s*(\.selo[^{]*:active[^{]*|\.selo[^{]*\.tocado[^{]*)\{', css)
        self.assertTrue(pressionados)
        for seletor in pressionados:
            for parte in seletor.split(','):
                with self.subTest(seletor=parte.strip()):
                    self.assertIn('.tocavel', parte)
        self.assertIn('0 0 0 3px var(--cor-selo-anel)', css)
        raiz = HOME[HOME.index(':root {'):HOME.index('}', HOME.index(':root {'))]
        for token in ('--cor-selo-tocado: #FFFFFF4D;', '--cor-selo-anel: #FFFFFF1F;', '--cor-borda-botao: #D9D9D9;'):
            self.assertIn(token, raiz)

    def test_toque_no_selo_so_com_selo_tocavel(self):
        principal = codigo_principal(HOME)
        toque = corpo_da_funcao(principal, 'function aoTocarNoSelo')
        self.assertIn('acaoDoToqueNoSelo(assEstado.bloco)', toque)
        self.assertLess(toque.index('if (acao === "nenhuma") return;'), toque.index('classList.add("tocado")'))
        self.assertLess(toque.index('if (acao === "nenhuma") return;'), toque.index('abrirFolhaPlano("selo")'))
        # Abrir a folha não chama a API nem mostra a tela da logo.
        for proibido in ('chamar(', 'enviarJson(', 'enviarComMetodo(', 'mostrarTelaLogo('):
            self.assertNotIn(proibido, toque)
            self.assertNotIn(proibido, corpo_da_funcao(principal, 'function abrirMinhaAssinatura'))
            self.assertNotIn(proibido, corpo_da_funcao(principal, 'function desenharMinhaAssinatura'))

    # ---------------------------- abertura ----------------------------
    def test_abertura_mantem_as_4_chamadas_e_decide_pelo_bloco_do_me(self):
        principal = codigo_principal(HOME)
        corpo = corpo_da_funcao(principal, 'async function carregar')
        self.assertEqual(corpo.count('chamar('), 4)
        self.assertIn('chamar("/me"), chamar("/resumo"), chamar("/historico"), chamar("/movimentacoes/recentes")', corpo)
        for proibido in ('ROTA_ASSINATURA', '"/assinatura"', 'enviarJson', 'recarregarAssinatura', 'buscarAssinaturas'):
            self.assertNotIn(proibido, corpo)
        # O bloco vem do /me (resultados[0].dados.assinatura / me.assinatura) e passa por duas funções.
        self.assertIn('decidirAberturaPelaAssinatura(resultados[0].dados.assinatura)', corpo)
        self.assertIn('aplicarAssinatura(me.assinatura)', corpo)
        # A decisão é a função pura, e a tela da logo só sai depois dela (o armazenamento é lido com a logo na tela).
        self.assertLess(corpo.index('decidirAberturaPelaAssinatura('), corpo.index('esconderTelaLogo();'))
        self.assertLess(corpo.index('aplicarAssinatura(me.assinatura)'), corpo.index('abrirExpiradaNaAbertura()'))
        decide = corpo_da_funcao(principal, 'async function decidirAberturaPelaAssinatura')
        self.assertIn('destinoDaAbertura(bloco, { link: link, vistaHoje: vistaHoje })', decide)
        # Só lê o armazenamento quando precisa: modo leitura sem link.
        self.assertIn('if (modoLeituraDoBloco(bloco) && !link) vistaHoje = await expiradaVistaHoje(', decide)
        self.assertIn('window.location.search', decide)
        self.assertIn('startParamDoTelegram()', decide)
        # Só a abertura decide por telas novas: as outras cargas (Registrar, tour...) só aplicam o bloco.
        self.assertIn('if (abertura && destino === "home")', corpo)
        # A chamada do GET /assinatura não aparece na home, só nas telas de assinatura e no cadastro.
        usos = [m.start() for m in re.finditer(r'chamar\(ROTA_ASSINATURA\)', principal)]
        self.assertGreaterEqual(len(usos), 3)

    def test_nenhuma_data_de_plano_e_calculada_no_cliente(self):
        principal = codigo_principal(HOME)
        puro = codigo_puro(HOME)
        # new Date só no relógio de Brasília de sempre e na marca "vista hoje"; Date.now só na marca; nenhum Date.parse/getTime em plano.
        permitido = {'function agoraEmBrasilia', 'function diaDeBrasilia'}
        sem_comentarios = lambda texto: re.sub(r'//[^\n]*', '', texto)
        for nome, codigo in (('principal', sem_comentarios(principal)), ('puro', sem_comentarios(puro))):
            for m in re.finditer(r'new Date\(', codigo):
                inicio = max(codigo.rfind('\n    function ', 0, m.start()), codigo.rfind('\n    async function ', 0, m.start()))
                cabeca = codigo[inicio:codigo.index('(', inicio + 5)].strip().split('\n')[-1]
                self.assertIn(cabeca, permitido, nome)
        self.assertNotIn('Date.parse', HOME)
        self.assertNotIn('getTime', HOME)
        for funcao in ('function formatarDataPlano', 'function textosDaFolha', 'function textosDaTelaAssinaturas', 'function textosDaExpirada',
                       'function textoConfirmarCancelamento', 'function textosDoTesteAtivado', 'function textosDoObrigado'):
            self.assertNotIn('Date', corpo_da_funcao(puro, funcao), funcao)
        # dd/mm/aaaa só por texto: o formatador usa a expressão regular, nenhum split de data com Date.
        self.assertIn(r'/^(\d{4})-(\d{2})-(\d{2})$/', corpo_da_funcao(puro, 'function formatarDataPlano'))

    def test_decisao_da_abertura_usa_a_ordem_da_nota(self):
        puro = codigo_puro(HOME)
        corpo = corpo_da_funcao(puro, 'function destinoDaAbertura')
        ordem = ['if (!b) return "home";', 'if (b.modo_leitura === true)', 'if (b.estado === "sem_plano") return "assinaturas";',
                 'if (ctx.link) return "assinaturas";', 'return "home";']
        posicoes = [corpo.index(trecho) for trecho in ordem[:-1]] + [corpo.rindex(ordem[-1])]
        self.assertEqual(posicoes, sorted(posicoes))

    # ---------------------------- modo somente leitura ----------------------------
    def test_bloqueios_do_modo_leitura_sao_a_primeira_coisa_das_funcoes(self):
        principal = codigo_principal(HOME)
        for funcao in ('function abrirOpcoes', 'function abrirEscolha(origem)', 'function abrirSalarioRecorrente', 'function abrirFormEditar',
                       'function abrirFolhaNova', 'function abrirFolhaEditar'):
            with self.subTest(funcao=funcao):
                corpo = corpo_da_funcao(principal, funcao)
                resto = corpo[corpo.index('{') + 1:].lstrip()
                self.assertTrue(resto.startswith('if (bloquearNoModoLeitura()) return;'), funcao)
        # O guarda não chama a API: só o aviso do cadeado.
        guarda = corpo_da_funcao(principal, 'function bloquearNoModoLeitura')
        self.assertIn('mostrarAvisoTopo(avisoDoModoLeitura(), "cadeado");', guarda)
        for proibido in ('chamar(', 'enviarJson(', 'enviarComMetodo(', 'fetch('):
            self.assertNotIn(proibido, guarda)
        # Os toques ligados a elas: Registrar, Adicionar Recorrente, item de recorrente, Nova Categoria e barra.
        self.assertIn('$("atalho-registrar").addEventListener("click", abrirOpcoes);', principal)
        self.assertIn('$("rec-adicionar").addEventListener("click", () => abrirEscolha("lista"));', principal)
        self.assertIn('if (r) abrirFormEditar(r);', principal)
        self.assertIn('$("cat-nova").addEventListener("click", abrirFolhaNova);', principal)
        self.assertIn('if (c) abrirFolhaEditar(c);', principal)

    def test_modo_leitura_so_liga_com_o_servidor_ou_com_um_403(self):
        principal = codigo_principal(HOME)
        atribuicoes = re.findall(r'assEstado\.leitura\s*=[^=]', principal)
        self.assertEqual(len(atribuicoes), 1)     # só o aplicarAssinatura mexe nele
        aplicar = corpo_da_funcao(principal, 'function aplicarAssinatura')
        self.assertIn('assEstado.leitura = !!forcarLeitura || modoLeituraDoBloco(bloco);', aplicar)
        liga = corpo_da_funcao(principal, 'function ligarModoLeitura')
        self.assertIn(', true);', liga)           # forcarLeitura: só a partir do 403
        self.assertEqual(len(re.findall(r'ligarModoLeitura\(\)', principal)), 2)    # a definição e o aoAcessoRecusado
        recusado = corpo_da_funcao(principal, 'function aoAcessoRecusado')
        self.assertIn('acaoDoAcessoRecusado(status, detalhe)', recusado)
        self.assertIn('abrirAssinaturasPorFalta();', recusado)

    def test_tratamento_central_do_403_fica_nas_duas_funcoes_que_chamam_a_api(self):
        principal = codigo_principal(HOME)
        for funcao in ('async function chamar(', 'async function enviarComMetodo'):
            with self.subTest(funcao=funcao):
                corpo = corpo_da_funcao(principal, funcao)
                self.assertIn('aoAcessoRecusado(resposta.status, detalhe);', corpo)
        self.assertEqual(principal.count('fetch('), 2)    # todo o tráfego passa por elas
        self.assertEqual(principal.count('aoAcessoRecusado('), 3)   # as duas chamadas e a definição
        # 403 nunca vira mensagem genérica nos pontos de erro.
        puro = codigo_puro(HOME)
        self.assertIn('if (status === 403) return "";', corpo_da_funcao(puro, 'function mensagemErroRegistro'))
        self.assertIn('if (resultado.erro === 403) return { acao: "erro", mensagem: "" };', corpo_da_funcao(puro, 'function decisaoDaGravacaoRecorrente'))
        self.assertIn('if (resultado.erro === 403) return { acao: "erro", mensagem: "" };', corpo_da_funcao(puro, 'function decisaoDaGravacaoCategoria'))
        self.assertIn('hidden = !decisao.mensagem', corpo_da_funcao(principal, 'async function apagarRecorrente'))

    def test_aviso_do_modo_leitura_e_o_mesmo_aviso_de_10_segundos_com_cadeado(self):
        principal = codigo_principal(HOME)
        aviso = corpo_da_funcao(principal, 'function mostrarAvisoTopo')
        self.assertIn('function mostrarAvisoTopo(aviso, icone)', aviso)
        self.assertIn('}, 10000)', aviso)
        self.assertIn('$("aviso-topo-cadeado").hidden = icone !== "cadeado";', aviso)
        self.assertIn('id="aviso-topo-cadeado"', HOME)
        self.assertIn('id="aviso-topo-check"', HOME)
        self.assertIn('<rect x="4.5" y="9" width="11" height="8" rx="2"/><path d="M7 9V6.5a3 3 0 0 1 6 0V9"/>', HOME)

    def test_linha_de_pausado_na_home_e_em_recorrentes(self):
        home = HOME[HOME.index('<main id="tela">'):HOME.index('<main id="detalhe"')]
        # Entre o topo (</header>) e os atalhos.
        self.assertLess(home.index('</header>'), home.index('id="pausado-home"'))
        self.assertLess(home.index('id="pausado-home"'), home.index('<nav class="atalhos"'))
        lista = bloco_main('recorrentes-lista')
        self.assertLess(lista.index('id="rec-pausado"'), lista.index('id="rec-adicionar"'))     # acima de "Adicionar Recorrente"
        self.assertIn('id="rec-pausado" hidden', lista)
        self.assertIn('id="pausado-home" hidden', home)
        # Só o modo leitura as mostra.
        aplicar = corpo_da_funcao(codigo_principal(HOME), 'function aplicarAssinatura')
        self.assertIn('$("pausado-home").hidden = !assEstado.leitura;', aplicar)
        self.assertIn('$("rec-pausado").hidden = !assEstado.leitura;', aplicar)
        self.assertIn('$("cat-dica").textContent = textoDicaCategorias(assEstado.leitura);', aplicar)
        # Categorias não tem a linha de pausado.
        self.assertNotIn('pausado', bloco_main('categorias'))

    # ---------------------------- navegação ----------------------------
    def test_voltar_das_assinaturas(self):
        principal = codigo_principal(HOME)
        voltar = corpo_da_funcao(principal, 'function voltarUmNivel')
        ordem = ['if (!$("tela-logo").hidden) return;', 'folhaAberta()', 'if (!$("assinatura-confirmando").hidden || !$("assinatura-resultado").hidden) return;',
                 'if (!$("expirada").hidden)', 'maisTarde();', 'if (!$("assinaturas").hidden)', 'voltarDasAssinaturas();', 'if (!$("categorias").hidden)']
        posicoes = [voltar.index(trecho) for trecho in ordem]
        self.assertEqual(posicoes, sorted(posicoes))
        self.assertIn('FOLHAS_RECORRENTES.concat(FOLHAS_CATEGORIAS).concat(FOLHAS_ASSINATURA)', corpo_da_funcao(principal, 'function folhaAberta'))
        self.assertIn('assEstado.ocupado', corpo_da_funcao(principal, 'function fecharFolhaAtual'))
        self.assertIn('if (assEstado.ocupado) return;', corpo_da_funcao(principal, 'function voltarDasAssinaturas'))
        # Mais Tarde e o BackButton da Expirada levam à mesma função (a home em modo leitura, sem recarregar se já está pronta).
        self.assertIn('irParaHomeDasAssinaturas();', corpo_da_funcao(principal, 'function maisTarde'))
        casa = corpo_da_funcao(principal, 'function irParaHomeDasAssinaturas')
        self.assertIn('if ($("numeros").hidden) {', casa)
        self.assertIn('$("tela").hidden = false;', casa)
        self.assertIn('$("exp-mais-tarde").addEventListener("click", maisTarde);', principal)
        # O Continuar do resultado: passo 3 no cadastro, home recarregada fora dele.
        continuar = corpo_da_funcao(principal, 'function continuarDoResultado')
        self.assertIn('voltarAoCadastro(3)', continuar)
        self.assertIn('irParaHomeDepoisDeAssinar()', continuar)
        self.assertIn('carregar();', corpo_da_funcao(principal, 'function irParaHomeDepoisDeAssinar'))

    def test_cadastro_passa_pelo_passo_assinaturas_entre_o_dia_e_as_categorias(self):
        principal = codigo_principal(HOME)
        continuar = corpo_da_funcao(principal, 'function continuarCadastro')
        self.assertLess(continuar.index('e.passo === 2 && cadastroPassaPelasAssinaturas(assEstado.previo.estado)'), continuar.index('e.passo++;'))
        self.assertIn('abrirAssinaturasDoCadastro();', continuar)
        abrir = corpo_da_funcao(principal, 'function abrirAssinaturasDoCadastro')
        self.assertIn('assEstado.origem = "cadastro";', abrir)
        self.assertIn('mostrarBackButton(true);', abrir)
        self.assertIn('voltarAoCadastro(2)', corpo_da_funcao(principal, 'function voltarDasAssinaturas'))
        # A escolha inicial sabe se os cadastros estão fechados pelo GET /assinatura (a abertura da home não usa esta chamada).
        self.assertIn('buscarAssinaturaPrevia();', corpo_da_funcao(principal, 'function abrirEscolhaInicial'))
        self.assertIn('cadastroFechado(assEstado.previo.bloco)', corpo_da_funcao(principal, 'function abrirCadastro'))

    # ---------------------------- teste, pagamento e renovação ----------------------------
    def test_testar_gratuitamente_usa_a_logo_com_tempo_minimo_e_a_rota_do_teste(self):
        principal = codigo_principal(HOME)
        corpo = corpo_da_funcao(principal, 'async function testarGratuitamente')
        self.assertIn('mostrarTelaLogo("Assinaturas"', corpo)
        self.assertIn('comTempoMinimo(', corpo)
        self.assertIn('enviarJson(ROTA_ASSINATURA_TESTE, undefined, 15000)', corpo)
        self.assertIn('TEMPO_MINIMO_LOGO_MS', corpo)
        self.assertLess(corpo.index('if (a.ocupado || a.testeEstado === "esgotado") return;'), corpo.index('mostrarTelaLogo('))
        # A logo sai na mesma tarefa em que o resultado entra.
        self.assertLess(corpo.index('mostrarResultado(t.titulo, t.texto);'), corpo.index('esconderTelaLogo();'))
        # Nenhuma nova tentativa sozinha: o erro mostra a linha vermelha e o botão tenta de novo.
        self.assertNotIn('gravarComTentativas', corpo)

    def test_pagamento_usa_a_fatura_o_openinvoice_e_o_confirmando(self):
        principal = codigo_principal(HOME)
        pagar = corpo_da_funcao(principal, 'async function pagarComStars')
        self.assertIn('enviarJson(ROTA_ASSINATURA_FATURA, undefined, 15000)', pagar)
        self.assertIn('tg.openInvoice(decisao.link, aoStatusDoPagamento);', pagar)
        self.assertLess(pagar.index('typeof tg.openInvoice !== "function"'), pagar.index('enviarJson('))   # fora do Telegram: sem chamada
        self.assertIn('MENSAGEM_FORA_DO_TELEGRAM', pagar)
        status = corpo_da_funcao(principal, 'function aoStatusDoPagamento')
        self.assertIn('decisaoDoPagamento(status)', status)
        confere = corpo_da_funcao(principal, 'async function conferirPagamento')
        self.assertIn('aguardarPagamento(() => chamar(ROTA_ASSINATURA), esperar, () => performance.now(), p.antes, PAGAMENTO_LIMITE_MS, PAGAMENTO_PASSO_MS)', confere)
        self.assertIn('aplicarAssinatura(r.bloco);', confere)
        self.assertNotIn('setInterval', HOME)
        self.assertIn('const PAGAMENTO_LIMITE_MS = 30000;', codigo_puro(HOME))
        self.assertIn('const PAGAMENTO_PASSO_MS = 2000;', codigo_puro(HOME))
        # A tela nunca libera sozinha: depois dos 30 s só o toque na área confere de novo.
        self.assertIn("$(\"conf-area\").addEventListener(\"click\", () => { if (!assEstado.pagamento.aguardando) conferirPagamento(false); });", principal)

    def test_cancelar_e_reativar_so_depois_do_showconfirm_e_sem_tela_da_logo(self):
        principal = codigo_principal(HOME)
        for funcao, esperado in (('function pedirCancelamento', 'mudarRenovacao(true)'), ('function pedirReativacao', 'mudarRenovacao(false)')):
            corpo = corpo_da_funcao(principal, funcao)
            self.assertIn('confirmarNativo(', corpo)
            self.assertLess(corpo.index('confirmarNativo('), corpo.index(esperado))
            self.assertNotIn('enviarJson', corpo)
        self.assertIn('tg.showConfirm(texto, (confirmado) => aoResponder(!!confirmado))', corpo_da_funcao(principal, 'function confirmarNativo'))
        muda = corpo_da_funcao(principal, 'async function mudarRenovacao')
        self.assertIn('enviarJson(ROTA_ASSINATURA_RENOVACAO, { cancelar: cancelar }, 20000)', muda)
        self.assertNotIn('mostrarTelaLogo', muda)
        self.assertNotIn('comTempoMinimo', muda)
        self.assertIn('aplicarAssinatura(resultado.dados);', muda)    # a resposta é o JSON novo
        # 409 reativacao_recusada: o alerta e, ao fechar, a folha Plano Pro.
        self.assertIn('alertaNativoComRetorno(decisao.mensagem, () => abrirFolhaPlano(', muda)
        self.assertIn('tg.showAlert(texto, aoFechar)', corpo_da_funcao(principal, 'function alertaNativoComRetorno'))

    def test_rotas_codigos_e_campos_sao_os_do_servidor(self):
        api = open(os.path.join(RAIZ, 'api', 'main.py'), encoding='utf-8').read()
        fonte = open(os.path.join(RAIZ, 'assinaturas.py'), encoding='utf-8').read()
        puro = codigo_puro(HOME)
        for constante, verbo, rota in (('ROTA_ASSINATURA', 'get', '/assinatura'), ('ROTA_ASSINATURA_TESTE', 'post', '/assinaturas/teste'),
                                       ('ROTA_ASSINATURA_FATURA', 'post', '/assinaturas/fatura'),
                                       ('ROTA_ASSINATURA_RENOVACAO', 'post', '/assinaturas/renovacao'), ('ROTA_TERMOS', 'get', '/termos')):
            with self.subTest(rota=rota):
                self.assertIn('const %s = "%s";' % (constante, rota), puro)
                self.assertRegex(api, r'@app\.%s\("%s"' % (verbo, re.escape(rota)))
        # Códigos 409 e 403 que a tela trata existem no servidor, com o status que a tela espera.
        for codigo in ('teste_ja_usado', 'plano_ativo', 'assinaturas_desligadas', 'reativacao_recusada'):
            with self.subTest(codigo=codigo):
                self.assertIn('"%s": 409' % codigo, api)
                self.assertIn(codigo, puro)
        for codigo in ('assinatura_vencida', 'assinatura_ausente'):
            with self.subTest(codigo=codigo):
                self.assertIn('detail=codigo', api)
                self.assertIn('"%s"' % codigo, api)
                self.assertIn('"%s"' % codigo, corpo_da_funcao(puro, 'function acaoDoAcessoRecusado'))
        self.assertIn('"assinatura_ausente" if situacao.estado == "sem_plano" else "assinatura_vencida"', api)
        self.assertIn('detail="cancelar_invalido"', api)
        # vagas_teste_esgotadas (PRO-109): o servidor manda (409) e a tela trata.
        self.assertIn("ErroRegistro('vagas_teste_esgotadas')", fonte)
        self.assertIn('"vagas_teste_esgotadas": 409', api)
        self.assertIn('vagas_teste_esgotadas', corpo_da_funcao(puro, 'function decisaoDoTeste'))
        # cadastro_fechado (PRO-109): o POST /cadastro manda 403 e a tela trata.
        self.assertIn('raise HTTPException(status_code=403, detail="cadastro_fechado")', api)
        self.assertIn('resultado.erro === 403 && resultado.detalhe === "cadastro_fechado"', corpo_da_funcao(puro, 'function decisaoDoCadastro'))
        # O corpo do cancelar é {"cancelar": bool}; o teste e a fatura não levam corpo.
        self.assertIn('cancelar: Any = None', api)
        # Os campos do bloco que a tela lê existem no montar_bloco_assinatura (cadastro_aberto desde a PRO-109).
        bloco = corpo_da_funcao(fonte, 'def montar_bloco_assinatura')
        lidos = ('ligada', 'estado', 'inicio', 'fim', 'teste_previsto', 'preco', 'limite_ia_dia', 'selo', 'selo_tocavel', 'dias_restantes',
                 'valor_stars', 'proxima_renovacao', 'renovacao_cancelada', 'vale_ate', 'modo_leitura', 'cadastro_aberto')
        for campo in lidos:
            with self.subTest(campo=campo):
                self.assertIn('"%s"' % campo, bloco)
                self.assertTrue(campo in puro, campo)
        self.assertIn('"stars": preco_pro_stars(), "reais_aproximado": PRECO_REAIS_APROXIMADO', bloco)
        # PRO-109: o campo existe no servidor (admin true; senão, o CADASTRO_ABERTO) e a tela o lê em cadastroFechado.
        self.assertIn('"cadastro_aberto": True if admin else cadastro_esta_aberto()', bloco)
        self.assertIn('bloco.cadastro_aberto === false', corpo_da_funcao(puro, 'function cadastroFechado'))
        # O texto do 409 assinaturas_desligadas foi decidido pelo Ygor.
        self.assertIn('const MSG_ASSINATURAS_DESLIGADAS = "As assinaturas ainda não estão abertas. Volte em breve.";', puro)

    def test_limite_do_429_vem_do_bloco(self):
        principal = codigo_principal(HOME)
        self.assertIn('mensagemErroRegistro(resultado, "interpretar", e.tipo, limiteIaDiaAtual())', corpo_da_funcao(principal, 'async function interpretar'))
        self.assertIn('assEstado.bloco.limite_ia_dia', corpo_da_funcao(principal, 'function limiteIaDiaAtual'))

    def test_folha_minha_assinatura_fecha_por_botao_fundo_e_arrasto(self):
        principal = codigo_principal(HOME)
        self.assertIn('FOLHAS_ASSINATURA.forEach(tornarFolhaArrastavel);', principal)
        arrasto = corpo_da_funcao(principal, 'function tornarFolhaArrastavel')
        for trecho in ('touchstart', 'touchmove', 'touchend', 'arrastoFechaAFolha(deslocamento)', 'folha.scrollTop > 0', 'camada.hidden = true;'):
            self.assertIn(trecho, arrasto)
        self.assertIn('$("min-fechar").addEventListener("click", fecharFolhaAtual);', principal)
        # O toque no fundo escuro é o [data-fechar] de sempre, ligado a todas as folhas.
        self.assertIn('document.querySelectorAll("[data-fechar]").forEach((fundo) => fundo.addEventListener("click", fecharFolhaAtual));', principal)
        self.assertIn('.folha { overscroll-behavior: contain; }', css_da_home())

    # ---------------------------- medidas dos quadros ----------------------------
    def test_medidas_do_css_seguem_os_quadros_do_paper(self):
        esperado = {
            '.selo': ('height: 28px', 'padding: 0 12px', 'border-radius: 999px', 'font-size: 13px', 'line-height: 18px', 'gap: 4px'),
            '.selo-linha': ('padding: 10px 24px 0',),
            '.pausado-linha': ('gap: 6px', 'font-size: 13px', 'line-height: 18px', 'font-weight: 500', 'color: var(--cor-texto-2)'),
            '.pausado-linha.na-home': ('padding: 14px 24px 0',),
            '.ass-corpo': ('padding: 16px 24px 32px', 'gap: 20px'),
            '.ass-apoio-bloco': ('gap: 16px',),
            '.ass-apoio': ('font-size: 15px', 'line-height: 22px', 'color: var(--cor-texto-2)'),
            '.ass-cartao': ('padding: 20px', 'gap: 14px', 'border-radius: 18px', 'border: 1px solid var(--cor-divisoria)'),
            '.ass-preco-valor': ('font-size: 34px', 'line-height: 40px', 'font-weight: 800', 'letter-spacing: -0.02em'),
            '.ass-cartao-titulo': ('font-size: 18px', 'line-height: 24px', 'font-weight: 800'),
            '.ass-caixa': ('padding: 4px 16px', 'border-radius: 14px', 'background: var(--cor-fundo-suave)'),
            '.ass-linha': ('height: 44px', 'gap: 12px'),
            '.ass-linha-valor': ('font-size: 15px', 'line-height: 20px', 'font-weight: 700'),
            '.ass-sep': ('height: 1px', 'background: var(--cor-branco)'),
            '.ass-aviso-caixa': ('padding: 14px 16px', 'gap: 4px', 'border-radius: 14px'),
            '.ass-erro-bloco': ('gap: 12px',),
            '.ass-confirmando': ('padding: 0 24px 116px',),
            '.ass-conf-caixa': ('height: 320px', 'gap: 16px'),
            '.plano-preco-caixa': ('padding: 14px 16px', 'gap: 6px', 'border-radius: 14px'),
            '.plano-preco-valor': ('font-size: 28px', 'line-height: 34px', 'font-weight: 800'),
            '.plano-itens': ('gap: 10px',),
            '.plano-item': ('gap: 10px', 'font-size: 15px', 'line-height: 20px', 'font-weight: 700'),
            '.plano-acoes': ('padding-top: 4px', 'gap: 6px'),
            '.min-termos': ('height: 32px',),
            '.min-apoio': ('padding-bottom: 8px', 'font-size: 14px', 'line-height: 20px'),
            '.botao-principal.claro': ('border: 1px solid var(--cor-borda-botao)', 'background: var(--cor-branco)'),
        }
        for seletor, trechos in esperado.items():
            regra = regra_css(seletor)
            for trecho in trechos:
                with self.subTest(seletor=seletor, medida=trecho):
                    self.assertIn(trecho, regra)

    def test_reaproveita_as_classes_dos_quadros_ja_existentes(self):
        # O resultado (Teste Ativado e Obrigado) é o molde estado-central: ícone de 64px, título 20/28 e apoio 15/22.
        resultado = bloco_main('assinatura-resultado')
        for classe in ('estado-central', 'estado-icone', 'estado-textos', 'estado-titulo', 'estado-texto', 'rodape-tela', 'botao-principal'):
            self.assertIn('class="%s' % classe, resultado)
        self.assertIn('width: 64px', regra_css('.estado-icone'))
        self.assertIn('font-size: 20px', regra_css('.estado-titulo'))
        # Carregando e Confirmando usam a logo animada de sempre (CSS só); o erro de rede, a caixa tracejada das Categorias.
        for identificador in ('assinaturas', 'assinatura-confirmando'):
            self.assertIn('class="logo-animada"', bloco_main(identificador))
        self.assertIn('class="cat-erro-caixa"', bloco_main('assinaturas'))
        # Os botões são o .botao-principal (52px, raio 16) e o Mais Tarde/Voltar/Fechar, o .botao-texto (44px).
        self.assertIn('height: 52px', regra_css('.botao-confirmar, .botao-principal'))
        self.assertIn('height: 44px', regra_css('.botao-texto'))
        self.assertIn('class="botao-texto ass-mais-tarde"', bloco_main('expirada'))
        # A folha é a mesma dos Recorrentes (raio 24, alça de 36x4, padding 12 24 32) e fica entre o aviso e a logo.
        self.assertIn('padding: 12px 24px calc(32px + var(--respiro-rodape))', regra_css('.folha'))
        self.assertIn('border-radius: 24px 24px 0 0', regra_css('.folha'))
        for identificador in ('folha-plano', 'folha-minha', 'folha-termos'):
            self.assertIn('<div class="folha-alca"></div>', bloco_folha(identificador))

    def test_cartao_de_preco_vem_antes_na_principal_e_depois_nas_outras_variacoes(self):
        self.assertIn('order: -2', regra_css('.ass-apoio-bloco'))
        self.assertIn('order: -1', regra_css('.ass-corpo.atual-primeiro #ass-cartao-atual'))
        bloco = bloco_main('assinaturas')
        self.assertLess(bloco.index('id="ass-cartao-preco"'), bloco.index('id="ass-cartao-atual"'))   # a ordem do HTML é a da principal
        puro = codigo_puro(HOME)
        self.assertIn('atualPrimeiro: false', corpo_da_funcao(puro, 'function textosDaTelaAssinaturas'))

    def test_termos_de_uso_abre_o_texto_do_get_termos(self):
        principal = codigo_principal(HOME)
        corpo = corpo_da_funcao(principal, 'async function abrirTermos')
        self.assertIn('chamar(ROTA_TERMOS)', corpo)
        self.assertIn('$("termos-texto").textContent = termosEstado.texto;', corpo)
        self.assertIn('$("plano-termos").addEventListener("click", abrirTermos);', principal)
        self.assertIn('$("min-termos").addEventListener("click", abrirTermos);', principal)
        self.assertIn('white-space: pre-wrap', regra_css('.termos-texto'))

    def test_todo_id_que_o_script_busca_existe_no_html(self):
        ids = set(ids_da_home())
        usados = set(re.findall(r'\$\("([^"]+)"\)', codigo_principal(HOME)))
        self.assertEqual(usados - ids, set())
        # Os ids montados por prefixo (desenharPreco e mostrarErroCampo) também existem.
        for prefixo in ('ass-preco', 'exp-preco', 'plano-preco'):
            for sufixo in ('-stars', '-reais'):
                self.assertIn(prefixo + sufixo, ids)
        for erro in ('ass-teste-erro', 'exp-erro', 'plano-erro'):
            self.assertIn('id="%s"' % erro, HOME)

    def test_nenhum_innerhtml_nas_telas_novas(self):
        principal = codigo_principal(HOME)
        for funcao in ('function desenharLinhas', 'function desenharAssinaturas', 'function desenharMinhaAssinatura', 'function desenharSelo',
                       'async function abrirTermos', 'function mostrarResultado', 'function abrirExpirada'):
            self.assertNotIn('innerHTML', corpo_da_funcao(principal, funcao))
        self.assertIn('criar("span", "ass-linha-rotulo", linha[0])', corpo_da_funcao(principal, 'function desenharLinhas'))


@unittest.skipUnless(NODE, 'node não encontrado')
class TestAssinaturasFuncoesPuras(unittest.TestCase):
    def rodar(self, corpo):
        return rodar_node(com_funcoes_puras(corpo))

    def test_selo_e_toque(self):
        obtido = self.rodar("""
            const blocos = { gratis: { ligada: true, selo: 'gratis', selo_tocavel: true }, pro: { ligada: true, selo: 'pro', selo_tocavel: true },
              assinar: { ligada: true, selo: 'assinar', selo_tocavel: true }, admin: { ligada: true, selo: 'pro', selo_tocavel: false },
              semTocavel: { ligada: true, selo: 'pro' }, nulo: { ligada: true, selo: null }, estranho: { ligada: true, selo: 'Free' },
              desligada: { ligada: false, selo: 'pro', selo_tocavel: true }, vazio: {}, nada: null, indefinido: undefined, texto: 'pro' };
            const saida = {};
            for (const [nome, b] of Object.entries(blocos)) saida[nome] = { selo: seloDaHome(b), acao: acaoDoToqueNoSelo(b) };
            console.log(JSON.stringify(saida));
        """)
        self.assertEqual(obtido['gratis'], {'selo': {'variante': 'gratis', 'texto': 'Grátis', 'seta': False, 'tocavel': True}, 'acao': 'minha'})
        self.assertEqual(obtido['pro'], {'selo': {'variante': 'pro', 'texto': 'Pro', 'seta': False, 'tocavel': True}, 'acao': 'minha'})
        self.assertEqual(obtido['assinar'], {'selo': {'variante': 'assinar', 'texto': 'Assinar', 'seta': True, 'tocavel': True}, 'acao': 'plano'})
        self.assertEqual(obtido['admin'], {'selo': {'variante': 'pro', 'texto': 'Pro', 'seta': False, 'tocavel': False}, 'acao': 'nenhuma'})
        self.assertEqual(obtido['semTocavel']['acao'], 'nenhuma')
        for nome in ('nulo', 'estranho', 'desligada', 'vazio', 'nada', 'indefinido', 'texto'):
            with self.subTest(sem_selo=nome):
                self.assertEqual(obtido[nome], {'selo': None, 'acao': 'nenhuma'})

    def test_destino_da_abertura(self):
        obtido = self.rodar("""
            const saida = [];
            const estados = { semBloco: undefined, nulo: null, desligada: { ligada: false, modo_leitura: true, estado: 'sem_plano' },
              teste: { ligada: true, estado: 'teste', modo_leitura: false }, pro: { ligada: true, estado: 'pro', modo_leitura: false },
              admin: { ligada: true, estado: 'pro', modo_leitura: false, selo_tocavel: false },
              semPlano: { ligada: true, estado: 'sem_plano', modo_leitura: false },
              testeVencido: { ligada: true, estado: 'teste_vencido', modo_leitura: true }, proVencido: { ligada: true, estado: 'pro_vencido', modo_leitura: true } };
            for (const [nome, b] of Object.entries(estados))
              for (const link of [false, true])
                for (const vistaHoje of [false, true])
                  saida.push({ nome, link, vistaHoje, destino: destinoDaAbertura(b, { link, vistaHoje }) });
            console.log(JSON.stringify(saida));
        """)
        esperado = {}
        for linha in obtido:
            esperado[(linha['nome'], linha['link'], linha['vistaHoje'])] = linha['destino']
        for nome in ('semBloco', 'nulo', 'desligada'):
            for link in (False, True):
                for vista in (False, True):
                    self.assertEqual(esperado[(nome, link, vista)], 'home', (nome, link, vista))   # (1) como hoje, até com link
        for nome in ('teste', 'pro', 'admin'):
            for vista in (False, True):
                self.assertEqual(esperado[(nome, False, vista)], 'home', nome)                      # (5)
                self.assertEqual(esperado[(nome, True, vista)], 'assinaturas', nome)                # (4) link
        for vista in (False, True):
            for link in (False, True):
                self.assertEqual(esperado[('semPlano', link, vista)], 'assinaturas')                # (3)
        for nome in ('testeVencido', 'proVencido'):
            self.assertEqual(esperado[(nome, False, False)], 'expirada')                            # (2) ainda não vista hoje
            self.assertEqual(esperado[(nome, False, True)], 'home')                                 # (2) vista: a home em modo leitura
            self.assertEqual(esperado[(nome, True, False)], 'expirada')                             # link: sempre a Expirada
            self.assertEqual(esperado[(nome, True, True)], 'expirada')

    def test_link_dia_de_brasilia_e_ja_vista(self):
        obtido = self.rodar("""
            console.log(JSON.stringify({
              links: [linkDeAssinatura('?tela=assinatura', null), linkDeAssinatura('', 'assinatura'), linkDeAssinatura('?a=1&tela=assinatura', undefined),
                      linkDeAssinatura('?tela=outra', 'x'), linkDeAssinatura('', null), linkDeAssinatura(undefined, undefined), linkDeAssinatura('?tela=Assinatura', null)],
              dias: [diaDeBrasilia(Date.UTC(2026, 10, 13, 2, 30), 'America/Sao_Paulo'), diaDeBrasilia(Date.UTC(2026, 10, 13, 3, 0), 'America/Sao_Paulo'),
                     diaDeBrasilia(Date.UTC(2026, 0, 1, 2, 59), 'America/Sao_Paulo'), diaDeBrasilia(Date.UTC(2026, 6, 15, 12, 0), 'America/Sao_Paulo')],
              vista: [expiradaJaVistaHoje(null, null, '2026-11-12'), expiradaJaVistaHoje('2026-11-12', null, '2026-11-12'),
                      expiradaJaVistaHoje(null, '2026-11-12', '2026-11-12'), expiradaJaVistaHoje('2026-11-11', '2026-11-11', '2026-11-12'),
                      expiradaJaVistaHoje(undefined, undefined, '2026-11-12'), expiradaJaVistaHoje('2026-11-12', '2026-11-12', ''),
                      expiradaJaVistaHoje('2026-11-12', null, null)],
            }));
        """)
        self.assertEqual(obtido['links'], [True, True, True, False, False, False, False])
        self.assertEqual(obtido['dias'], ['2026-11-12', '2026-11-13', '2025-12-31', '2026-07-15'])   # sempre o dia de Brasília, nunca o UTC
        self.assertEqual(obtido['vista'], [False, True, True, False, False, False, False])

    def test_formatos(self):
        obtido = self.rodar("""
            console.log(JSON.stringify({
              datas: ['2026-11-21', '2026-01-05', '', null, undefined, '21/11/2026', '2026-1-5', '2026-11-21T10:00:00'].map(formatarDataPlano),
              dias: [7, 2, 1, 0, -1, null, undefined, 1.5, '3'].map(textoDias),
              valor: [150, 100, null, 0, 1.5].map(textoValorStars),
              stars: [{ stars: 150 }, { stars: 10000 }, {}, null, { stars: 0 }].map(textoPrecoStars),
              reais: [{ reais_aproximado: 19 }, { reais_aproximado: 19.5 }, {}, null, { reais_aproximado: 0 }].map(textoPrecoReais),
              ia: [17, 25, null, 0, undefined].map(textoLimiteIa),
              dica: [textoDicaCategorias(false), textoDicaCategorias(true)],
              confirmar: textoConfirmarCancelamento('2026-12-12'),
              aviso: avisoDoModoLeitura(),
            }));
        """)
        self.assertEqual(obtido['datas'], ['21/11/2026', '05/01/2026', '', '', '', '', '', ''])
        self.assertEqual(obtido['dias'], ['Faltam 7 Dias.', 'Faltam 2 Dias.', 'Falta 1 Dia.', 'Termina Hoje.', '', '', '', '', ''])
        self.assertEqual(obtido['valor'], ['150 Stars por mês', '100 Stars por mês', '', '', ''])
        self.assertEqual(obtido['stars'], ['150 Stars', '10000 Stars', '', '', ''])
        self.assertEqual(obtido['reais'], ['≈ R$ 19 (varia conforme a loja)', '≈ R$ 19,5 (varia conforme a loja)', '', '', ''])
        self.assertEqual(obtido['ia'], ['Até 17 Registros Por IA Por Dia', 'Até 25 Registros Por IA Por Dia', '', '', ''])
        self.assertEqual(obtido['dica'][0], 'Clique Nas Barras Para Visualizar e Editar as Categorias')
        self.assertEqual(obtido['dica'][1], 'Veja Quanto Cada Categoria Gastou. Para Criar ou Editar, Assine o Plano Pro.')
        self.assertEqual(obtido['confirmar'], 'Cancelar a renovação? Seu Plano Pro continua ativo até 12/12/2026.')
        self.assertEqual(obtido['aviso'], {'antes': '', 'negrito': 'Assine o Pro', 'depois': ' Para Continuar se Organizando',
                                          'texto': 'Assine o Pro Para Continuar se Organizando'})

    def test_estado_da_folha_pela_tabela_da_nota(self):
        obtido = self.rodar("""
            const base = { ligada: true };
            const casos = {
              gratis: { selo: 'gratis' },
              gratisComRenovacao: { selo: 'gratis', proxima_renovacao: '2026-12-12' },
              proAtivo: { selo: 'pro', proxima_renovacao: '2026-12-12', renovacao_cancelada: false },
              proAtivoMesmoCancelado: { selo: 'pro', proxima_renovacao: '2026-12-12', renovacao_cancelada: true },
              cancelada: { selo: 'pro', proxima_renovacao: null, renovacao_cancelada: true },
              dado: { selo: 'pro', proxima_renovacao: null, renovacao_cancelada: false },
              dadoSemCampos: { selo: 'pro' },
              assinar: { selo: 'assinar' }, semSelo: { selo: null },
            };
            const saida = {};
            for (const [nome, c] of Object.entries(casos)) saida[nome] = estadoDaFolha(Object.assign({}, base, c));
            saida.desligada = estadoDaFolha({ ligada: false, selo: 'pro' });
            saida.nulo = estadoDaFolha(null);
            console.log(JSON.stringify(saida));
        """)
        self.assertEqual(obtido, {'gratis': 'gratis', 'gratisComRenovacao': 'gratis', 'proAtivo': 'pro-ativo', 'proAtivoMesmoCancelado': 'pro-ativo',
                                  'cancelada': 'renovacao-cancelada', 'dado': 'pro-dado', 'dadoSemCampos': 'pro-dado', 'assinar': None,
                                  'semSelo': None, 'desligada': None, 'nulo': None})

    def test_modos_da_tela_assinaturas_e_da_expirada(self):
        obtido = self.rodar("""
            const b = (c) => Object.assign({ ligada: true }, c);
            console.log(JSON.stringify({
              modos: [b({ estado: 'sem_plano' }), b({ estado: 'teste' }), b({ estado: 'pro', proxima_renovacao: '2026-12-12' }),
                      b({ estado: 'pro', renovacao_cancelada: true }), b({ estado: 'pro' }), b({ estado: 'teste_vencido' }), b({ estado: 'pro_vencido' }),
                      b({ estado: 'outro' }), { ligada: false, estado: 'teste' }, null].map(modoDaTelaAssinaturas),
              expirada: [b({ estado: 'teste_vencido', fim: '2026-11-11' }), b({ estado: 'pro_vencido', fim: '2026-11-01' }), b({ estado: 'teste' }), null].map(textosDaExpirada),
            }));
        """)
        self.assertEqual(obtido['modos'], ['principal', 'teste', 'pro-ativo', 'renovacao-cancelada', 'pro-dado', 'vencido', 'vencido', None, None, None])
        self.assertEqual(obtido['expirada'][0]['botao'], 'Assinar')
        self.assertFalse(obtido['expirada'][0]['renovacao'])
        self.assertEqual(obtido['expirada'][1]['botao'], 'Renovar Assinatura')
        self.assertTrue(obtido['expirada'][1]['renovacao'])
        self.assertEqual(obtido['expirada'][2:], [None, None])

    def test_decisoes_por_status_das_rotas_novas(self):
        obtido = self.rodar("""
            const r = (erro, detalhe, dados) => (erro === undefined ? { dados } : { erro, detalhe });
            console.log(JSON.stringify({
              teste: [r(undefined, null, { inicio: '2026-11-12', fim: '2026-11-22', repetido: false }), r(undefined, null, { inicio: 'a', fim: 'b', repetido: true }),
                      r(409, 'teste_ja_usado'), r(409, 'plano_ativo'), r(409, 'vagas_teste_esgotadas'), r(503, 'banco_ocupado'), r(500, 'erro_interno'),
                      r(0), r(401), r(403, 'assinatura_vencida')].map(decisaoDoTeste),
              fatura: [r(undefined, null, { link: 'https://t.me/$abc', stars: 150 }), r(undefined, null, { link: 'ftp://x' }), r(undefined, null, {}),
                       r(409, 'assinaturas_desligadas'), r(409, 'plano_ativo'), r(502, 'telegram_indisponivel'), r(500, 'erro_interno'), r(0), r(401)].map(decisaoDaFatura),
              pagamento: ['paid', 'pending', 'cancelled', 'failed', 'outra', undefined, null].map(decisaoDoPagamento),
              cancelar: [r(undefined, null, {}), r(502, 'telegram_indisponivel'), r(0), r(409, 'sem_assinatura_stars'), r(409, 'reativacao_recusada'), r(422, 'cancelar_invalido'),
                         r(401)].map((x) => decisaoDaRenovacao(true, x)),
              reativar: [r(undefined, null, {}), r(409, 'reativacao_recusada'), r(502, 'telegram_indisponivel'), r(0)].map((x) => decisaoDaRenovacao(false, x)),
              acesso: [[403, 'assinatura_vencida'], [403, 'assinatura_ausente'], [403, 'outra'], [409, 'assinatura_vencida'], [200, undefined], [401, 'assinatura_vencida']]
                        .map((x) => acaoDoAcessoRecusado(x[0], x[1])),
            }));
        """)
        sessao = 'Sua sessão expirou. Feche e abra o app de novo.'
        falhou_teste = 'Não Conseguimos Começar o Teste. Tente de Novo.'
        self.assertEqual(obtido['teste'], [
            {'acao': 'ativado', 'inicio': '2026-11-12', 'fim': '2026-11-22'}, {'acao': 'ativado', 'inicio': 'a', 'fim': 'b'},
            {'acao': 'recarregar'}, {'acao': 'recarregar'}, {'acao': 'esgotado'},
            {'acao': 'erro', 'mensagem': falhou_teste}, {'acao': 'erro', 'mensagem': falhou_teste}, {'acao': 'erro', 'mensagem': falhou_teste},
            {'acao': 'erro', 'mensagem': sessao}, {'acao': 'erro', 'mensagem': falhou_teste}])
        falhou_fatura = 'Não Conseguimos Abrir o Pagamento. Tente de Novo.'
        self.assertEqual(obtido['fatura'], [
            {'acao': 'abrir', 'link': 'https://t.me/$abc'}, {'acao': 'erro', 'mensagem': falhou_fatura}, {'acao': 'erro', 'mensagem': falhou_fatura},
            {'acao': 'erro', 'mensagem': 'As assinaturas ainda não estão abertas. Volte em breve.'}, {'acao': 'recarregar'},
            {'acao': 'erro', 'mensagem': falhou_fatura}, {'acao': 'erro', 'mensagem': falhou_fatura}, {'acao': 'erro', 'mensagem': falhou_fatura},
            {'acao': 'erro', 'mensagem': sessao}])
        self.assertEqual(obtido['pagamento'], ['confirmar', 'confirmar', 'voltar', 'recusado', 'voltar', 'voltar', 'voltar'])
        nao_cancelou = {'acao': 'erro', 'mensagem': 'Não foi possível cancelar agora. Tente de novo.'}
        self.assertEqual(obtido['cancelar'], [{'acao': 'atualizar'}, nao_cancelou, nao_cancelou, nao_cancelou, nao_cancelou, nao_cancelou,
                                              {'acao': 'erro', 'mensagem': sessao}])
        self.assertEqual(obtido['reativar'], [
            {'acao': 'atualizar'},
            {'acao': 'recusada', 'mensagem': 'Não foi possível reativar por aqui. Você pode assinar de novo sem perder nenhum dia.'},
            {'acao': 'erro', 'mensagem': 'Não foi possível reativar agora. Tente de novo.'},
            {'acao': 'erro', 'mensagem': 'Não foi possível reativar agora. Tente de novo.'}])
        self.assertEqual(obtido['acesso'], ['vencida', 'ausente', None, None, None, None])

    def test_pagamento_confirmado_so_pelo_json_do_servidor(self):
        obtido = self.rodar("""
            const b = (c) => Object.assign({ ligada: true, estado: 'pro', vale_ate: '2026-12-12', renovacao_cancelada: false }, c);
            console.log(JSON.stringify([
              pagamentoConfirmado(b({ estado: 'pro_vencido' }), b()),                       // venceu e pagou
              pagamentoConfirmado(b({ estado: 'teste' }), b()),                             // teste e pagou
              pagamentoConfirmado(null, b()),
              pagamentoConfirmado(b({ estado: 'pro_vencido' }), b({ estado: 'pro_vencido' })),     // ainda não chegou
              pagamentoConfirmado(b({ estado: 'teste' }), b({ estado: 'teste' })),
              pagamentoConfirmado(b(), b()),                                                // já era Pro e nada mudou
              pagamentoConfirmado(b(), b({ vale_ate: '2027-01-11' })),                      // pagou de novo: o fim andou
              pagamentoConfirmado(b({ renovacao_cancelada: true }), b({ renovacao_cancelada: false })),
              pagamentoConfirmado(b({ estado: 'teste' }), { ligada: false, estado: 'pro' }),
              pagamentoConfirmado(b({ estado: 'teste' }), null),
            ]));
        """)
        self.assertEqual(obtido, [True, True, True, False, False, False, True, True, False, False])

    def test_aguardar_pagamento_consulta_de_2_em_2_s_por_ate_30_s(self):
        obtido = self.rodar("""
            (async () => {
              const pago = { ligada: true, estado: 'pro', vale_ate: '2026-12-12' };
              const venceu = { ligada: true, estado: 'pro_vencido' };
              async function cenario(respostas) {
                let agora = 0; const esperas = []; let consultas = 0;
                const r = await aguardarPagamento(async () => { consultas++; const x = respostas(consultas); return x; },
                  async (ms) => { esperas.push(ms); agora += ms; }, () => agora, venceu, PAGAMENTO_LIMITE_MS, PAGAMENTO_PASSO_MS);
                return { r, consultas, esperas: esperas.length, passo: esperas[0] };
              }
              const logo = await cenario((n) => ({ dados: n >= 3 ? pago : venceu }));
              const nunca = await cenario(() => ({ dados: venceu }));
              const rede = await cenario((n) => (n < 5 ? { erro: 0 } : { dados: pago }));
              const primeira = await cenario(() => ({ dados: pago }));
              console.log(JSON.stringify({ logo, nunca, rede, primeira }));
            })();
        """)
        self.assertEqual(obtido['logo']['r'], {'confirmado': True, 'bloco': {'ligada': True, 'estado': 'pro', 'vale_ate': '2026-12-12'}})
        self.assertEqual((obtido['logo']['consultas'], obtido['logo']['esperas'], obtido['logo']['passo']), (3, 2, 2000))
        self.assertEqual(obtido['nunca']['r'], {'confirmado': False})
        self.assertEqual((obtido['nunca']['consultas'], obtido['nunca']['esperas']), (16, 15))    # 0, 2, 4, ..., 30 s
        self.assertTrue(obtido['rede']['r']['confirmado'])                                       # falha de rede na consulta: segue tentando
        self.assertEqual(obtido['rede']['consultas'], 5)
        self.assertEqual((obtido['primeira']['consultas'], obtido['primeira']['esperas']), (1, 0))

    def test_textos_das_folhas_da_tela_e_do_obrigado(self):
        obtido = self.rodar("""
            const b = (c) => Object.assign({ ligada: true, preco: { stars: 150, reais_aproximado: 19 } }, c);
            const pro = b({ estado: 'pro', selo: 'pro', inicio: '2026-11-12', fim: '2026-12-12', vale_ate: '2026-12-12', proxima_renovacao: '2026-12-12', valor_stars: 150 });
            console.log(JSON.stringify({
              obrigadoNova: textosDoObrigado('nova', 'Ygor', '2026-12-12'), obrigadoRenovacao: textosDoObrigado('renovacao', 'Ygor', '2026-12-12'),
              semNome: textosDoObrigado('nova', '  ', '2026-12-12'), semData: textosDoObrigado('nova', 'Ygor', null), semNomeRenovacao: textosDoObrigado('renovacao', null, null),
              tipos: [tipoDoObrigado(b({ estado: 'pro_vencido' })), tipoDoObrigado(b({ estado: 'teste' })), tipoDoObrigado(b({ estado: 'teste_vencido' })), tipoDoObrigado(null)],
              ativado: [textosDoTesteAtivado('2026-11-22'), textosDoTesteAtivado(undefined)],
              tela: { pro: textosDaTelaAssinaturas('pro-ativo', pro), dado: textosDaTelaAssinaturas('pro-dado', pro), nulo: textosDaTelaAssinaturas('pro-ativo', null),
                      outro: textosDaTelaAssinaturas('vencido', pro) },
              canc: textosDaTelaAssinaturas('renovacao-cancelada', pro).precoApoio,
              semPreco: textosDaTelaAssinaturas('principal', b({ preco: null, teste_previsto: { inicio: '2026-11-12', fim: '2026-11-22' } })).apoio,
              arrasto: [0, 50, 99, 100, 250, NaN, null].map(arrastoFechaAFolha),
              cadastro: [cadastroFechado({ ligada: true, cadastro_aberto: false }), cadastroFechado({ ligada: true, cadastro_aberto: true }),
                         cadastroFechado({ ligada: true }), cadastroFechado({ ligada: false, cadastro_aberto: false }), cadastroFechado(null)],
              passa: ['nao-iniciado', 'pendente', 'ligada', 'falhou', 'desligada'].map(cadastroPassaPelasAssinaturas),
            }));
        """)
        self.assertEqual(obtido['obrigadoNova'], {'titulo': 'Obrigado Pela Assinatura, Ygor!',
                         'texto': 'Seu Plano Pro Está Ativo. Ele Renova em 12/12/2026 Usando as Stars Que Estão na Sua Conta.'})
        self.assertEqual(obtido['obrigadoRenovacao'], {'titulo': 'Assinatura Renovada!', 'texto':
                         'Obrigado Por Continuar Com a Gente, Ygor. Agradecemos a Sua Confiança no L1d3r. Seu Plano Pro Renova em 12/12/2026 Usando as Stars Que Estão na Sua Conta.'})
        self.assertEqual(obtido['semNome']['titulo'], 'Obrigado Pela Assinatura!')
        self.assertEqual(obtido['semData']['texto'], 'Seu Plano Pro Está Ativo.')
        self.assertEqual(obtido['semNomeRenovacao'], {'titulo': 'Assinatura Renovada!',
                         'texto': 'Obrigado Por Continuar Com a Gente. Agradecemos a Sua Confiança no L1d3r.'})
        self.assertEqual(obtido['tipos'], ['renovacao', 'nova', 'nova', 'nova'])
        self.assertEqual(obtido['ativado'], [{'titulo': 'Seu Teste Começou!', 'texto': 'Vale Até 22/11/2026'}, {'titulo': 'Seu Teste Começou!', 'texto': ''}])
        self.assertEqual(obtido['tela']['pro']['linhas'], [['Ativo Até:', '12/12/2026'], ['Renovação:', 'Automática']])
        self.assertEqual(obtido['tela']['pro']['botao'], {'texto': 'Cancelar Renovação', 'acao': 'cancelar', 'claro': True})
        self.assertFalse(obtido['tela']['dado']['mostrarPreco'])
        self.assertIsNone(obtido['tela']['dado']['botao'])
        self.assertIsNone(obtido['tela']['nulo'])
        self.assertIsNone(obtido['tela']['outro'])
        self.assertEqual(obtido['canc'], 'Não Haverá Nova Cobrança. Reative Para Continuar Depois de 12/12/2026.')
        self.assertEqual(obtido['semPreco'], 'Nossa Aplicação Conta Com Uma Assinatura Simbólica Por Mês. Mas Não Se Preocupe: Liberamos 10 Dias Gratuitos Para Novos Usuários Testarem.')
        self.assertEqual(obtido['arrasto'], [False, False, False, True, True, False, False])
        # PRO-109: com a chave desligada, cadastro_aberto false também fecha (antes da PRO-109, false).
        self.assertEqual(obtido['cadastro'], [True, False, False, True, False])
        self.assertEqual(obtido['passa'], [True, True, True, True, False])

    def test_limite_do_429_vem_do_bloco(self):
        obtido = self.rodar("""
            const r = { erro: 429, detalhe: 'limite_diario_ia' };
            console.log(JSON.stringify([mensagemErroRegistro(r, 'interpretar', 'gasto', 25), mensagemErroRegistro(r, 'interpretar', 'gasto', 17),
                                        mensagemErroRegistro(r, 'interpretar', 'gasto'), mensagemErroRegistro(r, 'interpretar', 'gasto', null)]));
        """)
        self.assertEqual(obtido[0], 'Você usou os 25 registros com IA de hoje. Amanhã o limite volta a zero.')
        self.assertEqual(obtido[1], obtido[2])
        self.assertEqual(obtido[1], obtido[3])
        self.assertIn('17', obtido[1])
        self.assertEqual(assinaturas.LIMITE_IA_DIA_PADRAO, 17)



# =============================================================================
# TRAVA DE CADASTRO (PRO-109): cadastro_aberto com a chave desligada e o 403
# cadastro_fechado do POST /cadastro
# =============================================================================
class TestTravaDeCadastroNaTela(unittest.TestCase):
    def rotas_do_cadastro(self, assinatura, extras=None):
        return TestAssinaturasNaPagina.rotas_do_cadastro(self, assinatura, extras)

    def test_funcoes_puras_cadastro_fechado_e_decisao_do_403(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({
              fechado: [cadastroFechado({ ligada: false, cadastro_aberto: false }), cadastroFechado({ ligada: true, cadastro_aberto: false }),
                        cadastroFechado({ ligada: false, cadastro_aberto: true }), cadastroFechado({ ligada: false }),
                        cadastroFechado({ cadastro_aberto: 'false' }), cadastroFechado({ cadastro_aberto: 0 }), cadastroFechado(null),
                        cadastroFechado(undefined), cadastroFechado('x')],
              decisoes: [decisaoDoCadastro({ erro: 403, detalhe: 'cadastro_fechado' }), decisaoDoCadastro({ erro: 403, detalhe: 'outro' }),
                         decisaoDoCadastro({ erro: 409, detalhe: 'cadastro_fechado' })],
            }));
        """))
        self.assertEqual(obtido['fechado'], [True, True, False, False, False, False, False, False, False])
        falhou = {'seguir': False, 'mensagem': 'Não foi possível criar o seu cadastro. Tente de novo.'}
        self.assertEqual(obtido['decisoes'], [{'seguir': False, 'fechado': True, 'mensagem': None}, falhou, falhou])

    def test_chave_desligada_com_cadastro_fechado_apaga_o_sou_novo_por_aqui(self):
        bloco = bloco_assinatura(ligada=False, selo=None, cadastro_aberto=False)
        obtido = rodar_pagina({'rotas': self.rotas_do_cadastro(bloco)}, """
            await __ocioso();
            $('escolha-novo').click();                            // o toque não faz nada
            console.log(JSON.stringify({ desabilitado: $('escolha-novo').disabled, sub: $('escolha-novo-sub').textContent,
              caixa: __visivel('escolha-fechado'), amigo: $('escolha-amigo').disabled, amigoNovo: $('amigo-novo').disabled,
              boasVindas: __visivel('boas-vindas'), cadastro: __visivel('cadastro'), estado: assEstado.previo.estado }));
        """)
        self.assertTrue(obtido['desabilitado'])
        self.assertEqual(obtido['sub'], 'Cadastros fechados')
        self.assertTrue(obtido['caixa'])
        self.assertFalse(obtido['amigo'])                         # "Já Sou Amigo do Adm" continua normal
        self.assertTrue(obtido['amigoNovo'])                      # o "Sou Novo Por Aqui" do "Não Encontramos" também
        self.assertTrue(obtido['boasVindas'])
        self.assertFalse(obtido['cadastro'])
        self.assertEqual(obtido['estado'], 'desligada')           # o resto do "desligada" não muda

    def test_403_cadastro_fechado_volta_a_escolha_no_quadro_fechado(self):
        rotas = self.rotas_do_cadastro(bloco_assinatura(ligada=False, selo=None, cadastro_aberto=True), {
            'POST /cadastro': {'status': 403, 'body': {'detail': 'cadastro_fechado'}}})
        obtido = rodar_pagina({'rotas': rotas}, """
            await __ocioso();
            const antes = { desabilitado: $('escolha-novo').disabled, caixa: __visivel('escolha-fechado') };
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            continuarCadastro(); await __ocioso();                // desligada: direto para as categorias (passo 3)
            const passo = cadastroEstado.passo;
            continuarCadastro(); await __ocioso();                // POST /cadastro -> 403 cadastro_fechado
            console.log(JSON.stringify({ antes, passo, boasVindas: __visivel('boas-vindas'), cadastro: __visivel('cadastro'),
              tour: __visivel('tour'), logo: __visivel('tela-logo'), caixa: __visivel('escolha-fechado'),
              desabilitado: $('escolha-novo').disabled, sub: $('escolha-novo-sub').textContent, erro: cadastroEstado.erro,
              enviando: cadastroEstado.enviando, posts: __log.fetch.filter((c) => c.metodo === 'POST').map((c) => c.rota) }));
        """)
        self.assertEqual(obtido['antes'], {'desabilitado': False, 'caixa': False})
        self.assertEqual(obtido['passo'], 3)
        self.assertEqual(obtido['posts'], ['/cadastro'])          # uma vez só: o 403 não repete
        self.assertTrue(obtido['boasVindas'])
        self.assertFalse(obtido['cadastro'])
        self.assertFalse(obtido['tour'])
        self.assertFalse(obtido['logo'])
        self.assertTrue(obtido['caixa'])
        self.assertTrue(obtido['desabilitado'])
        self.assertEqual(obtido['sub'], 'Cadastros fechados')
        self.assertIsNone(obtido['erro'])                          # sem a mensagem genérica
        self.assertFalse(obtido['enviando'])

    def test_estrutura_do_403_e_textos_dos_quadros(self):
        principal = codigo_principal(HOME)
        enviar = corpo_da_funcao(principal, 'async function enviarCadastro')
        fechar = corpo_da_funcao(principal, 'function fecharCadastroNaEscolha')
        self.assertIn('mostrarTelaBoasVindas("boas-vindas");', fechar)
        self.assertIn('desenharCadastroFechado();', fechar)
        self.assertIn('{ cadastro_aberto: false }', fechar)
        # O fechado não espera o tempo mínimo da logo (o comTempoMinimo só espera quando segue).
        self.assertIn('(r) => decisaoDoCadastro(r).seguir', enviar)
        previa = corpo_da_funcao(principal, 'async function buscarAssinaturaPrevia')
        self.assertIn('previo.estado = "desligada";', previa)
        self.assertIn('previo.bloco = r.dados && typeof r.dados === "object" ? r.dados : null;', previa)
        self.assertNotIn('blocoDeAssinatura', corpo_da_funcao(codigo_puro(HOME), 'function cadastroFechado'))
        for texto in ('As Vagas de Teste de Hoje Acabaram.', 'Novos Cadastros Estão Fechados no Momento.'):
            with self.subTest(texto=texto):
                self.assertIn(texto, HOME)

    # Quem já tem acesso (admin = Pro dado, teste em andamento, Pro ativo, renovação cancelada) não fica preso na
    # tela Assinaturas do cadastro: vai direto ao passo 3. Sem plano continua vendo a tela.
    def test_funcao_pura_cadastro_pula_as_assinaturas(self):
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify(['teste', 'pro-ativo', 'renovacao-cancelada', 'pro-dado', 'principal', 'vencido', null]
              .map(cadastroPulaAsAssinaturas)));
        """))
        self.assertEqual(obtido, [True, True, True, True, False, False, False])

    def depois_do_passo_2(self, bloco):
        return rodar_pagina({'rotas': self.rotas_do_cadastro(bloco)}, """
            await __ocioso();
            $('escolha-novo').click();
            cadastroEstado.nome = 'Ygor'; cadastroEstado.dia = 5; cadastroEstado.passo = 2; atualizarCadastro();
            continuarCadastro();
            await __ocioso();
            console.log(JSON.stringify({ passo: cadastroEstado.passo, cadastro: __visivel('cadastro'), assinaturas: __visivel('assinaturas'),
              expirada: __visivel('expirada'), origem: assEstado.origem }));
        """)

    def test_cadastro_de_quem_ja_tem_acesso_vai_direto_as_categorias(self):
        for nome, bloco in (('admin', BLOCO_ADMIN), ('teste', BLOCO_TESTE), ('pro', BLOCO_PRO), ('cancelado', BLOCO_CANCELADO)):
            with self.subTest(bloco=nome):
                obtido = self.depois_do_passo_2(bloco)
                self.assertEqual(obtido['passo'], 3)
                self.assertTrue(obtido['cadastro'])
                self.assertFalse(obtido['assinaturas'])

    def test_cadastro_sem_plano_continua_mostrando_a_tela_assinaturas(self):
        obtido = self.depois_do_passo_2(BLOCO_SEM_PLANO)
        self.assertEqual(obtido['passo'], 2)
        self.assertFalse(obtido['cadastro'])
        self.assertTrue(obtido['assinaturas'])

    def test_rotear_so_pula_no_cadastro(self):
        rotear = corpo_da_funcao(codigo_principal(HOME), 'function rotearAssinaturas')
        self.assertIn('if (modo === null || (assEstado.origem === "cadastro" && cadastroPulaAsAssinaturas(modo))) {', rotear)
        self.assertLess(rotear.index('abrirExpirada();'), rotear.index('cadastroPulaAsAssinaturas(modo)'))

# ----------------------------------------------------------------------------
# PRO-112: tela cheia (só iPhone e iPad, em tela grande), respiros de segurança e atalho na tela inicial
# ----------------------------------------------------------------------------
TG_IOS = {'platform': 'ios', 'versao': 8.0, 'tela': [390, 844]}
TG_IOS_ATALHO = dict(TG_IOS, atalho={'status': 'missed'})
ATALHO_MARCA = 'atalho_tela_inicial_perguntado'

# Espera a abertura terminar e deixa passar mais voltas do laço de eventos (a espera de 1,5 s do atalho é um timer do relógio de mentira).
ESPERAR_TUDO = """
    await __ocioso();
    for (let i = 0; i < 300; i++) await new Promise((r) => setImmediate(r));
"""
SAIDA_TELEGRAM = """
    console.log(JSON.stringify({ telaCheia: __log.telaCheia, status: __log.status, atalhos: __log.atalhos,
      marca: __local && __local.dados ? (__local.dados.%s || null) : null,
      nuvem: __nuvem && __nuvem.dados ? Object.keys(__nuvem.dados) : null,
      classe: document.documentElement.classList.contains('tela-cheia-ativa'), chamadas: __chamadas(),
      home: __visivel('tela'), expirada: __visivel('expirada'), assinaturas: __visivel('assinaturas'),
      escolha: __visivel('boas-vindas'), folha: __visivel('folha-minha') }));
""" % ATALHO_MARCA


def abrir_no_telegram(telegram, rotas=None, extras_entrada=None, cenario=''):
    """Abre a home no navegador de mentira com 'telegram' (ver o harness) e devolve o resumo de SAIDA_TELEGRAM."""
    entrada = {'rotas': rotas if rotas is not None else rotas_da_home()}
    if telegram is not None:
        entrada['telegram'] = telegram
    entrada.update(extras_entrada or {})
    return rodar_pagina(entrada, ESPERAR_TUDO + cenario + SAIDA_TELEGRAM)


@unittest.skipUnless(NODE, 'node não encontrado')
class TestTelaCheiaEAtalhoFuncoesPuras(unittest.TestCase):
    def test_constantes_do_bloco_puro(self):
        puro = codigo_puro(HOME)
        self.assertIn('const ALTURA_MINIMA_TELA_CHEIA_PX = 700;', puro)
        self.assertIn('const ESPERA_AVISO_ATALHO_MS = 1500;', puro)
        for nome in ('deveEntrarEmTelaCheia', 'devePerguntarAtalho', 'statusPedeAtalho'):
            self.assertIn('function %s(' % nome, puro)

    def test_deve_entrar_em_tela_cheia(self):
        casos = [
            # (plataforma, versaoOk, jaEmTelaCheia, ladoMaior) -> esperado
            (('ios', True, False, 844), True),
            (('ios', True, False, 700), True),           # valor limite: 700 entra
            (('ios', True, False, 699), False),          # 699 não entra
            (('ios', True, False, 667), False),          # iPhone SE
            (('ios', True, False, 1366), True),          # iPad
            (('ios', True, False, None), False),         # sem leitura da tela
            (('ios', True, False, 'x'), False),
            (('ios', True, True, 844), False),           # já está em tela cheia
            (('ios', False, False, 844), False),         # versão antiga ou sem a função
            (('android', True, False, 900), False),      # Android não pede
        ] + [((plataforma, True, False, 900), False)
             for plataforma in ('tdesktop', 'macos', 'web', 'weba', 'webk', 'unigram', 'unknown', '', None, 'iOS')]
        obtido = rodar_node(com_funcoes_puras("""
            const casos = ENTRADA.casos.map((c) => c[0]);
            const sem_lado = [['ios', true, false, NaN], ['ios', true, false, undefined], ['ios', true, false, Infinity]];
            console.log(JSON.stringify({ casos: casos.map((c) => deveEntrarEmTelaCheia(...c)),
                                          semLado: sem_lado.map((c) => deveEntrarEmTelaCheia(...c)),
                                          vazio: deveEntrarEmTelaCheia() }));
        """, ), {'casos': casos})
        self.assertEqual(obtido['casos'], [esperado for _, esperado in casos])
        self.assertEqual(obtido['semLado'], [False, False, False])
        self.assertFalse(obtido['vazio'])

    def test_deve_perguntar_atalho(self):
        base = {'destino': 'home', 'modoLeitura': False, 'jaPerguntou': False, 'suporta': True}
        variantes = [
            base,
            dict(base, destino='escolha'), dict(base, destino='aviso'), dict(base, destino='expirada'),
            dict(base, destino='assinaturas'), dict(base, destino=None), dict(base, destino='Home'),
            dict(base, modoLeitura=True), dict(base, jaPerguntou=True),
            dict(base, suporta=False), dict(base, suporta=None), dict(base, suporta='sim'),
        ]
        obtido = rodar_node(com_funcoes_puras("""
            console.log(JSON.stringify({ r: ENTRADA.v.map((c) => devePerguntarAtalho(c)), vazio: devePerguntarAtalho(), nulo: devePerguntarAtalho(null) }));
        """), {'v': variantes})
        self.assertEqual(obtido['r'], [True] + [False] * (len(variantes) - 1))
        self.assertFalse(obtido['vazio'])
        self.assertFalse(obtido['nulo'])

    def test_status_pede_atalho_so_com_missed_ou_unknown(self):
        valores = ['missed', 'unknown', 'added', 'unsupported', None, '', 'Missed', 'Unknown', 'missed ', 'qualquer', 1, True]
        obtido = rodar_node(com_funcoes_puras("console.log(JSON.stringify(ENTRADA.v.map((v) => statusPedeAtalho(v)).concat([statusPedeAtalho()])));"),
                            {'v': valores})
        self.assertEqual(obtido, [True, True] + [False] * (len(valores) - 2 + 1))   # o último False é a chamada sem argumento


@unittest.skipUnless(NODE, 'node não encontrado')
class TestTelaCheiaNaPagina(unittest.TestCase):
    def test_so_o_ios_em_tela_grande_com_suporte_pede_a_tela_cheia_uma_vez(self):
        casos = [
            ('ios 844', TG_IOS, 1),
            ('ios deitado (lado maior = largura)', dict(TG_IOS, tela=[844, 390]), 1),
            ('ios 700', dict(TG_IOS, tela=[393, 700]), 1),
            ('ios 699', dict(TG_IOS, tela=[393, 699]), 0),
            ('ios SE', dict(TG_IOS, tela=[375, 667]), 0),
            ('ios sem leitura da tela', {'platform': 'ios', 'versao': 8.0}, 0),
            ('ios versão antiga', dict(TG_IOS, versao=7.10), 0),
            ('ios sem a função', dict(TG_IOS, semTelaCheia=True), 0),
            ('ios já em tela cheia', dict(TG_IOS, emTelaCheia=True), 0),
            ('android', dict(TG_IOS, platform='android'), 0),
        ] + [(plataforma or 'vazio', dict(TG_IOS, platform=plataforma), 0)
             for plataforma in ('tdesktop', 'macos', 'web', 'weba', 'webk', 'unigram', 'unknown', '')]
        for nome, telegram, esperado in casos:
            with self.subTest(caso=nome):
                obtido = abrir_no_telegram(telegram)
                self.assertEqual(obtido['telaCheia'], esperado)
                self.assertTrue(obtido['home'])
                self.assertEqual(obtido['chamadas'], ABERTURA)    # nada mudou na abertura

    def test_sem_o_objeto_de_telegram_novo_nada_acontece(self):
        obtido = abrir_no_telegram(None)
        self.assertEqual(obtido['telaCheia'], 0)
        self.assertFalse(obtido['classe'])
        self.assertTrue(obtido['home'])

    def test_a_classe_so_existe_com_a_tela_cheia_ligada(self):
        # Pedir a tela cheia não liga a classe: ela segue o tg.isFullscreen (confirmado pelo evento fullscreenChanged).
        obtido = abrir_no_telegram(TG_IOS)
        self.assertEqual(obtido['telaCheia'], 1)
        self.assertFalse(obtido['classe'])
        obtido = rodar_pagina({'rotas': rotas_da_home(), 'telegram': TG_IOS}, ESPERAR_TUDO + """
            const passos = [];
            const classe = () => document.documentElement.classList.contains('tela-cheia-ativa');
            passos.push(classe());
            __webapp.isFullscreen = true;  (__eventos.fullscreenChanged || []).forEach((f) => f());
            passos.push(classe());
            __webapp.isFullscreen = false; (__eventos.safeAreaChanged || []).forEach((f) => f());
            passos.push(classe());
            __webapp.isFullscreen = true;  (__eventos.contentSafeAreaChanged || []).forEach((f) => f());
            passos.push(classe());
            __webapp.isFullscreen = false; (__eventos.viewportChanged || []).forEach((f) => f());
            passos.push(classe());
            console.log(JSON.stringify({ passos, eventos: Object.keys(__eventos).sort(),
              ouvintes: ['fullscreenChanged', 'safeAreaChanged', 'contentSafeAreaChanged', 'viewportChanged'].map((e) => (__eventos[e] || []).length >= 1) }));
        """)
        self.assertEqual(obtido['passos'], [False, True, False, True, False])
        self.assertEqual(obtido['ouvintes'], [True, True, True, True])
        # Já aberto em tela cheia (qualquer plataforma): a classe liga na abertura, sem pedir nada.
        for plataforma in ('ios', 'android', 'tdesktop'):
            with self.subTest(plataforma=plataforma):
                obtido = abrir_no_telegram(dict(TG_IOS, platform=plataforma, emTelaCheia=True))
                self.assertTrue(obtido['classe'])
                self.assertEqual(obtido['telaCheia'], 0)

    def test_estrutura_da_chamada_no_iniciar(self):
        principal = codigo_principal(HOME)
        iniciar = corpo_da_funcao(principal, 'function iniciar()')
        self.assertLess(iniciar.index('tg.expand()'), iniciar.index('pedirTelaCheia();'))
        self.assertLess(iniciar.index('pedirTelaCheia();'), iniciar.index('carregar(true)'))
        for ainda in ('tg.ready()', 'tg.setHeaderColor("#2F3133")', 'tg.setBackgroundColor("#FFFFFF")'):
            self.assertIn(ainda, iniciar)
        pedir = corpo_da_funcao(principal, 'function pedirTelaCheia')
        self.assertIn('try {', pedir)
        self.assertIn('typeof tg.requestFullscreen === "function"', pedir)
        self.assertIn('tg.isVersionAtLeast("8.0")', pedir)
        self.assertIn('deveEntrarEmTelaCheia(tg.platform, versaoOk, tg.isFullscreen === true, ladoMaiorDaTelaDoAparelho())', pedir)
        self.assertIn('Math.max(window.screen.width, window.screen.height)', corpo_da_funcao(principal, 'function ladoMaiorDaTelaDoAparelho'))
        self.assertIn('catch (e) { return null; }', corpo_da_funcao(principal, 'function ladoMaiorDaTelaDoAparelho'))
        for evento in ('fullscreenChanged', 'safeAreaChanged', 'contentSafeAreaChanged'):
            self.assertIn('"%s"' % evento, corpo_da_funcao(principal, 'function ligarEventosDaTelaCheia'))
        aoMudar = corpo_da_funcao(principal, 'function aoMudarATelaCheia')
        self.assertIn('atualizarClasseTelaCheia();', aoMudar)
        self.assertIn('ajustarAlturaTela();', aoMudar)
        self.assertIn('tg.isFullscreen === true', corpo_da_funcao(principal, 'function atualizarClasseTelaCheia'))


@unittest.skipUnless(NODE, 'node não encontrado')
class TestAtalhoNaTelaInicial(unittest.TestCase):
    def test_missed_na_abertura_na_home_grava_a_marca_antes_e_pergunta_uma_vez(self):
        for nome, assinatura in (('sem a chave', 'ausente'), ('teste', BLOCO_TESTE), ('pro', BLOCO_PRO), ('admin', BLOCO_ADMIN)):
            with self.subTest(bloco=nome):
                obtido = abrir_no_telegram(TG_IOS_ATALHO, rotas_da_home(assinatura))
                self.assertTrue(obtido['home'])
                self.assertEqual(obtido['status'], 1)
                self.assertEqual(obtido['atalhos'], ['1'])    # uma chamada, e a marca já estava gravada nesse momento
                self.assertEqual(obtido['marca'], '1')
                self.assertNotIn(ATALHO_MARCA, obtido['nuvem'] or [])
                self.assertEqual(obtido['chamadas'], ABERTURA)

    def test_unknown_do_iphone_pergunta_uma_vez_e_a_segunda_abertura_nao(self):
        # No Telegram para iOS o checkHomeScreenStatus responde sempre "unknown": o aviso aparece, e a marca do aparelho segura a repetição.
        iphone = dict(TG_IOS, atalho={'status': 'unknown'})
        obtido = abrir_no_telegram(iphone)
        self.assertTrue(obtido['home'])
        self.assertEqual(obtido['status'], 1)
        self.assertEqual(obtido['atalhos'], ['1'])    # uma chamada, com a marca já gravada nesse momento
        self.assertEqual(obtido['marca'], '1')
        self.assertNotIn(ATALHO_MARCA, obtido['nuvem'] or [])
        obtido = abrir_no_telegram(iphone, extras_entrada={'local': {'dados': {ATALHO_MARCA: '1'}}})
        self.assertEqual(obtido['status'], 0)
        self.assertEqual(obtido['atalhos'], [])
        # Dentro da mesma sessão (carregar(true) de novo) também não repete.
        obtido = rodar_pagina({'rotas': rotas_da_home(), 'telegram': iphone}, ESPERAR_TUDO + """
            await carregar(true);
            for (let i = 0; i < 300; i++) await new Promise((r) => setImmediate(r));
            console.log(JSON.stringify({ atalhos: __log.atalhos.length, status: __log.status }));
        """)
        self.assertEqual((obtido['atalhos'], obtido['status']), (1, 1))

    def test_vale_para_android_e_qualquer_plataforma_com_suporte(self):
        for plataforma in ('android', 'ios', 'tdesktop'):
            with self.subTest(plataforma=plataforma):
                obtido = abrir_no_telegram(dict(TG_IOS_ATALHO, platform=plataforma))
                self.assertEqual(obtido['atalhos'], ['1'])
                self.assertEqual(obtido['telaCheia'], 1 if plataforma == 'ios' else 0)   # a tela cheia segue só o iOS

    def test_segunda_abertura_com_a_marca_nao_consulta_nem_pergunta(self):
        obtido = abrir_no_telegram(TG_IOS_ATALHO, extras_entrada={'local': {'dados': {ATALHO_MARCA: '1'}}})
        self.assertEqual(obtido['status'], 0)
        self.assertEqual(obtido['atalhos'], [])
        # Marca com outro valor ("0", vazia) não vale como "já perguntou": a pergunta ainda vale.
        obtido = abrir_no_telegram(TG_IOS_ATALHO, extras_entrada={'local': {'dados': {ATALHO_MARCA: '0'}}})
        self.assertEqual(obtido['atalhos'], ['1'])

    def test_a_pergunta_nao_se_repete_na_mesma_sessao_nem_na_seguinte(self):
        obtido = rodar_pagina({'rotas': rotas_da_home(), 'telegram': TG_IOS_ATALHO}, ESPERAR_TUDO + """
            const primeira = __log.atalhos.length;
            await carregar(true);
            for (let i = 0; i < 300; i++) await new Promise((r) => setImmediate(r));
            console.log(JSON.stringify({ primeira, depois: __log.atalhos.length, status: __log.status }));
        """)
        self.assertEqual((obtido['primeira'], obtido['depois'], obtido['status']), (1, 1, 1))

    def test_outros_status_e_falta_de_resposta_nao_perguntam_nem_gravam(self):
        for nome, atalho in (('added', {'status': 'added'}), ('unsupported', {'status': 'unsupported'}),
                             ('texto qualquer', {'status': 'qualquer'}), ('vazio', {'status': ''}), ('nulo', {'status': None}),
                             ('sem resposta em 2 s', {})):
            with self.subTest(status=nome):
                obtido = abrir_no_telegram(dict(TG_IOS, atalho=atalho))
                self.assertEqual(obtido['status'], 1)
                self.assertEqual(obtido['atalhos'], [])
                self.assertIsNone(obtido['marca'])
                self.assertTrue(obtido['home'])

    def test_sem_suporte_nem_consulta(self):
        for nome, telegram in (('versão antiga', dict(TG_IOS_ATALHO, versao=7.10)), ('sem objeto novo', None)):
            with self.subTest(caso=nome):
                obtido = abrir_no_telegram(telegram)
                self.assertEqual(obtido['status'], 0)
                self.assertEqual(obtido['atalhos'], [])
                self.assertIsNone(obtido['marca'])
        # Sem uma das duas funções: nada.
        for faltando in ('checkHomeScreenStatus', 'addToHomeScreen'):
            with self.subTest(sem=faltando):
                obtido = rodar_pagina({'rotas': rotas_da_home(), 'telegram': TG_IOS_ATALHO}, "delete __webapp.%s;\n" % faltando
                                      + ESPERAR_TUDO + SAIDA_TELEGRAM)
                self.assertEqual(obtido['atalhos'], [])
                self.assertIsNone(obtido['marca'])

    def test_localstorage_quebrado_ou_ausente_nao_pergunta(self):
        for nome, local in (('quebrado', {'modo': 'quebrado'}), ('ausente', {'modo': 'sem'})):
            with self.subTest(local=nome):
                obtido = abrir_no_telegram(TG_IOS_ATALHO, extras_entrada={'local': local})
                self.assertEqual(obtido['atalhos'], [])
                self.assertEqual(obtido['status'], 0)         # a leitura com erro já basta: nem consulta
                self.assertTrue(obtido['home'])
        # Lê bem e a escrita dá erro: consulta, mas não pergunta (sem a marca, repetiria em toda abertura).
        obtido = abrir_no_telegram(TG_IOS_ATALHO, extras_entrada={'local': {'modo': 'sem-escrita'}})
        self.assertEqual(obtido['status'], 1)
        self.assertEqual(obtido['atalhos'], [])
        self.assertTrue(obtido['home'])

    def test_nunca_na_escolha_inicial_expirada_assinaturas_nem_modo_leitura(self):
        nao_cadastrado = rotas_da_home(extras={'GET /me': {'status': 404, 'body': {'detail': 'usuario_nao_cadastrado'}}})
        casos = [
            ('escolha', nao_cadastrado, {}, 'escolha'),
            ('expirada', rotas_da_home(BLOCO_TESTE_VENCIDO), {}, 'expirada'),
            ('expirada de pro', rotas_da_home(BLOCO_PRO_VENCIDO), {}, 'expirada'),
            ('assinaturas (sem plano)', rotas_da_home(BLOCO_SEM_PLANO, extras={'GET /assinatura': {'status': 200, 'body': BLOCO_SEM_PLANO}}),
             {}, 'assinaturas'),
            ('assinaturas (link)', rotas_da_home(BLOCO_TESTE, extras={'GET /assinatura': {'status': 200, 'body': BLOCO_TESTE}}),
             {'busca': '?tela=assinatura'}, 'assinaturas'),
            ('modo leitura na home (Expirada já vista hoje)', rotas_da_home(BLOCO_TESTE_VENCIDO), {'local': {'hoje': True}}, 'home'),
        ]
        for nome, rotas, extras, tela in casos:
            with self.subTest(caso=nome):
                obtido = abrir_no_telegram(TG_IOS_ATALHO, rotas, extras)
                self.assertTrue(obtido[tela if tela != 'escolha' else 'escolha'], nome)
                self.assertEqual(obtido['status'], 0)
                self.assertEqual(obtido['atalhos'], [])
                self.assertIsNone(obtido['marca'])
        # Erro no /me (aviso): idem.
        obtido = abrir_no_telegram(TG_IOS_ATALHO, rotas_da_home(extras={'GET /me': {'status': 500, 'body': {'detail': 'x'}}}))
        self.assertEqual((obtido['status'], obtido['atalhos']), (0, []))

    def test_recargas_sem_abertura_nunca_perguntam(self):
        # O fim do tour, o "Já Sou Amigo do Adm", o Registrar e o "Tentar de novo" chamam carregar() sem 'abertura'.
        obtido = rodar_pagina({'rotas': rotas_da_home(), 'telegram': dict(TG_IOS, atalho={'status': 'added'})}, ESPERAR_TUDO + """
            ENTRADA.telegram.atalho.status = 'missed';
            const antes = { status: __log.status, atalhos: __log.atalhos.length };
            await carregar();
            for (let i = 0; i < 300; i++) await new Promise((r) => setImmediate(r));
            await carregar(false);
            for (let i = 0; i < 300; i++) await new Promise((r) => setImmediate(r));
            console.log(JSON.stringify({ antes, status: __log.status, atalhos: __log.atalhos.length,
              marca: __local.dados.%s || null }));
        """ % ATALHO_MARCA)
        self.assertEqual(obtido['antes'], {'status': 1, 'atalhos': 0})
        self.assertEqual(obtido['status'], 1)       # nenhuma consulta nova
        self.assertEqual(obtido['atalhos'], 0)
        self.assertIsNone(obtido['marca'])

    def test_se_a_home_nao_esta_mais_livre_depois_da_espera_nao_pergunta_nem_grava(self):
        for nome, abrir in (('uma folha por cima da home', 'folha'), ('outra tela no lugar da home', 'tela')):
            with self.subTest(caso=nome):
                obtido = abrir_no_telegram(dict(TG_IOS, atalho={'status': 'missed', 'abrir': abrir}))
                self.assertEqual(obtido['status'], 1)
                self.assertEqual(obtido['atalhos'], [])
                self.assertIsNone(obtido['marca'])
        # Na próxima abertura (sem a marca), pergunta.
        obtido = abrir_no_telegram(TG_IOS_ATALHO)
        self.assertEqual(obtido['atalhos'], ['1'])

    def test_estrutura_do_atalho(self):
        principal = codigo_principal(HOME)
        self.assertIn('const CHAVE_ATALHO_PERGUNTADO = "atalho_tela_inicial_perguntado";', principal)
        # A chamada é só no fim do carregar, na abertura, depois dos outros destinos.
        carregar = corpo_da_funcao(principal, 'async function carregar')
        self.assertEqual(principal.count('perguntarAtalhoDaTelaInicial('), 2)    # a chamada e a definição
        self.assertIn('else if (abertura) perguntarAtalhoDaTelaInicial(destinoAssinatura, me.assinatura);', carregar)
        self.assertGreater(carregar.index('perguntarAtalhoDaTelaInicial('), carregar.index('abrirAssinaturasNaAbertura();'))
        fluxo = corpo_da_funcao(principal, 'async function perguntarAtalhoDaTelaInicial')
        self.assertIn('try {', fluxo)
        self.assertIn('modoLeituraDoBloco(blocoAssinatura)', fluxo)
        self.assertIn('await esperar(ESPERA_AVISO_ATALHO_MS);', fluxo)
        self.assertLess(fluxo.index('await esperar(ESPERA_AVISO_ATALHO_MS);'), fluxo.index('homeLivreParaOAviso()'))
        self.assertLess(fluxo.index('homeLivreParaOAviso()'), fluxo.index('gravarAtalhoPerguntado()'))
        self.assertLess(fluxo.index('gravarAtalhoPerguntado()'), fluxo.index('tg.addToHomeScreen()'))
        self.assertEqual(principal.count('addToHomeScreen()'), 1)
        suporte = corpo_da_funcao(principal, 'function telegramSuportaAtalho')
        for trecho in ('tg.isVersionAtLeast("8.0")', 'typeof tg.checkHomeScreenStatus === "function"',
                       'typeof tg.addToHomeScreen === "function"', 'try {'):
            self.assertIn(trecho, suporte)
        # Tempo limite de 2 s num setTimeout só.
        self.assertIn('const TEMPO_STATUS_ATALHO_MS = 2000;', principal)
        self.assertIn('setTimeout(() => fim(null), TEMPO_STATUS_ATALHO_MS);', corpo_da_funcao(principal, 'function consultarStatusDoAtalho'))
        # A marca: só localStorage, sempre em try, e nada de nuvem.
        for assinatura in ('function atalhoJaPerguntado', 'function gravarAtalhoPerguntado'):
            corpo = corpo_da_funcao(principal, assinatura)
            self.assertIn('window.localStorage', corpo)
            self.assertIn('try {', corpo)
            self.assertIn('catch (e)', corpo)
            self.assertNotIn('CloudStorage', corpo)
        self.assertIn('catch (e) { return true; }', corpo_da_funcao(principal, 'function atalhoJaPerguntado'))   # erro de leitura: não pergunta
        self.assertIn('catch (e) { return false; }', corpo_da_funcao(principal, 'function gravarAtalhoPerguntado'))  # erro de escrita: não pergunta
        for tudo_fora in (re.sub(r'//[^\n]*', '', principal),):
            self.assertNotIn('CloudStorage.setItem(CHAVE_ATALHO', tudo_fora)
        self.assertNotIn('setInterval', HOME)
        # Sem texto novo na tela: nenhum elemento novo no HTML além dos já existentes.
        self.assertNotIn('addToHomeScreen', HOME[:HOME.index('INICIO FUNCOES PURAS')])


class TestRespirosDeSegurancaNoCss(unittest.TestCase):
    def test_variaveis_em_root_com_os_valores_de_hoje(self):
        raiz = regra_css(':root')
        self.assertIn('--respiro-topo: env(safe-area-inset-top, 0px);', raiz)
        self.assertIn('--respiro-rodape: env(safe-area-inset-bottom, 0px);', raiz)

    def test_bloco_da_tela_cheia(self):
        bloco = regra_css('html.tela-cheia-ativa')
        self.assertIn('--respiro-topo: calc(max(env(safe-area-inset-top, 0px), var(--tg-safe-area-inset-top, 0px)) '
                      '+ var(--tg-content-safe-area-inset-top, 0px));', bloco)
        self.assertIn('--respiro-rodape: calc(max(env(safe-area-inset-bottom, 0px), var(--tg-safe-area-inset-bottom, 0px)) '
                      '+ var(--tg-content-safe-area-inset-bottom, 0px));', bloco)

    def test_nenhum_env_da_area_segura_fora_das_variaveis(self):
        linhas = [l for l in HOME.splitlines() if 'env(safe-area-inset-' in l]
        self.assertEqual(len(linhas), 4)
        for linha in linhas:
            self.assertTrue(linha.strip().startswith('--respiro-'), linha)
        self.assertNotIn('safe-area-inset', HOME[HOME.index('</style>'):])   # nem no JS nem no HTML

    def test_rodapes_e_aviso_usam_as_variaveis_com_os_mesmos_numeros(self):
        casos = [
            ('.rodape', 'padding: 14px 16px calc(24px + var(--respiro-rodape))'),
            ('.registro-receita .rodape', 'padding-bottom: calc(34px + var(--respiro-rodape))'),
            ('.rodape-tela', 'padding: 14px 16px calc(32px + var(--respiro-rodape))'),
            ('.rodape-tela.com-dica', 'padding-bottom: calc(20px + var(--respiro-rodape))'),
            ('.tour-rodape', 'padding: 8px 16px calc(28px + var(--respiro-rodape))'),
            ('.folha', 'padding: 12px 24px calc(32px + var(--respiro-rodape))'),
            ('.aviso-topo', 'top: calc(12px + var(--respiro-topo))'),
        ]
        for seletor, texto in casos:
            with self.subTest(seletor=seletor):
                self.assertIn(texto, regra_css(seletor))

    def test_cabecalho_body_e_folha_so_mudam_dentro_da_tela_cheia(self):
        self.assertIn('padding-top: 16px;', regra_css('.cabecalho'))
        self.assertNotIn('respiro', regra_css('.cabecalho'))
        self.assertIn('padding-bottom: 40px;', regra_css('body'))
        self.assertNotIn('respiro', regra_css('body'))
        self.assertIn('max-height: calc(var(--altura-tela, 100vh) - 24px);', regra_css('.folha'))
        self.assertNotIn('--respiro-topo', regra_css('.folha'))
        self.assertIn('padding-top: calc(16px + var(--respiro-topo));', regra_css('html.tela-cheia-ativa .cabecalho'))
        self.assertIn('padding-bottom: calc(40px + var(--respiro-rodape));', regra_css('html.tela-cheia-ativa body'))
        self.assertIn('max-height: calc(var(--altura-tela, 100vh) - 24px - var(--respiro-topo));', regra_css('html.tela-cheia-ativa .folha'))

    def test_sem_altura_fixa_para_o_recuo_e_sem_setinterval(self):
        css = css_da_home()
        for linha in css.splitlines():
            if 'respiro' in linha and 'var(--respiro' not in linha and not linha.strip().startswith('--respiro-') and '/*' not in linha:
                self.fail(linha)
        self.assertNotRegex(css, r'--respiro-(topo|rodape):\s*\d')    # nenhum valor fixo em pixels
        self.assertNotIn('setInterval', HOME)


if __name__ == '__main__':
    unittest.main(verbosity=2)
