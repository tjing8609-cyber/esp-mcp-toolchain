from __future__ import annotations

import json

from ..paths import hardwork_dir
from ..utils.time_utils import now_iso


def review_state_path():
    return hardwork_dir() / "index" / "review_state.json"


def load_review_state() -> dict:
    path = review_state_path()
    if not path.exists():
        return {"status": "not_started", "attachment_count": 0}
    return json.loads(path.read_text(encoding="utf-8"))


def save_review_state(state: dict) -> dict:
    path = review_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {**state, "updated_at": now_iso()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def hardware_review_required() -> bool:
    return load_review_state().get("status") == "pending"

