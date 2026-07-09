from __future__ import annotations

from pathlib import Path

from ..backends.raw_repl_backend import execute_code
from ..config import get_selected_port
from ..errors import execution_error, not_implemented


def esp_exec_code(port: str | None = None, backend: str = "raw_repl", code: str = "", capture_ms: int = 3000) -> dict:
    if backend != "raw_repl":
        return execution_error(
            "unsupported_backend",
            f"Unsupported exec backend: {backend}",
            tool="esp_exec_code",
            suggested_next_actions=["Use backend=raw_repl"],
        )
    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_exec_code",
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
        )

    result = execute_code(selected_port, code, timeout_ms=capture_ms)
    result.update(
        {
            "tool": "esp_exec_code",
            "tool_name": "esp_exec_code",
            "tools鍚嶇О": "esp_exec_code",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
        }
    )
    return result


def esp_run_file(
    port: str,
    backend: str = "raw_repl",
    path: str = "",
    path_type: str = "remote",
    capture_ms: int = 5000,
) -> dict:
    if path_type == "local" and backend == "raw_repl":
        if not path:
            return execution_error("missing_path", "No local file path was provided.", tool="esp_run_file")
        local_path = Path(path)
        if not local_path.exists():
            return execution_error("path_not_found", f"Local file does not exist: {path}", tool="esp_run_file")
        result = esp_exec_code(
            port=port,
            backend=backend,
            code=local_path.read_text(encoding="utf-8"),
            capture_ms=capture_ms,
        )
        result["tool"] = "esp_run_file"
        result["tool_name"] = "esp_run_file"
        result["tools鍚嶇О"] = "esp_run_file"
        return result
    return not_implemented("esp_run_file")
