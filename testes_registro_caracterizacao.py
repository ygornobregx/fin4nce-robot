"""Teste de caracterização (golden master) do registro de gasto (PRO-85).

Roda os handlers REAIS do bot.py, do texto livre até a gravação, e imprime
tudo o que é observável: mensagens, botões, estados da conversa, esperas,
chamadas à IA (o prompt inteiro no G01 e o SHA-256 nos demais), o
user_data e as linhas das tabelas. A saída é comparada byte a byte antes e
depois de uma refatoração: se mudou, o comportamento mudou.

Uso (no Git Bash, da raiz do repositório):
    python testes_registro_caracterizacao.py > saida.txt && sha256sum saida.txt

Não usa .env, rede nem banco real:
  1. Antes do `import bot`: o load_dotenv vira no-op, o ambiente recebe
     valores falsos e o libsql.connect passa a levantar erro (qualquer
     tentativa de conexão real derruba o teste).
  2. bot.obter_conexao_db devolve um embrulho de um SQLite em memória cujo
     close() não fecha (só descarta o que não teve commit, como o banco real).
  3. A IA é falsa: grava o modelo e o prompt e devolve respostas roteirizadas.
  4. asyncio.sleep grava a espera e não dorme.
  5. enviar_fluxo, atualizar_callback, mostrar_resumo_temporario e
     responder_callback viram gravadores.
  6. Update e Context falsos, com message.date fixo em UTC.
  7. Relógio congelado (datetime.now) no bot, no calculos e no registro.
"""
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.stdout.reconfigure(encoding='utf-8')

RAIZ = os.path.dirname(os.path.abspath(__file__))
if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

# --- COSTURA 1: nada de .env, chave real nem conexão real (antes do import) ---
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False
for _chave in ('GOOGLE_API_KEY', 'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_CLOUD_PROJECT'):
    os.environ.pop(_chave, None)
os.environ['GEMINI_API_KEY'] = 'chave-falsa-dos-testes'
os.environ['TOKEN'] = ''
os.environ['TURSO_DATABASE_URL'] = ''
os.environ['TURSO_AUTH_TOKEN'] = ''

import libsql  # noqa: E402


def _libsql_proibido(*args, **kwargs):
    raise RuntimeError("Conexão real com o banco é proibida no teste de caracterização.")


libsql.connect = _libsql_proibido

import bot  # noqa: E402
import calculos  # noqa: E402

UID = 1
OUTRO_UID = 2
END = bot.ConversationHandler.END
DATA_MENSAGEM = datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)  # 12:30 em Brasília
AGORA_FIXO = datetime(2026, 11, 5, 15, 0, 0, tzinfo=timezone.utc)      # 12:00 em Brasília

EVENTOS = []
ESPERAS = []
CONEXOES_ABERTAS = set()


