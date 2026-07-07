from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from ..paths import hardwork_dir
from ..utils.time_utils import now_iso
from .hardwork_schema import ALLOWED_KINDS


def processed_path(kind: str) -> Path:
    filename = ALLOWED_KINDS.get(kind, f"{kind}.md")
    return hardwork_dir() / "processed" / filename


def index_path() -> Path:
    return hardwork_dir() / "index" / "hardwork_index.json"


def manifest_path() -> Path:
    return hardwork_dir() / "index" / "hardwork_manifest.json"


def load_index() -> dict:
    path = index_path()
    if not path.exists():
        return {"items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_index(index: dict) -> None:
    index_path().parent.mkdir(parents=True, exist_ok=True)
    index_path().write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_items(kind: str = "all") -> list[dict]:
    items = load_index().get("items", [])
    if kind == "all":
        return items
    return [item for item in items if item.get("kind") == kind]


def get_item(hardwork_id: str) -> dict | None:
    if hardwork_id == "index":
        return {"hardwork_id": "index", "kind": "index", "content": json.dumps(load_index(), ensure_ascii=False, indent=2)}
    for item in load_index().get("items", []):
        if item.get("hardwork_id") == hardwork_id or item.get("kind") == hardwork_id:
            path = Path(item["processed_path"])
            content = path.read_text(encoding="utf-8") if path.exists() else ""
            return {**item, "content": content}
    return None


def set_item(kind: str, title: str, content: str, source: str, confidence: float) -> dict:
    path = processed_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    index = load_index()
    items = [item for item in index.get("items", []) if item.get("kind") != kind]
    item = {
        "hardwork_id": kind if kind in ALLOWED_KINDS else f"hw_{uuid4().hex[:8]}",
        "kind": kind,
        "title": title,
        "processed_path": str(path),
        "source": source,
        "confidence": confidence,
        "updated_at": now_iso(),
    }
    items.append(item)
    index["items"] = sorted(items, key=lambda value: value["kind"])
    save_index(index)
    manifest_path().write_text(json.dumps({"updated_at": now_iso(), "count": len(items)}, indent=2) + "\n", encoding="utf-8")
    return item


def search_items(query: str, limit: int = 10) -> list[dict]:
    results = []
    needle = query.lower()
    for item in load_index().get("items", []):
        path = Path(item["processed_path"])
        content = path.read_text(encoding="utf-8") if path.exists() else ""
        haystack = f"{item.get('title', '')}\n{content}".lower()
        if not needle or needle in haystack:
            results.append({**item, "snippet": content[:500]})
            if len(results) >= limit:
                break
    return results

