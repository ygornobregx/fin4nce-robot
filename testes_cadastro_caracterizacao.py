"""Teste de caracterização (golden master) do cadastro do bot no chat (PRO-96).

Roda os handlers REAIS do bot.py do /start (start, receber_nome,
receber_renda, receber_nomes_categorias e receber_dia_fechamento) e imprime
tudo o que é observável: mensagens, botões, estados da conversa, o user_data,
as linhas de 'usuarios' e 'categorias', o que o bot escreveu no console e o
alerta agendado no job_queue. A saída é comparada byte a byte antes e depois
da extração da lógica para o cadastro.py: se mudou, o comportamento mudou.

Uso (no Git Bash, da raiz do repositório):
    python testes_cadastro_caracterizacao.py > saida.txt 2>&1 && sha256sum saida.txt

Não usa .env, rede nem banco real (mesmas costuras do
testes_registro_caracterizacao.py):
  1. Antes do `import bot`: o load_dotenv vira no-op, o ambiente recebe
     valores falsos e o libsql.connect passa a levantar erro. A variável
     URL_MINI_APP é RETIRADA do ambiente (este teste caracteriza o cadastro
     antigo, que é o caminho sem a URL do Mini App).
  2. bot.obter_conexao_db devolve um embrulho de um SQLite em memória cujo
     close() não fecha (só descarta o que não teve commit).
  3. enviar_fluxo vira um gravador.
  4. job_queue falso, que grava os agendamentos e as remoções.
  5. Relógio congelado (datetime.now) no bot e no calculos (e no cadastro.py,
     se ele existir).
"""
import asyncio
import contextlib
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

# --- COSTURA 1: nada de .env, chave real, conexão real nem URL do Mini App ---
import dotenv  # noqa: E402

dotenv.load_dotenv = lambda *args, **kwargs: False
for _chave in ('GOOGLE_API_KEY', 'GOOGLE_GENAI_USE_VERTEXAI', 'GOOGLE_CLOUD_PROJECT', 'URL_MINI_APP'):
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

# O import do bot não pode ter trazido a URL do Mini App de volta.
os.environ.pop('URL_MINI_APP', None)

UID = 1          # já cadastrado em todos os cenários
NOVO = 2         # sem cadastro
END = bot.ConversationHandler.END
AGORA_FIXO = datetime(2026, 11, 5, 15, 0, 0, tzinfo=timezone.utc)   # 12:00 em Brasília

EVENTOS = []
CONEXOES_ABERTAS = set()


