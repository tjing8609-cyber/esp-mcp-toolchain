from __future__ import annotations

from pathlib import Path


def parse_bom(path: str | Path) -> list[dict]:
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append({"raw": line})
    return rows