# --- COSTURA 7: relógio congelado ---
class RelogioCongelado(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return AGORA_FIXO.astimezone(calculos.FUSO_BRASILIA).replace(tzinfo=None)
        return AGORA_FIXO.astimezone(tz)


bot.datetime = RelogioCongelado
calculos.datetime = RelogioCongelado


# --- COSTURA 3: IA falsa ---
class ErroFalsoDaIA(Exception):
    pass


class IAFalsa:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.chamadas = []
        self.models = self

    def generate_content(self, model, contents):
        self.chamadas.append((model, contents))
        if not self.respostas:
            raise AssertionError("IA falsa chamada sem resposta roteirizada")
        resposta = self.respostas.pop(0)
        if isinstance(resposta, Exception):
            raise resposta
        return SimpleNamespace(text=resposta)


def instalar_ia_e_relogio(ia):
    """ÚNICO trecho que conhece as duas versões do código: antes da PRO-85, o
    cliente da IA é o global bot.ai_client; depois, fica no registro.py e é
    trocado por registro.definir_cliente_ia (e o relógio do registro também)."""
    if hasattr(bot, 'ai_client'):
        bot.ai_client = ia
    else:
        import registro
        registro.definir_cliente_ia(ia)
    registro = sys.modules.get('registro')
    if registro is not None and hasattr(registro, 'datetime'):
        registro.datetime = RelogioCongelado


# --- COSTURA 4: asyncio.sleep sem dormir ---
async def _sleep_falso(segundos, result=None):
    ESPERAS.append(segundos)
    return result


asyncio.sleep = _sleep_falso


# --- COSTURA 2: SQLite em memória compartilhado ---
class ConexaoFalsa:
    """Embrulho do SQLite em memória: o close() não fecha a conexão real (o
    fluxo abre e fecha várias vezes), mas descarta o que ficou sem commit,
    como o banco real faria, e registra isso como evento."""

    def __init__(self, real):
        self._real = real
        self._fechada = False
        CONEXOES_ABERTAS.add(self)

    def cursor(self):
        return self._real.cursor()

    def execute(self, *args):
        return self._real.execute(*args)

    def commit(self):
        self._real.commit()

    def rollback(self):
        self._real.rollback()

    def close(self):
        if self._real.in_transaction:
            EVENTOS.append("  banco: close() com alteração sem commit (descartada)")
            self._real.rollback()
        self._fechada = True
        CONEXOES_ABERTAS.discard(self)


# --- COSTURA 5: gravadores das mensagens ---
def _teclado(markup):
    if markup is None:
        return "None"
    return json.dumps(markup.to_dict(), ensure_ascii=False, sort_keys=True)


def _txt(valor):
    return json.dumps(valor, ensure_ascii=False)


async def enviar_fluxo_falso(context, chat_id, texto, reply_markup=None, parse_mode='HTML'):
    EVENTOS.append(f"  enviar_fluxo(chat={chat_id}, parse_mode={parse_mode!r})\n"
                   f"    texto: {_txt(texto)}\n"
                   f"    teclado: {_teclado(reply_markup)}")


async def atualizar_callback_falso(query, context, texto, reply_markup=None, parse_mode='HTML'):
    EVENTOS.append(f"  atualizar_callback(msg={query.message.message_id}, parse_mode={parse_mode!r})\n"
                   f"    texto: {_txt(texto)}\n"
                   f"    teclado: {_teclado(reply_markup)}")


async def mostrar_resumo_temporario_falso(context, chat_id, texto, segundos=10, parse_mode='HTML'):
    EVENTOS.append(f"  mostrar_resumo_temporario(chat={chat_id}, segundos={segundos}, parse_mode={parse_mode!r})\n"
                   f"    texto: {_txt(texto)}")


async def responder_callback_falso(query, texto=None):
    EVENTOS.append(f"  responder_callback(texto={_txt(texto)})")


bot.enviar_fluxo = enviar_fluxo_falso
bot.atualizar_callback = atualizar_callback_falso
bot.mostrar_resumo_temporario = mostrar_resumo_temporario_falso
bot.responder_callback = responder_callback_falso


def _sha(texto):
    return hashlib.sha256(texto.encode('utf-8')).hexdigest()


def resposta_json(**campos):
    return json.dumps(campos, ensure_ascii=False)


# --- COSTURA 6: Update e Context falsos, e o roteiro de cada cenário ---
class Cenario:
    def __init__(self, codigo, titulo, respostas_ia=(), com_categorias=True, imprimir_prompt=False,
                 com_receitas=False):
        print()
        print("=" * 78)
        print(f"{codigo} - {titulo}")
        print("=" * 78)
        EVENTOS.clear()
        ESPERAS.clear()
        CONEXOES_ABERTAS.clear()
        self.imprimir_prompt = imprimir_prompt
        # A tabela 'receitas' (PRO-86) só é impressa nos cenários de receita,
        # para os cenários antigos continuarem byte a byte iguais.
        self.com_receitas = com_receitas
        self.real = sqlite3.connect(':memory:')
        bot.obter_conexao_db = lambda: ConexaoFalsa(self.real)
        with contextlib.redirect_stdout(io.StringIO()):
            bot.iniciar_banco()
        self.real.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
                          "VALUES (?, 'Ygor', 5000.0, 29, '20:00')", (UID,))
        if com_categorias:
            for nome in ('Alimentação', 'Roupas', 'Lazer', 'Mercado'):
                self.real.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                                  "VALUES (?, ?, 0.0, 0.0, '2026-11-01 08:00:00')", (UID, nome))
        self.real.commit()
        self.ia = IAFalsa(respostas_ia)
        instalar_ia_e_relogio(self.ia)
        self.chamadas_vistas = 0
        self.user_data = {}
        self.context = SimpleNamespace(
            user_data=self.user_data,
            application=SimpleNamespace(handlers={}),
            bot=None,
            job_queue=None,
        )

    def preparar(self, sql, params=()):
        """Escrita direta no banco do cenário (fora do bot), impressa."""
        print(f"* preparo: {sql} {list(params)}")
        self.real.execute(sql, params)
        self.real.commit()

    def texto(self, nome_handler, texto, data=DATA_MENSAGEM, uid=UID):
        async def apagar():
            EVENTOS.append("  mensagem do usuário apagada")
        update = SimpleNamespace(
            message=SimpleNamespace(text=texto, date=data, delete=apagar),
            effective_user=SimpleNamespace(id=uid),
            effective_chat=SimpleNamespace(id=uid),
            callback_query=None,
        )
        self._rodar(f"{nome_handler}(texto={_txt(texto)}, date={data.isoformat()})",
                    lambda: getattr(bot, nome_handler)(update, self.context))

    def botao(self, nome_handler, dado, uid=UID):
        query = SimpleNamespace(
            data=dado,
            from_user=SimpleNamespace(id=uid),
            message=SimpleNamespace(chat_id=uid, message_id=500),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=uid),
            effective_chat=SimpleNamespace(id=uid),
            message=None,
        )
        self._rodar(f"{nome_handler}(botão={_txt(dado)})",
                    lambda: getattr(bot, nome_handler)(update, self.context))

    def chamar(self, nome_funcao, *args):
        print(f"> {nome_funcao}{tuple(a if not isinstance(a, ConexaoFalsa) else 'conn' for a in args)}")
        EVENTOS.clear()
        ESPERAS.clear()
        try:
            resultado = getattr(bot, nome_funcao)(*args)
            print(f"  retorno: {_txt(resultado)}")
        except Exception as e:  # noqa: BLE001
            print(f"  EXCEÇÃO: {type(e).__name__}")
        self._imprimir_efeitos()

    def conexao(self):
        return ConexaoFalsa(self.real)

    def _rodar(self, descricao, fabrica):
        print(f"> {descricao}")
        EVENTOS.clear()
        ESPERAS.clear()
        try:
            estado = asyncio.run(fabrica())
            self._imprimir_efeitos()
            print(f"  estado: {estado!r}")
        except Exception as e:  # noqa: BLE001
            self._imprimir_efeitos()
            print(f"  EXCEÇÃO: {type(e).__name__}")
        print(f"  user_data: {json.dumps(self.user_data, ensure_ascii=False, sort_keys=True, default=str)}")

    def _imprimir_efeitos(self):
        for evento in EVENTOS:
            print(evento)
        if ESPERAS:
            print(f"  esperas: {ESPERAS}")
        novas = self.ia.chamadas[self.chamadas_vistas:]
        self.chamadas_vistas = len(self.ia.chamadas)
        for modelo, prompt in novas:
            print(f"  IA: modelo={modelo!r} prompt_sha256={_sha(prompt)} tamanho={len(prompt)}")
            if self.imprimir_prompt:
                print("  prompt completo (entre barras, para mostrar os espaços):")
                for linha in prompt.split('\n'):
                    print(f"    |{linha}|")

    def fim(self):
        print("-" * 78)
        tabelas = [
            ('categorias', 'id, user_id, nome, orcamento_mensal, saldo_atual, data_criacao'),
            ('transacoes', 'id, user_id, categoria_nome, valor, data_transacao, metodo_pagamento, '
                           'descricao, parcelamento_id, estabelecimento'),
            ('parcelamentos', 'id, user_id, categoria_nome, descricao, valor_total, valor_parcela, '
                              'total_parcelas, parcelas_pagas, data_criacao, ativo, ciclo_referencia'),
            ('gastos_recorrentes', 'id, user_id, categoria_nome, descricao, valor, dia_cobranca, '
                                   'criado_em, ultimo_ciclo_cobrado'),
        ]
        if self.com_receitas:
            tabelas.append(('receitas', 'id, user_id, valor, descricao, data_receita, origem, '
                                        'receita_recorrente_id'))
        for tabela, colunas in tabelas:
            linhas = self.real.execute(f"SELECT {colunas} FROM {tabela} ORDER BY id").fetchall()
            print(f"tabela {tabela} ({colunas}):")
            if not linhas:
                print("  (vazia)")
            for linha in linhas:
                print(f"  {json.dumps(list(linha), ensure_ascii=False)}")
        print(f"conexões abertas sem fechar: {len(CONEXOES_ABERTAS)}")
        print(f"respostas da IA não usadas: {len(self.ia.respostas)}")
        self.real.close()


