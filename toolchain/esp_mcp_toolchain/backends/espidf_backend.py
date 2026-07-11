from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..utils.subprocess_utils import redact_command


DEFAULT_IDF_PATH = Path(r"C:\Espressif\frameworks\esp-idf-v5.2.1")
DEFAULT_IDF_PYTHON = Path(r"C:\Espressif\python_env\idf5.2_py3.11_env\Scripts\python.exe")
DEFAULT_TOOL_DIRS = [
    Path(r"C:\Espressif\tools\xtensa-esp-elf\esp-13.2.0_20230928\xtensa-esp-elf\bin"),
    Path(r"C:\Espressif\tools\cmake\3.24.0\bin"),
    Path(r"C:\Espressif\tools\ninja\1.11.1"),
    Path(r"C:\Espressif\tools\idf-git\2.43.0\cmd"),
    Path(r"C:\Espressif\tools\ccache\4.8\ccache-4.8-windows-x86_64"),
]


def _idf_path() -> Path | None:
    env_path = os.environ.get("IDF_PATH") or os.environ.get("ESP_MCP_IDF_PATH")
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None
    return DEFAULT_IDF_PATH if DEFAULT_IDF_PATH.exists() else None


def _idf_python() -> Path:
    env_python = os.environ.get("ESP_MCP_IDF_PYTHON")
    if env_python and Path(env_python).exists():
        return Path(env_python)
    if DEFAULT_IDF_PYTHON.exists():
        return DEFAULT_IDF_PYTHON
    return Path(sys.executable)


def _build_env(idf_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["IDF_PATH"] = str(idf_path)
    if os.name == "nt":
        env.setdefault("OS", "Windows_NT")
        env.setdefault("SYSTEMROOT", env.get("WINDIR", r"C:\Windows"))
        env.setdefault("PROCESSOR_ARCHITECTURE", "AMD64" if sys.maxsize > 2**32 else "x86")
    tool_paths = [str(path) for path in DEFAULT_TOOL_DIRS if path.exists()]
    env["PATH"] = os.pathsep.join([*tool_paths, env.get("PATH", "")])
    return env


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_idf_command(command: list[str], project_dir: Path, idf_path: Path, timeout_s: int) -> dict[str, Any]:
    popen_kwargs: dict[str, Any] = {
        "cwd": str(project_dir),
        "env": _build_env(idf_path),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        return {
            "ok": False,
            "error_kind": "idf_command_timeout",
            "message": f"ESP-IDF command timed out after {timeout_s} seconds.",
            "command": redact_command(command),
            "stdout": stdout,
            "stderr": stderr,
        }
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "command": redact_command(command),
        "stdout": stdout,
        "stderr": stderr,
    }


def _configured_target(project_dir: Path) -> str | None:
    sdkconfig = project_dir / "sdkconfig"
    if not sdkconfig.exists():
        return None
    match = re.search(r'^CONFIG_IDF_TARGET="([^"]+)"$', sdkconfig.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def run_idf_build(project_dir: Path, *, target: str = "esp32", timeout_s: int = 600) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
        }

    actions = ["build"] if _configured_target(project_dir) == target else ["set-target", target, "build"]
    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), *actions]
    try:
        result = _run_idf_command(command, project_dir, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "build_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    result["message"] = "ESP-IDF build completed." if result.get("ok") else result.get("message", "ESP-IDF build failed.")
    return result


def run_idf_flash(project_dir: Path, *, port: str, baud: int = 460800, timeout_s: int = 300) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
        }

    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), "-p", port, "-b", str(baud), "flash"]
    try:
        result = _run_idf_command(command, project_dir, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "flash_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    result["message"] = "ESP-IDF flash completed." if result.get("ok") else result.get("message", "ESP-IDF flash failed.")
    return result


def run_idf_clean(project_dir: Path, *, mode: str = "clean", timeout_s: int = 180) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
        }

    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), mode]
    try:
        result = _run_idf_command(command, project_dir, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "clean_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    result["message"] = f"ESP-IDF {mode} completed." if result.get("ok") else result.get("message", f"ESP-IDF {mode} failed.")
    return result
