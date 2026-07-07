from __future__ import annotations

from pathlib import Path


def describe_pdf(path: str | Path) -> dict:
    candidate = Path(path)
    return {"path": str(candidate), "exists": candidate.exists(), "parser": "manifest_only"}