# --- COSTURA 5: relógio congelado ---
class RelogioCongelado(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return AGORA_FIXO.astimezone(calculos.FUSO_BRASILIA).replace(tzinfo=None)
        return AGORA_FIXO.astimezone(tz)


bot.datetime = RelogioCongelado
calculos.datetime = RelogioCongelado


def instalar_relogio_no_cadastro():
    """Depois da extração (PRO-96), a data de criação pode ser calculada no
    cadastro.py: ele recebe o mesmo relógio congelado, se existir."""
    cadastro = sys.modules.get('cadastro')
    if cadastro is not None and hasattr(cadastro, 'datetime'):
        cadastro.datetime = RelogioCongelado


# --- COSTURA 2: SQLite em memória compartilhado ---
class ConexaoFalsa:
    """Embrulho do SQLite em memória: o close() não fecha a conexão real (o
    fluxo abre e fecha várias vezes), mas descarta o que ficou sem commit,
    como o banco real faria, e registra isso como evento."""

    def __init__(self, real):
        self._real = real
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
        CONEXOES_ABERTAS.discard(self)


# --- COSTURA 3: gravador das mensagens ---
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


bot.enviar_fluxo = enviar_fluxo_falso


# --- COSTURA 4: job_queue falso ---
class JobFalso:
    def __init__(self, nome, horario):
        self.name = nome
        self.horario = horario

    def schedule_removal(self):
        EVENTOS.append(f"  job_queue: remove o job {self.name!r} ({self.horario})")


class JobQueueFalsa:
    def __init__(self):
        self.jobs = []

    def get_jobs_by_name(self, nome):
        return [job for job in self.jobs if job.name == nome]

    def run_daily(self, callback, time, chat_id=None, user_id=None, name=None, **extras):
        horario = time.isoformat()
        EVENTOS.append(f"  job_queue: run_daily({callback.__name__}, {horario} tz={time.tzinfo}, "
                       f"chat_id={chat_id}, user_id={user_id}, name={name!r}, extras={sorted(extras)})")
        self.jobs.append(JobFalso(name, horario))


# --- Update e Context falsos, e o roteiro de cada cenário ---
class Cenario:
    def __init__(self, codigo, titulo):
        print()
        print("=" * 78)
        print(f"{codigo} - {titulo}")
        print("=" * 78)
        EVENTOS.clear()
        CONEXOES_ABERTAS.clear()
        instalar_relogio_no_cadastro()
        self.real = sqlite3.connect(':memory:')
        bot.obter_conexao_db = lambda: ConexaoFalsa(self.real)
        with contextlib.redirect_stdout(io.StringIO()):
            bot.iniciar_banco()
        self.real.execute("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta) "
                          "VALUES (?, 'Ygor', 5000.0, 29, '20:00')", (UID,))
        for nome in ('Alimentação', 'Lazer'):
            self.real.execute("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
                              "VALUES (?, ?, 0.0, 0.0, '2026-11-01 08:00:00')", (UID, nome))
        self.real.commit()
        self.user_data = {}
        self.job_queue = JobQueueFalsa()
        self.context = SimpleNamespace(
            user_data=self.user_data,
            application=SimpleNamespace(handlers={}),
            bot=None,
            job_queue=self.job_queue,
        )

    def preparar(self, sql, params=()):
        """Escrita direta no banco do cenário (fora do bot), impressa."""
        print(f"* preparo: {sql} {list(params)}")
        self.real.execute(sql, params)
        self.real.commit()

    def preparar_job(self, nome, horario):
        print(f"* preparo: job {nome!r} já agendado ({horario})")
        self.job_queue.jobs.append(JobFalso(nome, horario))

    def comando_start(self, uid=NOVO):
        self._rodar(f"start(user={uid})", self._update(uid, "/start"), bot.start)

    def texto(self, nome_handler, texto, uid=NOVO):
        self._rodar(f"{nome_handler}(user={uid}, texto={_txt(texto)})", self._update(uid, texto),
                    getattr(bot, nome_handler))

    def _update(self, uid, texto):
        async def apagar():
            EVENTOS.append("  mensagem do usuário apagada")
        return SimpleNamespace(
            message=SimpleNamespace(text=texto, delete=apagar),
            effective_user=SimpleNamespace(id=uid),
            effective_chat=SimpleNamespace(id=uid),
            callback_query=None,
        )

    def _rodar(self, descricao, update, handler):
        print(f"> {descricao}")
        EVENTOS.clear()
        console = io.StringIO()
        try:
            with contextlib.redirect_stdout(console):
                estado = asyncio.run(handler(update, self.context))
            self._imprimir_efeitos(console)
            print(f"  estado: {estado!r}")
        except Exception as e:  # noqa: BLE001
            self._imprimir_efeitos(console)
            print(f"  EXCEÇÃO: {type(e).__name__}: {e}")
        print(f"  user_data: {json.dumps(self.user_data, ensure_ascii=False, sort_keys=True, default=str)}")

    def _imprimir_efeitos(self, console):
        for evento in EVENTOS:
            print(evento)
        for linha in console.getvalue().splitlines():
            print(f"  console: {linha}")

    def fim(self):
        print("-" * 78)
        tabelas = [
            ('usuarios', 'user_id, nome, renda_mensal, dia_fechamento, horario_alerta, '
                         'typeof(horario_alerta), ultimo_reset', 'user_id'),
            ('categorias', 'id, user_id, nome, orcamento_mensal, saldo_atual, data_criacao', 'id'),
        ]
        for tabela, colunas, ordem in tabelas:
            linhas = self.real.execute(f"SELECT {colunas} FROM {tabela} ORDER BY {ordem}").fetchall()
            print(f"tabela {tabela} ({colunas}):")
            if not linhas:
                print("  (vazia)")
            for linha in linhas:
                print(f"  {json.dumps(list(linha), ensure_ascii=False)}")
        print("alertas agendados no job_queue:")
        if not self.job_queue.jobs:
            print("  (nenhum)")
        for job in self.job_queue.jobs:
            print(f"  {job.name!r} às {job.horario}")
        print(f"conexões abertas sem fechar: {len(CONEXOES_ABERTAS)}")
        self.real.close()


def c01_start_com_cadastro():
    c = Cenario("C01", "/start de quem já tem cadastro: Menu Principal, sem tocar nos dados")
    c.comando_start(uid=UID)
    c.fim()


def c02_cadastro_completo():
    c = Cenario("C02", "Cadastro completo, sem erros (nome, renda, categorias e dia)")
    c.comando_start()
    c.texto('receber_nome', "  Ana Paula  ")
    c.texto('receber_renda', "3.000,50")
    c.texto('receber_nomes_categorias', "Mercado, Lazer, Casa")
    c.texto('receber_dia_fechamento', "5")
    c.fim()


def c03_renda_invalida():
    c = Cenario("C03", "Renda inválida: 'abc', '12,3,4' e vazia são recusadas em silêncio; depois 'R$ 1.234,56'")
    c.comando_start()
    c.texto('receber_nome', "Bia")
    c.texto('receber_renda', "abc")
    c.texto('receber_renda', "12,3,4")
    c.texto('receber_renda', "   ")
    c.texto('receber_renda', "R$ 1.234,56")
    c.texto('receber_nomes_categorias', "Pet")
    c.texto('receber_dia_fechamento', "31")
    c.fim()


def c04_categorias_repetidas_e_vazias():
    c = Cenario("C04", "Categorias: só vírgulas (continua pedindo); depois repetidas, acentos, vazias e caixa")
    c.comando_start()
    c.texto('receber_nome', "Caio")
    c.texto('receber_renda', "2500")
    c.texto('receber_nomes_categorias', " , , ")
    c.texto('receber_nomes_categorias',
            "lazer, Lazer , ,Alimentação, alimentacao,  ALIMENTAÇÃO , mercado,, Saúde, saude, SAÚDE, cAsA")
    c.texto('receber_dia_fechamento', "12")
    c.fim()


def c05_dia_invalido_e_valido():
    c = Cenario("C05", "Dia: não numérico ('dez', '10.5', ''), fora da faixa (0, 32, -3) e, por fim, ' 10 '")
    c.comando_start()
    c.texto('receber_nome', "Duda")
    c.texto('receber_renda', "1000")
    c.texto('receber_nomes_categorias', "Casa")
    c.texto('receber_dia_fechamento', "dez")
    c.texto('receber_dia_fechamento', "10.5")
    c.texto('receber_dia_fechamento', "   ")
    c.texto('receber_dia_fechamento', "0")
    c.texto('receber_dia_fechamento', "32")
    c.texto('receber_dia_fechamento', "-3")
    c.texto('receber_dia_fechamento', " 10 ")
    c.fim()


def c06_substitui_linha_e_categorias():
    c = Cenario("C06", "Gravação final substitui: linha criada no meio (INSERT OR REPLACE), categorias "
                       "órfãs apagadas e alerta antigo do mesmo usuário trocado")
    c.preparar("INSERT INTO categorias (user_id, nome, orcamento_mensal, saldo_atual, data_criacao) "
               "VALUES (?, 'Órfã', 10.0, 7.0, '2026-01-01 00:00:00')", (NOVO,))
    c.preparar_job(str(NOVO), "08:00:00+00:00")
    c.comando_start()
    c.texto('receber_nome', "Eva")
    c.texto('receber_renda', "0")
    c.texto('receber_nomes_categorias', "Mercado")
    c.preparar("INSERT INTO usuarios (user_id, nome, renda_mensal, dia_fechamento, horario_alerta, ultimo_reset) "
               "VALUES (?, 'Antigo', 99.0, 3, '07:00', '2026-10-03')", (NOVO,))
    c.texto('receber_dia_fechamento', "28")
    print("* /start de novo, já com cadastro: Menu Principal")
    c.comando_start()
    c.fim()


def main():
    print("TESTE DE CARACTERIZAÇÃO DO CADASTRO NO CHAT (PRO-96)")
    c01_start_com_cadastro()
    c02_cadastro_completo()
    c03_renda_invalida()
    c04_categorias_repetidas_e_vazias()
    c05_dia_invalido_e_valido()
    c06_substitui_linha_e_categorias()
    print()
    print("FIM")


if __name__ == "__main__":
    main()
