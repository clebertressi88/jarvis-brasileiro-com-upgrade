from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from jarvis_config import CODE_MODEL


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


@dataclass(frozen=True)
class LanguageSpec:
    key: str
    label: str
    aliases: tuple[str, ...]
    entrypoint: str
    executable: bool = False


LANGUAGES = (
    LanguageSpec("python", "Python", ("python",), "main.py", True),
    LanguageSpec("javascript", "JavaScript", ("javascript", "java script", "node", "node.js"), "index.js", True),
    LanguageSpec("typescript", "TypeScript", ("typescript", "type script"), "index.ts"),
    LanguageSpec("html", "HTML", ("html", "site", "pagina web"), "index.html"),
    LanguageSpec("java", "Java", ("java",), "Main.java", True),
    LanguageSpec("c", "C", ("linguagem c", "c"), "main.c"),
    LanguageSpec("cpp", "C++", ("c++", "cpp", "c mais mais"), "main.cpp"),
    LanguageSpec("csharp", "C#", ("c#", "c sharp"), "Program.cs"),
    LanguageSpec("go", "Go", ("golang", "go"), "main.go"),
    LanguageSpec("rust", "Rust", ("rust",), "main.rs"),
    LanguageSpec("php", "PHP", ("php",), "index.php"),
    LanguageSpec("ruby", "Ruby", ("ruby",), "main.rb"),
    LanguageSpec("kotlin", "Kotlin", ("kotlin",), "Main.kt"),
    LanguageSpec("swift", "Swift", ("swift",), "main.swift"),
    LanguageSpec("lua", "Lua", ("lua",), "main.lua"),
    LanguageSpec("dart", "Dart", ("dart",), "main.dart"),
    LanguageSpec("r", "R", ("linguagem r",), "main.R"),
    LanguageSpec("sql", "SQL", ("sql", "banco de dados"), "main.sql"),
    LanguageSpec("json", "JSON", ("json",), "data.json"),
    LanguageSpec("yaml", "YAML", ("yaml", "yml"), "config.yaml"),
    LanguageSpec("markdown", "Markdown", ("markdown",), "README.md"),
)

LANGUAGE_BY_KEY = {spec.key: spec for spec in LANGUAGES}


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char)).lower()


def _normalized_phrase(value: str) -> str:
    return re.sub(r"[^a-z0-9+#.]+", " ", _plain(value)).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class PendingProgrammerAction:
    kind: str
    description: str
    expires_at: float
    execute: Callable[[], str]


