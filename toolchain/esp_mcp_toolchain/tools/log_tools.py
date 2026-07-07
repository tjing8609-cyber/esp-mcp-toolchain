from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..paths import ensure_runtime_dirs, logs_dir
from ..store.jsonl_store import append_jsonl, read_jsonl, tail_jsonl
from ..utils.time_utils import now_compact, now_iso


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{now_compact()}_{uuid4().hex[:8]}"


def latest_path() -> Path:
    return logs_dir() / "latest.json"


def session_path(run_id: str) -> Path:
    return logs_dir() / "sessions" / f"{run_id}.jsonl"


def write_event(
    tool: str,
    level: str,
    message: str,
    data: dict | None = None,
    *,
    run_id: str | None = None,
    source: str = "toolchain",
) -> dict:
    ensure_runtime_dirs()
    rid = run_id or new_run_id("run")
    event = {
        "event_id": f"evt_{uuid4().hex}",
        "run_id": rid,
        "ts": now_iso(),
        "tool": tool,
        "level": level,
        "source": source,
        "message": message,
        "data": data or {},
    }
    append_jsonl(session_path(rid), event)
    latest = {
        "run_id": rid,
        "status": "error" if level == "error" else "ok",
        "last_tool": tool,
        "has_error": level == "error",
        "summary": message,
        "updated_at": event["ts"],
    }
    latest_path().write_text(json.dumps(latest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return event


def esp_logs_latest() -> dict:
    path = latest_path()
    if not path.exists():
        return {"ok": True, "latest": None}
    return {"ok": True, "latest": json.loads(path.read_text(encoding="utf-8"))}


def esp_logs_get(run_id: str, tail: int = 80) -> dict:
    path = session_path(run_id)
    if not path.exists():
        return {"ok": False, "error_kind": "run_not_found", "message": f"No log for run_id {run_id}"}
    events = tail_jsonl(path, tail)
    return {"ok": True, "run_id": run_id, "events": events}


def esp_logs_query(query: str, limit: int = 20, level: str | None = None) -> dict:
    ensure_runtime_dirs()
    matches: list[dict] = []
    for path in sorted((logs_dir() / "sessions").glob("*.jsonl"), reverse=True):
        for event in reversed(read_jsonl(path)):
            if level and event.get("level") != level:
                continue
            haystack = json.dumps(event, ensure_ascii=False)
            if query.lower() in haystack.lower():
                matches.append(event)
                if len(matches) >= limit:
                    return {"ok": True, "matches": matches}
    return {"ok": True, "matches": matches}

