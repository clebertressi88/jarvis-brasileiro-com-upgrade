import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from jarvis_remote.actions import RemoteActionExecutor


class FakeCoordinator:
    def __init__(self):
        self.memory = SimpleNamespace(pending_action=None)
        self.programmer = SimpleNamespace(pending_action=None)
        self.installer = SimpleNamespace(pending_installation=None)
        self.computer = SimpleNamespace(pending_action=None, pending_save=None)

    def handle(self, text):
        if text == "conversa normal":
            return None
        if text == "apague o arquivo teste.txt":
            self.computer.pending_action = SimpleNamespace(
                description="apagar permanentemente teste.txt"
            )
            return "Diga confirmar ou cancelar."
        if text == "confirmar":
            self.computer.pending_action = None
            return "Arquivo apagado permanentemente."
        if text == "cancelar":
            self.computer.pending_action = None
            return "Ação cancelada."
        return "Ação segura executada."


class RemoteActionExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_action_uses_existing_coordinator(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = RemoteActionExecutor(
                FakeCoordinator(),
                confirmer=lambda _description: False,
                audit_log_path=Path(directory) / "audit.jsonl",
            )
            response = await executor.handle("abra a calculadora")
            self.assertEqual(response, "Ação segura executada.")

    async def test_dangerous_action_requires_pc_approval(self):
        confirmations = []
        with tempfile.TemporaryDirectory() as directory:
            audit = Path(directory) / "audit.jsonl"
            executor = RemoteActionExecutor(
                FakeCoordinator(),
                confirmer=lambda description: confirmations.append(description) or True,
                audit_log_path=audit,
            )
            response = await executor.handle("apague o arquivo teste.txt")

            self.assertIn("Autorização confirmada no computador", response)
            self.assertEqual(confirmations, ["apagar permanentemente teste.txt"])
            record = json.loads(audit.read_text(encoding="utf-8"))
            self.assertEqual(record["result"], "approved_on_pc")

    async def test_remote_request_is_cancelled_when_pc_denies(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = RemoteActionExecutor(
                FakeCoordinator(),
                confirmer=lambda _description: False,
                audit_log_path=Path(directory) / "audit.jsonl",
            )
            response = await executor.handle("apague o arquivo teste.txt")

            self.assertIn("não autorizada no computador", response)
            self.assertIsNone(executor.coordinator.computer.pending_action)

    async def test_non_action_falls_back_to_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            executor = RemoteActionExecutor(
                FakeCoordinator(),
                confirmer=lambda _description: False,
                audit_log_path=Path(directory) / "audit.jsonl",
            )
            self.assertIsNone(await executor.handle("conversa normal"))

    async def test_remote_text_cannot_confirm_preexisting_local_action(self):
        confirmations = []
        with tempfile.TemporaryDirectory() as directory:
            coordinator = FakeCoordinator()
            coordinator.computer.pending_action = SimpleNamespace(
                description="apagar permanentemente arquivo local"
            )
            executor = RemoteActionExecutor(
                coordinator,
                confirmer=lambda description: confirmations.append(description) or False,
                audit_log_path=Path(directory) / "audit.jsonl",
            )

            response = await executor.handle("confirmar")

            self.assertIn("não autorizada no computador", response)
            self.assertEqual(confirmations, ["apagar permanentemente arquivo local"])
            self.assertIsNone(coordinator.computer.pending_action)
