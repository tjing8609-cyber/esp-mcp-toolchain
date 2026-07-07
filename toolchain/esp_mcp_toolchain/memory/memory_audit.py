from __future__ import annotations

from ..store.jsonl_store import read_jsonl
from .memory_store import audit_path


def read_audit(limit: int = 50) -> list[dict]:
    return read_jsonl(audit_path())[-limit:]

