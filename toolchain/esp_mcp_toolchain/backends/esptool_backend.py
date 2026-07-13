from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .espidf_backend import _idf_path, _idf_python, _run_idf_command
from ..utils.subprocess_utils import redact_command


def run_read_flash(
    *,
    port: str,
    output_path: Path,
    chip: str = "esp32",
    address: int = 0,
    size: int = 0x400000,
    baud: int = 460800,
    timeout_s: int = 240,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(f"{output_path.name}.part")
    partial_path.unlink(missing_ok=True)
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found for flash backup.",
        }
    command = [
        str(_idf_python()),
        "-m",
        "esptool",
        "--chip",
        chip,
        "-p",
        port,
        "-b",
        str(baud),
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "read_flash",
        hex(address),
        hex(size),
        str(partial_path),
    ]
    try:
        result = _run_idf_command(command, output_path.parent, idf_path, timeout_s)
    except Exception as exc:
        partial_path.unlink(missing_ok=True)
        return {
            "ok": False,
            "error_kind": "backup_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    if not result.get("ok"):
        partial_path.unlink(missing_ok=True)
        if result.get("error_kind") == "idf_command_timeout":
            result["error_kind"] = "backup_timeout"
            result["message"] = f"esptool read_flash timed out after {timeout_s} seconds."
        else:
            result["message"] = result.get("message", "Flash backup failed.")
        return result

    if not partial_path.exists():
        return {
            **result,
            "ok": False,
            "error_kind": "backup_output_missing",
            "message": "esptool completed without creating a backup file.",
        }

    actual_size = partial_path.stat().st_size
    if actual_size != size:
        partial_path.unlink(missing_ok=True)
        return {
            **result,
            "ok": False,
            "error_kind": "backup_size_mismatch",
            "message": "Flash backup size does not match the requested size.",
            "expected_bytes": size,
            "actual_bytes": actual_size,
        }

    partial_path.replace(output_path)
    result["message"] = "Flash backup completed."
    return result


def run_erase_flash(*, port: str, chip: str = "esp32", timeout_s: int = 180) -> dict[str, Any]:
    command = [str(_idf_python()), "-m", "esptool", "--chip", chip, "-p", port, "erase_flash"]
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error_kind": "erase_timeout",
            "message": f"esptool erase_flash timed out after {timeout_s} seconds.",
            "command": redact_command(command),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "erase_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "Flash erase completed." if completed.returncode == 0 else "Flash erase failed.",
    }


def run_write_flash(
    *,
    port: str,
    input_path: Path,
    chip: str = "esp32",
    address: int = 0,
    baud: int = 460800,
    timeout_s: int = 300,
) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found for esptool.",
        }
    command = [
        str(_idf_python()),
        "-m",
        "esptool",
        "--chip",
        chip,
        "-p",
        port,
        "-b",
        str(baud),
        "write_flash",
        hex(address),
        str(input_path),
    ]
    try:
        result = _run_idf_command(command, input_path.parent, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "restore_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }
    result["message"] = "Flash image restored." if result.get("ok") else result.get("message", "Flash restore failed.")
    return result