class ProgrammerAgent:
    """Cria e mantém projetos dentro de um workspace dedicado.

    O modelo local pode produzir código-fonte, mas nunca fornece comandos de
    terminal. Execuções usam perfis fixos, `shell=False` e confirmação exata.
    """

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        model_name: str = CODE_MODEL,
        process_launcher: Callable[[list[str], Path], None] | None = None,
        code_generator: Callable[[LanguageSpec, str, str | None], str] | None = None,
        confirmation_ttl_seconds: float = 120.0,
    ) -> None:
        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        self.workspace = (workspace or profile / "Documents" / "Jarvis Workspace").resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self._process_launcher = process_launcher or self._default_process_launcher
        self._code_generator = code_generator or self._generate_code_with_ollama
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self.pending_action: PendingProgrammerAction | None = None

    def handle(self, user_text: str) -> str | None:
        text = user_text.strip()
        if not text:
            return None
        plain = _plain(text).strip()

        if self.pending_action is not None:
            confirmation = self._handle_confirmation(plain)
            if confirmation is not None:
                return confirmation

        handlers = (
            self._handle_help,
            self._handle_create_project,
            self._handle_modify_project,
            self._handle_list_projects,
            self._handle_open_project,
            self._handle_open_development_app,
            self._handle_compile_project,
            self._handle_execute_project,
            self._handle_extract_archive,
        )
        for handler in handlers:
            response = handler(text, plain)
            if response is not None:
                return response
        return None

    def _handle_confirmation(self, plain: str) -> str | None:
        assert self.pending_action is not None
        normalized = _normalized_phrase(plain)
        if time.monotonic() > self.pending_action.expires_at:
            self.pending_action = None
            return "A confirmação expirou. Peça a ação novamente."

        if normalized.startswith("cancelar") or normalized.startswith("cancele") or normalized.startswith("nao"):
            description = self.pending_action.description
            self.pending_action = None
            return f"Ação cancelada: {description}."

        expected = {
            "change": "confirmar alteracao",
            "execution": "confirmar execucao",
            "compilation": "confirmar compilacao",
            "extraction": "confirmar descompactacao",
        }[self.pending_action.kind]
        if normalized == expected:
            action = self.pending_action
            self.pending_action = None
            try:
                return action.execute()
            except Exception as exc:
                return f"Não consegui concluir a ação do agente programador: {exc}"

        if normalized.startswith("confirm") or normalized in {"sim", "pode fazer"}:
            return f"Para esta ação, diga exatamente: {expected}."

        return None

    def _handle_help(self, _text: str, plain: str) -> str | None:
        normalized = _normalized_phrase(plain)
        triggers = (
            "ajuda de programacao",
            "comandos de programacao",
            "linguagens de programacao",
            "quais linguagens",
        )
        if normalized != "agente programador" and not any(trigger in normalized for trigger in triggers):
            return None
        labels = ", ".join(spec.label for spec in LANGUAGES)
        return (
            f"O agente programador pode gerar projetos em {labels}. "
            "Python, JavaScript e Java podem ser executados quando o runtime estiver instalado, "
            "sempre após você dizer confirmar execução. Também posso compilar projetos com um "
            "compilador confiável, descompactar ZIP e TAR com validação de segurança, abrir o "
            "editor, o Prompt de Comando, o PowerShell e o Arduino IDE."
        )

    def _handle_create_project(self, text: str, plain: str) -> str | None:
        prefix = re.match(
            r"^(?:agente programador[,:]?\s+)?(?:crie|criar|gere|gerar|desenvolva|desenvolver)\s+"
            r"(?:um\s+)?(?:projeto|programa|codigo)\s+(?:em\s+)?",
            plain,
        )
        if not prefix:
            return None

        body_plain = plain[prefix.end() :]
        body_original = text[prefix.end() :]
        name_marker = re.search(r"\s+(?:chamado|com\s+o\s+nome)\s+", body_plain)
        if not name_marker:
            return (
                "Informe a linguagem e o nome. Exemplo: crie um projeto Python "
                "chamado gastos que soma despesas."
            )

        language_text = body_plain[: name_marker.start()].strip()
        spec = self._language_from_text(language_text)
        if spec is None:
            return "Não reconheci a linguagem. Diga “quais linguagens” para ouvir a lista."

        remainder_plain = body_plain[name_marker.end() :].strip()
        remainder_original = body_original[name_marker.end() :].strip()
        description_marker = re.search(r"\s+(?:que|para)\s+", remainder_plain)
        if description_marker:
            project_name = remainder_original[: description_marker.start()].strip()
            description = remainder_original[description_marker.end() :].strip()
        else:
            project_name = remainder_original.strip()
            description = "exibe uma mensagem de Olá Mundo"

        try:
            slug = self._safe_slug(project_name)
            project_path = self._project_path(slug, must_exist=False)
        except ValueError as exc:
            return str(exc)
        if project_path.exists():
            return "Já existe um projeto com esse nome. Escolha outro nome ou peça para alterá-lo."

        try:
            code = self._code_generator(spec, description, None)
            self._validate_generated_code(spec, code)
            self._create_project_atomic(project_path, project_name, spec, description, code)
        except Exception as exc:
            return f"Não consegui gerar o projeto: {exc}"

        return (
            f"Criei o projeto {project_name} em {spec.label}, dentro de Jarvis Workspace. "
            "O código não foi executado."
        )

    def _handle_modify_project(self, text: str, plain: str) -> str | None:
        prefix = re.match(r"^(?:agente programador[,:]?\s+)?no\s+projeto\s+", plain)
        if not prefix:
            return None

        body_plain = plain[prefix.end() :]
        body_original = text[prefix.end() :]
        marker = re.search(r"\s*,?\s*(?:altere|adicione|corrija|modifique|implemente)\s+", body_plain)
        if not marker:
            return "Diga o nome do projeto e a alteração desejada."

        project_name = body_original[: marker.start()].strip(" ,")
        request = body_original[marker.end() :].strip()
        project_path, manifest, error = self._load_project(project_name)
        if error:
            return error
        assert project_path is not None and manifest is not None
        spec = LANGUAGE_BY_KEY.get(manifest.get("language"))
        if spec is None:
            return "A linguagem registrada nesse projeto não é reconhecida."

        entrypoint = self._validated_entrypoint(project_path, manifest)
        try:
            current_code = self._read_safe_text(entrypoint)
            before_hash = _sha256(entrypoint)
            new_code = self._code_generator(spec, request, current_code)
            self._validate_generated_code(spec, new_code)
        except Exception as exc:
            return f"Não consegui preparar a alteração: {exc}"

        if new_code == current_code:
            return "O modelo não propôs nenhuma alteração no arquivo principal."

        def apply_change() -> str:
            self._revalidate_file(entrypoint, before_hash)
            checkpoint_dir = project_path / ".jarvis" / "checkpoints"
            checkpoint_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = checkpoint_dir / f"{timestamp}-{entrypoint.name}.bak"
            shutil.copy2(entrypoint, backup)
            self._atomic_write_text(entrypoint, new_code)
            return f"Alteração aplicada ao projeto {manifest['name']}. Um backup foi criado."

        self.pending_action = PendingProgrammerAction(
            kind="change",
            description=f"alterar o arquivo principal do projeto {manifest['name']}",
            expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            execute=apply_change,
        )
        return (
            f"Preparei uma alteração no projeto {manifest['name']}. "
            "Para aplicá-la, diga exatamente: confirmar alteração."
        )

    def _handle_list_projects(self, _text: str, plain: str) -> str | None:
        if not any(phrase in plain for phrase in ("liste meus projetos", "listar projetos", "quais sao meus projetos")):
            return None
        projects = []
        for manifest_path in sorted(self.workspace.glob("*/.jarvis/project.json")):
            try:
                project = json.loads(manifest_path.read_text(encoding="utf-8"))
                projects.append(f"{project['name']} em {LANGUAGE_BY_KEY[project['language']].label}")
            except Exception:
                continue
        if not projects:
            return "Ainda não existem projetos no Jarvis Workspace."
        return "Projetos encontrados: " + "; ".join(projects) + "."

    def _handle_open_project(self, text: str, plain: str) -> str | None:
        match = re.match(
            r"^(?:agente programador[,:]?\s+)?abra\s+(?:o\s+)?projeto\s+(.+?)(?:\s+(?:no|na)\s+(?:editor|vs\s*code|explorador))?\s*$",
            plain,
        )
        if not match:
            return None
        original_name = text[match.start(1) : match.end(1)].strip()
        project_path, manifest, error = self._load_project(original_name)
        if error:
            return error
        assert project_path is not None and manifest is not None

        editor = self._trusted_vscode()
        if editor is not None:
            command = [str(editor), str(project_path)]
            display = "o editor de código"
        else:
            command = [str(self._system_executable("explorer.exe")), str(project_path)]
            display = "o Explorador de Arquivos, pois o VS Code não está instalado"
        self._process_launcher(command, project_path)
        return f"Abrindo o projeto {manifest['name']} em {display}."

    def _handle_open_development_app(self, _text: str, plain: str) -> str | None:
        if re.match(r"^(?:abra|abrir)\s+(?:o\s+)?(?:editor de codigo|vs\s*code)$", plain):
            editor = self._trusted_vscode()
            if editor is not None:
                self._process_launcher([str(editor), str(self.workspace)], self.workspace)
                return "Abrindo o editor de código no Jarvis Workspace."
            notepad = self._system_executable("notepad.exe")
            self._process_launcher([str(notepad)], self.workspace)
            return "O VS Code não está instalado. Abri o Bloco de Notas como editor seguro."

        terminal_match = re.match(
            r"^(?:abra|abrir)\s+(?:o\s+)?(prompt(?: de comando)?|cmd|powershell|power shell|terminal)"
            r"(?:\s+no\s+projeto\s+(.+))?$",
            plain,
        )
        if terminal_match:
            requested = terminal_match.group(1)
            project_name = terminal_match.group(2)
            cwd = self.workspace
            if project_name:
                project_path, _manifest, error = self._load_project(project_name)
                if error:
                    return error
                assert project_path is not None
                cwd = project_path
            if requested in {"powershell", "power shell"}:
                executable = self._trusted_powershell()
                command = [
                    str(executable),
                    "-NoLogo",
                    "-NoProfile",
                    "-NoExit",
                    "-Command",
                    "Set-Location -LiteralPath $args[0]",
                    str(cwd),
                ]
                display = "o PowerShell"
            else:
                executable = self._system_executable("cmd.exe")
                command = [str(executable), "/K", f'cd /d "{cwd}"']
                display = "o Prompt de Comando"
            self._process_launcher(command, cwd)
            return f"Abrindo {display} em {cwd.name}. Nenhum código foi executado."

        if re.match(r"^(?:abra|abrir)\s+(?:o\s+)?arduino(?:\s+ide)?$", plain):
            arduino = Path(r"C:\Program Files\Arduino IDE\Arduino IDE.exe")
            if not arduino.is_file():
                return "O Arduino IDE não está instalado em um local autorizado."
            self._process_launcher([str(arduino)], self.workspace)
            return "Abrindo o Arduino IDE."
        return None

    def _handle_compile_project(self, text: str, plain: str) -> str | None:
        match = re.match(
            r"^(?:agente programador[,:]?\s+)?(?:compile|compilar|construa|construir)\s+"
            r"(?:o\s+)?projeto\s+(.+?)\s*$",
            plain,
        )
        if not match:
            return None
        project_name = text[match.start(1) : match.end(1)].strip()
        project_path, manifest, error = self._load_project(project_name)
        if error:
            return error
        assert project_path is not None and manifest is not None
        spec = LANGUAGE_BY_KEY.get(manifest.get("language"))
        if spec is None:
            return "A linguagem registrada nesse projeto não é reconhecida."
        entrypoint = self._validated_entrypoint(project_path, manifest)
        before_hash = _sha256(entrypoint)
        try:
            commands = self._compilation_commands(spec, entrypoint, project_path)
        except RuntimeError as exc:
            return str(exc)

        def compile_project() -> str:
            self._revalidate_file(entrypoint, before_hash)
            result = self._run_commands(commands, project_path, timeout=60)
            if result["returncode"] != 0:
                detail = result["output"] or "Sem mensagem de erro."
                return f"A compilação falhou com código {result['returncode']}. {detail}"
            detail = result["output"]
            return "Compilação concluída." + (f" {detail}" if detail else "")

        self.pending_action = PendingProgrammerAction(
            kind="compilation",
            description=f"compilar o projeto {manifest['name']}",
            expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            execute=compile_project,
        )
        return (
            f"Preparei a compilação do projeto {manifest['name']}. "
            "Para autorizar, diga exatamente: confirmar compilação."
        )

    def _handle_execute_project(self, text: str, plain: str) -> str | None:
        match = re.match(
            r"^(?:agente programador[,:]?\s+)?(?:execute|executar|rode|rodar|teste|testar)\s+"
            r"(?:o\s+)?projeto\s+(.+?)\s*$",
            plain,
        )
        if not match:
            return None
        project_name = text[match.start(1) : match.end(1)].strip()
        project_path, manifest, error = self._load_project(project_name)
        if error:
            return error
        assert project_path is not None and manifest is not None
        spec = LANGUAGE_BY_KEY.get(manifest.get("language"))
        if spec is None or not spec.executable:
            return "Essa linguagem pode ser editada, mas não possui execução automática autorizada."
        entrypoint = self._validated_entrypoint(project_path, manifest)
        before_hash = _sha256(entrypoint)
        try:
            commands = self._execution_commands(spec, entrypoint, project_path)
        except RuntimeError as exc:
            return str(exc)

        def execute() -> str:
            self._revalidate_file(entrypoint, before_hash)
            outputs = []
            for argv in commands:
                completed = subprocess.run(
                    argv,
                    cwd=project_path,
                    shell=False,
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    encoding="utf-8",
                    errors="replace",
                )
                combined = (completed.stdout + "\n" + completed.stderr).strip()
                if combined:
                    outputs.append(combined[:2000])
                if completed.returncode != 0:
                    return (
                        f"A execução falhou com código {completed.returncode}. "
                        + (outputs[-1] if outputs else "Sem mensagem de erro.")
                    )
            summary = outputs[-1] if outputs else "O programa terminou sem produzir texto."
            return f"Execução concluída. {summary}"

        self.pending_action = PendingProgrammerAction(
            kind="execution",
            description=f"executar o projeto {manifest['name']} com as permissões do usuário atual",
            expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            execute=execute,
        )
        return (
            f"O projeto {manifest['name']} será executado com acesso normal aos seus arquivos. "
            "Para autorizar, diga exatamente: confirmar execução."
        )

    def _handle_extract_archive(self, text: str, plain: str) -> str | None:
        match = re.match(
            r"^(?:agente programador[,:]?\s+)?(?:descompacte|descompactar|extraia|extrair)\s+"
            r"(?:o\s+)?(?:arquivo\s+)?(.+?)\s*$",
            plain,
        )
        if not match:
            return None
        reference = text[match.start(1) : match.end(1)].strip()
        archive_path, error = self._find_archive(reference)
        if error:
            return error
        assert archive_path is not None

        archive_kind = self._archive_kind(archive_path)
        if archive_kind is None:
            return "Posso descompactar com segurança apenas arquivos ZIP, TAR, TAR.GZ e TGZ."
        if archive_path.stat().st_size > 2 * 1024 * 1024 * 1024:
            return "O arquivo compactado excede o limite de 2 GB."

        base_name = archive_path.name
        for suffix in (".tar.gz", ".tgz", ".tar", ".zip"):
            if base_name.lower().endswith(suffix):
                base_name = base_name[: -len(suffix)]
                break
        try:
            destination_slug = "importado-" + self._safe_slug(base_name)
            destination = self._project_path(destination_slug, must_exist=False)
        except ValueError as exc:
            return str(exc)
        if destination.exists():
            return f"A pasta {destination.name} já existe no Jarvis Workspace. Renomeie-a antes de tentar novamente."

        archive_hash = _sha256(archive_path)

        def extract_archive() -> str:
            self._revalidate_archive_source(archive_path, archive_hash)
            if destination.exists():
                raise RuntimeError("A pasta de destino passou a existir. A extração foi cancelada.")
            file_count, total_size = self._extract_archive_atomic(
                archive_path,
                destination,
                archive_kind,
            )
            file_label = "arquivo" if file_count == 1 else "arquivos"
            return (
                f"Arquivo descompactado em Jarvis Workspace, pasta {destination.name}. "
                f"Foram extraídos {file_count} {file_label}, totalizando {self._format_bytes(total_size)}."
            )

        self.pending_action = PendingProgrammerAction(
            kind="extraction",
            description=f"descompactar {archive_path.name} no Jarvis Workspace",
            expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            execute=extract_archive,
        )
        return (
            f"O arquivo {archive_path.name} será validado e descompactado no Jarvis Workspace. "
            "Para autorizar, diga exatamente: confirmar descompactação."
        )

    def _language_from_text(self, value: str) -> LanguageSpec | None:
        normalized = _normalized_phrase(value)
        for spec in LANGUAGES:
            if normalized in {_normalized_phrase(alias) for alias in spec.aliases}:
                return spec
        return None

    def _safe_slug(self, name: str) -> str:
        normalized = _plain(name).strip()
        slug = re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")
        if not slug or len(slug) > 80:
            raise ValueError("Diga um nome de projeto curto e válido.")
        if slug.upper().split(".")[0] in WINDOWS_RESERVED_NAMES:
            raise ValueError("Esse nome é reservado pelo Windows. Escolha outro.")
        return slug

    def _project_path(self, slug: str, *, must_exist: bool) -> Path:
        candidate = (self.workspace / slug).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("O projeto precisa ficar dentro do Jarvis Workspace.") from exc
        if must_exist and not candidate.is_dir():
            raise ValueError("Projeto não encontrado no Jarvis Workspace.")
        return candidate

    def _load_project(self, spoken_name: str) -> tuple[Path | None, dict | None, str | None]:
        requested_slug = self._safe_slug(spoken_name)
        candidates = []
        for manifest_path in self.workspace.glob("*/.jarvis/project.json"):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("id") == requested_slug or _plain(manifest.get("name", "")) == _plain(spoken_name):
                    candidates.append((manifest_path.parent.parent.resolve(), manifest))
            except Exception:
                continue
        if not candidates:
            return None, None, "Projeto não encontrado no Jarvis Workspace."
        if len(candidates) > 1:
            return None, None, "Encontrei projetos ambíguos. Use o nome exato."
        project_path, manifest = candidates[0]
        if not self._is_safe_path(project_path, allow_directory=True):
            return None, None, "O projeto não passou na validação de segurança."
        return project_path, manifest, None

    def _create_project_atomic(
        self,
        project_path: Path,
        project_name: str,
        spec: LanguageSpec,
        description: str,
        code: str,
    ) -> None:
        temp_path = Path(tempfile.mkdtemp(prefix=".creating-", dir=self.workspace))
        try:
            entrypoint = temp_path / spec.entrypoint
            entrypoint.write_text(code, encoding="utf-8", newline="\n")
            metadata_dir = temp_path / ".jarvis"
            metadata_dir.mkdir()
            manifest = {
                "id": project_path.name,
                "name": project_name,
                "language": spec.key,
                "entrypoint": spec.entrypoint,
                "description": description,
                "model": self.model_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            (metadata_dir / "project.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temp_path, project_path)
        except Exception:
            shutil.rmtree(temp_path, ignore_errors=True)
            raise

    def _validated_entrypoint(self, project_path: Path, manifest: dict) -> Path:
        entrypoint_name = manifest.get("entrypoint", "")
        if not isinstance(entrypoint_name, str) or Path(entrypoint_name).name != entrypoint_name:
            raise ValueError("O arquivo principal registrado é inválido.")
        entrypoint = (project_path / entrypoint_name).resolve()
        if not entrypoint.is_file() or not self._is_safe_path(entrypoint):
            raise ValueError("O arquivo principal não passou na validação de segurança.")
        return entrypoint

    def _is_safe_path(self, path: Path, *, allow_directory: bool = False) -> bool:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(self.workspace)
        except ValueError:
            return False
        current = self.workspace
        for part in relative.parts:
            current = current / part
            if current.exists():
                stat = current.lstat()
                if current.is_symlink() or getattr(stat, "st_file_attributes", 0) & 0x400:
                    return False
                if current.is_file() and stat.st_nlink != 1:
                    return False
        return resolved.is_dir() if allow_directory else resolved.is_file()

    def _read_safe_text(self, path: Path) -> str:
        if not self._is_safe_path(path) or path.stat().st_size > 1024 * 1024:
            raise ValueError("O arquivo é inseguro ou excede 1 MB.")
        return path.read_text(encoding="utf-8")

    def _revalidate_file(self, path: Path, expected_hash: str) -> None:
        if not self._is_safe_path(path) or _sha256(path) != expected_hash:
            raise RuntimeError("O arquivo mudou após o pedido. A ação foi cancelada por segurança.")

    def _atomic_write_text(self, path: Path, content: str) -> None:
        if not self._is_safe_path(path):
            raise RuntimeError("O destino não passou na validação de segurança.")
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _validate_generated_code(self, spec: LanguageSpec, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("O modelo não devolveu código válido.")
        if "\x00" in code or len(code.encode("utf-8")) > 1024 * 1024:
            raise ValueError("O código gerado excede os limites de segurança.")
        if "```" in code:
            raise ValueError("O modelo devolveu marcação em vez de código-fonte limpo.")
        if spec.key == "python":
            try:
                ast.parse(code)
            except SyntaxError as exc:
                raise ValueError(f"O modelo gerou Python inválido na linha {exc.lineno}.") from exc
        elif spec.key == "json":
            try:
                json.loads(code)
            except json.JSONDecodeError as exc:
                raise ValueError("O modelo gerou JSON inválido.") from exc
        elif spec.key == "html" and "<" not in code:
            raise ValueError("O modelo não gerou uma página HTML válida.")
        elif spec.key == "java" and "class " not in code:
            raise ValueError("O modelo não gerou uma classe Java válida.")

    def _generate_code_with_ollama(
        self,
        spec: LanguageSpec,
        request: str,
        current_code: str | None,
    ) -> str:
        import ollama

        if current_code is None:
            task = f"Crie um único arquivo {spec.entrypoint}. Requisito: {request}"
        else:
            task = (
                f"Altere o código abaixo conforme o requisito. Preserve o que não precisa mudar.\n"
                f"Requisito: {request}\nCódigo atual:\n{current_code}"
            )
        response = ollama.chat(
            model=self.model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Você é um programador de {spec.label}. Responda somente JSON válido "
                        'no formato {"code":"conteúdo completo do arquivo"}. Não inclua markdown, '
                        "comandos de terminal nem arquivos adicionais. O valor de code deve ser "
                        f"código-fonte sintaticamente válido para {spec.label}, não uma explicação "
                        "nem a saída esperada. O código não será executado automaticamente."
                    ),
                },
                {"role": "user", "content": task},
            ],
            format="json",
            options={"temperature": 0.2},
        )
        try:
            content = response["message"]["content"]
        except (TypeError, KeyError):
            content = response.message.content
        payload = json.loads(content)
        code = payload.get("code")
        if not isinstance(code, str):
            raise ValueError("A resposta do modelo não contém o campo code.")
        return code

    def _compilation_commands(
        self,
        spec: LanguageSpec,
        entrypoint: Path,
        _project_path: Path,
    ) -> list[list[str]]:
        if spec.key == "python":
            return [[str(Path(sys.executable).resolve()), "-m", "py_compile", str(entrypoint)]]
        if spec.key == "java":
            javac, _java = self._trusted_java()
            if javac is None:
                raise RuntimeError("O compilador Java não foi encontrado em um local confiável.")
            return [[str(javac), str(entrypoint)]]
        if spec.key == "javascript":
            node = self._trusted_node()
            if node is None:
                raise RuntimeError("O Node.js não foi encontrado em um local confiável.")
            return [[str(node), "--check", str(entrypoint)]]
        raise RuntimeError(
            f"O projeto em {spec.label} pode ser editado, mas o compilador correspondente "
            "não está instalado ou ainda não foi autorizado."
        )

    def _run_commands(self, commands: list[list[str]], cwd: Path, *, timeout: int) -> dict[str, object]:
        outputs: list[str] = []
        last_returncode = 0
        for argv in commands:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                shell=False,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            last_returncode = completed.returncode
            combined = (completed.stdout + "\n" + completed.stderr).strip()
            if combined:
                outputs.append(combined[:4000])
            if completed.returncode != 0:
                break
        return {
            "returncode": last_returncode,
            "output": outputs[-1] if outputs else "",
        }

    def _find_archive(self, reference: str) -> tuple[Path | None, str | None]:
        plain_reference = _plain(reference).strip()
        folder_match = re.search(
            r"\s+(?:na|no|nas|nos|em)\s+(area de trabalho|desktop|documentos|downloads)\s*$",
            plain_reference,
        )
        requested_folder = None
        if folder_match:
            requested_folder = folder_match.group(1)
            reference = reference[: folder_match.start()].strip()
            plain_reference = plain_reference[: folder_match.start()].strip()

        clean_name = reference.strip().strip("'\"").rstrip(" .")
        if not clean_name or any(char in clean_name for char in ("\\", "/", ":", "*", "?", "<", ">", "|")):
            return None, "Diga somente o nome do arquivo compactado, sem caminhos ou caracteres especiais."

        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        roots = {
            "area de trabalho": (profile / "Desktop").resolve(),
            "desktop": (profile / "Desktop").resolve(),
            "documentos": (profile / "Documents").resolve(),
            "downloads": (profile / "Downloads").resolve(),
        }
        search_roots = [roots[requested_folder]] if requested_folder else [
            roots["area de trabalho"],
            roots["documentos"],
            roots["downloads"],
        ]
        query = _plain(clean_name)
        exact: list[Path] = []
        fuzzy: list[Path] = []
        for root in dict.fromkeys(search_roots):
            if not root.is_dir():
                continue
            for current_root, directories, files in os.walk(root):
                safe_directories = []
                for directory in directories:
                    candidate_dir = Path(current_root, directory)
                    try:
                        attributes = getattr(candidate_dir.lstat(), "st_file_attributes", 0)
                        if not candidate_dir.is_symlink() and not attributes & 0x400:
                            safe_directories.append(directory)
                    except OSError:
                        continue
                directories[:] = safe_directories
                for filename in files:
                    normalized = _plain(filename)
                    if normalized != query and query not in normalized:
                        continue
                    candidate = Path(current_root, filename).resolve()
                    if not self._is_safe_archive_source(candidate, roots.values()):
                        continue
                    if normalized == query:
                        exact.append(candidate)
                    else:
                        fuzzy.append(candidate)
                if len(exact) > 1 or len(fuzzy) > 20:
                    break
        matches = list(dict.fromkeys(exact or fuzzy))
        if not matches:
            return None, "Não encontrei esse arquivo em Downloads, Documentos ou Área de Trabalho."
        if len(matches) > 1:
            options = "; ".join(str(path) for path in matches[:5])
            return None, f"Encontrei mais de um arquivo. Especifique a pasta: {options}."
        return matches[0], None

    @staticmethod
    def _is_safe_archive_source(path: Path, allowed_roots) -> bool:
        resolved = path.resolve()
        matched_root = None
        for root in allowed_roots:
            try:
                resolved.relative_to(root)
                matched_root = root
                break
            except ValueError:
                continue
        if matched_root is None or not resolved.is_file():
            return False
        current = matched_root
        for part in resolved.relative_to(matched_root).parts:
            current = current / part
            try:
                current_stat = current.lstat()
            except OSError:
                return False
            if current.is_symlink() or getattr(current_stat, "st_file_attributes", 0) & 0x400:
                return False
            if current.is_file() and current_stat.st_nlink != 1:
                return False
        return True

    def _revalidate_archive_source(self, path: Path, expected_hash: str) -> None:
        profile = Path(os.environ.get("USERPROFILE", Path.home()))
        roots = tuple(
            (profile / folder).resolve()
            for folder in ("Desktop", "Documents", "Downloads")
        )
        if not self._is_safe_archive_source(path, roots) or _sha256(path) != expected_hash:
            raise RuntimeError("O arquivo compactado mudou após o pedido. A ação foi cancelada.")

    @staticmethod
    def _archive_kind(path: Path) -> str | None:
        lower_name = path.name.lower()
        if lower_name.endswith(".zip"):
            return "zip"
        if lower_name.endswith((".tar", ".tar.gz", ".tgz")):
            return "tar"
        return None

    def _extract_archive_atomic(
        self,
        archive_path: Path,
        destination: Path,
        archive_kind: str,
    ) -> tuple[int, int]:
        temporary = Path(tempfile.mkdtemp(prefix=".extracting-", dir=self.workspace))
        try:
            if archive_kind == "zip":
                file_count, total_size = self._extract_zip_safely(archive_path, temporary)
            else:
                file_count, total_size = self._extract_tar_safely(archive_path, temporary)
            for extracted in temporary.rglob("*"):
                extracted_stat = extracted.lstat()
                if extracted.is_symlink() or getattr(extracted_stat, "st_file_attributes", 0) & 0x400:
                    raise RuntimeError("O arquivo contém links ou pontos de nova análise não permitidos.")
                if extracted.is_file() and extracted_stat.st_nlink != 1:
                    raise RuntimeError("O arquivo contém hard links não permitidos.")
            os.replace(temporary, destination)
            return file_count, total_size
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise

    def _extract_zip_safely(self, archive_path: Path, destination: Path) -> tuple[int, int]:
        max_files = 10_000
        max_bytes = 500 * 1024 * 1024
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if len(members) > max_files:
                raise RuntimeError("O ZIP contém arquivos demais.")
            total_declared = sum(member.file_size for member in members if not member.is_dir())
            if total_declared > max_bytes:
                raise RuntimeError("O conteúdo descompactado excederia 500 MB.")
            if total_declared > max(archive_path.stat().st_size, 1) * 200:
                raise RuntimeError("A taxa de compressão do ZIP é suspeita.")

            file_count = 0
            total_written = 0
            for member in members:
                mode = (member.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK or member.flag_bits & 0x1:
                    raise RuntimeError("ZIP com links ou criptografia não é permitido.")
                target = self._safe_archive_member_path(destination, member.filename)
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, target.open("xb") as output:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > max_bytes:
                            raise RuntimeError("O conteúdo descompactado excedeu 500 MB.")
                        output.write(chunk)
                file_count += 1
            return file_count, total_written

    def _extract_tar_safely(self, archive_path: Path, destination: Path) -> tuple[int, int]:
        max_files = 10_000
        max_bytes = 500 * 1024 * 1024
        with tarfile.open(archive_path, mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) > max_files:
                raise RuntimeError("O TAR contém arquivos demais.")
            regular_members = [member for member in members if member.isfile()]
            if sum(member.size for member in regular_members) > max_bytes:
                raise RuntimeError("O conteúdo descompactado excederia 500 MB.")

            file_count = 0
            total_written = 0
            for member in members:
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise RuntimeError("TAR com links, dispositivos ou pipes não é permitido.")
                target = self._safe_archive_member_path(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise RuntimeError("O TAR contém um tipo de entrada não permitido.")
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError("Não foi possível ler uma entrada do TAR.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("xb") as output:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        total_written += len(chunk)
                        if total_written > max_bytes:
                            raise RuntimeError("O conteúdo descompactado excedeu 500 MB.")
                        output.write(chunk)
                file_count += 1
            return file_count, total_written

    @staticmethod
    def _safe_archive_member_path(destination: Path, member_name: str) -> Path:
        normalized_name = member_name.replace("\\", "/")
        pure_path = PurePosixPath(normalized_name)
        parts = tuple(part for part in pure_path.parts if part not in {"", "."})
        if pure_path.is_absolute() or not parts:
            raise RuntimeError("O arquivo compactado contém um caminho inválido.")
        for part in parts:
            base_name = part.split(".")[0].upper()
            if (
                part == ".."
                or ":" in part
                or part.rstrip(" .") != part
                or base_name in WINDOWS_RESERVED_NAMES
            ):
                raise RuntimeError("O arquivo compactado contém um caminho perigoso.")
        target = destination.joinpath(*parts).resolve()
        try:
            target.relative_to(destination.resolve())
        except ValueError as exc:
            raise RuntimeError("O arquivo compactado tentou sair da pasta de destino.") from exc
        return target

    @staticmethod
    def _format_bytes(byte_count: int) -> str:
        value = float(byte_count)
        for unit in ("bytes", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{int(value)} {unit}" if unit == "bytes" else f"{value:.1f} {unit}"
            value /= 1024
        return f"{value:.1f} GB"

    def _execution_commands(self, spec: LanguageSpec, entrypoint: Path, project_path: Path) -> list[list[str]]:
        if spec.key == "python":
            return [[str(Path(sys.executable).resolve()), str(entrypoint)]]
        if spec.key == "javascript":
            node = self._trusted_node()
            if node is None:
                raise RuntimeError("O Node.js não foi encontrado em um local confiável.")
            return [[str(node), str(entrypoint)]]
        if spec.key == "java":
            javac, java = self._trusted_java()
            if javac is None or java is None:
                raise RuntimeError("O Java JDK não foi encontrado em um local confiável.")
            return [
                [str(javac), str(entrypoint)],
                [str(java), "-cp", str(project_path), "Main"],
            ]
        raise RuntimeError("Essa linguagem não possui execução automática autorizada.")

    def _system_executable(self, name: str) -> Path:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidate = (system_root / "System32" / name).resolve()
        if not candidate.is_file():
            raise RuntimeError(f"O executável autorizado {name} não foi encontrado.")
        return candidate

    def _trusted_powershell(self) -> Path:
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        candidate = (system_root / "System32/WindowsPowerShell/v1.0/powershell.exe").resolve()
        if not candidate.is_file():
            raise RuntimeError("O PowerShell autorizado não foi encontrado.")
        return candidate

    def _trusted_vscode(self) -> Path | None:
        candidates = (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Microsoft VS Code/Code.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft VS Code/Code.exe",
        )
        return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)

    def _trusted_node(self) -> Path | None:
        candidates = list(
            (Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages").glob(
                "OpenJS.NodeJS*/node-*/node.exe"
            )
        )
        candidates.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs/node.exe")
        return next((candidate.resolve() for candidate in candidates if candidate.is_file()), None)

    def _trusted_java(self) -> tuple[Path | None, Path | None]:
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Eclipse Adoptium"
        javac_candidates = sorted(base.glob("jdk-*/bin/javac.exe"), reverse=True)
        java_candidates = sorted(base.glob("jdk-*/bin/java.exe"), reverse=True)
        javac = next((path.resolve() for path in javac_candidates if path.is_file()), None)
        java = next((path.resolve() for path in java_candidates if path.is_file()), None)
        return javac, java

    @staticmethod
    def _default_process_launcher(command: list[str], cwd: Path) -> None:
        subprocess.Popen(command, cwd=cwd, shell=False)
