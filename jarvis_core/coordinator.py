from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable

from .model_mode import model_mode
from .semantic_planner import PlannedAction, SemanticPlanner


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionHandler:
    name: str
    handle: Callable[[str], str | None]
    has_pending_action: Callable[[], bool]


class SafeActionCoordinator:
    """Plans, executes and verifies local actions through constrained agents.

    Deterministic parsers always run first. The semantic planner is consulted
    only for action-looking requests that no parser understood. Its output is a
    small enum-based JSON plan; model-generated shell commands are impossible.
    """

    def __init__(
        self,
        *,
        memory,
        programmer,
        installer,
        computer,
        semantic_planner: SemanticPlanner | None = None,
        mode_controller=model_mode,
    ) -> None:
        self.memory = memory
        self.programmer = programmer
        self.installer = installer
        self.computer = computer
        self.semantic_planner = semantic_planner or SemanticPlanner()
        self.mode_controller = mode_controller

        bindings = [
            ActionHandler("memory", memory.handle, lambda: memory.pending_action is not None),
            ActionHandler(
                "programmer",
                programmer.handle,
                lambda: programmer.pending_action is not None,
            ),
        ]
        if installer is not None:
            bindings.append(
                ActionHandler(
                    "installer",
                    installer.handle,
                    lambda: installer.pending_installation is not None,
                )
            )
        bindings.append(
            ActionHandler(
                "computer",
                computer.handle,
                lambda: computer.pending_action is not None
                or getattr(computer, "pending_save", None) is not None,
            )
        )
        self._handlers = tuple(bindings)

    def handle(self, user_text: str) -> str | None:
        text = user_text.strip()
        if not text:
            return None

        # Pending confirmations always have priority and are never interpreted
        # by the semantic planner.
        pending = [handler for handler in self._handlers if handler.has_pending_action()]
        if pending:
            response = self._call_handler(pending[0], text)
            if response is not None:
                return response

        mode_response = self.mode_controller.handle(text)
        if mode_response is not None:
            return mode_response

        steps = self._split_explicit_steps(text)
        if len(steps) == 1:
            return self._handle_single(steps[0])

        responses = []
        for index, step in enumerate(steps):
            response = self._handle_single(step)
            if response is None:
                responses.append(
                    f"Não entendi com segurança a etapa {index + 1}; as etapas seguintes não foram executadas."
                )
                break
            responses.append(response)
            if any(handler.has_pending_action() for handler in self._handlers):
                if index + 1 < len(steps):
                    responses.append(
                        "As etapas seguintes aguardam um novo pedido depois da confirmação."
                    )
                break
        return " ".join(responses)

    def _handle_single(self, user_text: str) -> str | None:
        for handler in self._handlers:
            response = self._call_handler(handler, user_text)
            if response is not None:
                return response

        try:
            actions = self.semantic_planner.plan(user_text)
        except Exception:
            logger.exception("Semantic planning failed; falling back to conversation.")
            return None
        if not actions:
            return None

        responses = []
        for index, action in enumerate(actions):
            response = self._execute_semantic_action(action)
            if response is None:
                return "Não consegui validar o plano local com segurança; nenhuma ação adicional foi executada."
            responses.append(response)
            if any(handler.has_pending_action() for handler in self._handlers):
                if index + 1 < len(actions):
                    responses.append(
                        "As ações seguintes não foram executadas porque existe uma confirmação pendente."
                    )
                break
        return " ".join(responses)

    def _call_handler(self, handler: ActionHandler, user_text: str) -> str | None:
        try:
            response = handler.handle(user_text)
        except Exception:
            logger.exception("Action execution failed in %s agent.", handler.name)
            return (
                "A ação local falhou e foi interrompida com segurança. "
                "Nenhum comando alternativo foi executado."
            )
        if response is None:
            return None
        if not isinstance(response, str) or not response.strip():
            logger.error("Action verification returned an invalid result: %s", handler.name)
            return "A ação não produziu um resultado verificável e foi interrompida."
        logger.info(
            "Action cycle completed: plan=%s, execution=deterministic, result=verified",
            handler.name,
        )
        return response.strip()

    def _execute_semantic_action(self, action: PlannedAction) -> str | None:
        program_commands = {
            "notepad": "Abra o bloco de notas",
            "calculator": "Abra a calculadora",
            "paint": "Abra o Paint",
            "explorer": "Abra o explorador de arquivos",
            "chrome": "Abra o Chrome",
            "edge": "Abra o Edge",
            "firefox": "Abra o Firefox",
            "camera": "Abra a câmera",
            "coreldraw": "Abra o CorelDRAW",
            "consumer": "Abra o Consumer",
        }
        media_commands = {
            "volume_up": "Aumente o volume",
            "volume_down": "Abaixe o volume",
            "mute": "Silencie o áudio",
            "play_pause": "Pause a música",
            "next_track": "Próxima faixa",
            "previous_track": "Faixa anterior",
        }
        install_commands = {
            "vscode_install": "Instale o VS Code",
            "7zip": "Instale o 7-Zip",
            "git": "Instale o Git",
            "python": "Instale o Python 3.12",
            "node": "Instale o Node.js LTS",
            "notepadpp": "Instale o Notepad++",
            "vlc": "Instale o VLC",
            "firefox": "Instale o Firefox",
            "chrome_install": "Instale o Chrome",
            "powertoys": "Instale o PowerToys",
            "arduino_install": "Instale o Arduino IDE",
        }
        development_commands = {
            "vscode": "Abra o VS Code",
            "powershell": "Abra o PowerShell",
            "cmd": "Abra o Prompt de Comando",
            "arduino": "Abra o Arduino IDE",
        }

        if action.type == "open_program":
            command = program_commands.get(action.target)
            return self.computer.handle(command) if command else None
        if action.type == "media":
            command = media_commands.get(action.target)
            return self.computer.handle(command) if command else None
        if action.type == "system_info" and action.target == "":
            return self.computer.handle("Mostre as informações do computador")
        if action.type == "find_file" and action.target == "":
            if not self._safe_query(action.query):
                return None
            return self.computer.handle(f"Procure o arquivo {action.query}")
        if action.type == "memory_search" and action.target == "":
            if not self._safe_query(action.query):
                return None
            return self.memory.handle(f"Pesquise na memória {action.query}")
        if action.type == "install_program" and self.installer is not None:
            command = install_commands.get(action.target)
            return self.installer.handle(command) if command else None
        if action.type == "list_projects" and action.target == "":
            return self.programmer.handle("Liste meus projetos")
        if action.type == "open_project" and action.target == "":
            if not self._safe_query(action.query):
                return None
            return self.programmer.handle(f"Abra o projeto {action.query}")
        if action.type == "development_app":
            command = development_commands.get(action.target)
            return self.programmer.handle(command) if command else None
        if action.type == "deep_mode" and action.target in {"on", "off"}:
            command = "Ative o modo profundo" if action.target == "on" else "Use o modo rápido"
            return self.mode_controller.handle(command)
        return None

    @staticmethod
    def _safe_query(query: str) -> bool:
        cleaned = query.strip()
        return bool(cleaned) and len(cleaned) <= 160 and not any(
            character in cleaned for character in ("\\", "/", ":", "*", "?", "<", ">", "|", "\x00")
        )

    @staticmethod
    def _split_explicit_steps(user_text: str) -> tuple[str, ...]:
        steps = tuple(
            part.strip(" ,.;")
            for part in re.split(
                r"\s+(?:e\s+depois|depois|em\s+seguida)\s+",
                user_text,
                flags=re.IGNORECASE,
            )
            if part.strip(" ,.;")
        )
        return steps or (user_text,)
