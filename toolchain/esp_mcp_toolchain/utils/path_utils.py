from __future__ import annotations

from pathlib import Path


def as_posix(path: str | Path) -> str:
    return Path(path).as_posix()

