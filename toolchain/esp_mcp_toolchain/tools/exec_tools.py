from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ..backends import mpremote_backend
from ..backends.raw_repl_backend import execute_code, interrupt_program
from ..config import get_selected_port
from ..errors import execution_error, not_implemented
from ..paths import safe_project_path
from ..utils.error_detection import parse_error_text
from .log_tools import logged_task


RawReplBackend = Literal["raw_repl"]
CaptureMs = Annotated[int, Field(ge=100, le=30_000)]
Baudrate = Annotated[int, Field(ge=1, le=10_000_000)]
StopTimeoutMs = Annotated[int, Field(ge=100, le=30_000)]


def _attach_error_report(result: dict) -> dict:
    error_report = parse_error_text(
        "\n".join(
            (
                str(result.get("stdout") or ""),
                str(result.get("stderr") or ""),
            )
        )
    )
    result["has_error"] = error_report["has_error"]
    result["error_report"] = error_report if error_report["has_error"] else None
    return result


@logged_task(
    task_type="exec_code",
    selected_port_arg="port",
    payload_args=("backend", "capture_ms"),
    completion_artifacts=("structured_error",),
)
def esp_exec_code(
    port: str | None = None,
    backend: RawReplBackend = "raw_repl",
    code: str = "",
    capture_ms: CaptureMs = 3000,
) -> dict:
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

    result = _attach_error_report(execute_code(selected_port, code, timeout_ms=capture_ms))
    result.update(
        {
            "tool": "esp_exec_code",
            "tool_name": "esp_exec_code",
            "tools名称": "esp_exec_code",
            "tools鍚嶇О": "esp_exec_code",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
        }
    )
    return result


@logged_task(
    task_type="program_stop",
    selected_port_arg="port",
    payload_args=("baudrate", "timeout_ms"),
    completion_artifacts=("result_error",),
)
def esp_program_stop(
    port: str | None = None,
    baudrate: Baudrate = 115200,
    timeout_ms: StopTimeoutMs = 1500,
) -> dict:
    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_program_stop",
            suggested_next_actions=[
                "Run esp_port_list",
                "Run esp_port_select with the confirmed board port",
            ],
        )

    result = interrupt_program(
        selected_port,
        baudrate=baudrate,
        timeout_ms=timeout_ms,
    )
    result.update(
        {
            "tool": "esp_program_stop",
            "tool_name": "esp_program_stop",
            "tools名称": "esp_program_stop",
            "tools鍚嶇О": "esp_program_stop",
            "implemented": True,
            "port": selected_port,
            "baudrate": baudrate,
            "reset_command_sent": False,
            "physical_reset_excluded": False,
        }
    )
    return result


@logged_task(
    task_type="run_file",
    selected_port_arg="port",
    payload_args=("backend", "path", "path_type", "capture_ms"),
    completion_artifacts=("structured_error",),
)
def esp_run_file(
    port: str | None = None,
    backend: str = "mpremote",
    path: str = "",
    path_type: str = "remote",
    capture_ms: int = 5000,
) -> dict:
    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool="esp_run_file",
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
        )
    if path_type == "remote" and backend == "mpremote":
        if not path:
            return execution_error("missing_path", "No remote file path was provided.", tool="esp_run_file")
        result = _attach_error_report(
            mpremote_backend.run_remote_file(
                port=selected_port,
                remote_path=path,
                timeout_s=max(1, capture_ms // 1000),
            )
        )
        result.update(
            {
                "tool": "esp_run_file",
                "tool_name": "esp_run_file",
                "tools名称": "esp_run_file",
                "tools鍚嶇О": "esp_run_file",
                "implemented": True,
                "port": selected_port,
                "backend": backend,
                "path": path,
                "path_type": path_type,
            }
        )
        return result
    if path_type == "remote" and backend == "raw_repl":
        if not path:
            return execution_error("missing_path", "No remote file path was provided.", tool="esp_run_file")
        code = f"exec(open({path!r}).read())"
        result = esp_exec_code(port=selected_port, backend="raw_repl", code=code, capture_ms=capture_ms)
        result["tool"] = "esp_run_file"
        result["tool_name"] = "esp_run_file"
        result["tools名称"] = "esp_run_file"
        result["tools鍚嶇О"] = "esp_run_file"
        result["path"] = path
        result["path_type"] = path_type
        return result
    if path_type == "local" and backend == "raw_repl":
        if not path:
            return execution_error("missing_path", "No local file path was provided.", tool="esp_run_file")
        try:
            local_path = safe_project_path(path)
        except ValueError as exc:
            return execution_error(
                "unsafe_local_path",
                str(exc),
                tool="esp_run_file",
                suggested_next_actions=["Use a path inside the selected workspace"],
            )
        if not local_path.exists():
            return execution_error("path_not_found", f"Local file does not exist: {path}", tool="esp_run_file")
        result = esp_exec_code(
            port=selected_port,
            backend="raw_repl",
            code=local_path.read_text(encoding="utf-8"),
            capture_ms=capture_ms,
        )
        result["tool"] = "esp_run_file"
        result["tool_name"] = "esp_run_file"
        result["tools名称"] = "esp_run_file"
        result["tools鍚嶇О"] = "esp_run_file"
        result["path"] = str(local_path)
        result["path_type"] = path_type
        return result
    return not_implemented("esp_run_file")
