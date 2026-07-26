from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from ..backends.serial_monitor_store import SerialLogStoreError, read_persisted_records
from ..errors import execution_error
from ..utils.error_detection import MicroPythonErrorDetector, parse_error_text
from .log_tools import LogScope, esp_logs_get


MaxErrorScanBytes = Annotated[int, Field(ge=4096, le=1_048_576)]


def esp_error_parse_text(text: str) -> dict[str, Any]:
    parsed = parse_error_text(text)
    return {"ok": True, **parsed}


def _bounded_file_text(path: Path, limit: int) -> tuple[str, int, bool]:
    with path.open("rb") as handle:
        payload = handle.read(limit + 1)
    truncated = len(payload) > limit
    bounded = payload[:limit]
    return bounded.decode("utf-8", errors="replace"), len(bounded), truncated


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload_json")
    if not isinstance(payload, dict):
        payload = event.get("data")
    return payload if isinstance(payload, dict) else {}


def esp_error_parse_log(run_id: str, max_bytes: MaxErrorScanBytes = 262_144) -> dict[str, Any]:
    if max_bytes < 4096 or max_bytes > 1_048_576:
        return execution_error(
            "invalid_max_bytes",
            "max_bytes must be between 4096 and 1048576.",
            tool="esp_error_parse_log",
        )

    logs = esp_logs_get(run_id=run_id, tail=10_000)
    if logs.get("ok") is False:
        return logs

    scope = LogScope.active()
    log_root = scope.log_root.resolve()
    chunks: list[str] = []
    scan_sources: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, str]] = []
    structured_reports: list[dict[str, Any]] = []
    scanned_bytes = 0
    scan_truncated = False

    for event in logs.get("events", []):
        if not isinstance(event, dict):
            continue
        payload = _event_payload(event)
        report = payload.get("error_report")
        if not isinstance(report, dict) and payload.get("has_error") is True and payload.get("error_kind"):
            report = payload
        if isinstance(report, dict) and report.get("has_error") is True:
            structured_reports.append(report)

    event_text = "\n".join(
        str(event.get("message") or "")
        for event in logs.get("events", [])
        if isinstance(event, dict)
    )
    if event_text:
        encoded = event_text.encode("utf-8")
        remaining = max_bytes - scanned_bytes
        bounded = encoded[:remaining]
        chunks.append(bounded.decode("utf-8", errors="replace"))
        scanned_bytes += len(bounded)
        scan_truncated = scan_truncated or len(encoded) > len(bounded)
        scan_sources.append({"kind": "sqlite_events", "bytes": len(bounded)})

    raw_candidates: list[Path] = []
    for event in logs.get("events", []):
        if not isinstance(event, dict):
            continue
        raw_value = _event_payload(event).get("raw_path")
        if not isinstance(raw_value, str) or not raw_value:
            continue
        candidate = Path(raw_value).expanduser().resolve()
        if not candidate.is_relative_to(log_root):
            skipped_sources.append({"kind": "raw_path", "reason": "outside_project_log_root"})
            continue
        if candidate.is_file() and candidate not in raw_candidates:
            raw_candidates.append(candidate)

    for candidate in raw_candidates:
        remaining = max_bytes - scanned_bytes
        if remaining <= 0:
            scan_truncated = True
            break
        try:
            text, used, truncated = _bounded_file_text(candidate, remaining)
        except OSError as exc:
            skipped_sources.append(
                {"kind": "raw_path", "reason": f"{type(exc).__name__}: {exc}"}
            )
            continue
        chunks.append(text)
        scanned_bytes += used
        scan_truncated = scan_truncated or truncated
        scan_sources.append(
            {
                "kind": "serial_capture_raw",
                "path": str(candidate),
                "bytes": used,
            }
        )

    monitor_dir = log_root / "serial" / run_id
    if monitor_dir.joinpath("manifest.json").is_file() and scanned_bytes < max_bytes:
        cursor: int | None = None
        monitor_bytes = 0
        try:
            while scanned_bytes < max_bytes:
                remaining = max_bytes - scanned_bytes
                page = read_persisted_records(
                    monitor_dir,
                    after_seq=cursor,
                    max_bytes=min(65_536, remaining),
                    representation="text",
                )
                records = page.get("records", [])
                if not records:
                    break
                for record in records:
                    text = str(record.get("text") or "")
                    encoded = text.encode("utf-8")
                    take = encoded[: max_bytes - scanned_bytes]
                    chunks.append(take.decode("utf-8", errors="replace"))
                    scanned_bytes += len(take)
                    monitor_bytes += len(take)
                    if len(take) < len(encoded):
                        scan_truncated = True
                        break
                next_cursor = page.get("next_after_seq")
                if next_cursor == cursor or scan_truncated:
                    break
                cursor = next_cursor
                if cursor is not None and cursor >= int(page.get("next_seq") or 1) - 1:
                    break
        except (FileNotFoundError, OSError, SerialLogStoreError, KeyError, TypeError, ValueError) as exc:
            skipped_sources.append(
                {"kind": "serial_monitor", "reason": f"{type(exc).__name__}: {exc}"}
            )
        if monitor_bytes:
            scan_sources.append(
                {
                    "kind": "serial_monitor_raw",
                    "path": str(monitor_dir),
                    "bytes": monitor_bytes,
                }
            )
        if scanned_bytes >= max_bytes:
            scan_truncated = True

    parsed = parse_error_text("\n".join(chunks))
    candidates = [parsed, *structured_reports]
    parsed = max(
        candidates,
        key=lambda report: (
            int(bool(report.get("has_error"))),
            int(bool(report.get("exception_type"))),
            int(bool(report.get("file"))),
            int(report.get("line") is not None),
        ),
    )
    if structured_reports:
        scan_sources.append(
            {
                "kind": "structured_error_report",
                "count": len(structured_reports),
                "bytes": 0,
            }
        )
    return {
        "ok": True,
        "run_id": run_id,
        "scan_sources": scan_sources,
        "skipped_sources": skipped_sources,
        "scanned_bytes": scanned_bytes,
        "scan_truncated": scan_truncated,
        **parsed,
    }


__all__ = [
    "MicroPythonErrorDetector",
    "esp_error_parse_log",
    "esp_error_parse_text",
    "parse_error_text",
]

