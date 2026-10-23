"""Testes do hook pós-commit (atualizar_fluxo.py). Rode: python testes_atualizar_fluxo.py"""
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import atualizar_fluxo

# Obsidian real: somente leitura, para garantir que os testes não o alteram
RAIZ_REAL = Path(r"C:\Users\l1d3r\Documentos\Obsidian\Projetos\Finance")
CANVAS_REAL = RAIZ_REAL / "Linha do Tempo Geral.canvas"

_estado_real_inicial = None


def _estado_real():
    """Hash do canvas real e lista ordenada de arquivos do projeto (None se não existir)."""
    if not RAIZ_REAL.is_dir():
        return None
    hash_canvas = None
    if CANVAS_REAL.is_file():
        hash_canvas = hashlib.sha256(CANVAS_REAL.read_bytes()).hexdigest()
    arquivos = sorted(str(p.relative_to(RAIZ_REAL)) for p in RAIZ_REAL.rglob("*"))
    return hash_canvas, arquivos


def setUpModule():
    global _estado_real_inicial
    _estado_real_inicial = _estado_real()


CARD_99 = "**PRO-99 - TESTE**\n\nAguardando Commit"
CARD_9 = "**PRO-9 - OUTRA**\n\nAguardando Commit"
CARD_50 = "**PRO-50 - ANTIGA**\n\nStatus: Commitado & Liberado"


class TestAtualizarFluxo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.projeto = self.root / "Projetos" / "Finance"
        self.aprovado = self.projeto / "Aprovado para Codar"
        self.liberadas = self.projeto / "Implantacoes Liberadas"
        self.aprovado.mkdir(parents=True)
        self.liberadas.mkdir()
        (self.aprovado / "PRO-99 - TESTE.md").write_text("nota 99", encoding="utf-8")
        (self.aprovado / "PRO-9 - OUTRA.md").write_text("nota 9", encoding="utf-8")
        self.canvas = self.projeto / "Linha do Tempo Geral.canvas"
        self.nos = [
            {"id": "a", "type": "text", "text": CARD_99, "x": 0, "y": 0, "width": 300, "height": 100, "color": "3"},
            {"id": "b", "type": "text", "text": CARD_9, "x": 400, "y": 0, "width": 300, "height": 100, "color": "3"},
            {"id": "c", "type": "text", "text": CARD_50, "x": 800, "y": 0, "width": 300, "height": 100, "color": "4"},
        ]
        with open(self.canvas, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"nodes": self.nos, "edges": []}, f, ensure_ascii=False, indent="\t")
            f.write("\n")

    def _estado(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in sorted(self.root.rglob("*")) if p.is_file()}

    def _rodar(self, mensagem, extra=(), projeto="Finance"):
        commit = f"abc1234def|{mensagem}|2026-10-22T19:04:31+00:00"
        argv = ["--obsidian-root", str(self.root), "--projeto", projeto, "--commit", commit, *extra]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            codigo = atualizar_fluxo.main(argv)
        self.assertEqual(codigo, 0)
        return buf.getvalue()

    def _cards(self):
        with open(self.canvas, "r", encoding="utf-8") as f:
            return {n["id"]: n for n in json.load(f)["nodes"]}

    def test_commit_com_codigo_move_nota_e_pinta_card(self):
        self._rodar("PRO-99 Mensagem de teste")

        self.assertFalse((self.aprovado / "PRO-99 - TESTE.md").exists())
        self.assertTrue((self.liberadas / "PRO-99 - TESTE.md").exists())
        self.assertTrue((self.aprovado / "PRO-9 - OUTRA.md").exists())
        self.assertFalse((self.liberadas / "PRO-9 - OUTRA.md").exists())

        cards = self._cards()
        c = cards["a"]
        self.assertEqual(c["color"], "4")
        self.assertEqual(c["text"].split("\n", 1)[0], "**PRO-99 - TESTE**")
        self.assertIn("Commit SHA: `abc1234`", c["text"])
        self.assertIn("Mensagem: PRO-99 Mensagem de teste", c["text"])
        self.assertIn("Data: 2026-10-22 16:04:31 -0300", c["text"])
        self.assertIn("Status: Commitado & Liberado", c["text"])
        self.assertNotIn("Aguardando Commit", c["text"])
        self.assertEqual(cards["b"], self.nos[1])
        self.assertEqual(cards["c"], self.nos[2])
        self.assertIn(b'\n\t"nodes"', self.canvas.read_bytes())

    def test_dry_run_nao_grava_nada(self):
        antes = self._estado()
        saida = self._rodar("PRO-99 Mensagem de teste", extra=["--dry-run"])
        self.assertIn("PRO-99 - TESTE.md", saida)
        self.assertIn("card", saida.lower())
        self.assertEqual(self._estado(), antes)

    def test_commit_sem_codigo_nao_muda_nada(self):
        antes = self._estado()
        saida = self._rodar("Atualiza CLAUDE.md")
        self.assertIn("Commit sem código de issue; nada a fazer.", saida)
        self.assertEqual(self._estado(), antes)

    def test_codigo_sem_nota_avisa_e_nao_muda_nada(self):
        antes = self._estado()
        saida = self._rodar("PRO-77 sem nota")
        self.assertIn("Aviso", saida)
        self.assertEqual(self._estado(), antes)

    def test_idempotencia(self):
        self._rodar("PRO-99 Mensagem de teste")
        depois_da_primeira = self._estado()
        self._rodar("PRO-99 Mensagem de teste")
        self.assertEqual(self._estado(), depois_da_primeira)

    def test_destino_existente_nao_sobrescreve(self):
        (self.liberadas / "PRO-99 - TESTE.md").write_text("antiga", encoding="utf-8")
        saida = self._rodar("PRO-99 Mensagem de teste")
        self.assertIn("Aviso", saida)
        self.assertEqual((self.liberadas / "PRO-99 - TESTE.md").read_text(encoding="utf-8"), "antiga")
        self.assertEqual((self.aprovado / "PRO-99 - TESTE.md").read_text(encoding="utf-8"), "nota 99")

    def test_mensagem_com_barra_vertical(self):
        self._rodar("PRO-99 a | b")
        self.assertIn("Mensagem: PRO-99 a | b", self._cards()["a"]["text"])

    def test_projeto_inexistente(self):
        antes = self._estado()
        saida = self._rodar("PRO-99 Mensagem de teste", projeto="Nada")
        self.assertIn("Aviso", saida)
        self.assertEqual(self._estado(), antes)

    def test_nome_do_projeto(self):
        self.assertEqual(atualizar_fluxo.nome_do_projeto(r"C:\x\Finance\.git"), "Finance")
        self.assertEqual(atualizar_fluxo.nome_do_projeto(".git", cwd=r"C:\x\Finance"), "Finance")

    def test_zz_obsidian_real_intacto(self):
        if _estado_real_inicial is None:
            self.skipTest("Obsidian real não encontrado")
        self.assertEqual(_estado_real(), _estado_real_inicial)


if __name__ == "__main__":
    unittest.main()
