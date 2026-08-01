from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path


CLIENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{16,64}$")
PAIRING_TTL_SECONDS = 300.0
MAX_PAIRING_FAILURES = 5
PROOF_PREFIX = b"jarvis-remote-v1\x00"


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


if os.name == "nt":
    from ctypes import wintypes

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]


    def _data_blob(value: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(value)
        blob = _DataBlob(
            len(value),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer


    def _protect(value: bytes) -> bytes:
        source, source_buffer = _data_blob(value)
        destination = _DataBlob()
        result = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(source),
            "Jarvis Remote pairing",
            None,
            None,
            None,
            0x1,
            ctypes.byref(destination),
        )
        del source_buffer
        if not result:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(destination.pbData)


    def _unprotect(value: bytes) -> bytes:
        source, source_buffer = _data_blob(value)
        destination = _DataBlob()
        result = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(destination),
        )
        del source_buffer
        if not result:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(destination.pbData, destination.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(destination.pbData)

else:

    def _protect(value: bytes) -> bytes:
        return value


    def _unprotect(value: bytes) -> bytes:
        return value


@dataclass(frozen=True)
class PairingRecord:
    client_id: str
    secret: bytes


class PairingStore:
    """Stores one paired client, using Windows DPAPI for the shared secret."""

    def __init__(self, path: Path | None = None) -> None:
        if path is None:
            profile = Path(os.environ.get("LOCALAPPDATA", Path.home()))
            path = profile / "Jarvis" / "remote-client.json"
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> PairingRecord | None:
        if not self.exists():
            return None
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        client_id = payload.get("client_id")
        protected_secret = payload.get("protected_secret")
        if not isinstance(client_id, str) or not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ValueError("identificador de cliente inválido no arquivo de pareamento")
        if not isinstance(protected_secret, str):
            raise ValueError("segredo protegido ausente no arquivo de pareamento")
        secret = _unprotect(base64.b64decode(protected_secret, validate=True))
        if len(secret) != 32:
            raise ValueError("segredo de pareamento inválido")
        return PairingRecord(client_id=client_id, secret=secret)

    def save(self, record: PairingRecord) -> None:
        if not CLIENT_ID_PATTERN.fullmatch(record.client_id):
            raise ValueError("identificador de cliente inválido")
        if len(record.secret) != 32:
            raise ValueError("o segredo deve ter 32 bytes")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "client_id": record.client_id,
            "protected_secret": base64.b64encode(_protect(record.secret)).decode("ascii"),
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        temporary.replace(self.path)


class PairingManager:
    def __init__(self, store: PairingStore, *, now=time.monotonic) -> None:
        self.store = store
        self._now = now
        self._pairing_code: str | None = None
        self._pairing_expires_at = 0.0
        self._pairing_failures = 0
        self._replace_existing = False

    def start_pairing(self, *, replace_existing: bool = False) -> str:
        if self.store.exists() and not replace_existing:
            raise RuntimeError(
                "já existe um celular pareado; use a substituição local para trocar o aparelho"
            )
        self._pairing_code = f"{secrets.randbelow(100_000_000):08d}"
        self._pairing_expires_at = self._now() + PAIRING_TTL_SECONDS
        self._pairing_failures = 0
        self._replace_existing = replace_existing
        return self._pairing_code

    def pair(self, client_id: str, code: str) -> str:
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ValueError("identificador do aplicativo inválido")
        if (
            self._pairing_code is None
            or self._now() > self._pairing_expires_at
            or self._pairing_failures >= MAX_PAIRING_FAILURES
        ):
            self._clear_pairing_window()
            raise PermissionError("pareamento indisponível ou expirado")
        if not hmac.compare_digest(code, self._pairing_code):
            self._pairing_failures += 1
            if self._pairing_failures >= MAX_PAIRING_FAILURES:
                self._clear_pairing_window()
            raise PermissionError("código de pareamento inválido")

        secret = secrets.token_bytes(32)
        self.store.save(PairingRecord(client_id=client_id, secret=secret))
        self._clear_pairing_window()
        return _base64url_encode(secret)

    def verify(self, client_id: str, challenge: str, proof: str) -> bool:
        record = self.store.load()
        if record is None or not hmac.compare_digest(record.client_id, client_id):
            return False
        expected = self.proof(record.secret, client_id, challenge)
        return hmac.compare_digest(expected, proof)

    @staticmethod
    def proof(secret: bytes, client_id: str, challenge: str) -> str:
        message = PROOF_PREFIX + client_id.encode("utf-8") + b"\x00" + challenge.encode("ascii")
        return _base64url_encode(hmac.new(secret, message, hashlib.sha256).digest())

    @staticmethod
    def decode_secret(encoded: str) -> bytes:
        secret = _base64url_decode(encoded)
        if len(secret) != 32:
            raise ValueError("segredo de pareamento inválido")
        return secret

    def _clear_pairing_window(self) -> None:
        self._pairing_code = None
        self._pairing_expires_at = 0.0
        self._pairing_failures = 0
        self._replace_existing = False
