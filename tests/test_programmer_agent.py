import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from jarvis_programmer import ProgrammerAgent


class ProgrammerAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp_dir.name)
        self.generated = []
        self.launched = []

        def generator(spec, request, current_code):
            self.generated.append((spec.key, request, current_code))
            if current_code is None:
                return 'print("projeto")\n'
            return current_code + '# alteração segura\n'

        self.agent = ProgrammerAgent(
            workspace=self.workspace,
            code_generator=generator,
            process_launcher=lambda command, cwd: self.launched.append((command, cwd)),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_python_project(self):
        return self.agent.handle(
            "Agente programador, crie um projeto Python chamado gastos que soma despesas"
        )

    def test_creates_project_and_manifest_inside_workspace(self):
        response = self._create_python_project()
        project = self.workspace / "gastos"
        self.assertIn("Criei o projeto", response)
        self.assertEqual((project / "main.py").read_text(encoding="utf-8"), 'print("projeto")\n')
        manifest = json.loads((project / ".jarvis" / "project.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["language"], "python")
        self.assertEqual(manifest["entrypoint"], "main.py")
        self.assertEqual(self.generated[0][:2], ("python", "soma despesas"))

    def test_lists_projects(self):
        self._create_python_project()
        response = self.agent.handle("Liste meus projetos")
        self.assertIn("gastos em Python", response)

    def test_change_requires_exact_confirmation_and_creates_checkpoint(self):
        self._create_python_project()
        source = self.workspace / "gastos" / "main.py"
        original = source.read_text(encoding="utf-8")

        response = self.agent.handle("No projeto gastos, adicione validação de valores")
        self.assertIn("confirmar alteração", response)
        self.assertEqual(source.read_text(encoding="utf-8"), original)

        response = self.agent.handle("sim")
        self.assertIn("diga exatamente", response.lower())
        self.assertEqual(source.read_text(encoding="utf-8"), original)

        response = self.agent.handle("confirmar alteração")
        self.assertIn("Alteração aplicada", response)
        self.assertIn("alteração segura", source.read_text(encoding="utf-8"))
        checkpoints = list((self.workspace / "gastos" / ".jarvis" / "checkpoints").glob("*.bak"))
        self.assertEqual(len(checkpoints), 1)

    def test_change_is_cancelled_if_file_changes_after_preview(self):
        self._create_python_project()
        source = self.workspace / "gastos" / "main.py"
        self.agent.handle("No projeto gastos, corrija a saída")
        source.write_text("mudança externa\n", encoding="utf-8")
        response = self.agent.handle("confirmar alteração")
        self.assertIn("mudou após o pedido", response)
        self.assertEqual(source.read_text(encoding="utf-8"), "mudança externa\n")

    def test_execution_never_starts_before_exact_confirmation(self):
        self._create_python_project()
        with patch("jarvis_programmer.agent.subprocess.run") as run:
            response = self.agent.handle("Execute o projeto gastos")
            self.assertIn("confirmar execução", response)
            run.assert_not_called()

            response = self.agent.handle("confirmar")
            self.assertIn("diga exatamente", response.lower())
            run.assert_not_called()

            run.return_value.returncode = 0
            run.return_value.stdout = "resultado correto\n"
            run.return_value.stderr = ""
            response = self.agent.handle("confirmar execução")
            self.assertIn("resultado correto", response)
            run.assert_called_once()
            self.assertFalse(run.call_args.kwargs["shell"])

    def test_reserved_project_name_is_blocked(self):
        response = self.agent.handle("Crie um projeto Python chamado CON")
        self.assertIn("reservado", response)
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_does_not_overwrite_existing_project(self):
        self._create_python_project()
        response = self._create_python_project()
        self.assertIn("Já existe", response)


if __name__ == "__main__":
    unittest.main()
