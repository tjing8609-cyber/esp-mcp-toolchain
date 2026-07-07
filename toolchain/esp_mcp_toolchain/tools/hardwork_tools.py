from __future__ import annotations

from ..hardwork.hardwork_store import get_item, list_items, search_items, set_item


def hardwork_list(kind: str = "all") -> dict:
    return {"ok": True, "items": list_items(kind)}


def hardwork_get(hardwork_id: str) -> dict:
    item = get_item(hardwork_id)
    if item is None:
        return {"ok": False, "error_kind": "hardwork_not_found", "message": f"No hardwork item: {hardwork_id}"}
    return {"ok": True, "item": item}


def hardwork_set(kind: str, title: str, content: str, source: str, confidence: float) -> dict:
    return {"ok": True, "item": set_item(kind=kind, title=title, content=content, source=source, confidence=confidence)}


def hardwork_search(query: str, limit: int = 10) -> dict:
    return {"ok": True, "matches": search_items(query=query, limit=limit)}

