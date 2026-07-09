from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..utils.subprocess_utils import redact_command


def _base_command(port: str) -> list[str]:
    return [sys.executable, "-m", "mpremote", "connect", port]


def run_mpremote(args: list[str], *, port: str, timeout_s: int = 30) -> dict[str, Any]:
    command = [*_base_command(port), *args]
    completed = None
    for attempt in range(2):
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "error_kind": "mpremote_timeout",
                "message": f"mpremote timed out after {timeout_s} seconds.",
                "command": redact_command(command),
            }
        except Exception as exc:
            return {
                "ok": False,
                "error_kind": "mpremote_spawn_failed",
                "message": str(exc),
                "command": redact_command(command),
            }
        if completed.returncode == 0 or "could not enter raw repl" not in completed.stderr.lower():
            break
        time.sleep(0.5)

    if completed is None:
        return {
            "ok": False,
            "error_kind": "mpremote_not_run",
            "message": "mpremote did not run.",
            "command": redact_command(command),
        }

    return {
        "ok": completed.returncode == 0,
        "returncode": completed.returncode,
        "command": redact_command(command),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "message": "mpremote command completed." if completed.returncode == 0 else "mpremote command failed.",
    }


def list_files(*, port: str, remote_dir: str = "/", timeout_s: int = 30) -> dict[str, Any]:
    return run_mpremote(["fs", "ls", remote_dir], port=port, timeout_s=timeout_s)


def read_file(*, port: str, remote_path: str, timeout_s: int = 30) -> dict[str, Any]:
    return run_mpremote(["fs", "cat", remote_path], port=port, timeout_s=timeout_s)


def upload_file(*, port: str, local_path: Path, remote_path: str, timeout_s: int = 60) -> dict[str, Any]:
    return run_mpremote(["fs", "cp", str(local_path), f":{remote_path}"], port=port, timeout_s=timeout_s)


def download_file(*, port: str, remote_path: str, local_path: Path, timeout_s: int = 60) -> dict[str, Any]:
    return run_mpremote(["fs", "cp", f":{remote_path}", str(local_path)], port=port, timeout_s=timeout_s)


def run_remote_file(*, port: str, remote_path: str, timeout_s: int = 30) -> dict[str, Any]:
    code = f"exec(open({remote_path!r}).read())"
    return run_mpremote(["exec", code], port=port, timeout_s=timeout_s)
