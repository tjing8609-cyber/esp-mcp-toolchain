from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

SUPPORT = Path(__file__).with_name("support")
sys.path.insert(0, str(SUPPORT))

from process_lock import can_acquire_process_lock  # noqa: E402


HELPER = SUPPORT / "fake_monitor_server.py"


def _wait_for(path: Path, process: subprocess.Popen, timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stderr = process.stderr.read().decode(errors="replace") if process.stderr else ""
            raise AssertionError(f"Child exited before becoming ready: {process.returncode}\n{stderr}")
        time.sleep(0.02)
    process.kill()
    raise AssertionError("Child MCP server did not become ready")


def _spawn_server(tmp_path: Path, tag: str = "first") -> tuple[subprocess.Popen, dict[str, Path]]:
    files = {
        "ready": tmp_path / f"ready-{tag}.txt",
        "exited": tmp_path / f"exited-{tag}.json",
        "error": tmp_path / f"error-{tag}.json",
        "port_lock": tmp_path / "fake-port.lock",
        "data_root": tmp_path / "project-data",
        "workspace": tmp_path / "workspace",
    }
    for key in ("ready", "exited", "error"):
        files[key].unlink(missing_ok=True)
    env = os.environ.copy()
    env.update(
        {
            "ESP_MCP_DATA_ROOT": str(files["data_root"]),
            "ESP_MCP_TEST_WORKSPACE": str(files["workspace"]),
            "ESP_MCP_TEST_READY": str(files["ready"]),
            "ESP_MCP_TEST_EXITED": str(files["exited"]),
            "ESP_MCP_TEST_ERROR": str(files["error"]),
            "ESP_MCP_TEST_PORT_LOCK": str(files["port_lock"]),
        }
    )
    process = subprocess.Popen(
        [sys.executable, str(HELPER)],
        cwd=HELPER.parents[3],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return process, files


def _start_server(tmp_path: Path, tag: str = "first") -> tuple[subprocess.Popen, dict[str, Path]]:
    process, files = _spawn_server(tmp_path, tag)
    _wait_for(files["ready"], process)
    return process, files


def _close_stdin_and_wait(process: subprocess.Popen, timeout: float = 8) -> tuple[bytes, bytes]:
    assert process.stdin is not None
    process.stdin.close()
    process.wait(timeout=timeout)
    stdout = process.stdout.read() if process.stdout else b""
    stderr = process.stderr.read() if process.stderr else b""
    return stdout, stderr


def test_mcp_stdin_eof_performs_bounded_monitor_cleanup(tmp_path):
    process, files = _start_server(tmp_path)
    assert can_acquire_process_lock(files["port_lock"]) is False

    _stdout, stderr = _close_stdin_and_wait(process)

    assert process.returncode == 0, stderr.decode(errors="replace")
    assert can_acquire_process_lock(files["port_lock"]) is True
    assert json.loads(files["exited"].read_text(encoding="utf-8"))["remaining_monitor_threads"] == []
    assert list((tmp_path / "locks" / "serial").glob("*.json")) == []


def test_forced_termination_releases_os_handle_and_next_start_cleans_stale_lock(tmp_path):
    first, files = _start_server(tmp_path)
    assert can_acquire_process_lock(files["port_lock"]) is False
    first.kill()
    first.wait(timeout=8)

    assert can_acquire_process_lock(files["port_lock"]) is True
    assert list((tmp_path / "locks" / "serial").glob("*.json"))

    second, second_files = _start_server(tmp_path, "second")
    assert can_acquire_process_lock(second_files["port_lock"]) is False
    _stdout, stderr = _close_stdin_and_wait(second)

    assert second.returncode == 0, stderr.decode(errors="replace")
    assert can_acquire_process_lock(second_files["port_lock"]) is True
    assert list((tmp_path / "locks" / "serial").glob("*.json")) == []
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in files["data_root"].rglob("manifest.json")]
    assert any(
        (manifest.get("last_error") or {}).get("error_kind") == "stale_monitor_recovered" for manifest in manifests
    )


def test_second_mcp_process_gets_explicit_live_port_lock_error(tmp_path):
    first, files = _start_server(tmp_path, "owner")
    second, second_files = _spawn_server(tmp_path, "contender")
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not second_files["error"].exists() and second.poll() is None:
        time.sleep(0.02)
    second.wait(timeout=8)

    assert second.returncode == 2
    error = json.loads(second_files["error"].read_text(encoding="utf-8"))
    assert error["error_kind"] == "serial_port_locked"
    assert error["monitor"]["last_error"]["owner"]["project_id"]
    assert can_acquire_process_lock(files["port_lock"]) is False
    live_manifests = [
        json.loads(path.read_text(encoding="utf-8")) for path in files["data_root"].rglob("manifest.json")
    ]
    assert any(manifest.get("state") == "RUNNING" and manifest.get("process_owner") for manifest in live_manifests)
    assert not any(
        (manifest.get("last_error") or {}).get("error_kind") == "stale_monitor_recovered"
        for manifest in live_manifests
    )

    _stdout, stderr = _close_stdin_and_wait(first)
    assert first.returncode == 0, stderr.decode(errors="replace")
    assert can_acquire_process_lock(files["port_lock"]) is True
