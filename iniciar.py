"""Iniciador da produção (PRO-94): o supervisor que o Discloud liga (MAIN do
discloud.config). Também serve para o ambiente igual no teste, localmente.

Uso (a partir da raiz do repositório):

    python iniciar.py               # porta 8080 (a do Discloud)
    python iniciar.py --porta 8000  # local, atrás do ngrok

Ordem:
  1. Carrega o .env da raiz (load_dotenv, sem sobrescrever o que já está no
     ambiente). Os filhos herdam o ambiente, com PYTHONUNBUFFERED=1 para o log
     aparecer na hora.
  2. Migração: roda o bot.iniciar_banco() num processo filho próprio e espera
     terminar. Se falhar, sai com código 1 e não sobe nada. (O `import bot` só
     define funções e lê o .env: o cliente da IA e a conexão com o Telegram só
     nascem no `python bot.py`.)
  3. Sobe dois filhos: o bot (`python bot.py`) e a API (uvicorn com
     `--workers 1`, porque o limite de 10 interpretações por minuto fica na
     memória de um processo só).
  4. Vigia os dois: se um terminar (mesmo com código 0), encerra o outro
     (terminate e, passados 10 s, kill) e sai com código 1. Com o AUTORESTART
     do Discloud, tudo é religado junto.
  5. O sinal de parada (SIGTERM do "Parar" do painel, ou Ctrl+C) é repassado
     aos dois filhos, e o iniciador sai. Os handlers são instalados à mão: num
     contêiner, o processo pode ser o PID 1, que ignora o SIGTERM sem handler.

O log tem uma linha por etapa e nunca mostra valores de variáveis. O iniciador
não agenda nada nem grava datas (os jobs continuam no bot.py, em Brasília).
"""
import argparse
import os
import signal
import subprocess
import sys
import time

from dotenv import load_dotenv

RAIZ = os.path.dirname(os.path.abspath(__file__))
PORTA_PADRAO = 8080
ESPERA_ENCERRAR = 10      # segundos entre o terminate e o kill
INTERVALO_VIGIA = 0.5     # segundos entre duas olhadas nos filhos
PREFIXO_LOG = "[iniciar]"


def log(mensagem):
    print(f"{PREFIXO_LOG} {mensagem}", flush=True)


# --- COMANDOS ---
def comando_migracao(python=None):
    return [python or sys.executable, "-c", "import bot; bot.iniciar_banco()"]


def comando_bot(python=None):
    return [python or sys.executable, "bot.py"]


def comando_api(porta, python=None):
    return [python or sys.executable, "-m", "uvicorn", "api.main:app",
            "--host", "0.0.0.0", "--port", str(porta), "--workers", "1"]


def montar_comandos(porta, python=None):
    """Os três comandos, pela ordem em que rodam."""
    return {
        "migracao": comando_migracao(python),
        "bot": comando_bot(python),
        "api": comando_api(porta, python),
    }


def ambiente_dos_filhos(base=None):
    """Cópia do ambiente (já com o .env carregado) com PYTHONUNBUFFERED=1 e,
    se ainda não houver, PYTHONIOENCODING=utf-8 (os acentos e emojis do log
    não quebram quando a saída vai para um arquivo ou um pipe)."""
    ambiente = dict(os.environ if base is None else base)
    ambiente["PYTHONUNBUFFERED"] = "1"
    ambiente.setdefault("PYTHONIOENCODING", "utf-8")
    return ambiente


def criar_processo_padrao(comando, ambiente):
    """Fábrica dos filhos: herdam a saída do iniciador (o log do Discloud) e
    rodam na raiz do repositório. Os testes trocam por uma fábrica própria."""
    return subprocess.Popen(comando, cwd=RAIZ, env=ambiente)


# --- ARGUMENTOS ---
def porta_valida(texto):
    try:
        porta = int(texto)
    except ValueError:
        raise argparse.ArgumentTypeError("a porta tem de ser um número") from None
    if not 1 <= porta <= 65535:
        raise argparse.ArgumentTypeError("a porta vai de 1 a 65535")
    return porta


def ler_porta(argv=None):
    parser = argparse.ArgumentParser(description="Sobe o bot e a API juntos (migração antes).")
    parser.add_argument("--porta", type=porta_valida, default=PORTA_PADRAO,
                        help=f"porta da API (padrão: {PORTA_PADRAO}, a do Discloud)")
    return parser.parse_args(argv).porta


