from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Any
from threading import RLock

from .utils.time_utils import now_iso


class ProjectContextError(RuntimeError):
    pass


_ACTIVE_CONTEXT: dict[str, Any] | None = None
_CONTEXT_LOCK = RLock()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:40] or "project"


def normalize_workspace_root(workspace_root: str | Path) -> Path:
    path = Path(workspace_root).expanduser().resolve()
    if not path.exists():
        raise ProjectContextError(f"workspace root does not exist: {path}")
    if not path.is_dir():
        raise ProjectContextError(f"workspace root is not a directory: {path}")
    return path


def project_id_for(workspace_root: str | Path) -> str:
    root = normalize_workspace_root(workspace_root)
    canonical = os.path.normcase(str(root))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{_slug(root.name)}-{digest}"


def storage_root() -> Path:
    configured = os.environ.get("ESP_MCP_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".codex" / "esp-mcp-toolchain" / "data" / "projects"


def legacy_storage_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parents[2] / "data" / "projects"]
    cache_root = Path.home() / ".codex" / "plugins" / "cache" / "personal-plugins" / "esp-mcp-toolchain"
    if cache_root.exists():
        roots.extend(path / "data" / "projects" for path in cache_root.iterdir() if path.is_dir())
    stable = storage_root().resolve()
    unique = []
    for root in roots:
        resolved = root.resolve()
        if resolved != stable and resolved not in unique:
            unique.append(resolved)
    return unique


def migrate_legacy_project(project_id: str, target: Path) -> dict[str, Any]:
    copied_files = 0
    sources = []
    for root in legacy_storage_roots():
        source = root / project_id
        if not source.exists() or not source.is_dir():
            continue
        sources.append(str(source))
        for source_path in source.rglob("*"):
            if not source_path.is_file():
                continue
            relative = source_path.relative_to(source)
            destination = target / relative
            if destination.exists():
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            copied_files += 1
    return {"sources": sources, "copied_files": copied_files}


def active_context_path() -> Path:
    return storage_root() / ".active" / "project.json"


def _write_active_context(context: dict[str, Any]) -> None:
    path = active_context_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_active_context() -> dict[str, Any] | None:
    path = active_context_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def select_project_context(workspace_root: str | Path) -> dict[str, Any]:
    global _ACTIVE_CONTEXT
    root = normalize_workspace_root(workspace_root)
    project_id = project_id_for(root)
    project_dir = storage_root() / project_id
    migration = {"sources": [], "copied_files": 0}
    if not os.environ.get("ESP_MCP_DATA_ROOT"):
        migration = migrate_legacy_project(project_id, project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = project_dir / "project.json"
    existing: dict[str, Any] = {}
    if metadata_path.exists():
        existing = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata = {
        "project_id": project_id,
        "workspace_root": str(root),
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
    }
    if migration["copied_files"]:
        metadata["migration"] = migration
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context = {**metadata, "project_dir": str(project_dir), "migration": migration}
    with _CONTEXT_LOCK:
        _ACTIVE_CONTEXT = context
        _write_active_context(context)
    return context


def clear_project_context() -> None:
    global _ACTIVE_CONTEXT
    with _CONTEXT_LOCK:
        _ACTIVE_CONTEXT = None
        active_context_path().unlink(missing_ok=True)


def get_project_context(*, required: bool = True) -> dict[str, Any] | None:
    with _CONTEXT_LOCK:
        persisted = _read_active_context()
        context = persisted or (dict(_ACTIVE_CONTEXT) if _ACTIVE_CONTEXT is not None else None)
    if context is None:
        configured = os.environ.get("ESP_MCP_WORKSPACE_ROOT")
        if configured:
            context = select_project_context(configured)
    if context is None and required:
        raise ProjectContextError(
            "No project context is selected. Call project_context_select with the current Codex workspace root."
        )
    return context


def project_context_status() -> dict[str, Any]:
    context = get_project_context(required=False)
    if context is None:
        return {
            "ok": False,
            "error_kind": "project_context_required",
            "message": "No project context is selected.",
            "suggested_next_actions": ["Call project_context_select with the current Codex workspace root"],
        }
    return {"ok": True, **context}

