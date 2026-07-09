from __future__ import annotations

import time
from typing import Any

from .pyserial_backend import get_serial_module


RAW_REPL_PROMPT = b"raw REPL; CTRL-B to exit"


def _read_until(ser: Any, markers: tuple[bytes, ...], timeout_s: float) -> bytes:
    deadline = time.monotonic() + max(timeout_s, 0)
    buffer = bytearray()
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buffer.extend(chunk)
            if any(marker in buffer for marker in markers):
                return bytes(buffer)
        else:
            time.sleep(0.01)
    return bytes(buffer)


def execute_code(port: str, code: str, *, baudrate: int = 115200, timeout_ms: int = 3000) -> dict[str, Any]:
    serial_mod = get_serial_module()
    if serial_mod is None:
        return {
            "ok": False,
            "error_kind": "pyserial_missing",
            "message": "pyserial is not installed.",
            "stdout": "",
            "stderr": "",
        }

    if not code.strip():
        return {
            "ok": False,
            "error_kind": "empty_code",
            "message": "No code was provided.",
            "stdout": "",
            "stderr": "",
        }

    timeout_s = max(timeout_ms, 100) / 1000
    try:
        ser = serial_mod.Serial(port, baudrate=baudrate, timeout=0.1)
        try:
            ser.dtr = False
            ser.rts = False
            ser.reset_input_buffer()

            ser.write(b"\r\x03\x03")
            time.sleep(0.1)
            ser.read(4096)

            banner = b""
            for _attempt in range(3):
                ser.write(b"\x01")
                banner += _read_until(ser, (RAW_REPL_PROMPT,), timeout_s)
                if RAW_REPL_PROMPT in banner:
                    break
                time.sleep(0.1)
            if RAW_REPL_PROMPT not in banner:
                return {
                    "ok": False,
                    "error_kind": "raw_repl_enter_failed",
                    "message": "Timed out waiting for raw REPL prompt.",
                    "stdout": "",
                    "stderr": banner.decode("utf-8", errors="replace"),
                }

            ser.write(code.encode("utf-8") + b"\x04")
            response = _read_until(ser, (b"\x04",), timeout_s)
            if b"OK" not in response:
                return {
                    "ok": False,
                    "error_kind": "raw_repl_execute_failed",
                    "message": "MicroPython raw REPL did not acknowledge the code.",
                    "stdout": "",
                    "stderr": response.decode("utf-8", errors="replace"),
                }

            tail = _read_until(ser, (b"\x04>",), timeout_s)
            payload = response + tail
            after_ok = payload.split(b"OK", 1)[1]
            parts = after_ok.split(b"\x04")
            stdout = parts[0].decode("utf-8", errors="replace")
            stderr = parts[1].decode("utf-8", errors="replace") if len(parts) > 1 else ""
            return {
                "ok": stderr.strip() == "",
                "stdout": stdout,
                "stderr": stderr,
                "message": "Code executed through MicroPython raw REPL.",
            }
        finally:
            try:
                ser.write(b"\x02")
            finally:
                ser.close()
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "raw_repl_io_error",
            "message": str(exc),
            "stdout": "",
            "stderr": "",
        }