# --- SINAIS ---
class Parada:
    """O pedido de parada. Só uma marca: o handler do sinal roda na thread
    principal, no meio do laço, e não pode pegar trava nenhuma (um
    threading.Event pegaria a trava interna dele e poderia travar). O wait é
    um sleep curto, que o sinal interrompe."""

    def __init__(self):
        self._pedida = False

    def set(self):
        self._pedida = True

    def is_set(self):
        return self._pedida

    def wait(self, segundos):
        time.sleep(segundos)


def sinais_de_parada():
    sinais = [signal.SIGTERM, signal.SIGINT]
    if hasattr(signal, "SIGBREAK"):   # Ctrl+Break no Windows
        sinais.append(signal.SIGBREAK)
    return sinais


def instalar_sinais(parada):
    """Troca os handlers de parada por um que só acende o evento `parada` (o
    laço do vigia faz o resto). Devolve os handlers anteriores, para
    restaurar."""
    def ao_receber_sinal(signum, frame):
        parada.set()

    anteriores = {}
    for sinal in sinais_de_parada():
        anteriores[sinal] = signal.signal(sinal, ao_receber_sinal)
    return anteriores


def restaurar_sinais(anteriores):
    for sinal, handler in anteriores.items():
        signal.signal(sinal, handler)


# --- ENCERRAMENTO ---
def encerrar(processos, espera=ESPERA_ENCERRAR):
    """terminate em todos os vivos; quem não sair em `espera` segundos leva kill."""
    vivos = [p for p in processos if p.poll() is None]
    for processo in vivos:
        try:
            processo.terminate()
        except OSError:
            pass
    limite = time.monotonic() + espera
    for processo in vivos:
        try:
            processo.wait(timeout=max(0.0, limite - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                processo.kill()
            except OSError:
                pass
            processo.wait()


# --- SUPERVISOR ---
def supervisionar(comandos, porta, criar_processo=criar_processo_padrao, parada=None,
                  intervalo=INTERVALO_VIGIA, espera=ESPERA_ENCERRAR, sinais=True):
    """Roda a migração, sobe o bot e a API e vigia os dois. Devolve o código
    de saída: 0 na parada pedida por sinal, 1 em qualquer falha ou queda."""
    parada = Parada() if parada is None else parada
    anteriores = instalar_sinais(parada) if sinais else {}
    try:
        ambiente = ambiente_dos_filhos()

        migracao = criar_processo(comandos["migracao"], ambiente)
        while True:
            codigo = migracao.poll()
            if codigo is not None:
                break
            if parada.is_set():
                log("Parada pedida durante a migração: encerrando")
                encerrar([migracao], espera)
                return 0
            parada.wait(intervalo)
        if codigo != 0:
            log(f"Migração falhou (código {codigo}): o bot e a API não foram iniciados")
            return 1
        log("Migração concluída")
        if parada.is_set():
            log("Encerrando o bot e a API")
            return 0

        filhos = {}
        try:
            filhos["bot"] = criar_processo(comandos["bot"], ambiente)
            log("Bot iniciado")
            filhos["api"] = criar_processo(comandos["api"], ambiente)
            log(f"API iniciada na porta {porta}")
        except Exception as erro:
            log(f"Não consegui iniciar um processo ({type(erro).__name__}): encerrando")
            encerrar(filhos.values(), espera)
            return 1

        while True:
            if parada.is_set():
                log("Encerrando o bot e a API")
                encerrar(filhos.values(), espera)
                return 0
            for nome, processo in filhos.items():
                codigo = processo.poll()
                if codigo is not None:
                    log(f"Processo {nome} terminou (código {codigo}): encerrando o outro")
                    encerrar(filhos.values(), espera)
                    return 1
            parada.wait(intervalo)
    finally:
        restaurar_sinais(anteriores)


def main(argv=None, criar_processo=criar_processo_padrao, parada=None, intervalo=INTERVALO_VIGIA,
         espera=ESPERA_ENCERRAR, sinais=True, comandos=None):
    porta = ler_porta(argv)
    load_dotenv(os.path.join(RAIZ, ".env"), override=False)
    return supervisionar(comandos or montar_comandos(porta), porta, criar_processo=criar_processo,
                         parada=parada, intervalo=intervalo, espera=espera, sinais=sinais)


if __name__ == "__main__":
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(encoding="utf-8")
        except AttributeError:
            pass
    sys.exit(main())
