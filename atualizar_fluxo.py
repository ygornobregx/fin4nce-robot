import os
import json
import subprocess
import sys
from pathlib import Path

# Garante a codificação UTF-8 no terminal do Windows
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# Caminho do seu Obsidian conforme seu prompt[cite: 1]
OBSIDIAN_ROOT = Path(r"C:\Users\l1d3r\Documentos\Obsidian")

def get_last_commit():
    """Pega os dados do último commit feito."""
    try:
        cmd = ["git", "log", "-1", "--format=%H|%s|%ai"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=True)
        parts = result.stdout.strip().split("|")
        return {
            "hash": parts[0][:7], 
            "message": parts[1], 
            "date": parts[2]
        }
    except Exception as e:
        print(f"Erro ao ler histórico do Git: {e}")
        return None

def main():
    # Detecta o nome do repositório Git atual
    try:
        repo_root = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, encoding="utf-8", check=True).stdout.strip()
        project_name = Path(repo_root).name
    except Exception:
        print("Não foi possível detectar a pasta do repositório Git.")
        return

    project_path = OBSIDIAN_ROOT / "Projetos" / project_name
    if not project_path.exists():
        print(f"Aviso: Pasta do projeto não encontrada no Obsidian: {project_path}")
        return

    commit = get_last_commit()
    if not commit:
        return

    print(f"Commit detectado: [{commit['hash']}] {commit['message']}")

    # 1. Move as notas de 'Aprovado para Codar' para 'Implantacoes Liberadas'[cite: 1]
    aprovado_dir = project_path / "Aprovado para Codar"
    liberadas_dir = project_path / "Implantacoes Liberadas"
    liberadas_dir.mkdir(parents=True, exist_ok=True)

    if aprovado_dir.exists():
        for note in aprovado_dir.glob("*.md"):
            dest = liberadas_dir / note.name
            note.rename(dest)
            print(f"-> Nota movida: {note.name}")

    # 2. Atualiza os arquivos .canvas (muda amarelo para verde e insere o commit)[cite: 1]
    canvas_files = list(project_path.glob("*.canvas"))
    for canvas_file in canvas_files:
        with open(canvas_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        updated = False
        for node in data.get("nodes", []):
            if node.get("type") == "text":
                text = node.get("text", "")
                if "Aguardando Commit" in text or node.get("color") == "3": 
                    node["color"] = "4" # Cor verde padrão do Obsidian Canvas
                    node["text"] = (
                        f"Status: Commitado & Liberado\n\n"
                        f"- Mensagem: {commit['message']}\n"
                        f"- Commit SHA: `{commit['hash']}`\n"
                        f"- Data: {commit['date']}"
                    )
                    updated = True
        
        if updated:
            with open(canvas_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"-> Canvas atualizado: {canvas_file.name}")

if __name__ == "__main__":
    main()