# --- RESPOSTAS DA IA USADAS EM MAIS DE UM CENÁRIO ---
IA_PAO = resposta_json(tipo="despesa", valor=12.0, categoria="alimentação",
                       descricao="Pão na Padaria Cisne Branco", estabelecimento="Cisne Branco")
IA_ALMOCO = resposta_json(tipo="despesa", valor=35.5, categoria="Alimentação",
                          descricao="Almoço", estabelecimento=None)
IA_TENIS = resposta_json(tipo="despesa", valor=300.0, categoria="Roupas",
                         descricao="Tênis na Nike", estabelecimento="Nike")


def g01_debito_pix_com_estabelecimento():
    c = Cenario("G01", "Débito/Pix com estabelecimento, categoria em minúscula, prompt completo",
                [IA_PAO], imprimir_prompt=True)
    c.botao('iniciar_gasto', 'menu_gastar')
    c.texto('salvar_gasto_livre', "pão na padaria cisne branco 12")
    c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
    c.fim()


def g02_cercas_de_codigo_e_descricao_com_espacos():
    resposta = "```json\n" + resposta_json(tipo="despesa", valor=59.9, categoria="Roupas",
                                            descricao="  Camisa  ", estabelecimento=None) + "\n```"
    c = Cenario("G02", "Resposta entre cercas de código, descrição com espaços, sem estabelecimento", [resposta])
    c.texto('salvar_gasto_livre', "camisa 59,90")
    c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
    c.fim()


def g03_credito_a_vista():
    c = Cenario("G03", "Crédito à vista (Crédito -> Não)", [IA_ALMOCO])
    c.texto('salvar_gasto_livre', "almoço 35,50")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_nao')
    c.fim()


def g04_parcelado_3x_e_g18_banco_adiou():
    c = Cenario("G04", "Crédito parcelado em 3x; depois G18 (Banco Adiou no mesmo parcelamento)", [IA_TENIS])
    c.texto('salvar_gasto_livre', "tênis 300 na nike")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('escolher_numero_parcelas', 'parcela_n_3')
    print("* G18 - Banco Adiou no parcelamento do G04")
    c.botao('ajustar_fatura_parcelamento', 'ajustar_fatura_1')
    c.fim()


def g05_digitar_total_de_parcelas():
    resposta = resposta_json(tipo="despesa", valor=100.0, categoria="Roupas",
                             descricao="  Calça Jeans  ", estabelecimento="  Renner ")
    c = Cenario("G05", "Digitar Total: recusas de '1' e 'abc', depois 7 parcelas; descrição com espaços", [resposta])
    c.texto('salvar_gasto_livre', "calça jeans 100,00 na renner")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('escolher_numero_parcelas', 'parcela_custom')
    c.texto('digitar_numero_parcelas', "1")
    c.texto('digitar_numero_parcelas', "abc")
    c.texto('digitar_numero_parcelas', " 7 ")
    c.fim()


def g06_ciclo_da_fatura():
    resposta = resposta_json(tipo="despesa", valor=80.0, categoria="Lazer", descricao="Cinema", estabelecimento=None)
    c = Cenario("G06a", "Parcelado 2x no próprio dia de fechamento (29, 10:00 em Brasília): fatura seguinte",
                [resposta])
    c.texto('salvar_gasto_livre', "cinema 80", data=datetime(2026, 11, 29, 13, 0, 0, tzinfo=timezone.utc))
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('escolher_numero_parcelas', 'parcela_n_2')
    c.fim()

    c = Cenario("G06b", "Parcelado 2x às 02:00 UTC do dia 29 (dia 28, 23:00 em Brasília): fatura atual",
                [resposta])
    c.texto('salvar_gasto_livre', "cinema 80", data=datetime(2026, 11, 29, 2, 0, 0, tzinfo=timezone.utc))
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('escolher_numero_parcelas', 'parcela_n_2')
    c.fim()


