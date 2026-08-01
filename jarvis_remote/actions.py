from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


logger = logging.getLogger(__name__)
MAX_AUDIT_TEXT = 500


@dataclass(frozen=True)
class PendingLocalConfirmation:
    description: str
    confirmation_phrase: str


def confirm_on_windows(description: str) -> bool:
    """Show a confirmation on the PC. Non-Windows systems always deny."""
    if os.name != "nt":
        return False

    safe_description = " ".join(description.split())[:500]
    message = (
        "Um comando autenticado chegou pelo celular:\n\n"
        f"{safe_description}\n\n"
        "Autorizar esta ação no computador?"
    )
    flags = 0x00000004 | 0x00000030 | 0x00010000 | 0x00040000
    return ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
        None,
        message,
        "Confirmação local - Jarvis Remote",
        flags,
    ) == 6


class RemoteActionExecutor:
    """Runs the same constrained coordinator used by local voice commands.

    Any agent that creates a pending destructive or privileged operation is
    confirmed through a dialog on the PC. A remote message can never approve
    its own pending action.
    """

    def __init__(
        self,
        coordinator,
        *,
        confirmer: Callable[[str], bool] = confirm_on_windows,
        audit_log_path: Path | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.confirmer = confirmer
        profile = Path(os.environ.get("LOCALAPPDATA", Path.home()))
        self.audit_log_path = (
            audit_log_path or profile / "Jarvis" / "remote-actions.jsonl"
        )
        self._lock = threading.RLock()

    async def handle(self, text: str) -> str | None:
        return await asyncio.to_thread(self._handle_sync, text)

    def _handle_sync(self, text: str) -> str | None:
        with self._lock:
            # A confirmation created by the local voice/UI flow must never be
            # approved by typing "confirmar" on the remote client.
            pending = self._pending_confirmation()
            if pending is not None:
                return self._resolve_pending(text, pending)

            try:
                response = self.coordinator.handle(text)
            except Exception:
                logger.exception("Remote action coordinator failed")
                self._audit(text, "failed", "coordinator error")
                return "A ação local falhou e foi interrompida com segurança."

            if response is None:
                return None

            pending = self._pending_confirmation()
            if pending is None:
                self._audit(text, "handled", response)
                return response

            return self._resolve_pending(text, pending)

    def _resolve_pending(
        self,
        text: str,
        pending: PendingLocalConfirmation,
    ) -> str:
        try:
            approved = bool(self.confirmer(pending.description))
        except Exception:
            logger.exception("Local confirmation dialog failed")
            approved = False

        follow_up = self.coordinator.handle(
            pending.confirmation_phrase if approved else "cancelar"
        )
        if approved:
            result = follow_up or "A ação foi autorizada no computador."
            self._audit(text, "approved_on_pc", result)
            return f"Autorização confirmada no computador. {result}"

        result = follow_up or "A ação foi recusada no computador."
        self._audit(text, "denied_on_pc", result)
        return f"Ação não autorizada no computador. {result}"

    def _pending_confirmation(self) -> PendingLocalConfirmation | None:
        memory = getattr(self.coordinator, "memory", None)
        memory_pending = getattr(memory, "pending_action", None)
        if memory_pending is not None:
            phrase = (
                "confirmar indexacao"
                if getattr(memory_pending, "kind", "") == "index"
                else "confirmar esquecimento"
            )
            return PendingLocalConfirmation(
                "alterar dados da memória local do Jarvis",
                phrase,
            )

        programmer = getattr(self.coordinator, "programmer", None)
        programmer_pending = getattr(programmer, "pending_action", None)
        if programmer_pending is not None:
            phrases = {
                "change": "confirmar alteracao",
                "execution": "confirmar execucao",
                "compilation": "confirmar compilacao",
                "extraction": "confirmar descompactacao",
            }
            phrase = phrases.get(getattr(programmer_pending, "kind", ""))
            if phrase is not None:
                return PendingLocalConfirmation(
                    str(getattr(programmer_pending, "description", "ação de programação")),
                    phrase,
                )

        installer = getattr(self.coordinator, "installer", None)
        installer_pending = getattr(installer, "pending_installation", None)
        if installer_pending is not None:
            package = getattr(installer_pending, "package", None)
            name = getattr(package, "display_name", "programa solicitado")
            return PendingLocalConfirmation(
                f"instalar {name}",
                "confirmar instalacao",
            )

        computer = getattr(self.coordinator, "computer", None)
        computer_pending = getattr(computer, "pending_action", None)
        if computer_pending is not None:
            return PendingLocalConfirmation(
                str(getattr(computer_pending, "description", "ação no computador")),
                "confirmar",
            )
        return None

    def _audit(self, text: str, result: str, detail: str) -> None:
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            normalized = " ".join(text.split())
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result": result,
                "command": normalized[:MAX_AUDIT_TEXT],
                "command_sha256": hashlib.sha256(
                    normalized.encode("utf-8")
                ).hexdigest(),
                "detail": " ".join(detail.split())[:MAX_AUDIT_TEXT],
            }
            with self.audit_log_path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.exception("Could not write the local remote-action audit")


def build_remote_action_executor() -> RemoteActionExecutor:
    from jarvis_core import SafeActionCoordinator
    from jarvis_installer import InstallerAgent
    from jarvis_memory import get_local_memory
    from jarvis_programmer import ProgrammerAgent
    from jarvis_tools import ComputerCommandRouter

    try:
        installer = InstallerAgent()
    except Exception:
        logger.exception("Remote installer agent unavailable")
        installer = None

    coordinator = SafeActionCoordinator(
        memory=get_local_memory(),
        programmer=ProgrammerAgent(),
        installer=installer,
        computer=ComputerCommandRouter(),
    )
    return RemoteActionExecutor(coordinator)
