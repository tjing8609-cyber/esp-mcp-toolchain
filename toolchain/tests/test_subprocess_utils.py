from __future__ import annotations

import subprocess

from esp_mcp_toolchain.utils import subprocess_utils


class FakeProcess:
    pid = 4321

    def __init__(self):
        self.returncode = None
        self.communicate_timeouts: list[float] = []
        self.kill_calls = 0

    def poll(self):
        return self.returncode

    def communicate(self, timeout: float):
        self.communicate_timeouts.append(timeout)
        if len(self.communicate_timeouts) == 1:
            raise subprocess.TimeoutExpired(
                cmd=["python", "worker.py"],
                timeout=timeout,
                output="partial stdout",
                stderr="partial stderr",
            )
        if len(self.communicate_timeouts) == 2:
            raise subprocess.TimeoutExpired(
                cmd=["python", "worker.py"],
                timeout=timeout,
            )
        self.returncode = -9
        return "", ""

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9


def test_managed_timeout_has_bounded_cleanup_and_kill_fallback(monkeypatch):
    process = FakeProcess()
    terminated: list[int] = []
    monkeypatch.setattr(
        subprocess_utils.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        subprocess_utils,
        "terminate_process_tree",
        lambda target, *, timeout_s: terminated.append(target.pid) or None,
    )

    result = subprocess_utils.run_managed_command(
        ["python", "worker.py"],
        timeout_s=1,
        termination_timeout_s=0.25,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "managed_command_timeout"
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"
    assert result["process_tree_termination_attempted"] is True
    assert result["process_tree_terminated"] is True
    assert terminated == [4321]
    assert process.kill_calls == 1
    assert process.communicate_timeouts == [1, 0.25, 0.25]


def test_windows_tree_termination_uses_bounded_taskkill(monkeypatch):
    process = FakeProcess()
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(subprocess_utils.os, "name", "nt")
    monkeypatch.setattr(
        subprocess_utils.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs))
        or subprocess.CompletedProcess(command, returncode=0),
    )

    cleanup_error = subprocess_utils.terminate_process_tree(
        process,
        timeout_s=2.5,
    )

    assert cleanup_error is None
    assert calls[0][0] == ["taskkill", "/PID", "4321", "/T", "/F"]
    assert calls[0][1]["timeout"] == 2.5


def test_managed_spawn_failure_is_structured(monkeypatch):
    monkeypatch.setattr(
        subprocess_utils.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(5, "Access is denied")
        ),
    )

    result = subprocess_utils.run_managed_command(
        ["python", "worker.py"],
        timeout_s=1,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "managed_command_spawn_failed"
    assert result["stdout"] == ""
    assert result["stderr"] == ""
