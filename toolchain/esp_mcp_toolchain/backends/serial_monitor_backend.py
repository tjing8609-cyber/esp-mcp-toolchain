from __future__ import annotations

import base64
import codecs
from collections import deque
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .serial_monitor_lock import PortLease, PortLockError, current_process_owner, identity_key
from .serial_monitor_store import (
    MAX_RECORD_BYTES,
    SerialLogQuotaError,
    SerialLogStore,
    SerialLogStoreError,
    load_manifest,
    mark_serial_run_sqlite_reconciled,
    read_persisted_records,
    recover_serial_runs,
)
from ..database import log_repository
from ..tools.log_tools import LogScope, finish_run, write_event
from ..utils.time_utils import now_utc_iso


DEFAULT_BUFFER_BYTES = 1024 * 1024
TERMINAL_STATES = {"STOPPED", "FAILED", "DISCONNECTED"}
_UTF8_MAX_PENDING_BYTES = 3
_INPUT_SLICE_BYTES = MAX_RECORD_BYTES - _UTF8_MAX_PENDING_BYTES
_SERIAL_READ_MAX_BYTES = 1024
_SERIAL_IDLE_SLEEP_SECONDS = 0.005


class MonitorConflictError(RuntimeError):
    def __init__(self, error_kind: str, message: str):
        super().__init__(message)
        self.error_kind = error_kind


class SerialLogWriteFailure(RuntimeError):
    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


class MonitorState(str, Enum):
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


_TRANSITIONS = {
    MonitorState.STARTING: {MonitorState.RUNNING, MonitorState.STOPPING, MonitorState.FAILED},
    MonitorState.RUNNING: {MonitorState.STOPPING, MonitorState.DISCONNECTED, MonitorState.FAILED},
    MonitorState.STOPPING: {MonitorState.STOPPED, MonitorState.FAILED},
    MonitorState.STOPPED: set(),
    MonitorState.FAILED: set(),
    MonitorState.DISCONNECTED: set(),
}


@dataclass(frozen=True)
class MonitorBinding:
    run_id: str
    project_id: str
    project_dir: Path
    log_root: Path
    session_name: str
    port: str
    port_identity: dict
    baudrate: int


@dataclass(frozen=True)
class SerialRecord:
    seq: int
    timestamp_utc: str
    raw: bytes
    decode_error: bool


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _error_payload(error_kind: str, exc: BaseException) -> dict:
    return {
        "error_kind": error_kind,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "timestamp_utc": now_utc_iso(),
    }


