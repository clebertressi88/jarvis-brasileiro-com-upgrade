from .memory import LocalMemory, PendingMemoryAction

_default_memory = None


def get_local_memory() -> LocalMemory:
    global _default_memory
    if _default_memory is None:
        _default_memory = LocalMemory()
    return _default_memory


__all__ = ["LocalMemory", "PendingMemoryAction", "get_local_memory"]
