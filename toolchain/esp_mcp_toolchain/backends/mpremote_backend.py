from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from ..utils.subprocess_utils import run_managed_command


def _base_command(port: str) -> list[str]:
    return [sys.executable, "-m", "mpremote", "connect", port]


def run_mpremote(args: list[str], *, port: str, timeout_s: int = 30) -> dict[str, Any]:
    command = [*_base_command(port), *args]
    result: dict[str, Any] | None = None
    for _attempt in range(2):
        result = run_managed_command(command, timeout_s=timeout_s)
        if result.get("error_kind") == "managed_command_timeout":
            result["error_kind"] = "mpremote_timeout"
            result["message"] = f"mpremote timed out after {timeout_s} seconds."
            return result
        if result.get("error_kind") == "managed_command_spawn_failed":
            result["error_kind"] = "mpremote_spawn_failed"
            return result
        if result.get("ok") or "could not enter raw repl" not in str(result.get("stderr", "")).lower():
            break
        time.sleep(0.5)

    if result is None:
        return {
            "ok": False,
            "error_kind": "mpremote_not_run",
            "message": "mpremote did not run.",
            "command": " ".join(command),
        }

    result["message"] = "mpremote command completed." if result.get("ok") else "mpremote command failed."
    return result


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
