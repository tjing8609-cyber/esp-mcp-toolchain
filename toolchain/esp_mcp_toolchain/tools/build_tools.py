from __future__ import annotations

from ..backends.espidf_backend import run_idf_build, run_idf_clean
from ..errors import execution_error, not_implemented
from ..paths import safe_project_path


def esp_project_build(project_dir: str = ".", backend: str = "espidf", target: str = "esp32", log_name: str = "build_default") -> dict:
    if backend != "espidf":
        return execution_error(
            "unsupported_backend",
            f"Unsupported build backend: {backend}",
            tool="esp_project_build",
            suggested_next_actions=["Use backend=espidf"],
        )
    try:
        path = safe_project_path(project_dir)
    except ValueError as exc:
        return execution_error("unsafe_project_path", str(exc), tool="esp_project_build")
    if not path.exists():
        return execution_error("project_dir_missing", f"Project directory does not exist: {path}", tool="esp_project_build")

    result = run_idf_build(path, target=target)
    result.update(
        {
            "tool": "esp_project_build",
            "tool_name": "esp_project_build",
            "tools鍚嶇О": "esp_project_build",
            "implemented": True,
            "backend": backend,
            "target": target,
            "project_dir": str(path),
            "log_name": log_name,
        }
    )
    return result


def esp_project_clean(project_dir: str = ".", mode: str = "clean", confirm: bool = False) -> dict:
    if mode not in {"clean", "fullclean"}:
        return execution_error(
            "unsupported_clean_mode",
            f"Unsupported clean mode: {mode}",
            tool="esp_project_clean",
            suggested_next_actions=["Use mode=clean or mode=fullclean"],
        )
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Project clean removes build artifacts and requires confirm=True.",
            tool="esp_project_clean",
            recoverable=True,
            suggested_next_actions=["Review project_dir", "Call again with confirm=True only after user approval"],
        )
    try:
        path = safe_project_path(project_dir)
    except ValueError as exc:
        return execution_error("unsafe_project_path", str(exc), tool="esp_project_clean")
    if not path.exists():
        return execution_error("project_dir_missing", f"Project directory does not exist: {path}", tool="esp_project_clean")

    result = run_idf_clean(path, mode=mode)
    result.update(
        {
            "tool": "esp_project_clean",
            "tool_name": "esp_project_clean",
            "tools鍚嶇О": "esp_project_clean",
            "implemented": True,
            "backend": "espidf",
            "mode": mode,
            "project_dir": str(path),
        }
    )
    return result
