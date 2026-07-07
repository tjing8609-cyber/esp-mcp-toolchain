from __future__ import annotations

from ..memory.memory_store import delete_memory, read_memory, search_memory, update_memory, write_memory


def memory_write(namespace: str, key: str, value: str, memory_type: str, source: str, confidence: float) -> dict:
    return write_memory(namespace, key, value, memory_type, source, confidence)


def memory_read(namespace: str, key: str) -> dict:
    return read_memory(namespace, key)


def memory_search(query: str, limit: int = 10) -> dict:
    return search_memory(query, limit)


def memory_update(memory_id: str, value: str, reason: str, confidence: float) -> dict:
    return update_memory(memory_id, value, reason, confidence)


def memory_delete(memory_id: str, reason: str) -> dict:
    return delete_memory(memory_id, reason)

