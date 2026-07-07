from __future__ import annotations

from pathlib import Path

from ..paths import data_dir


def artifact_path(kind: str, name: str) -> Path:
    path = data_dir() / "artifacts" / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

