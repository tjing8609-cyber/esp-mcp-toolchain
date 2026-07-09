from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .espidf_backend import _idf_python
from ..utils.subprocess_utils import redact_command


def run_read_flash(
    *,
    port: str,
    output_path: Path,
    chip: str = "esp32",
    address: int = 0,
    size: int = 0x400000,
    baud: int = 460800,
    timeout_s: int = 300,
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
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
        "read_flash",
        hex(address),
        hex(size),
        str(output_path),
    ]
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
            "error_kind": "backup_timeout",
            "message": f"esptool read_flash timed out after {timeout_s} seconds.",
            "command": redact_command(command),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "backup_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "Flash backup completed." if completed.returncode == 0 else "Flash backup failed.",
    }


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
