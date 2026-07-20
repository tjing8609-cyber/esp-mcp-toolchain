from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import time
from typing import BinaryIO, TextIO

from ..store.jsonl_store import read_jsonl
from ..utils.time_utils import now_utc_iso


DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_SESSION_BYTES = 256 * 1024 * 1024
DEFAULT_PROJECT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_FLUSH_BYTES = 64 * 1024
DEFAULT_FLUSH_SECONDS = 0.25
MAX_RECORD_BYTES = 4096
_CHUNK_NAME_PATTERN = re.compile(r"^chunk-\d{6}\.bin$")


class SerialLogStoreError(RuntimeError):
    pass


class SerialLogQuotaError(SerialLogStoreError):
    pass


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _env_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for candidate in path.rglob("*"):
        if candidate.is_file():
            try:
                total += candidate.stat().st_size
            except OSError:
                continue
    return total


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


class SerialLogStore:
    def __init__(self, log_root: Path, run_id: str, manifest: dict):
        self.serial_root = log_root / "serial"
        self.run_dir = self.serial_root / run_id
        self.manifest_path = self.run_dir / "manifest.json"
        self.records_path = self.run_dir / "records.jsonl"
        self.chunk_limit = _env_positive_int("ESP_MCP_MONITOR_CHUNK_BYTES", DEFAULT_CHUNK_BYTES)
        self.session_limit = _env_positive_int("ESP_MCP_MONITOR_SESSION_BYTES", DEFAULT_SESSION_BYTES)
        self.project_limit = _env_positive_int("ESP_MCP_MONITOR_PROJECT_BYTES", DEFAULT_PROJECT_BYTES)
        self.flush_bytes = _env_positive_int("ESP_MCP_MONITOR_FLUSH_BYTES", DEFAULT_FLUSH_BYTES)
        self.flush_seconds = _env_positive_float("ESP_MCP_MONITOR_FLUSH_SECONDS", DEFAULT_FLUSH_SECONDS)
        self.project_bytes_at_start = _directory_size(self.serial_root)
        if self.project_bytes_at_start >= self.project_limit:
            raise SerialLogQuotaError("Project serial log quota is already exhausted.")

        self.run_dir.mkdir(parents=True, exist_ok=False)
        self._manifest = dict(manifest)
        self._manifest.update(
            {
                "format_version": 1,
                "records_path": str(self.records_path),
                "chunks": [],
                "persisted_bytes": 0,
                "created_at": now_utc_iso(),
            }
        )
        self._records_handle: TextIO = self.records_path.open("a", encoding="utf-8")
        self._chunk_handle: BinaryIO | None = None
        self._chunk_number = 0
        self._chunk_offset = 0
        self._chunk_part_path: Path | None = None
        self._bytes_since_flush = 0
        self._last_flush_at = time.monotonic()
        self._closed = False
        try:
            _atomic_json(self.manifest_path, self._manifest)
        except BaseException:
            self._records_handle.close()
            raise

    @property
    def persisted_bytes(self) -> int:
        return int(self._manifest.get("persisted_bytes", 0))

    def _open_chunk(self) -> None:
        self._chunk_number += 1
        self._chunk_offset = 0
        self._chunk_part_path = self.run_dir / f"chunk-{self._chunk_number:06d}.bin.part"
        self._chunk_handle = self._chunk_part_path.open("xb")

    def _finalize_chunk(self) -> None:
        if self._chunk_handle is None or self._chunk_part_path is None:
            return
        self._chunk_handle.flush()
        os.fsync(self._chunk_handle.fileno())
        self._chunk_handle.close()
        final_path = self._chunk_part_path.with_suffix("")
        self._chunk_part_path.replace(final_path)
        self._manifest["chunks"].append(
            {
                "chunk_id": self._chunk_number,
                "path": str(final_path),
                "byte_length": final_path.stat().st_size,
                "sha256": _sha256(final_path),
            }
        )
        self._chunk_handle = None
        self._chunk_part_path = None
        self._chunk_offset = 0
        _atomic_json(self.manifest_path, self._manifest)

    def _flush_if_needed(self, *, force: bool = False) -> None:
        elapsed = time.monotonic() - self._last_flush_at
        if not force and self._bytes_since_flush < self.flush_bytes and elapsed < self.flush_seconds:
            return
        if self._chunk_handle is not None:
            self._chunk_handle.flush()
        self._records_handle.flush()
        self._bytes_since_flush = 0
        self._last_flush_at = time.monotonic()

    def append(self, seq: int, timestamp_utc: str, raw: bytes) -> dict:
        if self._closed:
            raise SerialLogStoreError("Serial log store is closed.")
        if not raw:
            raise SerialLogStoreError("Empty serial records are not persisted.")
        if len(raw) > MAX_RECORD_BYTES:
            raise SerialLogStoreError(f"Serial records cannot exceed {MAX_RECORD_BYTES} bytes.")
        projected_session = self.persisted_bytes + len(raw)
        if projected_session > self.session_limit:
            raise SerialLogQuotaError("Serial monitor session log quota exceeded.")
        if self.project_bytes_at_start + projected_session > self.project_limit:
            raise SerialLogQuotaError("Project serial log quota exceeded.")
        if self._chunk_handle is None:
            self._open_chunk()
        if self._chunk_offset and self._chunk_offset + len(raw) > self.chunk_limit:
            self._finalize_chunk()
            self._open_chunk()

        assert self._chunk_handle is not None
        offset = self._chunk_offset
        self._chunk_handle.write(raw)
        self._chunk_offset += len(raw)
        self._manifest["persisted_bytes"] = projected_session
        try:
            raw.decode("utf-8", errors="strict")
            decode_error = False
        except UnicodeDecodeError:
            decode_error = True
        record = {
            "seq": seq,
            "timestamp_utc": timestamp_utc,
            "chunk_id": self._chunk_number,
            "chunk_path": f"chunk-{self._chunk_number:06d}.bin",
            "byte_offset": offset,
            "raw_size": len(raw),
            "decode_error": decode_error,
        }
        self._records_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._bytes_since_flush += len(raw)
        self._flush_if_needed()
        return record

    def update_manifest(self, **values: object) -> None:
        self._manifest.update(values)
        _atomic_json(self.manifest_path, self._manifest)

    def close(self, **values: object) -> None:
        if self._closed:
            return
        self._flush_if_needed(force=True)
        self._finalize_chunk()
        self._records_handle.flush()
        os.fsync(self._records_handle.fileno())
        self._records_handle.close()
        self._manifest.update(values)
        _atomic_json(self.manifest_path, self._manifest)
        self._closed = True


