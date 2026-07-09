from __future__ import annotations

import os
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
    tool_paths = [str(path) for path in DEFAULT_TOOL_DIRS if path.exists()]
    env["PATH"] = os.pathsep.join([*tool_paths, env.get("PATH", "")])
    return env


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

    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), "set-target", target, "build"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(project_dir),
            env=_build_env(idf_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_kind": "build_timeout",
            "message": f"ESP-IDF build timed out after {timeout_s} seconds.",
            "command": redact_command(command),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "build_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "ESP-IDF build completed." if completed.returncode == 0 else "ESP-IDF build failed.",
    }


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
        completed = subprocess.run(
            command,
            cwd=str(project_dir),
            env=_build_env(idf_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_kind": "flash_timeout",
            "message": f"ESP-IDF flash timed out after {timeout_s} seconds.",
            "command": redact_command(command),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "flash_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "ESP-IDF flash completed." if completed.returncode == 0 else "ESP-IDF flash failed.",
    }


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
        completed = subprocess.run(
            command,
            cwd=str(project_dir),
            env=_build_env(idf_path),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_kind": "clean_timeout",
            "message": f"ESP-IDF {mode} timed out after {timeout_s} seconds.",
            "command": redact_command(command),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "clean_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": f"ESP-IDF {mode} completed." if completed.returncode == 0 else f"ESP-IDF {mode} failed.",
    }
