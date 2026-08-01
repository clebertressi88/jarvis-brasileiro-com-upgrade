import tempfile
import unittest
from pathlib import Path

from jarvis_core import SafeActionCoordinator
from jarvis_core.model_mode import ModelModeController
from jarvis_core.semantic_planner import PlannedAction
from jarvis_tools import ComputerCommandRouter


class FakeAgent:
    def __init__(self, response=None, *, pending=False, installer=False):
        self.response = response
        self.calls = []
        self.pending_action = object() if pending else None
        if installer:
            self.pending_installation = object() if pending else None

    def handle(self, text):
        self.calls.append(text)
        return self.response


class ActionCoordinatorTests(unittest.TestCase):
    def test_pending_confirmation_has_priority(self):
        memory = FakeAgent(response="resposta comum")
        programmer = FakeAgent(response=None)
        installer = FakeAgent(response="instalado e verificado", pending=True, installer=True)
        computer = FakeAgent(response=None)
        coordinator = SafeActionCoordinator(
            memory=memory,
            programmer=programmer,
            installer=installer,
            computer=computer,
        )

        response = coordinator.handle("confirmar instalação")

        self.assertEqual(response, "instalado e verificado")
        self.assertEqual(installer.calls, ["confirmar instalação"])
        self.assertEqual(memory.calls, [])

    def test_agent_exception_fails_closed(self):
        class BrokenAgent(FakeAgent):
            def handle(self, _text):
                raise RuntimeError("falha simulada")

        coordinator = SafeActionCoordinator(
            memory=BrokenAgent(),
            programmer=FakeAgent(),
            installer=None,
            computer=FakeAgent(),
        )
        response = coordinator.handle("ação local")
        self.assertIn("interrompida com segurança", response)

    def test_pending_save_location_has_priority_over_other_agents(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            documents = root / "Documents"
            desktop = root / "Desktop"
            downloads = root / "Downloads"
            for folder in (documents, desktop, downloads):
                folder.mkdir()

            memory = FakeAgent(response=None)
            computer = ComputerCommandRouter(
                allowed_roots={
                    "area de trabalho": desktop,
                    "documentos": documents,
                    "downloads": downloads,
                },
                process_launcher=lambda _command: None,
                file_opener=lambda _path: None,
                key_sender=lambda _key: None,
                installed_program_provider=lambda: (),
            )
            coordinator = SafeActionCoordinator(
                memory=memory,
                programmer=FakeAgent(),
                installer=None,
                computer=computer,
                semantic_planner=object(),
                mode_controller=ModelModeController(),
            )

            question = coordinator.handle(
                "Crie um arquivo chamado prioridades.txt contendo teste"
            )
            self.assertIn("Onde deseja salvar", question)

            memory.response = "resposta que não deve interceptar"
            memory.calls.clear()
            response = coordinator.handle("Downloads")

            self.assertIn("Arquivo criado", response)
            self.assertTrue((downloads / "prioridades.txt").is_file())
            self.assertEqual(memory.calls, [])

    def test_semantic_plan_uses_only_canonical_safe_computer_command(self):
        class FakePlanner:
            def plan(self, _text):
                return (PlannedAction("open_program", "calculator", "", 0.99),)

        launched = []
        computer = ComputerCommandRouter(
            process_launcher=launched.append,
            file_opener=lambda _path: None,
            key_sender=lambda _key: None,
        )
        coordinator = SafeActionCoordinator(
            memory=FakeAgent(),
            programmer=FakeAgent(),
            installer=None,
            computer=computer,
            semantic_planner=FakePlanner(),
            mode_controller=ModelModeController(),
        )

        response = coordinator.handle("Você consegue iniciar aquele aplicativo de contas?")

        self.assertIn("Calculadora", response)
        self.assertEqual(launched, [["calc.exe"]])

    def test_explicit_multi_step_request_runs_in_order(self):
        launched = []
        keys = []
        computer = ComputerCommandRouter(
            process_launcher=launched.append,
            file_opener=lambda _path: None,
            key_sender=keys.append,
        )
        coordinator = SafeActionCoordinator(
            memory=FakeAgent(),
            programmer=FakeAgent(),
            installer=None,
            computer=computer,
            semantic_planner=object(),
            mode_controller=ModelModeController(),
        )

        response = coordinator.handle("Abra a calculadora e depois aumente o volume")

        self.assertIn("Calculadora", response)
        self.assertIn("Aumentei o volume", response)
        self.assertEqual(launched, [["calc.exe"]])
        self.assertEqual(keys, ["volume up", "volume up", "volume up"])

    def test_suspicious_semantic_file_query_is_never_forwarded(self):
        class FakePlanner:
            def plan(self, _text):
                return (PlannedAction("find_file", "", "..\\segredo.txt", 1.0),)

        computer = FakeAgent()
        coordinator = SafeActionCoordinator(
            memory=FakeAgent(),
            programmer=FakeAgent(),
            installer=None,
            computer=computer,
            semantic_planner=FakePlanner(),
            mode_controller=ModelModeController(),
        )
        response = coordinator.handle("Procure aquele arquivo")
        self.assertIn("validar o plano", response)
        self.assertNotIn("..\\segredo.txt", computer.calls)


if __name__ == "__main__":
    unittest.main()
