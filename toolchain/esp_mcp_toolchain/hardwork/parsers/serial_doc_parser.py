from __future__ import annotations

from pathlib import Path


def parse_serial_doc(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8")