def g07_receita():
    resposta = resposta_json(tipo="Receita ", valor="abc", categoria="Salário", descricao="Salário",
                             estabelecimento=None)
    c = Cenario("G07", "Receita com categoria inexistente e valor inválido: recusa antes de tudo", [resposta])
    c.texto('salvar_gasto_livre', "recebi 5000 de salário")
    c.fim()


def g08_categoria_fora_da_lista():
    resposta = resposta_json(tipo="despesa", valor=20.0, categoria="transporte", descricao="Uber",
                             estabelecimento="Uber")
    c = Cenario("G08a", "Categoria fora da lista", [resposta])
    c.texto('salvar_gasto_livre', "uber 20")
    c.fim()

    c = Cenario("G08b", "Usuário sem categorias ('nenhuma categoria cadastrada')", [resposta], com_categorias=False)
    c.texto('salvar_gasto_livre', "uber 20")
    c.fim()


def g09_erros_temporarios_da_ia():
    erro_503 = ErroFalsoDaIA("503 UNAVAILABLE. {'error': {'code': 503, 'message': 'The model is overloaded.'}}")
    erro_429 = ErroFalsoDaIA("429 RESOURCE_EXHAUSTED. {'error': {'code': 429}}")
    c = Cenario("G09a", "IA: 503, 503, 503 (desiste depois de 3 tentativas)", [erro_503, erro_503, erro_503])
    c.texto('salvar_gasto_livre', "pão 12")
    c.fim()

    c = Cenario("G09b", "IA: 429 e depois resposta (segue o fluxo)", [erro_429, IA_PAO])
    c.texto('salvar_gasto_livre', "pão 12")
    c.fim()

    c = Cenario("G09c", "IA: três 429", [erro_429, erro_429, erro_429])
    c.texto('salvar_gasto_livre', "pão 12")
    c.fim()

    c = Cenario("G09d", "IA: erro comum (sem nova tentativa)", [RuntimeError("Falha qualquer da IA")])
    c.texto('salvar_gasto_livre', "pão 12")
    c.fim()


def g10_respostas_invalidas_da_ia():
    casos = [
        ("G10a", "JSON sem 'valor'", resposta_json(tipo="despesa", categoria="Mercado", descricao="X")),
        ("G10b", "'valor' = 'doze'", resposta_json(tipo="despesa", valor="doze", categoria="Mercado", descricao="X")),
        ("G10c", "Resposta sem JSON", "Não entendi a mensagem."),
        ("G10d", "JSON sem 'tipo'", resposta_json(valor=10, categoria="Mercado")),
        ("G10e", "'categoria' nula", resposta_json(tipo="despesa", valor=10, categoria=None)),
        ("G10f", "'valor' inválido e categoria inexistente (o valor é lido antes)",
         resposta_json(tipo="despesa", valor="doze", categoria="Inexistente")),
        ("G10g", "Sem 'descricao' nem 'estabelecimento' (segue o fluxo)",
         resposta_json(tipo="DESPESA", valor="15.5", categoria="MERCADO")),
        ("G10h", "Lista JSON no lugar de objeto", '[{"tipo": "despesa"}]'),
    ]
    for codigo, titulo, resposta in casos:
        c = Cenario(codigo, titulo, [resposta])
        c.texto('salvar_gasto_livre', "mercado 10")
        c.fim()


def g11_mensagem_sem_texto():
    c = Cenario("G11", "Mensagem sem texto: encerra sem chamar a IA")
    c.texto('salvar_gasto_livre', None)
    c.fim()


def g12_editar_valor():
    c = Cenario("G12a", "Depois de G01: Editar Gasto -> Editar Valor -> 'abc' e '13,50'", [IA_PAO])
    c.texto('salvar_gasto_livre', "pão na padaria cisne branco 12")
    c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
    c.botao('iniciar_editar_ultimo_gasto', 'editar_ultimo_gasto')
    c.botao('iniciar_editar_valor_ultimo_gasto', 'editar_gasto_valor')
    c.texto('salvar_valor_editado_gasto', "abc")
    c.texto('salvar_valor_editado_gasto', "13,50")
    c.fim()

    for codigo, valor in (("G12b", "-5"), ("G12c", "0"), ("G12d", "R$ 1.234,56")):
        c = Cenario(codigo, f"Editar Valor com {valor!r} (aceito como hoje; risco R6)", [IA_PAO])
        c.texto('salvar_gasto_livre', "pão na padaria cisne branco 12")
        c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
        c.botao('iniciar_editar_ultimo_gasto', 'editar_ultimo_gasto')
        c.botao('iniciar_editar_valor_ultimo_gasto', 'editar_gasto_valor')
        c.texto('salvar_valor_editado_gasto', valor)
        c.fim()


def g13_editar_categoria():
    c = Cenario("G13", "Depois de G03: Editar Categoria -> inexistente, a mesma, '  roupas '", [IA_ALMOCO])
    c.texto('salvar_gasto_livre', "almoço 35,50")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_nao')
    c.botao('iniciar_editar_ultimo_gasto', 'editar_ultimo_gasto')
    c.botao('iniciar_editar_categoria_ultimo_gasto', 'editar_gasto_categoria')
    c.texto('salvar_categoria_editada_gasto', "Viagem <b>")
    c.texto('salvar_categoria_editada_gasto', "ALIMENTACAO")
    c.texto('salvar_categoria_editada_gasto', "  roupas ")
    c.fim()


