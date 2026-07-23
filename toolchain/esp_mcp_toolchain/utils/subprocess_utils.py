from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Any, Mapping

def redact_command(args: list[str]) -> str:
    return " ".join(args)


def _exception_output(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def terminate_process_tree(
    process: subprocess.Popen[str],
    *,
    timeout_s: float = 2.0,
) -> str | None:
    """Attempt to terminate a managed subprocess tree within a fixed deadline."""

    if process.poll() is not None:
        return None
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=max(timeout_s, 0.01),
            )
        except subprocess.TimeoutExpired:
            return f"taskkill timed out after {timeout_s} seconds"
        except Exception as exc:
            return f"taskkill failed: {type(exc).__name__}: {exc}"
        if completed.returncode != 0:
            return f"taskkill exited with code {completed.returncode}"
        return None
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return None
    except Exception as exc:
        return f"killpg failed: {type(exc).__name__}: {exc}"
    return None


def run_managed_command(
    args: list[str],
    *,
    cwd: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout_s: float,
    termination_timeout_s: float = 2.0,
) -> dict[str, Any]:
    """Run a bounded child process without inheriting the MCP stdio channel."""

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if cwd is not None:
        popen_kwargs["cwd"] = str(cwd)
    if env is not None:
        popen_kwargs["env"] = dict(env)
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    try:
        process = subprocess.Popen(args, **popen_kwargs)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "managed_command_spawn_failed",
            "message": str(exc),
            "command": redact_command(args),
            "stdout": "",
            "stderr": "",
            "process_tree_termination_attempted": False,
            "process_tree_terminated": None,
            "cleanup_completed": True,
            "cleanup_errors": [],
        }

    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        cleanup_errors: list[str] = []
        termination_error: str | None = None
        try:
            termination_error = terminate_process_tree(
                process,
                timeout_s=termination_timeout_s,
            )
        except Exception as cleanup_exc:
            termination_error = (
                "process-tree termination raised "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        if termination_error:
            cleanup_errors.append(termination_error)

        stdout = _exception_output(exc.stdout)
        stderr = _exception_output(exc.stderr)
        process_reaped = False
        try:
            final_stdout, final_stderr = process.communicate(
                timeout=termination_timeout_s
            )
            stdout = final_stdout or stdout
            stderr = final_stderr or stderr
            process_reaped = True
        except subprocess.TimeoutExpired as cleanup_timeout:
            stdout = _exception_output(cleanup_timeout.stdout) or stdout
            stderr = _exception_output(cleanup_timeout.stderr) or stderr
            try:
                process.kill()
            except Exception as kill_exc:
                cleanup_errors.append(
                    f"direct kill failed: {type(kill_exc).__name__}: {kill_exc}"
                )
            try:
                final_stdout, final_stderr = process.communicate(
                    timeout=termination_timeout_s
                )
                stdout = final_stdout or stdout
                stderr = final_stderr or stderr
                process_reaped = True
            except subprocess.TimeoutExpired as final_timeout:
                stdout = _exception_output(final_timeout.stdout) or stdout
                stderr = _exception_output(final_timeout.stderr) or stderr
                cleanup_errors.append(
                    "process did not exit within the final "
                    f"{termination_timeout_s}-second cleanup window"
                )
            except Exception as final_exc:
                cleanup_errors.append(
                    "final process collection failed: "
                    f"{type(final_exc).__name__}: {final_exc}"
                )
        except Exception as cleanup_exc:
            cleanup_errors.append(
                "process collection after tree termination failed: "
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )

        return {
            "ok": False,
            "error_kind": "managed_command_timeout",
            "message": f"Command timed out after {timeout_s} seconds.",
            "command": redact_command(args),
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "process_tree_termination_attempted": True,
            "process_tree_terminated": process_reaped and termination_error is None,
            "process_tree_termination_error": termination_error,
            "cleanup_completed": process_reaped and not cleanup_errors,
            "cleanup_errors": cleanup_errors,
        }

    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "command": redact_command(args),
        "stdout": stdout,
        "stderr": stderr,
        "process_tree_termination_attempted": False,
        "process_tree_terminated": None,
        "cleanup_completed": True,
        "cleanup_errors": [],
    }

