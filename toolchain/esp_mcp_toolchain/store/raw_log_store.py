from __future__ import annotations

from pathlib import Path

from ..paths import logs_dir


def write_raw_log(name: str, content: str) -> Path:
    path = logs_dir() / "raw" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path

