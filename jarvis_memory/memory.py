from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Sequence

from jarvis_config import EMBED_MODEL, MEMORY_DATABASE, MEMORY_ROOTS


TEXT_EXTENSIONS = {
    ".c",
    ".cpp",
    ".cs",
    ".csv",
    ".go",
    ".html",
    ".java",
    ".js",
    ".json",
    ".md",
    ".php",
    ".py",
    ".rs",
    ".sql",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
MAX_FILE_BYTES = 512 * 1024
MAX_INDEXED_FILES = 300
MAX_INDEXED_CHUNKS = 900
WINDOWS_REPARSE_POINT = 0x400
MEMORY_STOPWORDS = {
    "a", "ao", "as", "de", "do", "dos", "e", "em", "eu", "meu", "minha",
    "o", "os", "preferido", "preferida", "que", "um", "uma",
}


def _plain(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_marks).strip().lower()


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", _plain(value)))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


@dataclass
class PendingMemoryAction:
    kind: str
    expires_at: float
    items: tuple[str, ...] = ()


class LocalMemory:
    """Persistent, local-only memory with optional Ollama embeddings.

    The class never scans a folder on startup. Document contents enter the
    database only after the user explicitly requests indexing and confirms it.
    """

    def __init__(
        self,
        *,
        database_path: Path | None = None,
        allowed_roots: Iterable[Path] | None = None,
        embed_model: str = EMBED_MODEL,
        embedding_provider: Callable[[list[str]], list[list[float]]] | None = None,
        confirmation_ttl_seconds: float = 120.0,
    ) -> None:
        self.database_path = (database_path or MEMORY_DATABASE).resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.allowed_roots = tuple(
            Path(root).resolve() for root in (allowed_roots or MEMORY_ROOTS)
        )
        self.embed_model = embed_model
        self._embedding_provider = embedding_provider or self._embed_with_ollama
        self.confirmation_ttl_seconds = confirmation_ttl_seconds
        self.pending_action: PendingMemoryAction | None = None
        self._lock = threading.RLock()
        self._initialize_database()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        with self._lock, self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY,
                    content TEXT NOT NULL UNIQUE,
                    embedding TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exchanges (
                    id INTEGER PRIMARY KEY,
                    user_text TEXT NOT NULL,
                    assistant_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS document_chunks (
                    path TEXT NOT NULL,
                    modified_ns INTEGER NOT NULL,
                    size INTEGER NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT,
                    indexed_at TEXT NOT NULL,
                    PRIMARY KEY (path, chunk_index)
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

    def handle(self, user_text: str) -> str | None:
        text = user_text.strip()
        plain = _plain(text)
        if not plain:
            return None

        if self.pending_action is not None:
            response = self._handle_confirmation(plain)
            if response is not None:
                return response

        remember = re.match(r"^(?:jarvis[, ]+)?(?:lembre|guarde|memorize)\s+(?:que\s+)?", plain)
        if remember:
            fact = text[remember.end() :].strip()
            if not fact:
                return "Diga o que você quer que eu lembre."
            self.add_memory(fact)
            return "Certo. Guardei essa informação somente na memória local."

        if re.match(r"^(?:jarvis[, ]+)?(?:o que voce lembra|mostre (?:a )?memoria)", plain):
            query_match = re.search(r"\bsobre\s+(.+)$", text, flags=re.IGNORECASE)
            query = query_match.group(1).strip() if query_match else ""
            memories = self.recall(query, limit=6)
            if not memories:
                return "Minha memória local ainda não contém lembranças correspondentes."
            return "Na memória local encontrei: " + "; ".join(memories) + "."

        if re.match(r"^(?:jarvis[, ]+)?(?:esqueca|apague|limpe)\s+(?:toda\s+)?(?:a\s+)?memoria", plain):
            self.pending_action = PendingMemoryAction(
                kind="clear",
                expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            )
            return (
                "Isso apagará as lembranças, o histórico e o índice local de documentos. "
                "Diga exatamente confirmar esquecimento ou cancelar."
            )

        forget = re.match(
            r"^(?:jarvis[, ]+)?(?:esqueca|remova da memoria)\s+(?:que\s+|a informacao\s+)?",
            plain,
        )
        if forget:
            query = text[forget.end() :].strip()
            matches = self._memories_matching_forget(query) if query else []
            if not matches:
                return "Não encontrei uma lembrança correspondente para esquecer."
            self.pending_action = PendingMemoryAction(
                kind="forget",
                items=tuple(matches),
                expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            )
            description = "; ".join(matches)
            return (
                f"Vou remover da memória local: {description}. "
                "Diga exatamente confirmar esquecimento ou cancelar."
            )

        if re.match(r"^(?:jarvis[, ]+)?(?:indexe|indexar|catalogue|catalogar)\s+(?:os\s+)?(?:meus\s+)?documentos", plain):
            self.pending_action = PendingMemoryAction(
                kind="index",
                expires_at=time.monotonic() + self.confirmation_ttl_seconds,
            )
            roots = " e ".join(str(root) for root in self.allowed_roots)
            return (
                f"Posso criar um índice local dos arquivos de texto em {roots}. "
                "Nenhum conteúdo será enviado à internet. Diga exatamente confirmar indexação ou cancelar."
            )

        search = re.match(
            r"^(?:jarvis[, ]+)?(?:pesquise|procure|consulte)\s+(?:na\s+memoria|nos\s+meus\s+documentos)\s+(?:por\s+)?",
            plain,
        )
        if search:
            query = text[search.end() :].strip()
            if not query:
                return "Diga o assunto que devo procurar na memória local."
            results = self.search_documents(query, limit=5)
            if not results:
                return "Não encontrei esse assunto no índice local. Talvez seja necessário indexar os documentos."
            descriptions = [f"{path}: {excerpt}" for path, excerpt in results]
            return "Encontrei no índice local: " + "; ".join(descriptions)

        return None

    def _handle_confirmation(self, plain: str) -> str | None:
        assert self.pending_action is not None
        if time.monotonic() > self.pending_action.expires_at:
            self.pending_action = None
            return "A confirmação da ação de memória expirou. Faça o pedido novamente."
        if plain in {"cancelar", "cancele", "nao", "nao faca isso"}:
            self.pending_action = None
            return "A ação de memória foi cancelada."

        if self.pending_action.kind == "clear":
            if plain != "confirmar esquecimento":
                return "Para apagar a memória, diga exatamente confirmar esquecimento ou cancelar."
            self.pending_action = None
            self.clear()
            return "A memória local, o histórico e o índice de documentos foram apagados."

        if self.pending_action.kind == "forget":
            if plain != "confirmar esquecimento":
                return "Para remover essa lembrança, diga exatamente confirmar esquecimento ou cancelar."
            items = self.pending_action.items
            self.pending_action = None
            self._delete_memories(items)
            return f"Removi {len(items)} lembrança(s) da memória local."

        if self.pending_action.kind == "index":
            if plain not in {"confirmar indexacao", "confirmar indexação"}:
                return "Para indexar, diga exatamente confirmar indexação ou cancelar."
            self.pending_action = None
            files, chunks = self.index_documents()
            return f"Indexação local concluída: {files} arquivos e {chunks} trechos catalogados."
        return None

    def _embed_with_ollama(self, texts: list[str]) -> list[list[float]]:
        import ollama

        try:
            response = ollama.embed(model=self.embed_model, input=texts)
            embeddings = response["embeddings"]
        except AttributeError:
            embeddings = [
                ollama.embeddings(model=self.embed_model, prompt=text)["embedding"]
                for text in texts
            ]
        return [list(map(float, embedding)) for embedding in embeddings]

    def _safe_embeddings(self, texts: list[str]) -> list[list[float] | None]:
        if not texts:
            return []
        try:
            embedded = self._embedding_provider(texts)
            if len(embedded) != len(texts):
                raise ValueError("quantidade de embeddings incompatível")
            return embedded
        except Exception:
            # Lexical search remains available when Ollama or the embedding
            # model is temporarily unavailable.
            return [None] * len(texts)

    def add_memory(self, content: str) -> None:
        cleaned = re.sub(r"\s+", " ", content).strip()[:2000]
        embedding = self._safe_embeddings([cleaned])[0]
        encoded = json.dumps(embedding) if embedding is not None else None
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO memories(content, embedding, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(content) DO UPDATE SET embedding=excluded.embedding
                """,
                (cleaned, encoded, now),
            )

    def recall(self, query: str, *, limit: int = 5) -> list[str]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT content, embedding FROM memories ORDER BY id DESC LIMIT 200"
            ).fetchall()
        if not rows:
            return []
        if not query:
            return [row["content"] for row in rows[:limit]]
        return [item[0] for item in self._rank_rows(query, rows, limit=limit)]

    def record_exchange(self, user_text: str, assistant_text: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                "INSERT INTO exchanges(user_text, assistant_text, created_at) VALUES (?, ?, ?)",
                (user_text[:8000], assistant_text[:16000], datetime.now(timezone.utc).isoformat()),
            )
            # Keep a useful audit trail without allowing unbounded growth.
            connection.execute(
                "DELETE FROM exchanges WHERE id NOT IN (SELECT id FROM exchanges ORDER BY id DESC LIMIT 500)"
            )

    def recent_messages(self, *, interactions: int) -> list[dict[str, str]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT user_text, assistant_text FROM exchanges ORDER BY id DESC LIMIT ?",
                (interactions,),
            ).fetchall()
        messages: list[dict[str, str]] = []
        for row in reversed(rows):
            messages.append({"role": "user", "content": row["user_text"]})
            messages.append({"role": "assistant", "content": row["assistant_text"]})
        return messages

    def get_summary(self) -> str:
        with self._lock, self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key='conversation_summary'"
            ).fetchone()
        return row["value"] if row else ""

    def set_summary(self, summary: str) -> None:
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES ('conversation_summary', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (summary[:6000],),
            )

    def context_for(self, query: str, *, limit: int = 4) -> str:
        memories = self.recall(query, limit=limit)
        documents = self.search_documents(query, limit=limit)
        sections: list[str] = []
        if memories:
            sections.append("Lembranças explícitas:\n- " + "\n- ".join(memories))
        if documents:
            formatted = [f"{path}: {excerpt}" for path, excerpt in documents]
            sections.append("Trechos do índice local:\n- " + "\n- ".join(formatted))
        return "\n\n".join(sections)

    def index_documents(self) -> tuple[int, int]:
        candidates: list[Path] = []
        for root in self.allowed_roots:
            if not root.exists() or self._is_reparse(root):
                continue
            for current_root, directories, files in os.walk(root):
                current_path = Path(current_root)
                directories[:] = [
                    directory
                    for directory in directories
                    if not directory.startswith(".")
                    and not self._is_reparse(current_path / directory)
                ]
                for filename in files:
                    path = current_path / filename
                    if path.suffix.lower() in TEXT_EXTENSIONS:
                        candidates.append(path)
                        if len(candidates) >= MAX_INDEXED_FILES:
                            break
                if len(candidates) >= MAX_INDEXED_FILES:
                    break
            if len(candidates) >= MAX_INDEXED_FILES:
                break

        indexed_files = 0
        indexed_chunks = 0
        for path in candidates:
            if indexed_chunks >= MAX_INDEXED_CHUNKS:
                break
            try:
                status = path.stat()
                if status.st_size > MAX_FILE_BYTES or status.st_nlink != 1:
                    continue
                resolved = path.resolve(strict=True)
                if not self._is_allowed(resolved) or self._is_reparse(path):
                    continue
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            chunks = self._chunks(content)[: MAX_INDEXED_CHUNKS - indexed_chunks]
            if not chunks:
                continue
            embeddings: list[list[float] | None] = []
            for start in range(0, len(chunks), 16):
                embeddings.extend(self._safe_embeddings(chunks[start : start + 16]))
            now = datetime.now(timezone.utc).isoformat()
            with self._lock, self._connection() as connection:
                connection.execute("DELETE FROM document_chunks WHERE path=?", (str(resolved),))
                connection.executemany(
                    """
                    INSERT INTO document_chunks(
                        path, modified_ns, size, chunk_index, content, embedding, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            str(resolved),
                            status.st_mtime_ns,
                            status.st_size,
                            index,
                            chunk,
                            json.dumps(embedding) if embedding is not None else None,
                            now,
                        )
                        for index, (chunk, embedding) in enumerate(zip(chunks, embeddings))
                    ],
                )
            indexed_files += 1
            indexed_chunks += len(chunks)
        return indexed_files, indexed_chunks

    def search_documents(self, query: str, *, limit: int = 5) -> list[tuple[str, str]]:
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT path, content, embedding FROM document_chunks LIMIT ?",
                (MAX_INDEXED_CHUNKS,),
            ).fetchall()
        ranked = self._rank_rows(query, rows, limit=limit)
        return [(Path(item[1]["path"]).name, item[0][:350].replace("\n", " ")) for item in ranked]

    def _rank_rows(self, query: str, rows: Sequence[sqlite3.Row], *, limit: int):
        if not rows:
            return []
        query_embedding = self._safe_embeddings([query])[0]
        query_tokens = _tokens(query)
        ranked = []
        for row in rows:
            content = row["content"]
            content_tokens = _tokens(content)
            lexical = len(query_tokens & content_tokens) / max(1, len(query_tokens))
            semantic = 0.0
            if query_embedding is not None and row["embedding"]:
                try:
                    semantic = _cosine(query_embedding, json.loads(row["embedding"]))
                except (TypeError, ValueError, json.JSONDecodeError):
                    semantic = 0.0
            score = semantic * 0.75 + lexical * 0.25
            # Ignore weak cosine similarities, which are common between short
            # unrelated sentences and would make selective forgetting unsafe.
            if score >= 0.55:
                ranked.append((content, row, score))
        ranked.sort(key=lambda item: item[2], reverse=True)
        return ranked[:limit]

    def clear(self) -> None:
        with self._lock, self._connection() as connection:
            connection.execute("DELETE FROM memories")
            connection.execute("DELETE FROM exchanges")
            connection.execute("DELETE FROM document_chunks")
            connection.execute("DELETE FROM settings")

    def _delete_memories(self, contents: Sequence[str]) -> None:
        if not contents:
            return
        with self._lock, self._connection() as connection:
            connection.executemany(
                "DELETE FROM memories WHERE content=?",
                [(content,) for content in contents],
            )

    def _memories_matching_forget(self, query: str) -> list[str]:
        distinctive = _tokens(query) - MEMORY_STOPWORDS
        if not distinctive:
            return []
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT content FROM memories ORDER BY id DESC LIMIT 200"
            ).fetchall()
        return [
            row["content"]
            for row in rows
            if distinctive.issubset(_tokens(row["content"]))
        ][:10]

    def _is_allowed(self, path: Path) -> bool:
        for root in self.allowed_roots:
            try:
                path.resolve().relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _is_reparse(path: Path) -> bool:
        try:
            status = path.lstat()
        except OSError:
            return True
        return path.is_symlink() or bool(
            getattr(status, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
        )

    @staticmethod
    def _chunks(content: str, *, size: int = 1200, overlap: int = 150) -> list[str]:
        cleaned = content.replace("\x00", "").strip()
        if not cleaned:
            return []
        chunks = []
        start = 0
        while start < len(cleaned):
            end = min(len(cleaned), start + size)
            chunk = cleaned[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end == len(cleaned):
                break
            start = end - overlap
        return chunks
