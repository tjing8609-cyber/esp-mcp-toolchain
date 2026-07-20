from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading


REPO_ROOT = Path(
    os.environ.get("ESP_MCP_SOURCE_ROOT", Path(__file__).resolve().parents[3])
).resolve()
SOURCE_TOOLCHAIN = REPO_ROOT / "toolchain"
sys.path.insert(0, str(SOURCE_TOOLCHAIN))

from process_lock import acquire_process_lock, release_process_lock  # noqa: E402
import esp_mcp_toolchain  # noqa: E402
from esp_mcp_toolchain.project_context import select_project_context  # noqa: E402
from esp_mcp_toolchain.server import serve_stdio  # noqa: E402
from esp_mcp_toolchain.tools import serial_tools  # noqa: E402


if not Path(esp_mcp_toolchain.__file__).resolve().is_relative_to(SOURCE_TOOLCHAIN):
    raise RuntimeError(
        f"Expected ESP MCP source under {SOURCE_TOOLCHAIN}, loaded {esp_mcp_toolchain.__file__}"
    )


class FakeSerial:
    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.rtscts = True
        self.dsrdtr = True
        self.xonxoff = True
        self.dtr = True
        self.rts = True
        self._closed = threading.Event()
        self._handle = None

    def open(self):
        self._handle = acquire_process_lock(os.environ["ESP_MCP_TEST_PORT_LOCK"])
        Path(os.environ["ESP_MCP_TEST_READY"]).write_text("ready\n", encoding="utf-8")

    def read(self, _size: int):
        self._closed.wait(0.05)
        return b""

    def cancel_read(self):
        self._closed.set()

    def close(self):
        self._closed.set()
        if self._handle is not None:
            release_process_lock(self._handle)
            self._handle = None


class FakeSerialModule:
    Serial = FakeSerial


def fake_identity(port: str) -> dict:
    return {
        "port": port,
        "device_path": port,
        "vid": "FFFF",
        "pid": "0002",
        "serial_number": "PROCESS-FAKE-PORT",
        "location": "process-test",
    }


def main() -> int:
    workspace = Path(os.environ["ESP_MCP_TEST_WORKSPACE"])
    workspace.mkdir(parents=True, exist_ok=True)
    select_project_context(workspace)
    serial_tools.get_serial_module = lambda: FakeSerialModule
    serial_tools.describe_serial_port = fake_identity
    result = serial_tools.esp_serial_monitor_start("FAKE_PROCESS_PORT", session_name="process-test")
    if not result.get("ok"):
        Path(os.environ["ESP_MCP_TEST_ERROR"]).write_text(json.dumps(result), encoding="utf-8")
        return 2
    serve_stdio()
    remaining = [thread.name for thread in threading.enumerate() if thread.name.startswith("esp-monitor-")]
    Path(os.environ["ESP_MCP_TEST_EXITED"]).write_text(
        json.dumps({"remaining_monitor_threads": remaining}),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