def g14_sessao_expirada():
    c = Cenario("G14a", "Sem gasto_pendente nos botões e textos do fluxo")
    c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
    c.botao('responder_parcela_sim_nao', 'parcela_nao')
    c.botao('escolher_numero_parcelas', 'parcela_n_2')
    c.texto('digitar_numero_parcelas', "3")
    c.botao('voltar_metodo_pagamento', 'voltar_metodo_pagamento')
    c.botao('voltar_confirmar_parcela', 'voltar_confirmar_parcela')
    c.fim()

    c = Cenario("G14b", "Sem ultimo_gasto na edição")
    c.botao('iniciar_editar_ultimo_gasto', 'editar_ultimo_gasto')
    c.botao('iniciar_editar_valor_ultimo_gasto', 'editar_gasto_valor')
    c.botao('iniciar_editar_categoria_ultimo_gasto', 'editar_gasto_categoria')
    c.texto('salvar_valor_editado_gasto', "10")
    c.texto('salvar_categoria_editada_gasto', "Lazer")
    c.fim()

    c = Cenario("G14c", "ultimo_gasto aponta para transação de outro usuário (filtro por user_id)")
    c.preparar("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
               "VALUES (?, 'Lazer', 0.0, 0.0, '2026-11-01 08:00:00')", (OUTRO_UID,))
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Lazer', 50.0, '2026-11-02 10:00:00')", (OUTRO_UID,))
    c.user_data['ultimo_gasto'] = {'transacao_id': 1, 'categoria_id': 5, 'categoria': 'Lazer', 'tipo': 'despesa'}
    c.botao('iniciar_editar_categoria_ultimo_gasto', 'editar_gasto_categoria')
    c.texto('salvar_valor_editado_gasto', "10")
    c.user_data['ultimo_gasto'] = {'transacao_id': 1, 'categoria_id': 5, 'categoria': 'Lazer', 'tipo': 'despesa'}
    c.texto('salvar_categoria_editada_gasto', "Mercado")
    c.fim()


def g15_categoria_apagada_no_meio():
    c = Cenario("G15a", "Categoria apagada antes de escolher as parcelas: rollback, nada gravado", [IA_TENIS])
    c.texto('salvar_gasto_livre', "tênis 300 na nike")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.preparar("DELETE FROM categorias WHERE user_id = ? AND nome = 'Roupas'", (UID,))
    c.botao('escolher_numero_parcelas', 'parcela_n_2')
    c.fim()

    c = Cenario("G15b", "Categoria apagada antes do Débito/Pix (avulso)", [IA_TENIS])
    c.texto('salvar_gasto_livre', "tênis 300 na nike")
    c.preparar("DELETE FROM categorias WHERE user_id = ? AND nome = 'Roupas'", (UID,))
    c.botao('responder_metodo_pagamento', 'metodo_debito_pix')
    c.fim()


def g16_botoes_voltar():
    c = Cenario("G16", "Botões '← Voltar' do fluxo", [IA_TENIS, IA_TENIS])
    c.botao('iniciar_gasto', 'menu_gastar')
    c.texto('salvar_gasto_livre', "tênis 300 na nike")
    c.botao('voltar_gasto_valor', 'voltar_gasto_valor')
    c.texto('salvar_gasto_livre', "tênis 300 na nike")
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('voltar_metodo_pagamento', 'voltar_metodo_pagamento')
    c.botao('responder_metodo_pagamento', 'metodo_credito')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('voltar_confirmar_parcela', 'voltar_confirmar_parcela')
    c.botao('responder_parcela_sim_nao', 'parcela_sim')
    c.botao('escolher_numero_parcelas', 'parcela_custom')
    c.botao('voltar_escolher_parcelas', 'voltar_escolher_parcelas')
    c.botao('voltar_para_menu', 'voltar_menu')
    c.fim()


def g17_lancar_item_recorrente():
    c = Cenario("G17", "_lancar_item_recorrente: lança, não lança duas vezes, categoria inexistente")
    c.preparar("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em) "
               "VALUES (?, 'Mercado', 'Academia & Cia', 99.9, 5, '2026-10-01 08:00:00')", (UID,))
    c.preparar("INSERT INTO gastos_recorrentes (user_id, categoria_nome, descricao, valor, dia_cobranca, criado_em) "
               "VALUES (?, 'Apagada', 'Streaming', 20.0, 5, '2026-10-01 08:00:00')", (UID,))
    conn = c.conexao()
    c.chamar('_lancar_item_recorrente', conn, UID, 1, '2026-11')
    c.chamar('_lancar_item_recorrente', conn, UID, 1, '2026-11')
    c.chamar('_lancar_item_recorrente', conn, UID, 2, '2026-11')
    c.chamar('_lancar_item_recorrente', conn, UID, 99, '2026-11')
    conn.close()
    c.fim()


def g19_prompt_da_recorrente():
    resposta = resposta_json(descricao="Assinatura do Spotify", valor=12.9, dia_cobranca=14)
    c = Cenario("G19a", "Recorrente completo pela IA", [resposta])
    c.botao('iniciar_criar_recorrente', 'rec_criar_4')
    c.texto('receber_texto_recorrente', "Spotify 12,90 todo dia 14")
    c.fim()

    resposta = "```json\n" + resposta_json(descricao="  Netflix ", valor=None, dia_cobranca=40) + "\n```"
    c = Cenario("G19b", "Recorrente com campos faltando (valor nulo, dia 40)", [resposta])
    c.botao('iniciar_criar_recorrente', 'rec_criar_3')
    c.texto('receber_texto_recorrente', "Netflix")
    c.texto('receber_campo_faltante_recorrente', "abc")
    c.texto('receber_campo_faltante_recorrente', "39,90")
    c.texto('receber_campo_faltante_recorrente', "14")
    c.fim()

    erro_503 = ErroFalsoDaIA("503 UNAVAILABLE")
    c = Cenario("G19c", "Recorrente com a IA indisponível (503 três vezes)", [erro_503, erro_503, erro_503])
    c.botao('iniciar_criar_recorrente', 'rec_criar_4')
    c.texto('receber_texto_recorrente', "Spotify 12,90 todo dia 14")
    c.fim()