def recover_serial_runs(log_root: Path, *, skip_run_ids: set[str] | None = None) -> list[dict]:
    serial_root = log_root / "serial"
    recovered = []
    skipped = skip_run_ids or set()
    if not serial_root.exists():
        return recovered
    for run_dir in serial_root.iterdir():
        if not run_dir.is_dir():
            continue
        if run_dir.name in skipped:
            continue
        parts = sorted(run_dir.glob("chunk-*.bin.part"))
        manifest_path = run_dir / "manifest.json"
        if not parts and not manifest_path.exists():
            continue
        manifest = {}
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        process_owner = manifest.get("process_owner")
        if process_owner:
            from .serial_monitor_lock import process_owner_is_live

            if process_owner_is_live(process_owner):
                continue
        changed = False
        chunks = list(manifest.get("chunks") or [])
        known_chunk_names = {Path(chunk.get("path", "")).name for chunk in chunks}
        unresolved_parts = []
        for part in parts:
            final_path = part.with_suffix("")
            if final_path.exists():
                unresolved_parts.append(str(part))
                continue
            part.replace(final_path)
            changed = True
            if final_path.name not in known_chunk_names:
                try:
                    chunk_id = int(final_path.name.split(".", 1)[0].rsplit("-", 1)[1])
                except (IndexError, ValueError):
                    chunk_id = len(chunks) + 1
                chunks.append(
                    {
                        "chunk_id": chunk_id,
                        "path": str(final_path),
                        "byte_length": final_path.stat().st_size,
                        "sha256": _sha256(final_path),
                        "recovered": True,
                    }
                )
                known_chunk_names.add(final_path.name)
        stale_state = manifest.get("state") in {"STARTING", "RUNNING", "STOPPING"}
        last_error = manifest.get("last_error")
        needs_sqlite_reconciliation = (
            manifest.get("state") == "FAILED"
            and isinstance(last_error, dict)
            and last_error.get("error_kind") == "stale_monitor_recovered"
            and not manifest.get("sqlite_reconciled")
        )
        if stale_state or changed:
            manifest.update(
                {
                    "run_id": run_dir.name,
                    "state": "FAILED",
                    "stopped_at": now_utc_iso(),
                    "last_error": {
                        "error_kind": "stale_monitor_recovered",
                        "message": "A previous monitor process ended without completing cleanup.",
                    },
                    "chunks": sorted(chunks, key=lambda chunk: int(chunk.get("chunk_id", 0))),
                    "sqlite_reconciled": False,
                }
            )
            if unresolved_parts:
                manifest["recovery_unresolved_parts"] = unresolved_parts
            _atomic_json(manifest_path, manifest)
        if stale_state or changed or needs_sqlite_reconciliation:
            manifest["run_id"] = run_dir.name
            recovered.append(manifest)
    return recovered