def _looks_disconnected(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(
        token in message
        for token in (
            "device disconnected",
            "device not connected",
            "device not found",
            "file not found",
            "no such device",
            "clearcommerror",
            "input/output error",
            "i/o error",
        )
    )


def _open_serial(serial_module: Any, binding: MonitorBinding) -> Any:
    serial_port = serial_module.Serial()
    try:
        serial_port.port = binding.port
        serial_port.baudrate = binding.baudrate
        serial_port.timeout = 0
        for name, value in (("rtscts", False), ("dsrdtr", False), ("xonxoff", False)):
            if hasattr(serial_port, name):
                setattr(serial_port, name, value)
        for name in ("dtr", "rts"):
            try:
                setattr(serial_port, name, False)
            except (AttributeError, OSError, ValueError):
                pass
        serial_port.open()
        return serial_port
    except BaseException:
        try:
            serial_port.close()
        except Exception:
            pass
        raise


def _read_serial_chunk(serial_port: Any) -> bytes:
    try:
        waiting = int(serial_port.in_waiting)
    except AttributeError:
        return bytes(serial_port.read(_SERIAL_READ_MAX_BYTES))
    if waiting <= 0:
        time.sleep(_SERIAL_IDLE_SLEEP_SECONDS)
        return b""
    return bytes(serial_port.read(min(waiting, _SERIAL_READ_MAX_BYTES)))


class MonitorSession:
    def __init__(self, binding: MonitorBinding, serial_module: Any):
        self.binding = binding
        self.serial_module = serial_module
        self.state = MonitorState.STARTING
        self.started_at: str | None = None
        self.stopped_at: str | None = None
        self.last_data_at: str | None = None
        self.bytes_received = 0
        self.dropped_bytes = 0
        self.unpersisted_bytes = 0
        self.last_error: dict | None = None
        self._next_seq = 1
        self._dropped_before_seq: int | None = None
        self._records: deque[SerialRecord] = deque()
        self._buffered_bytes = 0
        self._buffer_limit = _env_positive_int("ESP_MCP_MONITOR_BUFFER_BYTES", DEFAULT_BUFFER_BYTES)
        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._serial: Any | None = None
        self._lease: PortLease | None = None
        self._thread = threading.Thread(
            target=self._worker,
            name=f"esp-monitor-{binding.run_id}",
            daemon=True,
        )
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._pending_raw = b""
        self._store = SerialLogStore(
            binding.log_root,
            binding.run_id,
            {
                "run_id": binding.run_id,
                "project_id": binding.project_id,
                "session_name": binding.session_name,
                "port": binding.port,
                "port_identity": binding.port_identity,
                "baudrate": binding.baudrate,
                "state": self.state.value,
                "process_owner": current_process_owner(),
            },
        )

    def start(self) -> None:
        self._thread.start()

    def wait_until_ready(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + max(timeout, 0)
        with self._condition:
            while self.state == MonitorState.STARTING and time.monotonic() < deadline:
                self._condition.wait(max(0, deadline - time.monotonic()))

    def _transition(self, state: MonitorState) -> None:
        with self._condition:
            if state == self.state:
                return
            if state not in _TRANSITIONS[self.state]:
                raise RuntimeError(f"Invalid monitor state transition: {self.state.value} -> {state.value}")
            self.state = state
            if state == MonitorState.RUNNING:
                self.started_at = now_utc_iso()
            if state.value in TERMINAL_STATES:
                self.stopped_at = now_utc_iso()
            self._condition.notify_all()

    def _safe_manifest_update(self) -> None:
        try:
            self._store.update_manifest(**self.status())
        except OSError as exc:
            self.last_error = _error_payload("monitor_manifest_write_failed", exc)

    def _log_scope(self) -> LogScope:
        return LogScope.bound(
            project_id=self.binding.project_id,
            log_root=self.binding.log_root,
        )

    def _emit(
        self,
        level: str,
        message: str,
        data: dict | None = None,
        *,
        phase: str = "execute",
    ) -> None:
        try:
            write_event(
                "esp_serial_monitor",
                level,
                message,
                data or {},
                run_id=self.binding.run_id,
                phase=phase,
                source="esp32",
                scope=self._log_scope(),
            )
        except Exception:
            pass

    def _append_record(self, raw: bytes) -> None:
        if not raw:
            return
        timestamp = now_utc_iso()
        seq = self._next_seq
        try:
            stored = self._store.append(seq, timestamp, raw)
        except SerialLogQuotaError:
            self.unpersisted_bytes += len(raw)
            raise
        except (OSError, SerialLogStoreError) as exc:
            self.unpersisted_bytes += len(raw)
            raise SerialLogWriteFailure(exc) from exc
        record = SerialRecord(
            seq=seq,
            timestamp_utc=timestamp,
            raw=raw,
            decode_error=bool(stored.get("decode_error")),
        )
        with self._condition:
            self._next_seq += 1
            self._records.append(record)
            self._buffered_bytes += len(raw)
            while self._records and self._buffered_bytes > self._buffer_limit:
                removed = self._records.popleft()
                self._buffered_bytes -= len(removed.raw)
                self.dropped_bytes += len(removed.raw)
                self._dropped_before_seq = removed.seq
            self.last_data_at = timestamp
            self._condition.notify_all()

    def _consume(self, data: bytes) -> None:
        with self._condition:
            self.bytes_received += len(data)
        for offset in range(0, len(data), _INPUT_SLICE_BYTES):
            current = data[offset : offset + _INPUT_SLICE_BYTES]
            combined = self._pending_raw + current
            self._decoder.decode(current, final=False)
            pending, _flag = self._decoder.getstate()
            consumed_length = len(combined) - len(pending)
            consumed = combined[:consumed_length]
            self._pending_raw = bytes(pending)
            if consumed:
                self._append_record(consumed)

    def _flush_decoder(self) -> None:
        self._decoder.decode(b"", final=True)
        pending = self._pending_raw
        self._pending_raw = b""
        if pending:
            self._append_record(pending)

    def _worker(self) -> None:
        terminal_message = "Serial monitor stopped."
        try:
            self._lease = PortLease.acquire(
                self.binding.port_identity,
                run_id=self.binding.run_id,
                project_id=self.binding.project_id,
            )
            if self._lease.stale_owner:
                self._emit("warning", "Recovered a stale serial monitor lock.", {"owner": self._lease.stale_owner})
            if self._stop_event.is_set():
                self._transition(MonitorState.STOPPED)
                return
            self._serial = _open_serial(self.serial_module, self.binding)
            if self._stop_event.is_set():
                if self.state == MonitorState.STARTING:
                    self._transition(MonitorState.STOPPING)
            else:
                self._transition(MonitorState.RUNNING)
                self._safe_manifest_update()
                self._emit(
                    "info",
                    f"Serial monitor started on {self.binding.port}.",
                    {"port": self.binding.port, "baudrate": self.binding.baudrate},
                )

            while not self._stop_event.is_set():
                data = _read_serial_chunk(self._serial)
                if data:
                    self._consume(data)

            if self.state == MonitorState.RUNNING:
                self._transition(MonitorState.STOPPING)
            self._flush_decoder()
            if self.state == MonitorState.STOPPING:
                self._transition(MonitorState.STOPPED)
        except PortLockError as exc:
            self.last_error = {
                **_error_payload("serial_port_locked", exc),
                "owner": exc.owner,
            }
            if self.state in {MonitorState.STARTING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor failed because the port is reserved."
        except SerialLogQuotaError as exc:
            self.last_error = _error_payload("serial_log_quota_exceeded", exc)
            if self.state in {MonitorState.STARTING, MonitorState.RUNNING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor stopped because its log quota was exceeded."
        except SerialLogWriteFailure as exc:
            error_kind = "serial_log_disk_full" if getattr(exc.cause, "errno", None) == 28 else "serial_log_write_failed"
            self.last_error = _error_payload(error_kind, exc.cause)
            if self.state in {MonitorState.STARTING, MonitorState.RUNNING, MonitorState.STOPPING}:
                self._transition(MonitorState.FAILED)
            terminal_message = "Serial monitor stopped because its log could not be written."
        except Exception as exc:
            if self.state == MonitorState.STOPPING and self._stop_event.is_set():
                terminal_message = "Serial monitor stopped."
            else:
                disconnected = self.state == MonitorState.RUNNING and _looks_disconnected(exc)
                opening = self.state == MonitorState.STARTING
                error_kind = "serial_port_open_failed" if opening else (
                    "serial_disconnected" if disconnected else "serial_monitor_failed"
                )
                self.last_error = _error_payload(error_kind, exc)
                target = MonitorState.DISCONNECTED if disconnected else MonitorState.FAILED
                if target in _TRANSITIONS[self.state]:
                    self._transition(target)
                terminal_message = "Serial monitor disconnected." if disconnected else (
                    "Serial monitor could not open the requested port." if opening else "Serial monitor failed."
                )
        finally:
            serial_port = self._serial
            if serial_port is not None:
                try:
                    serial_port.close()
                except Exception as exc:
                    if self.last_error is None:
                        self.last_error = _error_payload("serial_close_failed", exc)
            if self._lease is not None:
                self._lease.release()
            if self.state == MonitorState.STARTING:
                self._transition(MonitorState.FAILED)
            elif self.state == MonitorState.STOPPING:
                self._transition(MonitorState.STOPPED)
            try:
                final_status = self.status()
                final_status["worker_alive"] = False
                self._store.close(**final_status)
            except Exception as exc:
                if self.last_error is None:
                    self.last_error = _error_payload("monitor_log_close_failed", exc)
            level = "error" if self.state in {MonitorState.FAILED, MonitorState.DISCONNECTED} else "info"
            self._emit(
                level,
                terminal_message,
                {"state": self.state.value, "last_error": self.last_error},
                phase="complete",
            )
            try:
                final_run_status = (
                    "failed"
                    if self.state in {MonitorState.FAILED, MonitorState.DISCONNECTED}
                    else "cancelled"
                )
                finish_run(
                    self.binding.run_id,
                    final_run_status,
                    summary=terminal_message,
                    payload={"state": self.state.value, "last_error": self.last_error},
                    scope=self._log_scope(),
                )
            except Exception:
                pass
            with self._condition:
                self._condition.notify_all()

    def request_stop(self, timeout: float) -> dict:
        with self._condition:
            if self.state.value in TERMINAL_STATES:
                serial_port = self._serial
            else:
                if self.state in {MonitorState.STARTING, MonitorState.RUNNING}:
                    self._transition(MonitorState.STOPPING)
                self._stop_event.set()
                serial_port = self._serial
        if serial_port is not None:
            cancel_read = getattr(serial_port, "cancel_read", None)
            if callable(cancel_read):
                try:
                    cancel_read()
                except Exception:
                    pass
        self._thread.join(max(timeout, 0))
        if self._thread.is_alive() and serial_port is not None:
            try:
                serial_port.close()
            except Exception:
                pass
            self._thread.join(0.25)
        result = self.status()
        result["cleanup_complete"] = not self._thread.is_alive()
        return result

    def status(self) -> dict:
        with self._condition:
            return {
                "run_id": self.binding.run_id,
                "project_id": self.binding.project_id,
                "session_name": self.binding.session_name,
                "port": self.binding.port,
                "port_identity": self.binding.port_identity,
                "baudrate": self.binding.baudrate,
                "state": self.state.value,
                "started_at": self.started_at,
                "stopped_at": self.stopped_at,
                "last_data_at": self.last_data_at,
                "bytes_received": self.bytes_received,
                "persisted_bytes": self._store.persisted_bytes,
                "buffered_bytes": self._buffered_bytes,
                "dropped_bytes": self.dropped_bytes,
                "unpersisted_bytes": self.unpersisted_bytes,
                "last_error": self.last_error,
                "worker_alive": self._thread.is_alive(),
                "log_dir": str(self._store.run_dir),
                "next_seq": self._next_seq,
            }

    def read(self, *, after_seq: int | None, max_bytes: int, wait_ms: int, representation: str) -> dict:
        deadline = time.monotonic() + wait_ms / 1000
        with self._condition:
            while True:
                available = any(after_seq is None or record.seq > after_seq for record in self._records)
                if available or self.state.value in TERMINAL_STATES or wait_ms == 0:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(remaining)

            selected: list[dict] = []
            used = 0
            for record in self._records:
                if after_seq is not None and record.seq <= after_seq:
                    continue
                if selected and used + len(record.raw) > max_bytes:
                    break
                payload: dict[str, Any] = {
                    "seq": record.seq,
                    "timestamp_utc": record.timestamp_utc,
                    "raw_size": len(record.raw),
                    "decode_error": record.decode_error,
                }
                if representation in {"text", "both"}:
                    payload["text"] = record.raw.decode("utf-8", errors="replace")
                if representation in {"base64", "both"}:
                    payload["raw_base64"] = base64.b64encode(record.raw).decode("ascii")
                selected.append(payload)
                used += len(record.raw)
                if used >= max_bytes:
                    break

            last_seq = selected[-1]["seq"] if selected else after_seq
            return {
                "run_id": self.binding.run_id,
                "records": selected,
                "next_after_seq": last_seq,
                "next_seq": self._next_seq,
                "dropped_before_seq": self._dropped_before_seq,
                "state": self.state.value,
            }


class SerialMonitorManager:
    def __init__(self):
        self._sessions: dict[str, MonitorSession] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _reconcile_recovered_runs(binding: MonitorBinding, recovered: list[dict]) -> None:
        scope = LogScope.bound(project_id=binding.project_id, log_root=binding.log_root)
        for manifest in recovered:
            manifest_project_id = str(manifest.get("project_id") or binding.project_id)
            run_id = str(manifest.get("run_id") or "")
            if manifest_project_id != binding.project_id or not run_id:
                continue
            try:
                existing = log_repository.get_run(
                    scope.database_file,
                    project_id=scope.project_id,
                    run_id=run_id,
                )
                if existing is not None and existing["status"] == "failed":
                    mark_serial_run_sqlite_reconciled(binding.log_root, run_id)
                    continue
                if existing is not None and existing["status"] != "running":
                    continue
                last_error = manifest.get("last_error")
                if not isinstance(last_error, dict):
                    last_error = {
                        "error_kind": "stale_monitor_recovered",
                        "message": "A previous monitor process ended without completing cleanup.",
                    }
                stopped_at = str(manifest.get("stopped_at") or "")
                if not stopped_at:
                    continue
                message = str(
                    last_error.get("message")
                    or "A previous monitor process ended without completing cleanup."
                )
                event = write_event(
                    "esp_serial_monitor",
                    "error",
                    message,
                    {"state": "FAILED", "last_error": last_error},
                    run_id=run_id,
                    ts=stopped_at,
                    phase="complete",
                    event_uuid=str(
                        uuid5(
                            NAMESPACE_URL,
                            f"esp-mcp-toolchain:stale-monitor:{scope.project_id}:{run_id}",
                        )
                    ),
                    source="monitor_recovery",
                    task_type="serial_monitor",
                    selected_port=(
                        str(manifest["port"]) if isinstance(manifest.get("port"), str) else None
                    ),
                    scope=scope,
                )
                if event.get("ok") is False:
                    continue
                finished = finish_run(
                    run_id,
                    "failed",
                    summary=message,
                    payload={"state": "FAILED", "last_error": last_error},
                    scope=scope,
                )
                if finished["status"] == "failed":
                    mark_serial_run_sqlite_reconciled(binding.log_root, run_id)
            except Exception:
                continue

    def start(self, binding: MonitorBinding, serial_module: Any) -> MonitorSession:
        port_key = identity_key(binding.port_identity)
        with self._lock:
            active_run_ids = {
                session.binding.run_id
                for session in self._sessions.values()
                if session.status()["state"] not in TERMINAL_STATES
            }
            recovered = recover_serial_runs(binding.log_root, skip_run_ids=active_run_ids)
            self._reconcile_recovered_runs(binding, recovered)
            for session in self._sessions.values():
                status = session.status()
                if status["state"] in TERMINAL_STATES:
                    continue
                if session.binding.project_id == binding.project_id and session.binding.session_name == binding.session_name:
                    raise MonitorConflictError(
                        "monitor_session_conflict",
                        f"Session name {binding.session_name!r} is already active in this project.",
                    )
                if identity_key(session.binding.port_identity) == port_key:
                    raise MonitorConflictError(
                        "serial_port_monitored",
                        f"Serial port {binding.port!r} is already monitored by run {session.binding.run_id}.",
                    )
            session = MonitorSession(binding, serial_module)
            self._sessions[binding.run_id] = session
            session.start()
        session.wait_until_ready()
        return session

    def _session_for_project(self, run_id: str, project_id: str) -> MonitorSession | None:
        with self._lock:
            session = self._sessions.get(run_id)
        if session is None or session.binding.project_id != project_id:
            return None
        return session

    def stop(self, run_id: str, project_id: str, timeout: float) -> dict | None:
        session = self._session_for_project(run_id, project_id)
        return None if session is None else session.request_stop(timeout)

    def status(self, project_id: str, run_id: str | None = None) -> list[dict]:
        with self._lock:
            sessions = list(self._sessions.values())
        return [
            session.status()
            for session in sessions
            if session.binding.project_id == project_id and (run_id is None or session.binding.run_id == run_id)
        ]

    def persisted_status(self, log_root: Path, run_id: str) -> dict | None:
        return load_manifest(log_root / "serial" / run_id)

    def read(
        self,
        *,
        project_id: str,
        log_root: Path,
        run_id: str,
        after_seq: int | None,
        max_bytes: int,
        wait_ms: int,
        representation: str,
    ) -> dict:
        session = self._session_for_project(run_id, project_id)
        if session is not None:
            return session.read(
                after_seq=after_seq,
                max_bytes=max_bytes,
                wait_ms=wait_ms,
                representation=representation,
            )
        return read_persisted_records(
            log_root / "serial" / run_id,
            after_seq=after_seq,
            max_bytes=max_bytes,
            representation=representation,
        )

    def shutdown_all(self, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + max(timeout, 0)
        with self._lock:
            sessions = list(self._sessions.values())
        results = []
        for session in sessions:
            remaining = max(0, deadline - time.monotonic())
            results.append(session.request_stop(remaining))
        return {
            "ok": all(not result.get("worker_alive") for result in results),
            "sessions": results,
        }


SERIAL_MONITOR_MANAGER = SerialMonitorManager()