# --- G28 A G30 (PRO-86): TEXTOS DO PAINEL E DOS RESUMOS, AINDA SEM RECEITA ---
# Gravados contra o bot.py da main ANTES da PRO-86 mexer no código: sem
# receita no banco, estas linhas têm de sair idênticas depois dela.
class BotFalso:
    """Faz o papel de context.bot nos resumos diário e mensal: grava cada
    envio e cada exclusão e devolve ids previsíveis (900, 901...)."""

    def __init__(self):
        self.proximo_id = 900

    async def send_message(self, chat_id, text, reply_markup=None, parse_mode=None):
        msg_id = self.proximo_id
        self.proximo_id += 1
        EVENTOS.append(f"  bot.send_message(chat={chat_id}, parse_mode={parse_mode!r}) -> msg {msg_id}\n"
                       f"    texto: {_txt(text)}\n"
                       f"    teclado: {_teclado(reply_markup)}")
        return SimpleNamespace(message_id=msg_id)

    async def delete_message(self, chat_id, message_id):
        EVENTOS.append(f"  bot.delete_message(chat={chat_id}, msg={message_id})")


def _preparar_gastos_dos_resumos(c):
    """Massa comum ao G28, ao G29a e ao G30 (fechamento 29, renda 5.000,
    relógio em 05/11/2026 12:00 de Brasília: fatura vigente 2026-11, de 29/10
    a 28/11, já com Sobra; a 2026-10 é anterior à data de corte, sem Sobra)."""
    for categoria, valor, data, descricao in (
        ('Alimentação', 120.0, '2026-10-10 12:00:00', 'Restaurante'),       # fatura 2026-10
        ('Lazer', 60.0, '2026-10-20 21:00:00', 'Show'),                     # fatura 2026-10
        ('Lazer', 80.0, '2026-10-30 20:00:00', 'Cinema'),                   # fatura 2026-11
        ('Mercado', 150.0, '2026-11-02 10:00:00', 'Compras do Mês'),        # fatura 2026-11
        ('Mercado', -200.0, '2026-11-05 08:00:00', 'Reembolso'),            # reembolso antigo: fora
        ('Alimentação', 45.5, '2026-11-05 09:15:00', 'Café'),               # hoje
        ('Lazer', 25.0, '2026-11-05 11:00:00', 'Livro'),                    # hoje
    ):
        c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, "
                   "metodo_pagamento, descricao) VALUES (?, ?, ?, ?, 'debito_pix', ?)",
                   (UID, categoria, valor, data, descricao))
    # Parcelado 3x de 100 (parcela 1 também é transação, que sai das avulsas).
    c.preparar("INSERT INTO parcelamentos (user_id, categoria_nome, descricao, valor_total, valor_parcela, "
               "total_parcelas, parcelas_pagas, data_criacao, ativo, ciclo_referencia) "
               "VALUES (?, 'Roupas', 'Tênis', 300.0, 100.0, 3, 1, '2026-10-15 10:00:00', 1, '2026-10')", (UID,))
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao, metodo_pagamento, "
               "descricao, parcelamento_id) VALUES (?, 'Roupas', 100.0, '2026-10-15 10:00:00', 'credito', "
               "'Tênis (1/3)', 1)", (UID,))
    # Outro usuário: não entra em nada.
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Mercado', 999.0, '2026-11-05 10:00:00')", (OUTRO_UID,))


def g28_painel_sem_receita():
    c = Cenario("G28", "Painel ('Ver Resumo' e histórico) sem receita: vigente com Sobra, "
                       "fechada antes do corte sem Sobra, futura só com parcela")
    _preparar_gastos_dos_resumos(c)
    c.botao('botao_clicado', 'menu_resumo')
    c.botao('ver_mes_historico', 'select_month_history_2026-11')
    c.botao('ver_mes_historico', 'select_month_history_2026-10')
    c.botao('ver_mes_historico', 'select_month_history_2026-12')
    c.fim()


def _rodar_resumo_diario(c):
    c.context.bot = BotFalso()
    c.context.job = SimpleNamespace(chat_id=UID)
    c.user_data['msgs_fluxo'] = [700, 701]
    c._rodar("enviar_resumo_diario(job.chat_id=1)", lambda: bot.enviar_resumo_diario(c.context))


def g29_resumo_diario_sem_receita():
    c = Cenario("G29a", "Resumo diário sem receita, dia com gasto (bloco do mês com Sobra)")
    _preparar_gastos_dos_resumos(c)
    _rodar_resumo_diario(c)
    c.fim()

    c = Cenario("G29b", "Resumo diário sem receita, dia sem gasto (o reembolso de hoje não conta)")
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Mercado', 150.0, '2026-11-02 10:00:00')", (UID,))
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Mercado', -200.0, '2026-11-05 08:00:00')", (UID,))
    _rodar_resumo_diario(c)
    c.fim()

    c = Cenario("G29c", "Resumo diário sem receita, usuário sem dia de fechamento (sem bloco do mês)")
    c.preparar("UPDATE usuarios SET dia_fechamento = NULL WHERE user_id = ?", (UID,))
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Lazer', 25.0, '2026-11-05 11:00:00')", (UID,))
    _rodar_resumo_diario(c)
    c.fim()


