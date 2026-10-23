"""Hook pós-commit: move a pasta da tarefa de Aguardando Commit para Implantacoes Concluidas
no Obsidian e pinta o card do Canvas de verde.

Cada tarefa é uma pasta "PRO-xx - TÍTULO" (com notas e subpastas) dentro de
Projetos/<projeto>/<etapa>/. O hook só move a pasta inteira quando ela está em
Aguardando Commit; em etapas anteriores ele apenas avisa.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from calculos import FUSO_BRASILIA

# Garante a codificação UTF-8 no terminal do Windows
if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

OBSIDIAN_PADRAO = r"C:\Users\l1d3r\Documentos\Obsidian"
PASTA_AGUARDANDO = "Aguardando Commit"
PASTA_CONCLUIDAS = "Implantacoes Concluidas"
# Etapas anteriores: se a tarefa ainda estiver numa delas, o hook só avisa
ETAPAS_ANTERIORES = ("Fila de Tarefas", "Aprovado para Codar", "Testando Localmente")
TEXTO_AGUARDANDO = "Aguardando Commit"
COR_VERDE = "4"


def nome_do_projeto(git_common_dir, cwd=None):
    """Nome da pasta que contém o .git comum (igual na main e nas worktrees)."""
    return Path(cwd or os.getcwd(), git_common_dir).resolve().parent.name


def detectar_projeto():
    saida = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    return nome_do_projeto(saida)


def formatar_data(iso):
    """Converte uma data ISO 8601 para o horário de Brasília."""
    dt = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=FUSO_BRASILIA)
    return dt.astimezone(FUSO_BRASILIA).strftime("%Y-%m-%d %H:%M:%S %z")


def ler_commit_git():
    saida = subprocess.run(
        ["git", "log", "-1", "--format=%H%x00%aI%x00%s"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    ).stdout.strip()
    sha, data, mensagem = saida.split("\x00", 2)
    return sha, mensagem, formatar_data(data)


def ler_commit_simulado(valor):
    """Formato: sha|mensagem|data (a mensagem pode conter '|')."""
    sha, resto = valor.split("|", 1)
    mensagem, data = resto.rsplit("|", 1)
    return sha, mensagem, formatar_data(data)


def texto_commitado(titulo, mensagem, sha, data):
    return (
        f"{titulo}\n\n"
        f"Status: Commitado & Liberado\n\n"
        f"- Mensagem: {mensagem}\n"
        f"- Commit SHA: `{sha[:7]}`\n"
        f"- Data: {data}"
    )


def pastas_da_tarefa(pasta_etapa, codigo):
    """Pastas da etapa cujo nome começa com 'PRO-xx - ' (PRO-9 não casa PRO-99)."""
    if not pasta_etapa.is_dir():
        return []
    return sorted(p for p in pasta_etapa.iterdir()
                  if p.is_dir() and p.name.startswith(f"{codigo} - "))


def decidir_acoes(pasta_projeto, codigo, mensagem, sha, data):
    """Monta a lista de ações (sem gravar nada)."""
    acoes = []

    concluidas = pasta_projeto / PASTA_CONCLUIDAS
    pastas = pastas_da_tarefa(pasta_projeto / PASTA_AGUARDANDO, codigo)
    for pasta in pastas:
        destino = concluidas / pasta.name
        if destino.exists():
            print(f"Aviso: destino já existe, pasta não movida: {destino}")
            continue
        acoes.append({"tipo": "mover_pasta", "origem": pasta, "destino": destino})

    if not pastas:
        for etapa in ETAPAS_ANTERIORES:
            for pasta in pastas_da_tarefa(pasta_projeto / etapa, codigo):
                print(f"Aviso: tarefa ainda não está em {PASTA_AGUARDANDO}: {etapa}/{pasta.name}")

    padrao = re.compile(rf"(?<![\w-]){re.escape(codigo)}(?!\d)")
    for canvas in sorted(pasta_projeto.glob("*.canvas")):
        try:
            with open(canvas, "r", encoding="utf-8") as f:
                dados = json.load(f)
        except (OSError, ValueError) as e:
            print(f"Aviso: não foi possível ler {canvas.name}: {e}")
            continue

        cards = []
        for no in dados.get("nodes", []):
            texto = no.get("text", "")
            if (no.get("type") == "text" and padrao.search(texto)
                    and TEXTO_AGUARDANDO in texto):
                titulo = texto.split("\n", 1)[0]
                no["color"] = COR_VERDE
                no["text"] = texto_commitado(titulo, mensagem, sha, data)
                cards.append(titulo)
        if cards:
            acoes.append({"tipo": "pintar_card", "canvas": canvas, "dados": dados, "cards": cards})

    return acoes


def aplicar_acoes(acoes, dry_run):
    prefixo = "[dry-run] " if dry_run else ""
    for acao in acoes:
        if acao["tipo"] == "mover_pasta":
            origem, destino = acao["origem"], acao["destino"]
            print(f"{prefixo}-> Mover pasta: {origem.name} => {destino.parent.name}/")
            if not dry_run:
                destino.parent.mkdir(parents=True, exist_ok=True)
                origem.rename(destino)
        else:
            canvas = acao["canvas"]
            for titulo in acao["cards"]:
                print(f"{prefixo}-> Pintar card de verde em {canvas.name}: {titulo}")
            if not dry_run:
                with open(canvas, "w", encoding="utf-8", newline="\n") as f:
                    json.dump(acao["dados"], f, ensure_ascii=False, indent="\t")
                    f.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Atualiza o Obsidian após um commit.")
    parser.add_argument("--dry-run", action="store_true", help="só mostra as ações, sem gravar")
    parser.add_argument("--obsidian-root", default=OBSIDIAN_PADRAO)
    parser.add_argument("--projeto", help="nome do projeto (padrão: detectado pelo Git)")
    parser.add_argument("--commit", help='simula o commit: "sha|mensagem|data ISO 8601"')
    args = parser.parse_args(argv)

    sha, mensagem, data = ler_commit_simulado(args.commit) if args.commit else ler_commit_git()
    print(f"Commit detectado: [{sha[:7]}] {mensagem}")

    achado = re.match(r"^([A-Z]+-\d+)\b", mensagem)
    if not achado:
        print("Commit sem código de issue; nada a fazer.")
        return 0
    codigo = achado.group(1)

    projeto = args.projeto or detectar_projeto()
    pasta_projeto = Path(args.obsidian_root) / "Projetos" / projeto
    if not pasta_projeto.is_dir():
        print(f"Aviso: pasta do projeto não encontrada no Obsidian: {pasta_projeto}")
        return 0

    acoes = decidir_acoes(pasta_projeto, codigo, mensagem, sha, data)
    if not acoes:
        print(f"Aviso: nenhuma pasta ou card pendente para {codigo}; nada a fazer.")
        return 0

    aplicar_acoes(acoes, args.dry_run)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Erro ao atualizar o fluxo (ignorado): {e}")
        sys.exit(0)
