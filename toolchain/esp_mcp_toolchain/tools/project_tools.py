from __future__ import annotations

from ..errors import execution_error
from ..project_migration import LegacyMigrationError, migrate_legacy_data
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


def project_migrate_legacy_data(source_root: str, confirm: bool = False) -> dict:
    try:
        result = migrate_legacy_data(source_root, confirm=confirm)
    except (LegacyMigrationError, ProjectContextError) as exc:
        error_kind = exc.error_kind if isinstance(exc, LegacyMigrationError) else "project_context_required"
        return execution_error(
            error_kind,
            str(exc),
            tool="project_migrate_legacy_data",
            suggested_next_actions=[
                "Select the target project context",
                "Provide the exact root of an existing legacy plugin or repository",
                "Run without confirm first to review the migration preview",
            ],
        )
    result.update(
        {
            "tool": "project_migrate_legacy_data",
            "tool_name": "project_migrate_legacy_data",
            "tools名称": "project_migrate_legacy_data",
            "implemented": True,
        }
    )
    return result

