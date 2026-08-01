from __future__ import annotations

import ctypes
import difflib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".html",
    ".css",
    ".js",
    ".xml",
    ".yaml",
    ".yml",
}

ACTIVE_SCRIPT_EXTENSIONS = {".js", ".py"}

OPEN_EXTENSIONS = (TEXT_EXTENSIONS - ACTIVE_SCRIPT_EXTENSIONS) | {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".wav",
    ".mp3",
    ".mp4",
}

FOLDER_PATTERN = r"area de trabalho|desktop|documentos|downloads"
CONFIRMATION_TTL_SECONDS = 45.0
SAVE_LOCATION_TTL_SECONDS = 120.0
INSTALLED_PROGRAM_CACHE_TTL_SECONDS = 300.0
WINDOWS_REPARSE_POINT = 0x400
BLOCKED_START_APP_PATTERN = re.compile(
    r"\.(?:bat|cmd|ps1|psm1|vbs|vbe|js|jse|wsf|wsh|reg|url)(?:$|[;\s])",
    re.IGNORECASE,
)
WAKE_WORD_PATTERN = re.compile(r"^(?:jarvis|jarves|charves|javis)[,\s]+", re.IGNORECASE)
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _format_size(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "bytes":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


@dataclass
class PendingAction:
    description: str
    execute: Callable[[], str]
    expires_at: float


@dataclass
class PendingFileSave:
    name: str
    content: str
    expires_at: float


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    device: int
    inode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ProgramSpec:
    key: str
    display_name: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class InstalledProgram:
    display_name: str
    app_id: str


PROGRAM_SPECS = (
    ProgramSpec(
        "notepad",
        "Bloco de Notas",
        ("bloco de notas", "notepad", "editor de texto", "caderno de notas"),
    ),
    ProgramSpec(
        "calculator",
        "Calculadora",
        (
            "calculadora",
            "calculator",
            "calc",
            "aplicativo de contas",
            "app de contas",
            "programa de calculo",
            "de calculo",
        ),
    ),
    ProgramSpec(
        "paint",
        "Paint",
        ("paint", "microsoft paint", "programa de desenho", "aplicativo de desenho"),
    ),
    ProgramSpec(
        "explorer",
        "Explorador de Arquivos",
        (
            "explorador",
            "explorador de arquivos",
            "gerenciador de arquivos",
            "minhas pastas",
        ),
    ),
    ProgramSpec(
        "chrome",
        "Google Chrome",
        (
            "chrome",
            "google chrome",
            "navegador chrome",
            "navegador do google",
            "crome",
            "navegador",
        ),
    ),
    ProgramSpec(
        "edge",
        "Microsoft Edge",
        (
            "edge",
            "microsoft edge",
            "navegador edge",
            "navegador da microsoft",
            "edje",
            "navegador",
        ),
    ),
    ProgramSpec(
        "firefox",
        "Firefox",
        ("firefox", "mozilla firefox", "navegador firefox", "fire fox", "navegador"),
    ),
    ProgramSpec(
        "camera",
        "Câmera",
        ("camera", "webcam", "web cam", "aplicativo da camera", "app da camera"),
    ),
    ProgramSpec(
        "arduino",
        "Arduino IDE",
        ("arduino", "arduino ide", "editor do arduino"),
    ),
    ProgramSpec(
        "coreldraw",
        "CorelDRAW",
        ("coreldraw", "corel draw", "corel", "editor corel", "programa corel"),
    ),
    ProgramSpec(
        "consumer",
        "Consumer",
        ("consumer", "sistema consumer", "programa consumer", "consumir"),
    ),
)

PROGRAMS_BY_KEY = {spec.key: spec for spec in PROGRAM_SPECS}
PROGRAM_CLOSE_TARGETS = {
    "notepad": ("Notepad.exe",),
    "calculator": ("CalculatorApp.exe", "Calculator.exe"),
    "paint": ("mspaint.exe",),
    "chrome": ("chrome.exe",),
    "edge": ("msedge.exe",),
    "firefox": ("firefox.exe",),
    "camera": ("WindowsCamera.exe",),
    "arduino": ("Arduino IDE.exe", "arduino-ide.exe"),
    "coreldraw": ("CorelDRW.exe",),
    "consumer": ("Consumer.exe",),
}
PROGRAM_REQUEST_PREFIXES = (
    "aquele programa chamado ",
    "aquele aplicativo chamado ",
    "programa chamado ",
    "aplicativo chamado ",
    "app chamado ",
    "programa de nome ",
    "aplicativo de nome ",
    "chamado ",
)
PROGRAM_REQUEST_SUFFIXES = (
    " por favor",
    " para mim",
    " pra mim",
    " agora",
    " que esta instalado",
    " que esta no computador",
    " do meu computador",
)
GENERIC_PROGRAM_TOKENS = {
    "app",
    "application",
    "aplicativo",
    "programa",
    "desktop",
    "edition",
    "google",
    "microsoft",
    "mozilla",
    "version",
    "versao",
    "windows",
    "x64",
    "x86",
}


class ComputerCommandRouter:
    """Interpreta comandos locais em português sem executar texto gerado pelo LLM.

    Programas registrados no Menu Iniciar e arquivos dentro das raízes permitidas
    podem ser acessados. Texto do usuário nunca é executado como comando. Exclusões
    e substituições exigem confirmação em uma segunda fala do usuário.
    """

    def __init__(
        self,
        *,
        allowed_roots: Mapping[str, Path] | None = None,
        process_launcher: Callable[[list[str]], None] | None = None,
        process_closer: Callable[[tuple[str, ...]], bool] | None = None,
        file_opener: Callable[[Path], None] | None = None,
        key_sender: Callable[[str], None] | None = None,
        installed_program_provider: Callable[[], tuple[InstalledProgram, ...]] | None = None,
    ) -> None:
        if allowed_roots is None:
            profile = Path(os.environ.get("USERPROFILE", Path.home()))
            allowed_roots = {
                "area de trabalho": profile / "Desktop",
                "documentos": profile / "Documents",
                "downloads": profile / "Downloads",
            }

        self.allowed_roots = {
            name: Path(path).expanduser().resolve()
            for name, path in allowed_roots.items()
        }
        self._process_launcher = process_launcher or self._default_process_launcher
        self._process_closer = process_closer or self._default_process_closer
        self._file_opener = file_opener or self._default_file_opener
        self._key_sender = key_sender or self._default_key_sender
        self._installed_program_provider = (
            installed_program_provider or self._default_installed_program_provider
        )
        self._installed_program_cache: tuple[InstalledProgram, ...] = ()
        self._installed_program_cache_expires_at = 0.0
        self.pending_action: PendingAction | None = None
        self.pending_save: PendingFileSave | None = None

    def handle(self, user_text: str) -> str | None:
        text = user_text.strip()
        if not text:
            return None

        plain = _plain(text).strip()
        plain = WAKE_WORD_PATTERN.sub("", plain).strip()

        if self.pending_action is not None:
            return self._handle_confirmation(plain)
        if self.pending_save is not None:
            return self._handle_pending_save_location(plain)

        handlers = (
            self._handle_help,
            self._handle_file_create,
            self._handle_file_append,
            self._handle_file_replace,
            self._handle_file_delete,
            self._handle_file_read,
            self._handle_file_open,
            self._handle_file_search,
            self._handle_program_close,
            self._handle_program,
            self._handle_media,
            self._handle_system_info,
        )
        for handler in handlers:
            response = handler(text, plain)
            if response is not None:
                return response
        return None

    def _handle_confirmation(self, plain: str) -> str:
        assert self.pending_action is not None

        if time.monotonic() > self.pending_action.expires_at:
            description = self.pending_action.description
            self.pending_action = None
            return f"A confirmação expirou e a ação foi cancelada: {description}."

        normalized = " ".join(re.findall(r"[a-z0-9]+", plain))
        confirmations = {
            "sim",
            "confirmar",
            "confirmo",
            "pode fazer",
            "pode fazer agora",
            "pode executar",
            "tenho certeza",
        }
        cancellations = ("nao", "cancelar", "cancele", "deixa", "pare")

        if normalized in confirmations:
            action = self.pending_action
            self.pending_action = None
            try:
                return action.execute()
            except Exception as exc:
                return f"Não consegui concluir a ação: {exc}"

        if any(
            normalized == item or normalized.startswith(item + " ")
            for item in cancellations
        ):
            description = self.pending_action.description
            self.pending_action = None
            return f"Ação cancelada: {description}."

        return (
            f"Ainda aguardo confirmação para {self.pending_action.description}. "
            "Diga confirmar ou cancelar."
        )

    def _handle_help(self, _text: str, plain: str) -> str | None:
        triggers = (
            "o que voce pode fazer no computador",
            "quais comandos voce pode executar",
            "ajuda com comandos",
            "liste os comandos",
        )
        if not any(trigger in plain for trigger in triggers):
            return None

        return (
            "Posso abrir e fechar programas conhecidos, procurar, ler, abrir, criar e editar "
            "arquivos de texto na Área de Trabalho, Documentos e Downloads, controlar "
            "volume e reprodução de mídia e informar o estado do computador. "
            "Quando você não disser onde salvar, perguntarei a pasta. Para fechar programas "
            "com trabalho não salvo, apagar ou substituir arquivos, pedirei confirmação."
        )

    def _handle_program_close(self, _text: str, plain: str) -> str | None:
        match = re.match(
            r"^(?:jarvis[,\s]+)?(?:(?:por favor|voce pode|pode|voce consegue|consegue|"
            r"poderia|quero que voce)\s+)?"
            r"(?:feche|fecha|fechar|encerre|encerra|encerrar)\s+"
            r"(?:(?:o|a)\s+)?(?:(?:programa|aplicativo|app)\s+)?(.+?)\s*$",
            plain,
        )
        if not match:
            return None

        requested = self._clean_program_request(match.group(1))
        app, candidates = self._resolve_program_name(requested, allow_fuzzy=False)
        if candidates:
            return self._program_clarification(requested, candidates)
        if app is None:
            app, candidates = self._resolve_program_name(requested)
        if candidates:
            return self._program_clarification(requested, candidates)
        if app is None:
            return (
                "Não encontrei uma opção segura de fechamento para esse programa. "
                "Diga o nome como ele aparece no Menu Iniciar."
            )
        if app == "explorer":
            return (
                "Não fecho o Explorador de Arquivos por voz porque ele também controla "
                "partes da interface do Windows."
            )

        process_names = PROGRAM_CLOSE_TARGETS.get(app)
        if process_names is None:
            return "Ainda não tenho uma opção segura para fechar esse programa."
        display_name = PROGRAMS_BY_KEY[app].display_name

        def close_program() -> str:
            closed = self._process_closer(process_names)
            if closed:
                return f"Solicitei o fechamento de {display_name}."
            return f"{display_name} já estava fechado ou não respondeu ao pedido."

        if app == "calculator":
            try:
                closed = self._process_closer(process_names)
                return "Fechei a Calculadora." if closed else "A Calculadora já estava fechada."
            except Exception as exc:
                return f"Não consegui fechar a Calculadora: {exc}"

        self._set_pending_action(
            description=(
                f"fechar {display_name}; alterações que ainda não foram salvas podem ser perdidas"
            ),
            execute=close_program,
        )
        return (
            f"Posso fechar {display_name}, mas pode haver trabalho não salvo. "
            "Diga confirmar ou cancelar."
        )

    def _handle_program(self, _text: str, plain: str) -> str | None:
        if "programas autorizados" in plain:
            return (
                "Posso abrir qualquer aplicativo registrado no Menu Iniciar do Windows. "
                "Diga, por exemplo, abra o Word, abra o CorelDRAW ou inicie o Arduino."
            )

        match = re.match(
            r"^(?:jarvis[,\s]+)?(?:(?:por favor|voce pode|pode|voce consegue|consegue|"
            r"poderia|seria possivel|quero que voce)\s+)?"
            r"(?:abra|abre|abrir|inicie|inicia|iniciar|execute|executa|executar|rode|roda|rodar)\s+"
            r"(?:(?:(?:por favor|para mim|pra mim))\s+)*"
            r"(?:(?:o|a|um|uma)\s+)?(?:(?:programa|aplicativo|app)\s+)?(.+?)\s*$",
            plain,
        )
        if not match:
            return None

        requested = self._clean_program_request(match.group(1))
        app, candidates = self._resolve_program_name(requested, allow_fuzzy=False)
        if candidates:
            return self._program_clarification(requested, candidates)
        if app is not None:
            return self._launch_static_program(app)

        installed, candidates = self._resolve_installed_program_name(
            requested, allow_fuzzy=False
        )
        if candidates:
            return self._program_clarification(requested, candidates)
        if installed is not None:
            return self._launch_installed_program(installed)

        app, candidates = self._resolve_program_name(requested)
        if candidates:
            return self._program_clarification(requested, candidates)
        if app is not None:
            return self._launch_static_program(app)

        installed, candidates = self._resolve_installed_program_name(requested)
        if candidates:
            return self._program_clarification(requested, candidates)
        if installed is not None:
            return self._launch_installed_program(installed)

        return (
            "Não encontrei esse aplicativo entre os programas instalados. "
            "Diga o nome como ele aparece no Menu Iniciar."
        )

    @staticmethod
    def _program_clarification(requested: str, candidates: tuple[str, ...]) -> str:
        options = ", ".join(candidates[:-1])
        if options:
            options += f" ou {candidates[-1]}"
        else:
            options = candidates[0]
        return (
            f"O nome {requested} pode indicar {options}. "
            "Diga o nome completo do programa que deseja abrir."
        )

    def _launch_static_program(self, app: str) -> str:
        try:
            command, display_name = self._program_command(app)
            self._process_launcher(command)
            return f"Abrindo {display_name}."
        except Exception as exc:
            return f"Não consegui abrir o programa autorizado: {exc}"

    def _launch_installed_program(self, program: InstalledProgram) -> str:
        try:
            self._process_launcher(
                ["explorer.exe", rf"shell:AppsFolder\{program.app_id}"]
            )
            return f"Abrindo {program.display_name}."
        except Exception as exc:
            return f"Não consegui abrir o aplicativo instalado: {exc}"

    @staticmethod
    def _clean_program_request(requested: str) -> str:
        cleaned = re.sub(r"[^a-z0-9+]+", " ", _plain(requested)).strip()
        changed = True
        while changed:
            changed = False
            for prefix in PROGRAM_REQUEST_PREFIXES:
                if cleaned.startswith(prefix):
                    cleaned = cleaned[len(prefix) :].strip()
                    changed = True
            for suffix in PROGRAM_REQUEST_SUFFIXES:
                if cleaned.endswith(suffix):
                    cleaned = cleaned[: -len(suffix)].strip()
                    changed = True
        return cleaned

    @staticmethod
    def _resolve_program_name(
        requested: str,
        *,
        allow_fuzzy: bool = True,
    ) -> tuple[str | None, tuple[str, ...]]:
        normalized = ComputerCommandRouter._clean_program_request(requested)
        if not normalized:
            return None, ()

        aliases_by_key = {
            spec.key: tuple(
                ComputerCommandRouter._clean_program_request(alias)
                for alias in spec.aliases
            )
            for spec in PROGRAM_SPECS
        }
        exact_keys = tuple(
            spec.key
            for spec in PROGRAM_SPECS
            if normalized in aliases_by_key[spec.key]
        )
        if len(exact_keys) == 1:
            return exact_keys[0], ()
        if len(exact_keys) > 1:
            return None, tuple(
                PROGRAMS_BY_KEY[key].display_name for key in exact_keys
            )

        if not allow_fuzzy:
            return None, ()

        if len(normalized) < 4:
            return None, ()

        requested_tokens = set(normalized.split())
        scores: list[tuple[float, str]] = []
        for spec in PROGRAM_SPECS:
            best = 0.0
            for alias in aliases_by_key[spec.key]:
                alias_tokens = set(alias.split())
                sequence_score = difflib.SequenceMatcher(
                    None, normalized, alias
                ).ratio()
                union = requested_tokens | alias_tokens
                token_score = (
                    len(requested_tokens & alias_tokens) / len(union)
                    if union
                    else 0.0
                )
                containment_score = 0.94 if (
                    min(len(normalized), len(alias)) >= 5
                    and (normalized in alias or alias in normalized)
                    and min(len(normalized), len(alias)) / max(len(normalized), len(alias)) >= 0.65
                ) else 0.0
                best = max(best, sequence_score, token_score, containment_score)
            scores.append((best, spec.key))

        scores.sort(reverse=True)
        best_score, best_key = scores[0]
        if best_score < 0.80:
            return None, ()
        close_keys = tuple(
            key
            for score, key in scores
            if score >= 0.80 and best_score - score < 0.08
        )
        if len(close_keys) > 1:
            return None, tuple(
                PROGRAMS_BY_KEY[key].display_name for key in close_keys
            )
        return best_key, ()

    def _resolve_installed_program_name(
        self,
        requested: str,
        *,
        allow_fuzzy: bool = True,
    ) -> tuple[InstalledProgram | None, tuple[str, ...]]:
        normalized = self._clean_program_request(requested)
        if not normalized:
            return None, ()

        programs = self._installed_programs()
        aliases = {
            program: self._installed_program_aliases(program)
            for program in programs
        }
        exact = tuple(
            program for program in programs if normalized in aliases[program]
        )
        if len(exact) == 1:
            return exact[0], ()
        if len(exact) > 1:
            names = tuple(dict.fromkeys(program.display_name for program in exact))
            if len(names) == 1:
                return exact[0], ()
            return None, names[:5]

        if not allow_fuzzy or len(normalized) < 4:
            return None, ()

        requested_tokens = set(normalized.split())
        scores: list[tuple[float, InstalledProgram]] = []
        for program in programs:
            best = 0.0
            for alias in aliases[program]:
                alias_tokens = set(alias.split())
                sequence_score = difflib.SequenceMatcher(
                    None, normalized, alias
                ).ratio()
                union = requested_tokens | alias_tokens
                token_score = (
                    len(requested_tokens & alias_tokens) / len(union)
                    if union
                    else 0.0
                )
                containment_score = 0.94 if (
                    min(len(normalized), len(alias)) >= 5
                    and (normalized in alias or alias in normalized)
                    and min(len(normalized), len(alias)) / max(len(normalized), len(alias)) >= 0.65
                ) else 0.0
                best = max(best, sequence_score, token_score, containment_score)
            scores.append((best, program))

        scores.sort(key=lambda item: (-item[0], item[1].display_name.casefold()))
        if not scores or scores[0][0] < 0.82:
            return None, ()
        best_score, best_program = scores[0]
        close = tuple(
            program
            for score, program in scores
            if score >= 0.82 and best_score - score < 0.06
        )
        close_names = tuple(dict.fromkeys(program.display_name for program in close))
        if len(close_names) > 1:
            return None, close_names[:5]
        return best_program, ()

    @staticmethod
    def _installed_program_aliases(program: InstalledProgram) -> tuple[str, ...]:
        normalized = ComputerCommandRouter._clean_program_request(
            program.display_name
        )
        tokens = normalized.split()
        aliases = {normalized}
        useful_tokens = tuple(
            token
            for token in tokens
            if len(token) >= 4
            and token not in GENERIC_PROGRAM_TOKENS
            and not token.isdigit()
        )
        if useful_tokens:
            aliases.add(" ".join(useful_tokens))
            aliases.update(useful_tokens)
        if "visual studio code" in normalized:
            aliases.update(("vs code", "vscode"))
        return tuple(sorted(alias for alias in aliases if alias))

    def _installed_programs(self) -> tuple[InstalledProgram, ...]:
        now = time.monotonic()
        if now < self._installed_program_cache_expires_at:
            return self._installed_program_cache

        try:
            discovered = self._installed_program_provider()
        except Exception:
            discovered = ()

        validated: list[InstalledProgram] = []
        seen: set[tuple[str, str]] = set()
        for program in discovered:
            if not isinstance(program, InstalledProgram):
                continue
            name = program.display_name.strip()
            app_id = program.app_id.strip()
            if not name or not app_id or len(name) > 160 or len(app_id) > 600:
                continue
            if any(ord(character) < 32 for character in name + app_id):
                continue
            if BLOCKED_START_APP_PATTERN.search(app_id):
                continue
            identity = (self._clean_program_request(name), app_id.casefold())
            if not identity[0] or identity in seen:
                continue
            seen.add(identity)
            validated.append(InstalledProgram(name, app_id))

        self._installed_program_cache = tuple(validated)
        self._installed_program_cache_expires_at = (
            now + INSTALLED_PROGRAM_CACHE_TTL_SECONDS
        )
        return self._installed_program_cache

    @staticmethod
    def _default_installed_program_provider() -> tuple[InstalledProgram, ...]:
        powershell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        if not powershell.is_file():
            return ()
        script = (
            "[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
            "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
        )
        completed = subprocess.run(
            [
                str(powershell.resolve()),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            errors="replace",
            timeout=12,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return ()
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return ()
        if isinstance(payload, dict):
            payload = [payload]
        if not isinstance(payload, list):
            return ()
        return tuple(
            InstalledProgram(item["Name"], item["AppID"])
            for item in payload
            if isinstance(item, dict)
            and isinstance(item.get("Name"), str)
            and isinstance(item.get("AppID"), str)
        )

    def _program_command(self, app: str) -> tuple[list[str], str]:
        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        commands: dict[str, tuple[list[str], str]] = {
            "notepad": (["notepad.exe"], "o Bloco de Notas"),
            "calculator": (["calc.exe"], "a Calculadora"),
            "paint": (["mspaint.exe"], "o Paint"),
            "explorer": (["explorer.exe", str(profile)], "o Explorador de Arquivos"),
            "camera": (
                [
                    "explorer.exe",
                    r"shell:AppsFolder\Microsoft.WindowsCamera_8wekyb3d8bbwe!App",
                ],
                "a Câmera",
            ),
        }
        if app in commands:
            return commands[app]

        if app == "chrome":
            candidates = (
                Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
                / "Google/Chrome/Application/chrome.exe",
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Google/Chrome/Application/chrome.exe",
            )
            executable = next((path for path in candidates if path.is_file()), None)
            if executable is None:
                raise FileNotFoundError("Google Chrome não foi encontrado em um local confiável")
            return ([str(executable.resolve())], "o Google Chrome")

        trusted_programs: dict[str, tuple[tuple[Path, ...], str]] = {
            "firefox": (
                (
                    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
                    / "Mozilla Firefox/firefox.exe",
                    Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
                    / "Mozilla Firefox/firefox.exe",
                ),
                "o Firefox",
            ),
            "arduino": (
                (Path(r"C:\Program Files\Arduino IDE\Arduino IDE.exe"),),
                "o Arduino IDE",
            ),
            "coreldraw": (
                (
                    Path(
                        r"C:\Program Files\Corel\CorelDRAW Graphics Suite 2022\Programs64\CorelDRW.exe"
                    ),
                ),
                "o CorelDRAW",
            ),
            "consumer": (
                (
                    Path(
                        r"C:\Program Files (x86)\RAL Tecnologia\Consumer\Arquivos\Consumer.exe"
                    ),
                ),
                "o Consumer",
            ),
        }
        trusted = trusted_programs.get(app)
        if trusted is not None:
            candidates, display_name = trusted
            executable = next((path for path in candidates if path.is_file()), None)
            if executable is None:
                raise FileNotFoundError(
                    f"{PROGRAMS_BY_KEY[app].display_name} não foi encontrado em um local confiável"
                )
            return ([str(executable.resolve())], display_name)

        if app != "edge":
            raise ValueError("programa estático não autorizado")

        candidates = (
            Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
            / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
            / "Microsoft/Edge/Application/msedge.exe",
        )
        executable = next((path for path in candidates if path.is_file()), None)
        if executable is None:
            raise FileNotFoundError("Microsoft Edge não foi encontrado em um local confiável")
        return ([str(executable.resolve())], "o Microsoft Edge")

    def _handle_media(self, _text: str, plain: str) -> str | None:
        key_and_message: tuple[str, str] | None = None

        if any(phrase in plain for phrase in ("aumente o volume", "suba o volume", "volume mais alto")):
            key_and_message = ("volume up", "Aumentei o volume.")
        elif any(phrase in plain for phrase in ("abaixe o volume", "diminua o volume", "volume mais baixo")):
            key_and_message = ("volume down", "Diminuí o volume.")
        elif any(phrase in plain for phrase in ("silencie", "tire o som", "deixe mudo", "mute o som")):
            key_and_message = ("volume mute", "Alterei o estado de silêncio do áudio.")
        elif any(phrase in plain for phrase in ("pause a musica", "pause a midia", "pausar musica")):
            key_and_message = ("play/pause media", "Pausei ou retomei a reprodução.")
        elif any(phrase in plain for phrase in ("continue a musica", "retome a musica", "toque a musica")):
            key_and_message = ("play/pause media", "Pausei ou retomei a reprodução.")
        elif any(phrase in plain for phrase in ("proxima musica", "proxima faixa")):
            key_and_message = ("next track", "Avancei para a próxima faixa.")
        elif any(phrase in plain for phrase in ("musica anterior", "faixa anterior")):
            key_and_message = ("previous track", "Voltei para a faixa anterior.")

        if key_and_message is None:
            return None

        key, message = key_and_message
        try:
            repetitions = 3 if key in {"volume up", "volume down"} else 1
            for _ in range(repetitions):
                self._key_sender(key)
            return message
        except Exception as exc:
            return f"Não consegui controlar o áudio: {exc}"

    def _handle_system_info(self, _text: str, plain: str) -> str | None:
        triggers = (
            "informacoes do computador",
            "informacao do computador",
            "status do computador",
            "espaco no disco",
            "memoria ram",
            "quanto de memoria",
            "como esta o computador",
        )
        if not any(trigger in plain for trigger in triggers):
            return None

        system_drive = Path(os.environ.get("SystemDrive", "C:") + "\\")
        disk = shutil.disk_usage(system_drive)
        memory_total, memory_available = self._memory_info()
        cpu_name = platform.processor() or "processador não identificado"
        return (
            f"Computador {socket.gethostname()}, Windows {platform.release()}, "
            f"{os.cpu_count() or 0} processadores lógicos, {cpu_name}. "
            f"Memória: {_format_size(memory_available)} disponíveis de {_format_size(memory_total)}. "
            f"Disco C: {_format_size(disk.free)} livres de {_format_size(disk.total)}."
        )

    def _handle_file_create(self, text: str, plain: str) -> str | None:
        prefix = re.match(r"^(?:crie|criar|cria)\s+(?:um\s+)?arquivo(?:\s+chamado)?\s+", plain)
        if not prefix:
            return None

        original_body = text[prefix.end() :].strip()
        plain_body = plain[prefix.end() :].strip()
        content = ""
        content_marker = re.search(r"\s+(?:com\s+(?:o\s+)?conteudo|contendo)\s+", plain_body)
        if content_marker:
            content = original_body[content_marker.end() :].strip()
            original_body = original_body[: content_marker.start()].strip()
            plain_body = plain_body[: content_marker.start()].strip()

        name, folder = self._split_name_and_folder(original_body, plain_body)
        if folder is None:
            try:
                self._new_text_path(name, "documentos")
            except ValueError as exc:
                return str(exc)
            self.pending_save = PendingFileSave(
                name=name,
                content=content,
                expires_at=time.monotonic() + SAVE_LOCATION_TTL_SECONDS,
            )
            return (
                f"Onde deseja salvar o arquivo {name}? "
                "Diga Documentos, Downloads ou Área de Trabalho."
            )

        return self._create_text_file(name, content, folder)

    def _handle_pending_save_location(self, plain: str) -> str:
        assert self.pending_save is not None

        if time.monotonic() > self.pending_save.expires_at:
            name = self.pending_save.name
            self.pending_save = None
            return f"O pedido para salvar {name} expirou e foi cancelado."

        normalized = " ".join(re.findall(r"[a-z0-9]+", plain))
        if any(
            normalized == item or normalized.startswith(item + " ")
            for item in ("nao", "cancelar", "cancele", "deixa", "pare")
        ):
            name = self.pending_save.name
            self.pending_save = None
            return f"Salvamento cancelado: {name}."

        folder = self._folder_from_reply(plain)
        if folder is None:
            return (
                "Ainda preciso saber onde salvar. "
                "Diga Documentos, Downloads ou Área de Trabalho, ou diga cancelar."
            )

        pending = self.pending_save
        self.pending_save = None
        return self._create_text_file(pending.name, pending.content, folder)

    def _create_text_file(self, name: str, content: str, folder: str) -> str:
        try:
            path = self._new_text_path(name, folder)
        except ValueError as exc:
            return str(exc)

        if path.exists() or path.is_symlink():
            try:
                snapshot = self._snapshot_existing_file(path)
            except (OSError, ValueError) as exc:
                return f"Não posso substituir esse arquivo: {exc}"

            def overwrite_file() -> str:
                self._write_existing_file(snapshot, content)
                return f"Arquivo criado em {self._display_path(snapshot.path)}."

            self._set_pending_action(
                description=f"substituir o arquivo {self._display_path(path)}",
                execute=overwrite_file,
            )
            return (
                f"O arquivo {self._display_path(path)} já existe. "
                "Diga confirmar para substituí-lo ou cancelar."
            )

        try:
            with path.open("x", encoding="utf-8") as file:
                file.write(content)
        except FileExistsError:
            return "O arquivo passou a existir durante a operação e não foi substituído."
        return f"Arquivo criado em {self._display_path(path)}."

    @staticmethod
    def _folder_from_reply(plain: str) -> str | None:
        matches: list[str] = []
        if re.search(r"\b(?:area de trabalho|desktop)\b", plain):
            matches.append("area de trabalho")
        if re.search(r"\bdocumentos?\b", plain):
            matches.append("documentos")
        if re.search(r"\bdownloads?\b", plain):
            matches.append("downloads")
        return matches[0] if len(matches) == 1 else None

    def _handle_file_append(self, text: str, plain: str) -> str | None:
        prefix = re.match(r"^(?:adicione|adicionar|acrescente|acrescentar)\s+", plain)
        if not prefix:
            return None

        original_body = text[prefix.end() :]
        plain_body = plain[prefix.end() :]
        marker = re.search(r"\s+(?:ao|no)\s+arquivo\s+", plain_body)
        if not marker:
            return "Diga o conteúdo e o nome do arquivo, por exemplo: adicione tarefa ao arquivo notas.txt."

        content = original_body[: marker.start()].strip()
        reference = original_body[marker.end() :].strip()
        plain_reference = plain_body[marker.end() :].strip()
        path, error = self._resolve_reference(reference, plain_reference)
        if error:
            return error
        assert path is not None
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            return "Só posso editar diretamente arquivos de texto."

        try:
            snapshot = self._snapshot_existing_file(path)
            self._append_existing_file(snapshot, content)
        except (OSError, ValueError) as exc:
            return f"Não posso editar esse arquivo: {exc}"
        return f"Conteúdo adicionado ao arquivo {self._display_path(path)}."

    def _handle_file_replace(self, text: str, plain: str) -> str | None:
        prefix = re.match(
            r"^(?:substitua|substituir|troque|trocar)\s+(?:o\s+)?conteudo\s+(?:do|no)\s+arquivo\s+",
            plain,
        )
        if not prefix:
            return None

        original_body = text[prefix.end() :]
        plain_body = plain[prefix.end() :]
        marker = re.search(r"\s+por\s+", plain_body)
        if not marker:
            return "Diga o nome do arquivo e o novo conteúdo usando a palavra por."

        reference = original_body[: marker.start()].strip()
        plain_reference = plain_body[: marker.start()].strip()
        content = original_body[marker.end() :].strip()
        path, error = self._resolve_reference(reference, plain_reference)
        if error:
            return error
        assert path is not None
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            return "Só posso editar diretamente arquivos de texto."

        try:
            snapshot = self._snapshot_existing_file(path)
        except (OSError, ValueError) as exc:
            return f"Não posso substituir esse arquivo: {exc}"

        def replace_file() -> str:
            self._write_existing_file(snapshot, content)
            return f"Conteúdo substituído em {self._display_path(snapshot.path)}."

        self._set_pending_action(
            description=f"substituir o conteúdo de {self._display_path(path)}",
            execute=replace_file,
        )
        return (
            f"Isso substituirá todo o conteúdo de {self._display_path(path)}. "
            "Diga confirmar ou cancelar."
        )

    def _handle_file_delete(self, text: str, plain: str) -> str | None:
        prefix = re.match(
            r"^(?:apague|apagar|exclua|excluir|delete|deletar)\s+(?:o\s+)?arquivo\s+",
            plain,
        )
        if not prefix:
            return None

        reference = text[prefix.end() :].strip()
        plain_reference = plain[prefix.end() :].strip()
        path, error = self._resolve_reference(reference, plain_reference)
        if error:
            return error
        assert path is not None

        try:
            snapshot = self._snapshot_existing_file(path)
        except (OSError, ValueError) as exc:
            return f"Não posso apagar esse arquivo: {exc}"

        def delete_file() -> str:
            self._revalidate_snapshot(snapshot)
            snapshot.path.unlink()
            return f"Arquivo {self._display_path(snapshot.path)} apagado permanentemente."

        self._set_pending_action(
            description=f"apagar permanentemente {self._display_path(path)}",
            execute=delete_file,
        )
        return (
            f"Você pediu para apagar permanentemente {self._display_path(path)}. "
            "Diga confirmar ou cancelar."
        )

    def _handle_file_read(self, text: str, plain: str) -> str | None:
        prefix = re.match(r"^(?:leia|ler)\s+(?:o\s+)?arquivo\s+", plain)
        if not prefix:
            return None

        reference = text[prefix.end() :].strip()
        plain_reference = plain[prefix.end() :].strip()
        path, error = self._resolve_reference(reference, plain_reference)
        if error:
            return error
        assert path is not None
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            return "Consigo ler em voz alta apenas arquivos de texto. Posso abrir esse arquivo para você."

        try:
            snapshot = self._snapshot_existing_file(path)
            content = self._read_existing_file(snapshot)
        except UnicodeDecodeError:
            return "O arquivo não está em um formato de texto que eu consiga ler."
        except (OSError, ValueError) as exc:
            return f"Não posso ler esse arquivo: {exc}"
        if not content.strip():
            return f"O arquivo {self._display_path(path)} está vazio."
        excerpt = content.strip()[:1200]
        suffix = " O restante foi omitido." if len(content.strip()) > 1200 else ""
        return f"Conteúdo de {self._display_path(path)}: {excerpt}.{suffix}"

    def _handle_file_open(self, text: str, plain: str) -> str | None:
        prefix = re.match(r"^(?:abra|abrir)\s+(?:o\s+)?arquivo\s+", plain)
        if not prefix:
            return None

        reference = text[prefix.end() :].strip()
        plain_reference = plain[prefix.end() :].strip()
        path, error = self._resolve_reference(reference, plain_reference)
        if error:
            return error
        assert path is not None
        if path.suffix.lower() in ACTIVE_SCRIPT_EXTENSIONS:
            return "Não posso abrir automaticamente arquivos Python ou JavaScript."
        if path.suffix.lower() not in OPEN_EXTENSIONS:
            return "Esse tipo de arquivo não está autorizado para abertura automática."
        try:
            snapshot = self._snapshot_existing_file(path)
            self._revalidate_snapshot(snapshot)
            self._file_opener(snapshot.path)
            return f"Abrindo {self._display_path(snapshot.path)}."
        except Exception as exc:
            return f"Não consegui abrir o arquivo: {exc}"

    def _handle_file_search(self, text: str, plain: str) -> str | None:
        prefix = re.match(
            r"^(?:procure|procurar|encontre|encontrar|localize|localizar)\s+(?:(?:o|um)\s+)?(?:arquivo|documento)?\s*",
            plain,
        )
        if not prefix:
            return None

        query = text[prefix.end() :].strip().strip("'\"")
        if not query:
            return "Diga parte do nome do arquivo que deseja procurar."
        if any(char in query for char in ("\\", "/", ":", "*", "?", "<", ">", "|")):
            return "Use somente o nome ou parte do nome do arquivo na pesquisa."

        matches = self._search_files(query, limit=10)
        if not matches:
            return "Não encontrei arquivos com esse nome nas pastas autorizadas."
        descriptions = "; ".join(self._display_path(path) for path in matches)
        if len(matches) == 1:
            return f"Encontrei: {descriptions}."
        return f"Encontrei {len(matches)} arquivos: {descriptions}."

    def _split_name_and_folder(self, original: str, plain: str) -> tuple[str, str | None]:
        folder_match = re.search(
            rf"\s+(?:na|no|nas|nos|em)\s+(?P<folder>{FOLDER_PATTERN})\s*$",
            plain,
        )
        if folder_match:
            return original[: folder_match.start()].strip(), folder_match.group("folder")
        return original.strip(), None

    def _resolve_reference(self, original: str, plain: str) -> tuple[Path | None, str | None]:
        name, folder = self._split_name_and_folder(original, plain)
        try:
            safe_name = self._safe_filename(name, add_default_extension=False)
        except ValueError as exc:
            return None, str(exc)

        roots = [self._root_for_folder(folder)] if folder else list(self.allowed_roots.values())
        exact_matches: list[Path] = []
        fuzzy_matches: list[Path] = []
        query = _plain(safe_name)

        for root in roots:
            if not root.exists():
                continue
            for current_root, directories, files in os.walk(root):
                directories[:] = [
                    item
                    for item in directories
                    if not item.startswith(".")
                    and not self._is_reparse_point(Path(current_root, item))
                ]
                for filename in files:
                    candidate = Path(current_root, filename)
                    try:
                        candidate = self._snapshot_existing_file(candidate).path
                    except (OSError, ValueError):
                        continue
                    normalized_name = _plain(filename)
                    if normalized_name == query:
                        exact_matches.append(candidate)
                    elif self._filename_matches(query, normalized_name):
                        fuzzy_matches.append(candidate)
                if len(exact_matches) > 1 or len(fuzzy_matches) > 20:
                    break

        matches = exact_matches or fuzzy_matches
        unique_matches = list(dict.fromkeys(matches))
        if not unique_matches:
            return None, "Não encontrei esse arquivo nas pastas autorizadas."
        if len(unique_matches) > 1:
            options = "; ".join(self._display_path(path) for path in unique_matches[:5])
            return None, f"Encontrei mais de um arquivo. Especifique a pasta: {options}."
        return unique_matches[0], None

    def _new_text_path(self, name: str, folder: str | None) -> Path:
        safe_name = self._safe_filename(name, add_default_extension=True)
        if Path(safe_name).suffix.lower() not in TEXT_EXTENSIONS:
            raise ValueError(
                "Posso criar e editar apenas arquivos de texto, como TXT, Markdown, CSV ou JSON."
            )
        root = self._root_for_folder(folder)
        candidate = root / safe_name
        if candidate.parent != root or not self._is_allowed_path(candidate):
            raise ValueError("O arquivo precisa ficar em uma pasta autorizada.")
        return candidate

    def _safe_filename(self, name: str, *, add_default_extension: bool) -> str:
        cleaned = name.strip().strip("'\"").rstrip(" .")
        if not cleaned or cleaned in {".", ".."}:
            raise ValueError("Diga um nome de arquivo válido.")
        if any(char in cleaned for char in ("\\", "/", ":", "*", "?", "<", ">", "|")):
            raise ValueError("O nome do arquivo contém caracteres não permitidos.")
        device_name = cleaned.split(".", 1)[0].upper()
        if device_name in WINDOWS_RESERVED_NAMES:
            raise ValueError("Esse nome de arquivo é reservado pelo Windows e não é permitido.")
        if add_default_extension and not Path(cleaned).suffix:
            cleaned += ".txt"
        return cleaned

    def _root_for_folder(self, folder: str | None) -> Path:
        if folder is None:
            return self.allowed_roots["documentos"]
        normalized = _plain(folder)
        if normalized in {"area de trabalho", "desktop"}:
            return self.allowed_roots["area de trabalho"]
        if normalized == "downloads":
            return self.allowed_roots["downloads"]
        return self.allowed_roots["documentos"]

    def _search_files(self, query: str, *, limit: int) -> list[Path]:
        normalized_query = _plain(query)
        matches: list[Path] = []
        for root in self.allowed_roots.values():
            if not root.exists():
                continue
            for current_root, directories, files in os.walk(root):
                directories[:] = [
                    item
                    for item in directories
                    if not item.startswith(".")
                    and not self._is_reparse_point(Path(current_root, item))
                ]
                for filename in files:
                    if not self._filename_matches(normalized_query, _plain(filename)):
                        continue
                    candidate = Path(current_root, filename)
                    try:
                        candidate = self._snapshot_existing_file(candidate).path
                    except (OSError, ValueError):
                        continue
                    matches.append(candidate)
                    if len(matches) >= limit:
                        return matches
        return matches

    @staticmethod
    def _filename_matches(normalized_query: str, normalized_name: str) -> bool:
        if normalized_query in normalized_name:
            return True
        query_terms = re.findall(r"[a-z0-9]+", normalized_query)
        return bool(query_terms) and all(term in normalized_name for term in query_terms)

    def _is_allowed_path(self, path: Path) -> bool:
        resolved = path.resolve()
        for root in self.allowed_roots.values():
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _set_pending_action(self, description: str, execute: Callable[[], str]) -> None:
        self.pending_action = PendingAction(
            description=description,
            execute=execute,
            expires_at=time.monotonic() + CONFIRMATION_TTL_SECONDS,
        )

    @staticmethod
    def _is_reparse_point(path: Path) -> bool:
        try:
            status = path.lstat()
        except OSError:
            return False
        return path.is_symlink() or bool(
            getattr(status, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        )

    def _snapshot_existing_file(self, path: Path) -> FileSnapshot:
        absolute = Path(os.path.abspath(path))
        containing_root: Path | None = None
        relative: Path | None = None
        for root in self.allowed_roots.values():
            try:
                relative = absolute.relative_to(root)
                containing_root = root
                break
            except ValueError:
                continue
        if containing_root is None or relative is None:
            raise ValueError("o caminho está fora das pastas autorizadas")

        current = containing_root
        for component in relative.parts:
            current = current / component
            status = current.lstat()
            if current.is_symlink() or bool(
                getattr(status, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
            ):
                raise ValueError("links e pontos de reanálise não são permitidos")

        resolved = absolute.resolve(strict=True)
        if not self._is_allowed_path(resolved) or not resolved.is_file():
            raise ValueError("o arquivo não está em uma pasta autorizada")
        status = resolved.stat()
        if status.st_nlink != 1:
            raise ValueError("arquivos com hard links não são permitidos")
        return FileSnapshot(
            path=resolved,
            device=status.st_dev,
            inode=status.st_ino,
            size=status.st_size,
            modified_ns=status.st_mtime_ns,
        )

    @staticmethod
    def _snapshot_matches_status(snapshot: FileSnapshot, status: os.stat_result) -> bool:
        return (
            status.st_dev == snapshot.device
            and status.st_ino == snapshot.inode
            and status.st_size == snapshot.size
            and status.st_mtime_ns == snapshot.modified_ns
            and status.st_nlink == 1
        )

    def _revalidate_snapshot(self, snapshot: FileSnapshot) -> None:
        current = self._snapshot_existing_file(snapshot.path)
        if current != snapshot:
            raise RuntimeError("o arquivo mudou desde o pedido; a ação foi cancelada")

    def _open_verified_file(self, snapshot: FileSnapshot, flags: int) -> int:
        self._revalidate_snapshot(snapshot)
        descriptor = os.open(snapshot.path, flags | getattr(os, "O_BINARY", 0))
        if not self._snapshot_matches_status(snapshot, os.fstat(descriptor)):
            os.close(descriptor)
            raise RuntimeError("o arquivo mudou durante a operação")
        return descriptor

    def _write_existing_file(self, snapshot: FileSnapshot, content: str) -> None:
        descriptor = self._open_verified_file(snapshot, os.O_WRONLY)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.seek(0)
            file.truncate()
            file.write(content)

    def _append_existing_file(self, snapshot: FileSnapshot, content: str) -> None:
        descriptor = self._open_verified_file(snapshot, os.O_WRONLY | os.O_APPEND)
        separator = "" if snapshot.size == 0 else "\n"
        with os.fdopen(descriptor, "a", encoding="utf-8") as file:
            file.write(separator + content)

    def _read_existing_file(self, snapshot: FileSnapshot) -> str:
        descriptor = self._open_verified_file(snapshot, os.O_RDONLY)
        with os.fdopen(descriptor, "r", encoding="utf-8") as file:
            return file.read()

    def _display_path(self, path: Path) -> str:
        resolved = path.resolve()
        for label, root in self.allowed_roots.items():
            try:
                relative = resolved.relative_to(root)
                return f"{label}\\{relative}"
            except ValueError:
                continue
        return path.name

    @staticmethod
    def _default_process_launcher(command: list[str]) -> None:
        if not command:
            raise ValueError("comando de programa vazio")

        executable = Path(command[0])
        if not executable.is_absolute():
            windows_directory = Path(os.environ.get("WINDIR", r"C:\Windows"))
            trusted_system_paths = {
                "calc.exe": windows_directory / "System32" / "calc.exe",
                "explorer.exe": windows_directory / "explorer.exe",
                "mspaint.exe": windows_directory / "System32" / "mspaint.exe",
                "notepad.exe": windows_directory / "System32" / "notepad.exe",
            }
            trusted_path = trusted_system_paths.get(executable.name.lower())
            if trusted_path is None:
                raise ValueError("executável relativo não autorizado")
            executable = trusted_path

        executable = executable.resolve(strict=True)
        if not executable.is_file():
            raise FileNotFoundError("executável autorizado não encontrado")
        subprocess.Popen([str(executable), *command[1:]], shell=False)

    @staticmethod
    def _default_process_closer(process_names: tuple[str, ...]) -> bool:
        closed = False
        for process_name in process_names:
            completed = subprocess.run(
                ["taskkill.exe", "/IM", process_name, "/T"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=8,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            closed = closed or completed.returncode == 0
        return closed

    @staticmethod
    def _default_file_opener(path: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("A abertura automática está disponível apenas no Windows")
        os.startfile(str(path))  # type: ignore[attr-defined]

    @staticmethod
    def _default_key_sender(key: str) -> None:
        import keyboard

        keyboard.send(key)

    @staticmethod
    def _memory_info() -> tuple[int, int]:
        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if os.name == "nt" and ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys), int(status.ullAvailPhys)
        return 0, 0
