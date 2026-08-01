import os
import sys

# Ollama is installed for this Windows user, but its directory is not in the
# system PATH. Add it before importing Jarvis, which checks Ollama on import.
ollama_dir = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama")
os.environ["PATH"] = ollama_dir + os.pathsep + os.environ.get("PATH", "")

import jarvis

sys.argv = [
    "jarvis.py",
    "--input",
    "voice",
    "--output",
    "voice",
    "--push-to-talk",
    "on",
    "--interface",
    "ui",
]

jarvis.main()
