from __future__ import annotations

from .hardwork_schema import ALLOWED_KINDS
from .hardwork_store import set_item


def initialize_default_items() -> list[dict]:
    items = []
    for kind, filename in ALLOWED_KINDS.items():
        title = filename.removesuffix(".md").replace("_", " ").title()
        items.append(set_item(kind, title, f"# {title}\n\nTBD.\n", "initial_structure", 0.1))
    return items