def g30_resumo_mensal_sem_receita():
    c = Cenario("G30", "Resumo Mensal sem receita, no dia de fechamento: fatura com Sobra, "
                       "anterior ao corte, só parcela e sem gasto nenhum")
    _preparar_gastos_dos_resumos(c)
    bot_falso = BotFalso()
    for agora in (
        datetime(2026, 11, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-11 (com Sobra)
        datetime(2026, 10, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-10 (antes do corte)
        datetime(2026, 12, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-12 (só a parcela 3/3)
        datetime(2027, 2, 28, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),    # fecha 2027-02 (nenhum gasto)
    ):
        c.user_data['msgs_fluxo'] = [700]
        c._rodar(f"enviar_resumo_mensal(user_id=1, nome='Ygor', dia_fechamento=29, agora={agora.isoformat()})",
                 lambda agora=agora: bot.enviar_resumo_mensal(bot_falso, c.user_data, UID, 'Ygor', 29, agora))
    c.fim()


# --- G20 A G27 (PRO-86): RECEITA AVULSA PELO "REGISTRAR GASTO" ---
IA_BICICLETA = resposta_json(tipo="receita", valor=500, categoria="Mercado",
                             descricao="Venda da Bicicleta", estabelecimento=None)


def g20_receita_confirmada():
    c = Cenario("G20", "Receita 'vendi a bicicleta por 500' -> prévia -> Confirmar", [IA_BICICLETA],
                com_receitas=True)
    c.botao('iniciar_gasto', 'menu_gastar')
    c.texto('salvar_gasto_livre', "vendi a bicicleta por 500")
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()


def g21_receita_cancelada():
    c = Cenario("G21", "Receita -> prévia -> Cancelar (nada gravado)", [IA_BICICLETA], com_receitas=True)
    c.texto('salvar_gasto_livre', "vendi a bicicleta por 500")
    c.botao('cancelar_receita', 'receita_cancelar')
    c.fim()


def g22_g23_valor_zero_e_negativo():
    for codigo, valor in (("G22", 0), ("G23", -200)):
        resposta = resposta_json(tipo="receita", valor=valor, categoria=None, descricao="Reembolso",
                                 estabelecimento=None)
        c = Cenario(codigo, f"Receita com valor {valor}: mensagem de valor inválido, nada gravado", [resposta],
                    com_receitas=True)
        c.texto('salvar_gasto_livre', f"recebi {valor} de reembolso")
        c.fim()


def g24_toque_duplo_no_confirmar():
    c = Cenario("G24", "Toque duplo no Confirmar: grava uma vez só; o segundo toque é 'Sessão expirada'",
                [IA_BICICLETA], com_receitas=True)
    c.texto('salvar_gasto_livre', "vendi a bicicleta por 500")
    c.botao('confirmar_receita', 'receita_confirmar')
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()


def g25_voltar_no_meio_da_previa():
    c = Cenario("G25", "Receita -> '← Voltar' (voltar_menu) no meio da prévia: limpa, nada gravado",
                [IA_BICICLETA], com_receitas=True)
    c.texto('salvar_gasto_livre', "vendi a bicicleta por 500")
    c.botao('voltar_para_menu', 'voltar_menu')
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()


def g26_descricao_vazia_e_com_html():
    resposta = resposta_json(tipo="receita", valor="150.5", categoria=None, descricao="   ",
                             estabelecimento=None)
    c = Cenario("G26a", "Receita com descrição vazia: 'Sem Informação' e NULL no banco", [resposta],
                com_receitas=True)
    c.texto('salvar_gasto_livre', "recebi 150,50")
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()

    resposta = resposta_json(tipo="Receita", valor=80.257, categoria=None,
                             descricao="  Venda <b>Bike</b> & Cia ", estabelecimento=None)
    c = Cenario("G26b", "Receita com HTML na descrição: escapada na mensagem, gravada aparada; valor arredondado",
                [resposta], com_receitas=True)
    c.texto('salvar_gasto_livre', "vendi <b>bike</b> & cia por 80,257")
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()


def g27_receita_com_categoria_fora_da_lista():
    resposta = resposta_json(tipo="receita", valor=5000, categoria="Salário", descricao="Salário",
                             estabelecimento="Empresa")
    c = Cenario("G27", "Receita com categoria fora da lista: a categoria é ignorada e registra normalmente",
                [resposta], com_receitas=True)
    c.texto('salvar_gasto_livre', "recebi 5000 de salário")
    c.botao('confirmar_receita', 'receita_confirmar')
    c.fim()

    resposta = resposta_json(tipo="transferência", valor=50, categoria="Mercado", descricao="Pix para a Ana",
                             estabelecimento=None)
    c = Cenario("G27b", "IA devolve um 'tipo' que não é despesa nem receita: nada gravado", [resposta],
                com_receitas=True)
    c.texto('salvar_gasto_livre', "mandei 50 para a ana")
    c.fim()


# --- G28 A G30 COM RECEITA (PRO-86): A LINHA NOVA, NO LUGAR COMBINADO ---
def _preparar_receitas_dos_resumos(c):
    """Receitas por cima da massa dos resumos: fatura 2026-11 = 500 + 60 = 560
    (o 60 cai no primeiro minuto do dia de fechamento 29/10); fatura 2026-10 =
    300 + 40 = 340 (o 40 às 23:59 do dia 28/10); a 2026-12 fica sem receita;
    a de outro usuário não entra."""
    for user_id, valor, data, descricao in (
        (UID, 300.0, '2026-10-15 09:00:00', 'Venda do Celular'),        # fatura 2026-10
        (UID, 40.0, '2026-10-28 23:59:00', 'Reembolso do Amigo'),       # fatura 2026-10
        (UID, 60.0, '2026-10-29 00:01:00', 'Cashback'),                 # fatura 2026-11
        (UID, 500.0, '2026-11-03 10:00:00', 'Venda da Bicicleta'),      # fatura 2026-11
        (OUTRO_UID, 999.0, '2026-11-04 10:00:00', 'De outro usuário'),  # não entra
    ):
        c.preparar("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem) "
                   "VALUES (?, ?, ?, ?, 'avulsa')", (user_id, valor, descricao, data))


def g28_painel_com_receita():
    c = Cenario("G28r", "Painel com receita: 'Receitas' abaixo de 'Renda' (também sem Sobra); "
                        "fatura sem receita igual a antes", com_receitas=True)
    _preparar_gastos_dos_resumos(c)
    _preparar_receitas_dos_resumos(c)
    c.botao('botao_clicado', 'menu_resumo')
    c.botao('ver_mes_historico', 'select_month_history_2026-11')
    c.botao('ver_mes_historico', 'select_month_history_2026-10')
    c.botao('ver_mes_historico', 'select_month_history_2026-12')
    c.fim()


def g29_resumo_diario_com_receita():
    c = Cenario("G29ar", "Resumo diário com receita, dia com gasto: 'Receitas no Mês' entre o gasto e a Sobra",
                com_receitas=True)
    _preparar_gastos_dos_resumos(c)
    _preparar_receitas_dos_resumos(c)
    _rodar_resumo_diario(c)
    c.fim()

    c = Cenario("G29br", "Resumo diário com receita hoje e nenhum gasto hoje: mensagem curta de sempre",
                com_receitas=True)
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Mercado', 150.0, '2026-11-02 10:00:00')", (UID,))
    c.preparar("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem) "
               "VALUES (?, 200.0, 'Reembolso', '2026-11-05 08:00:00', 'avulsa')", (UID,))
    _rodar_resumo_diario(c)
    c.fim()

    c = Cenario("G29cr", "Resumo diário com receita, usuário sem dia de fechamento (sem bloco do mês)",
                com_receitas=True)
    c.preparar("UPDATE usuarios SET dia_fechamento = NULL WHERE user_id = ?", (UID,))
    c.preparar("INSERT INTO transacoes (user_id, categoria_nome, valor, data_transacao) "
               "VALUES (?, 'Lazer', 25.0, '2026-11-05 11:00:00')", (UID,))
    c.preparar("INSERT INTO receitas (user_id, valor, descricao, data_receita, origem) "
               "VALUES (?, 200.0, 'Reembolso', '2026-11-05 08:00:00', 'avulsa')", (UID,))
    _rodar_resumo_diario(c)
    c.fim()


def g30_resumo_mensal_com_receita():
    c = Cenario("G30r", "Resumo Mensal com receita: 'Receitas' entre 'Renda' e o total gasto; "
                        "fatura sem receita igual a antes", com_receitas=True)
    _preparar_gastos_dos_resumos(c)
    _preparar_receitas_dos_resumos(c)
    bot_falso = BotFalso()
    for agora in (
        datetime(2026, 11, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-11 (receitas 560, com Sobra)
        datetime(2026, 10, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-10 (receitas 340, sem Sobra)
        datetime(2026, 12, 29, 0, 5, 0, tzinfo=calculos.FUSO_BRASILIA),   # fecha 2026-12 (sem receita)
    ):
        c.user_data['msgs_fluxo'] = [700]
        c._rodar(f"enviar_resumo_mensal(user_id=1, nome='Ygor', dia_fechamento=29, agora={agora.isoformat()})",
                 lambda agora=agora: bot.enviar_resumo_mensal(bot_falso, c.user_data, UID, 'Ygor', 29, agora))
    c.fim()


def main():
    print("TESTE DE CARACTERIZAÇÃO DO REGISTRO DE GASTO (PRO-85)")
    g01_debito_pix_com_estabelecimento()
    g02_cercas_de_codigo_e_descricao_com_espacos()
    g03_credito_a_vista()
    g04_parcelado_3x_e_g18_banco_adiou()
    g05_digitar_total_de_parcelas()
    g06_ciclo_da_fatura()
    g07_receita()
    g08_categoria_fora_da_lista()
    g09_erros_temporarios_da_ia()
    g10_respostas_invalidas_da_ia()
    g11_mensagem_sem_texto()
    g12_editar_valor()
    g13_editar_categoria()
    g14_sessao_expirada()
    g15_categoria_apagada_no_meio()
    g16_botoes_voltar()
    g17_lancar_item_recorrente()
    g19_prompt_da_recorrente()
    g28_painel_sem_receita()
    g29_resumo_diario_sem_receita()
    g30_resumo_mensal_sem_receita()
    g20_receita_confirmada()
    g21_receita_cancelada()
    g22_g23_valor_zero_e_negativo()
    g24_toque_duplo_no_confirmar()
    g25_voltar_no_meio_da_previa()
    g26_descricao_vazia_e_com_html()
    g27_receita_com_categoria_fora_da_lista()
    g28_painel_com_receita()
    g29_resumo_diario_com_receita()
    g30_resumo_mensal_com_receita()
    print()
    print("FIM")


if __name__ == "__main__":
    main()
