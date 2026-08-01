from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _normalized_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9+.]+", " ", _plain(value)).strip()


@dataclass(frozen=True)
class TrustedPackage:
    key: str
    display_name: str
    package_id: str
    aliases: tuple[str, ...]


TRUSTED_PACKAGES = (
    TrustedPackage(
        "vscode",
        "Visual Studio Code",
        "Microsoft.VisualStudioCode",
        ("vs code", "vscode", "visual studio code", "editor visual studio code"),
    ),
    TrustedPackage("7zip", "7-Zip", "7zip.7zip", ("7 zip", "7zip", "sete zip")),
    TrustedPackage("git", "Git", "Git.Git", ("git",)),
    TrustedPackage(
        "python",
        "Python 3.12",
        "Python.Python.3.12",
        ("python", "python 3", "python 3.12"),
    ),
    TrustedPackage(
        "node",
        "Node.js LTS",
        "OpenJS.NodeJS.LTS",
        ("node", "node js", "node.js", "node lts"),
    ),
    TrustedPackage(
        "notepadpp",
        "Notepad++",
        "Notepad++.Notepad++",
        ("notepad++", "notepad mais mais", "notepad plus plus"),
    ),
    TrustedPackage("vlc", "VLC media player", "VideoLAN.VLC", ("vlc", "vlc media player")),
    TrustedPackage("firefox", "Mozilla Firefox", "Mozilla.Firefox", ("firefox", "mozilla firefox")),
    TrustedPackage("chrome", "Google Chrome", "Google.Chrome", ("chrome", "google chrome")),
    TrustedPackage(
        "powertoys",
        "Microsoft PowerToys",
        "Microsoft.PowerToys",
        ("power toys", "powertoys", "microsoft powertoys"),
    ),
    TrustedPackage(
        "arduino",
        "Arduino IDE",
        "ArduinoSA.IDE.stable",
        ("arduino", "arduino ide"),
    ),
)


@dataclass(frozen=True)
class PackageMetadata:
    version: str
    fingerprint: str
    normalized_output: str


@dataclass
class PendingInstallation:
    package: TrustedPackage
    metadata: PackageMetadata
    winget_fingerprint: tuple[int, int]
    expires_at: float