def load_manifest(run_dir: Path) -> dict | None:
    path = run_dir / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def mark_serial_run_sqlite_reconciled(log_root: Path, run_id: str) -> dict:
    manifest_path = log_root / "serial" / run_id / "manifest.json"
    manifest = load_manifest(manifest_path.parent)
    if manifest is None:
        raise FileNotFoundError(f"No monitor manifest for {run_id}")
    manifest["sqlite_reconciled"] = True
    manifest["sqlite_reconciled_at"] = now_utc_iso()
    _atomic_json(manifest_path, manifest)
    return manifest


def read_persisted_records(
    run_dir: Path,
    *,
    after_seq: int | None,
    max_bytes: int,
    representation: str,
) -> dict:
    manifest = load_manifest(run_dir)
    if manifest is None:
        raise FileNotFoundError(f"No monitor manifest for {run_dir.name}")
    rows = read_jsonl(run_dir / "records.jsonl")
    selected = []
    used = 0
    for row in rows:
        seq = int(row["seq"])
        if after_seq is not None and seq <= after_seq:
            continue
        size = int(row["raw_size"])
        if selected and used + size > max_bytes:
            break
        chunk_name = row.get("chunk_path")
        if not isinstance(chunk_name, str) or not _CHUNK_NAME_PATTERN.fullmatch(chunk_name):
            raise SerialLogStoreError("Serial record contains an invalid chunk path.")
        if size < 0 or size > MAX_RECORD_BYTES:
            raise SerialLogStoreError("Serial record contains an invalid raw_size.")
        offset = int(row["byte_offset"])
        if offset < 0:
            raise SerialLogStoreError("Serial record contains an invalid byte_offset.")
        chunk = run_dir / chunk_name
        if not chunk.exists():
            chunk = chunk.with_suffix(chunk.suffix + ".part")
        if not chunk.exists():
            raise SerialLogStoreError(f"Serial chunk is missing: {chunk_name}")
        with chunk.open("rb") as handle:
            handle.seek(offset)
            raw = handle.read(size)
        if len(raw) != size:
            raise SerialLogStoreError(f"Serial chunk is truncated: {chunk_name}")
        payload = {
            "seq": seq,
            "timestamp_utc": row["timestamp_utc"],
            "raw_size": len(raw),
            "decode_error": bool(row.get("decode_error")),
        }
        if representation in {"text", "both"}:
            payload["text"] = raw.decode("utf-8", errors="replace")
        if representation in {"base64", "both"}:
            payload["raw_base64"] = base64.b64encode(raw).decode("ascii")
        selected.append(payload)
        used += len(raw)
        if used >= max_bytes:
            break
    last_seq = selected[-1]["seq"] if selected else after_seq
    next_seq = (int(rows[-1]["seq"]) + 1) if rows else 1
    return {
        "run_id": manifest.get("run_id", run_dir.name),
        "records": selected,
        "next_after_seq": last_seq,
        "next_seq": next_seq,
        "dropped_before_seq": None,
        "state": str(manifest.get("state", "FAILED")).upper(),
    }
