from __future__ import annotations

import re
from typing import Any


TRACEBACK_HEADER = "Traceback (most recent call last)"
TRACEBACK_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in .*)?')
STRICT_EXCEPTION_RE = re.compile(
    r"^((?:[A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning))|"
    r"Exception|Warning|KeyboardInterrupt|SystemExit|StopIteration|GeneratorExit):\s*(.*)$"
)
TRACEBACK_EXCEPTION_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):\s*(.*)$"
)
_NON_EXCEPTION_LABELS = {"DEBUG", "INFO", "NOTICE", "STATE", "STATUS", "TRACE"}


def _traceback_exception(lines: list[str], start: int) -> tuple[str | None, str | None]:
    """Return the first exception terminator after a traceback header.

    A traceback proves the context, so custom exception names such as
    ``BuzzerFault`` are valid here. Outside a traceback we intentionally keep
    the stricter Error/Exception/Warning suffix rule to avoid treating normal
    log labels such as ``INFO:`` or ``state:`` as exceptions.
    """

    for raw_line in lines[start + 1 :]:
        line = raw_line.strip()
        if not line or TRACEBACK_FILE_RE.search(line):
            continue
        match = TRACEBACK_EXCEPTION_RE.match(line)
        if match and match.group(1).upper() not in _NON_EXCEPTION_LABELS:
            return match.group(1), match.group(2)
    return None, None


def parse_error_text(text: str) -> dict[str, Any]:
    lines = text.splitlines()
    traceback_positions = [
        index for index, line in enumerate(lines) if TRACEBACK_HEADER in line
    ]
    has_traceback = bool(traceback_positions)
    file_match = None
    for match in TRACEBACK_FILE_RE.finditer(text):
        file_match = match

    exception_type = None
    message = None
    if has_traceback:
        exception_type, message = _traceback_exception(lines, traceback_positions[-1])

    if exception_type is None:
        for line in reversed([line.strip() for line in lines if line.strip()]):
            match = STRICT_EXCEPTION_RE.match(line)
            if match:
                exception_type = match.group(1)
                message = match.group(2)
                break

    has_error = bool(has_traceback or exception_type)
    return {
        "has_error": has_error,
        "error_kind": "micropython_traceback" if has_traceback else ("exception_text" if exception_type else None),
        "file": file_match.group(1) if file_match else None,
        "line": int(file_match.group(2)) if file_match else None,
        "exception_type": exception_type,
        "message": message,
        "recoverable": has_error,
        "suggested_next_actions": [
            "Open the related source file",
            "Fix the reported exception",
            "Upload or build again",
            "Run again and capture serial output",
        ]
        if has_error
        else [],
    }


class MicroPythonErrorDetector:
    """Bounded incremental detector that preserves tokens split across serial chunks."""

    def __init__(self, max_chars: int = 65_536):
        if max_chars < 1024:
            raise ValueError("max_chars must be at least 1024")
        self.max_chars = max_chars
        self._buffer = ""
        self._report: dict[str, Any] | None = None

    @property
    def report(self) -> dict[str, Any] | None:
        return dict(self._report) if self._report is not None else None

    def feed(self, text: str) -> dict[str, Any] | None:
        if not text:
            return self.report
        self._buffer = (self._buffer + text)[-self.max_chars :]
        parsed = parse_error_text(self._buffer)
        if parsed["has_error"]:
            self._report = parsed
        return self.report