class InstallerAgent:
    """Instala somente pacotes predefinidos da origem oficial `winget`.

    Nenhuma parte do comando é produzida pelo modelo ou aceita da fala. Uma
    confirmação exata e uma nova consulta de metadados são obrigatórias.
    """

    def __init__(
        self,
        *,
        winget_path: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        confirmation_ttl_seconds: float = 60.0,
        audit_log_path: Path | None = None,
    ) -> None:
        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        self.winget_path = (winget_path or self._find_trusted_winget()).resolve()
        self._validate_winget()
        self._runner = runner or subprocess.run
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self.pending_installation: PendingInstallation | None = None
        self.audit_log_path = (
            audit_log_path
            or profile / "Documents" / "Jarvis Workspace" / ".jarvis" / "installer-audit.jsonl"
        )

    def handle(self, user_text: str) -> str | None:
        text = user_text.strip()
        if not text:
            return None
        plain = _plain(text).strip()

        if self.pending_installation is not None:
            confirmation = self._handle_confirmation(plain)
            if confirmation is not None:
                return confirmation

        if self._is_help_request(plain):
            names = ", ".join(package.display_name for package in TRUSTED_PACKAGES)
            return (
                f"Posso instalar somente estes programas confiáveis pela origem oficial winget: {names}. "
                "Não instalo arquivos EXE, MSI, links, drivers, antivírus, VPNs ou programas fora da lista."
            )

        match = re.match(
            r"^(?:agente instalador[,:]?\s+)?(?:instale|instalar|baixe\s+e\s+instale)\s+"
            r"(?:o\s+)?(?:programa\s+)?(.+?)\s*$",
            plain,
        )
        if not match:
            return None

        requested = _normalized_phrase(match.group(1))
        package = self._package_from_alias(requested)
        if package is None:
            return (
                "Esse programa não está na lista autorizada. Diga “agente instalador” "
                "para ouvir os programas disponíveis."
            )

        try:
            metadata = self._query_metadata(package)
            winget_fingerprint = self._winget_fingerprint()
        except Exception as exc:
            return f"Não consegui verificar o pacote antes da instalação: {exc}"

        self.pending_installation = PendingInstallation(
            package=package,
            metadata=metadata,
            winget_fingerprint=winget_fingerprint,
            expires_at=time.monotonic() + self.confirmation_ttl_seconds,
        )
        version_text = f", versão {metadata.version}" if metadata.version else ""
        return (
            f"Preparei a instalação de {package.display_name}{version_text}, ID {package.package_id}, "
            "pela origem oficial winget. A instalação não permitirá reinício automático e poderá "
            "mostrar uma janela de administrador que somente você pode aprovar. Para autorizar, "
            "diga exatamente: confirmar instalação."
        )

    def _handle_confirmation(self, plain: str) -> str | None:
        assert self.pending_installation is not None
        normalized = _normalized_phrase(plain)
        pending = self.pending_installation

        if time.monotonic() > pending.expires_at:
            self.pending_installation = None
            return "A confirmação da instalação expirou. Peça a instalação novamente."

        if normalized.startswith("cancelar") or normalized.startswith("cancele") or normalized.startswith("nao"):
            self.pending_installation = None
            return f"Instalação de {pending.package.display_name} cancelada."

        if normalized in {"sim", "confirmar", "pode instalar", "pode fazer"}:
            return "Para instalar, diga exatamente: confirmar instalação."

        if normalized != "confirmar instalacao":
            return None

        self.pending_installation = None
        try:
            self._validate_winget()
            if self._winget_fingerprint() != pending.winget_fingerprint:
                raise RuntimeError("o executável winget mudou após o pedido")
            current_metadata = self._query_metadata(pending.package)
            if current_metadata.fingerprint != pending.metadata.fingerprint:
                raise RuntimeError("os metadados ou a versão do pacote mudaram após o pedido")
            return self._install(pending.package, current_metadata)
        except Exception as exc:
            self._write_audit(
                package=pending.package,
                version=pending.metadata.version,
                result="cancelled",
                exit_code=None,
                detail=str(exc),
            )
            return f"A instalação foi cancelada por segurança: {exc}."

    def _install(self, package: TrustedPackage, metadata: PackageMetadata) -> str:
        argv = [
            str(self.winget_path),
            "install",
            "--id",
            package.package_id,
            "--exact",
            "--source",
            "winget",
            "--silent",
            "--disable-interactivity",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--no-upgrade",
        ]
        started = time.monotonic()
        try:
            completed = self._runner(
                argv,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=900,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            self._write_audit(
                package=package,
                version=metadata.version,
                result="timeout_uncertain",
                exit_code=None,
                detail="timeout after 900 seconds",
            )
            return (
                "A instalação ultrapassou 15 minutos e o resultado ficou incerto. "
                "Não tente novamente antes de verificar se o programa foi instalado."
            )

        duration = round(time.monotonic() - started, 2)
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        self._write_audit(
            package=package,
            version=metadata.version,
            result="success" if completed.returncode == 0 else "failed",
            exit_code=completed.returncode,
            detail=f"duration_seconds={duration}",
        )
        if completed.returncode != 0:
            detail = self._compact_output(output)
            return (
                f"A instalação de {package.display_name} falhou com código {completed.returncode}. "
                + (detail or "O winget não forneceu uma mensagem legível.")
            )

        installed = self._verify_installed(package)
        if not installed:
            return (
                f"O winget terminou sem erro, mas não consegui confirmar {package.display_name} "
                "na lista de programas. Verifique antes de tentar novamente."
            )
        return f"{package.display_name} foi instalado com sucesso. Nenhum programa foi aberto automaticamente."

    def _query_metadata(self, package: TrustedPackage) -> PackageMetadata:
        argv = [
            str(self.winget_path),
            "show",
            "--id",
            package.package_id,
            "--exact",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        completed = self._run_query(argv, timeout=30)
        if completed.returncode != 0:
            raise RuntimeError(f"winget não encontrou o ID exato {package.package_id}")
        normalized_output = "\n".join(
            line.strip() for line in (completed.stdout or "").splitlines() if line.strip()
        )
        if package.package_id.lower() not in normalized_output.lower():
            raise RuntimeError("a resposta do winget não corresponde ao pacote autorizado")
        version_match = re.search(
            r"^(?:vers[aã]o|version)\s*:\s*(.+)$",
            normalized_output,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        version = version_match.group(1).strip() if version_match else ""
        fingerprint = hashlib.sha256(normalized_output.encode("utf-8")).hexdigest()
        return PackageMetadata(
            version=version,
            fingerprint=fingerprint,
            normalized_output=normalized_output,
        )

    def _verify_installed(self, package: TrustedPackage) -> bool:
        argv = [
            str(self.winget_path),
            "list",
            "--id",
            package.package_id,
            "--exact",
            "--source",
            "winget",
            "--accept-source-agreements",
            "--disable-interactivity",
        ]
        try:
            completed = self._run_query(argv, timeout=30)
        except Exception:
            return False
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).lower()
        return completed.returncode == 0 and package.package_id.lower() in output

    def _run_query(self, argv: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        return self._runner(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )

    def _validate_winget(self) -> None:
        if not self.winget_path.is_file():
            raise RuntimeError("winget não foi encontrado em um local confiável")
        path_lower = str(self.winget_path).lower()
        stat_result = self.winget_path.lstat()
        expected_alias = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft/WindowsApps/winget.exe"
        ).resolve()
        if self.winget_path == expected_alias:
            if stat_result.st_size != 0 or not getattr(stat_result, "st_file_attributes", 0) & 0x400:
                raise RuntimeError("o alias winget não pertence ao App Installer oficial")
            return
        if (
            "\\program files\\windowsapps\\microsoft.desktopappinstaller_" not in path_lower
            or stat_result.st_size < 10_000
        ):
            raise RuntimeError("o executável winget não pertence ao App Installer oficial")

    def _winget_fingerprint(self) -> tuple[int, int]:
        stat_result = self.winget_path.stat()
        return stat_result.st_size, stat_result.st_mtime_ns

    @staticmethod
    def _find_trusted_winget() -> Path:
        alias = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Microsoft/WindowsApps/winget.exe"
        )
        if alias.is_file():
            return alias
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        windows_apps = program_files / "WindowsApps"
        candidates = sorted(
            windows_apps.glob(
                "Microsoft.DesktopAppInstaller_*_x64__8wekyb3d8bbwe/winget.exe"
            ),
            reverse=True,
        )
        candidate = next((path for path in candidates if path.is_file()), None)
        if candidate is None:
            raise RuntimeError("winget oficial não foi encontrado")
        return candidate

    @staticmethod
    def _package_from_alias(value: str) -> TrustedPackage | None:
        for package in TRUSTED_PACKAGES:
            aliases = {_normalized_phrase(alias) for alias in package.aliases}
            if value in aliases:
                return package
        return None

    @staticmethod
    def _is_help_request(plain: str) -> bool:
        normalized = _normalized_phrase(plain)
        return normalized == "agente instalador" or any(
            phrase in normalized
            for phrase in (
                "programas que pode instalar",
                "programas autorizados para instalar",
                "ajuda de instalacao",
                "comandos de instalacao",
            )
        )

    @staticmethod
    def _compact_output(output: str) -> str:
        compact = re.sub(r"\s+", " ", output).strip()
        return compact[:800]

    def _write_audit(
        self,
        *,
        package: TrustedPackage,
        version: str,
        result: str,
        exit_code: int | None,
        detail: str,
    ) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "package_id": package.package_id,
            "version": version,
            "source": "winget",
            "result": result,
            "exit_code": exit_code,
            "detail": detail[:300],
        }
        try:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_log_path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            pass
