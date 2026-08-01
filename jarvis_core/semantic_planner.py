from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Callable

import ollama

from jarvis_config import CODE_MODEL


ALLOWED_ACTIONS = {
    "open_program",
    "media",
    "system_info",
    "find_file",
    "install_program",
    "memory_search",
    "list_projects",
    "open_project",
    "development_app",
    "deep_mode",
}

ALLOWED_TARGETS = {
    "",
    "notepad",
    "calculator",
    "paint",
    "explorer",
    "chrome",
    "edge",
    "firefox",
    "camera",
    "coreldraw",
    "consumer",
    "volume_up",
    "volume_down",
    "mute",
    "play_pause",
    "next_track",
    "previous_track",
    "vscode",
    "powershell",
    "cmd",
    "arduino",
    "vscode_install",
    "7zip",
    "git",
    "python",
    "node",
    "notepadpp",
    "vlc",
    "firefox",
    "chrome_install",
    "powertoys",
    "arduino_install",
    "on",
    "off",
}

VALID_TARGETS_BY_ACTION = {
    "open_program": {
        "notepad", "calculator", "paint", "explorer", "chrome", "edge", "firefox",
        "camera", "coreldraw", "consumer",
    },
    "media": {"volume_up", "volume_down", "mute", "play_pause", "next_track", "previous_track"},
    "system_info": {""},
    "find_file": {""},
    "install_program": {
        "vscode_install", "7zip", "git", "python", "node", "notepadpp", "vlc",
        "firefox", "chrome_install", "powertoys", "arduino_install",
    },
    "memory_search": {""},
    "list_projects": {""},
    "open_project": {""},
    "development_app": {"vscode", "powershell", "cmd", "arduino"},
    "deep_mode": {"on", "off"},
}

QUERY_ACTIONS = {"find_file", "memory_search", "open_project"}

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {
        "actions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": sorted(ALLOWED_ACTIONS)},
                    "target": {"type": "string", "enum": sorted(ALLOWED_TARGETS)},
                    "query": {"type": "string", "maxLength": 160},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["type", "target", "query", "confidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["actions"],
    "additionalProperties": False,
}


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip().lower()


@dataclass(frozen=True)
class PlannedAction:
    type: str
    target: str
    query: str
    confidence: float


class SemanticPlanner:
    """Classifies explicit local actions but never executes model text."""

    def __init__(
        self,
        *,
        model_name: str = CODE_MODEL,
        chat_client: Callable | None = None,
        minimum_confidence: float = 0.78,
    ) -> None:
        self.model_name = model_name
        self._chat_client = chat_client or ollama.chat
        self.minimum_confidence = minimum_confidence

    @staticmethod
    def looks_actionable(user_text: str) -> bool:
        plain = _plain(user_text)
        action_terms = (
            "abra ", "abre ", "abrir ", "inicie ", "inicia ", "execute ",
            "executa ", "rode ", "roda ", "rodar ", "mostre ", "procure ",
            "encontre ", "instale ", "aumente ", "abaixe ", "diminua ",
            "silencie ", "pause ", "toque ", "avance ", "volte a faixa",
            "meus projetos", "modo profundo", "modo rapido", "no computador",
        )
        polite_terms = (
            "poderia ", "voce pode ", "pode ", "voce consegue ", "consegue ",
            "seria possivel ", "quero que voce ", "por favor ",
        )
        return any(term in plain for term in action_terms) or (
            any(term in plain for term in polite_terms)
            and any(
                verb in plain
                for verb in ("abr", "rod", "execut", "procur", "instal", "volume", "musica")
            )
        )

    def plan(self, user_text: str) -> tuple[PlannedAction, ...]:
        if not self.looks_actionable(user_text):
            return ()
        response = self._chat_client(
            model=self.model_name,
            think=False,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Classifique somente ações locais explicitamente pedidas pelo usuário. "
                        "Não invente etapas, arquivos, programas ou confirmações. Use query apenas "
                        "para nomes ou buscas presentes no pedido. Retorne actions vazio quando o "
                        "pedido for conversa, explicação, criação/edição de arquivo ou programação "
                        "não representada no esquema. Para ações compostas, preserve a ordem e use "
                        "no máximo três itens. Compatibilidade obrigatória: open_program usa apenas "
                        "notepad, calculator, paint, explorer, chrome, edge, firefox, camera, "
                        "coreldraw ou consumer; media usa apenas "
                        "volume_up, volume_down, mute, play_pause, next_track ou previous_track; "
                        "install_program usa somente alvos terminados em _install ou pacotes do "
                        "catálogo; development_app usa vscode, powershell, cmd ou arduino; deep_mode "
                        "usa on ou off. system_info, find_file, memory_search, list_projects e "
                        "open_project sempre usam target vazio. Somente find_file, memory_search e "
                        "open_project usam query; todas as outras ações usam query vazia. "
                        "Aplicativo de contas significa calculator. Preserve o nome pesquisado na query."
                    ),
                },
                {
                    "role": "user",
                    "content": "Você poderia abrir aquele aplicativo de contas?",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "actions": [
                                {
                                    "type": "open_program",
                                    "target": "calculator",
                                    "query": "",
                                    "confidence": 0.98,
                                }
                            ]
                        }
                    ),
                },
                {
                    "role": "user",
                    "content": "Encontre o documento relatório julho",
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "actions": [
                                {
                                    "type": "find_file",
                                    "target": "",
                                    "query": "relatório julho",
                                    "confidence": 0.98,
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                },
                {"role": "user", "content": user_text},
            ],
            format=PLANNER_SCHEMA,
            options={"temperature": 0.0, "num_ctx": 2048},
        )
        try:
            content = response["message"]["content"]
        except (TypeError, KeyError):
            content = response.message.content
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            return ()
        raw_actions = payload.get("actions")
        if not isinstance(raw_actions, list) or len(raw_actions) > 3:
            return ()

        actions = []
        for raw in raw_actions:
            if not isinstance(raw, dict) or set(raw) != {"type", "target", "query", "confidence"}:
                continue
            action_type = raw.get("type")
            target = raw.get("target")
            query = raw.get("query")
            confidence = raw.get("confidence")
            if action_type not in ALLOWED_ACTIONS or target not in ALLOWED_TARGETS:
                continue
            if target not in VALID_TARGETS_BY_ACTION[action_type]:
                continue
            if not isinstance(query, str) or len(query) > 160:
                continue
            if action_type in QUERY_ACTIONS and not query.strip():
                continue
            if action_type not in QUERY_ACTIONS and query.strip():
                continue
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                continue
            if float(confidence) < self.minimum_confidence:
                continue
            actions.append(
                PlannedAction(action_type, target, query.strip(), float(confidence))
            )
        return tuple(actions)
