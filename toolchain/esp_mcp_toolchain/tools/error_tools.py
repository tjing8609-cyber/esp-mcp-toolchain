from __future__ import annotations

import re

from .log_tools import esp_logs_get


TRACEBACK_FILE_RE = re.compile(r'File "([^"]+)", line (\d+)(?:, in .*)?')
EXCEPTION_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning)?):\s*(.*)$")


def parse_error_text(text: str) -> dict:
    has_traceback = "Traceback (most recent call last)" in text
    file_match = None
    for match in TRACEBACK_FILE_RE.finditer(text):
        file_match = match

    exception_type = None
    message = None
    for line in reversed([line.strip() for line in text.splitlines() if line.strip()]):
        match = EXCEPTION_RE.match(line)
        if match:
            exception_type = match.group(1)
            message = match.group(2)
            break

    return {
        "has_error": bool(has_traceback or exception_type),
        "error_kind": "micropython_traceback" if has_traceback else ("exception_text" if exception_type else None),
        "file": file_match.group(1) if file_match else None,
        "line": int(file_match.group(2)) if file_match else None,
        "exception_type": exception_type,
        "message": message,
        "recoverable": bool(has_traceback or exception_type),
        "suggested_next_actions": [
            "Open the related source file",
            "Fix the reported exception",
            "Upload or build again",
            "Run again and capture serial output",
        ]
        if (has_traceback or exception_type)
        else [],
    }


def esp_error_parse_text(text: str) -> dict:
    parsed = parse_error_text(text)
    return {"ok": True, **parsed}


def esp_error_parse_log(run_id: str) -> dict:
    logs = esp_logs_get(run_id=run_id, tail=500)
    if logs.get("ok") is False:
        return logs
    text = "\n".join(event.get("message", "") for event in logs.get("events", []))
    parsed = parse_error_text(text)
    return {"ok": True, "run_id": run_id, **parsed}

