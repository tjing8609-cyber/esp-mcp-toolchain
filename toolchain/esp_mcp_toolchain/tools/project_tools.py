from __future__ import annotations

from ..errors import execution_error
from ..project_context import ProjectContextError, project_context_status as read_context_status
from ..project_context import select_project_context


def project_context_select(workspace_root: str) -> dict:
    try:
        context = select_project_context(workspace_root)
    except ProjectContextError as exc:
        return execution_error(
            "invalid_project_context",
            str(exc),
            tool="project_context_select",
            suggested_next_actions=["Provide an existing Codex workspace directory"],
        )
    return {
        "ok": True,
        "message": "Project context selected.",
        "context": context,
        "project_id": context["project_id"],
        "workspace_root": context["workspace_root"],
        "project_dir": context["project_dir"],
    }


def project_context_status() -> dict:
    return read_context_status()

