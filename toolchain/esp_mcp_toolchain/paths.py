from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return PROJECT_ROOT


def toolchain_dir() -> Path:
    return PROJECT_ROOT / "toolchain"


def data_dir() -> Path:
    return PROJECT_ROOT / "data"


def logs_dir() -> Path:
    return data_dir() / "logs"


def hardwork_dir() -> Path:
    return PROJECT_ROOT / "hardwork"


def memory_dir() -> Path:
    return data_dir() / "memory"


def ensure_runtime_dirs() -> None:
    for path in (
        logs_dir() / "sessions",
        logs_dir() / "raw",
        memory_dir(),
        data_dir() / "artifacts" / "build",
        data_dir() / "artifacts" / "flash",
        data_dir() / "artifacts" / "exports",
        hardwork_dir() / "processed",
        hardwork_dir() / "index",
    ):
        path.mkdir(parents=True, exist_ok=True)


def safe_project_path(value: str | Path) -> Path:
    root = project_root().resolve()
    candidate = (root / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    if root not in (candidate, *candidate.parents):
        raise ValueError(f"path is outside project root: {candidate}")
    return candidate

