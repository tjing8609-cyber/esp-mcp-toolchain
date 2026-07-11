from __future__ import annotations

from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from .utils.time_utils import now_iso


class ProjectContextError(RuntimeError):
    pass


_ACTIVE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("esp_mcp_project_context", default=None)


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
    return Path(__file__).resolve().parents[2] / "data" / "projects"


def select_project_context(workspace_root: str | Path) -> dict[str, Any]:
    root = normalize_workspace_root(workspace_root)
    project_id = project_id_for(root)
    project_dir = storage_root() / project_id
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
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    context = {**metadata, "project_dir": str(project_dir)}
    _ACTIVE_CONTEXT.set(context)
    return context


def clear_project_context() -> None:
    _ACTIVE_CONTEXT.set(None)


def get_project_context(*, required: bool = True) -> dict[str, Any] | None:
    context = _ACTIVE_CONTEXT.get()
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

