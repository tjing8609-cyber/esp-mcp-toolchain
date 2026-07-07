from __future__ import annotations

from typing import Any


def execution_error(
    error_kind: str,
    message: str,
    *,
    tool: str | None = None,
    recoverable: bool = True,
    suggested_next_actions: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "error_kind": error_kind,
        "recoverable": recoverable,
        "message": message,
        "suggested_next_actions": suggested_next_actions or [],
    }
    if tool:
        result["tool"] = tool
    result.update(extra)
    return result


def not_implemented(tool: str) -> dict[str, Any]:
    return execution_error(
        "not_implemented",
        f"{tool} is declared but not implemented in the current phase.",
        tool=tool,
        suggested_next_actions=["Implement the backend adapter", "Add a focused test", "Re-run the CLI command"],
    )

