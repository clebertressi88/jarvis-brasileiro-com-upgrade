from __future__ import annotations

import os
from pathlib import Path


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


CHAT_MODEL = os.environ.get("JARVIS_CHAT_MODEL", "jarvis:3b-fast")
QUALITY_MODEL = os.environ.get("JARVIS_QUALITY_MODEL", "jarvis:4b-pt")
CODE_MODEL = os.environ.get("JARVIS_CODE_MODEL", "qwen2.5-coder:3b")
EMBED_MODEL = os.environ.get("JARVIS_EMBED_MODEL", "nomic-embed-text")

# 4K keeps voice latency acceptable on this CPU. The quality profile can be
# launched with a larger value through the environment variable.
CHAT_CONTEXT_WINDOW = _bounded_int("JARVIS_CONTEXT_WINDOW", 4096, 2048, 16384)
RECENT_INTERACTIONS = _bounded_int("JARVIS_RECENT_INTERACTIONS", 10, 3, 12)

_profile = Path(os.environ.get("USERPROFILE", Path.home()))
_local_data = Path(os.environ.get("LOCALAPPDATA", _profile / "AppData" / "Local"))
MEMORY_DATABASE = Path(
    os.environ.get(
        "JARVIS_MEMORY_DATABASE",
        _local_data / "Jarvis" / "jarvis-memory.sqlite3",
    )
)

# Document indexing is deliberately narrower than normal file tools. Downloads
# are not indexed automatically because they often contain installers and large
# temporary files.
MEMORY_ROOTS = (
    (_profile / "Documents").resolve(),
    (_profile / "Desktop").resolve(),
)
