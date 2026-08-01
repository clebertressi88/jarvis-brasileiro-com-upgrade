from __future__ import annotations

import re
import threading
import unicodedata

from jarvis_config import CHAT_MODEL, QUALITY_MODEL


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip().lower()


class ModelModeController:
    def __init__(
        self,
        *,
        fast_model: str = CHAT_MODEL,
        quality_model: str = QUALITY_MODEL,
    ) -> None:
        self.fast_model = fast_model
        self.quality_model = quality_model
        self._deep_enabled = False
        self._lock = threading.RLock()

    def active_model(self) -> str:
        with self._lock:
            return self.quality_model if self._deep_enabled else self.fast_model

    def is_deep(self) -> bool:
        with self._lock:
            return self._deep_enabled

    def handle(self, user_text: str) -> str | None:
        plain = _plain(user_text).strip(" .!?\t\r\n")
        enable = (
            "ative o modo profundo",
            "ativar modo profundo",
            "ligue o modo profundo",
            "usar modo profundo",
            "use o modo profundo",
        )
        disable = (
            "desative o modo profundo",
            "desativar modo profundo",
            "desligue o modo profundo",
            "volte ao modo rapido",
            "use o modo rapido",
        )
        status = (
            "qual modo esta ativo",
            "qual modelo esta ativo",
            "modo do jarvis",
        )
        if plain in enable:
            with self._lock:
                self._deep_enabled = True
            return (
                "Modo profundo ativado. Usarei o modelo de maior qualidade nas próximas "
                "conversas; neste computador, uma resposta pode levar mais de um minuto."
            )
        if plain in disable:
            with self._lock:
                self._deep_enabled = False
            return "Modo rápido ativado. Voltarei a priorizar respostas de voz mais ágeis."
        if plain in status:
            if self.is_deep():
                return f"O modo profundo está ativo com o modelo {self.quality_model}."
            return f"O modo rápido está ativo com o modelo {self.fast_model}."
        return None


model_mode = ModelModeController()
