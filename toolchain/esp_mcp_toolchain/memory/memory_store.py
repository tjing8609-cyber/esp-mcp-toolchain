from __future__ import annotations

import json
from uuid import uuid4

from ..paths import ensure_runtime_dirs, memory_dir
from ..store.jsonl_store import append_jsonl, read_jsonl
from ..utils.time_utils import now_iso
from .memory_policy import validate_memory
from .memory_schema import ACTIVE_STATUS, DELETED_STATUS


def memory_path():
    return memory_dir() / "memory.jsonl"


def audit_path():
    return memory_dir() / "memory_audit.jsonl"


def all_memory() -> list[dict]:
    ensure_runtime_dirs()
    return read_jsonl(memory_path())


def active_memory() -> list[dict]:
    return [item for item in all_memory() if item.get("status") == ACTIVE_STATUS]


def find_by_key(namespace: str, key: str) -> dict | None:
    for item in reversed(active_memory()):
        if item.get("namespace") == namespace and item.get("key") == key:
            return item
    return None


def write_memory(namespace: str, key: str, value: str, memory_type: str, source: str, confidence: float) -> dict:
    errors = validate_memory(namespace, key, value, source, confidence)
    if errors:
        return {"ok": False, "error_kind": "invalid_memory", "message": "; ".join(errors)}

    existing = find_by_key(namespace, key)
    if existing and existing.get("value") != value:
        audit = {
            "audit_id": f"audit_{uuid4().hex}",
            "memory_id": existing["memory_id"],
            "action": "conflict",
            "old_value": existing.get("value"),
            "new_value": value,
            "reason": "memory_write conflict",
            "created_at": now_iso(),
        }
        append_jsonl(audit_path(), audit)
        return {
            "ok": False,
            "error_kind": "memory_conflict",
            "message": "Existing memory has a different value.",
            "existing": existing,
            "audit": audit,
        }

    if existing:
        return {"ok": True, "memory": existing, "created": False}

    item = {
        "memory_id": f"mem_{uuid4().hex}",
        "namespace": namespace,
        "key": key,
        "value": value,
        "memory_type": memory_type,
        "source": source,
        "confidence": confidence,
        "status": ACTIVE_STATUS,
        "created_at": now_iso(),
        "updated_at": None,
    }
    append_jsonl(memory_path(), item)
    return {"ok": True, "memory": item, "created": True}


def read_memory(namespace: str, key: str) -> dict:
    item = find_by_key(namespace, key)
    if item is None:
        return {"ok": False, "error_kind": "memory_not_found", "message": f"No memory for {namespace}:{key}"}
    return {"ok": True, "memory": item}


def search_memory(query: str, limit: int = 10) -> dict:
    needle = query.lower()
    matches = []
    for item in reversed(active_memory()):
        haystack = json.dumps(item, ensure_ascii=False).lower()
        if not needle or needle in haystack:
            matches.append(item)
            if len(matches) >= limit:
                break
    return {"ok": True, "matches": matches}


def update_memory(memory_id: str, value: str, reason: str, confidence: float) -> dict:
    for item in active_memory():
        if item["memory_id"] == memory_id:
            updated = {**item, "value": value, "confidence": confidence, "updated_at": now_iso()}
            append_jsonl(memory_path(), updated)
            append_jsonl(
                audit_path(),
                {
                    "audit_id": f"audit_{uuid4().hex}",
                    "memory_id": memory_id,
                    "action": "update",
                    "old_value": item.get("value"),
                    "new_value": value,
                    "reason": reason,
                    "created_at": now_iso(),
                },
            )
            return {"ok": True, "memory": updated}
    return {"ok": False, "error_kind": "memory_not_found", "message": f"No memory_id {memory_id}"}


def delete_memory(memory_id: str, reason: str) -> dict:
    for item in active_memory():
        if item["memory_id"] == memory_id:
            deleted = {**item, "status": DELETED_STATUS, "updated_at": now_iso()}
            append_jsonl(memory_path(), deleted)
            append_jsonl(
                audit_path(),
                {
                    "audit_id": f"audit_{uuid4().hex}",
                    "memory_id": memory_id,
                    "action": "delete",
                    "old_value": item.get("value"),
                    "new_value": None,
                    "reason": reason,
                    "created_at": now_iso(),
                },
            )
            return {"ok": True, "memory": deleted}
    return {"ok": False, "error_kind": "memory_not_found", "message": f"No memory_id {memory_id}"}

