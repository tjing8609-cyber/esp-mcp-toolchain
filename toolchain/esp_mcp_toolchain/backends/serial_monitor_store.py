from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import BinaryIO, Callable, Iterator, TextIO
from uuid import uuid4

from ..store.jsonl_store import read_jsonl
from ..utils.time_utils import now_utc_iso


DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_SESSION_BYTES = 256 * 1024 * 1024
DEFAULT_PROJECT_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_FLUSH_BYTES = 64 * 1024
DEFAULT_FLUSH_SECONDS = 0.25
MAX_RECORD_BYTES = 4096
SQLITE_ARTIFACT_RECONCILIATION_VERSION = 1
SQLITE_ARTIFACT_MARKER_NAME = "sqlite-artifacts-v1.json"
_CHUNK_NAME_PATTERN = re.compile(r"^chunk-\d{6}\.bin$")
_CHUNK_PART_NAME_PATTERN = re.compile(r"^chunk-\d{6}\.bin\.part$")
_SQLITE_ARTIFACT_MARKER_PATTERN = re.compile(r"^sqlite-artifacts-v\d+\.json$")
_WINDOWS_REPARSE_POINT = 0x400
_LEGACY_SQLITE_ARTIFACT_KEYS = {
    "terminal_marker",
    "sqlite_artifact_projection",
    "sqlite_artifacts_reconciliation_error",
    "sqlite_artifacts_reconciliation_version",
}
_SERIAL_MANIFEST_STATUS_EXCLUSIONS = {
    *_LEGACY_SQLITE_ARTIFACT_KEYS,
    "logging_persisted",
    "logging_persistence_state",
}


class SerialLogStoreError(RuntimeError):
    pass


class SerialLogQuotaError(SerialLogStoreError):
    pass


class SerialLogReconciliationBusy(SerialLogStoreError):
    error_kind = "monitor_artifact_reconciliation_busy"


class SerialRunReconciliationLease:
    def __init__(self, path: Path, lock_id: str, handle: BinaryIO):
        self.path = path
        self.lock_id = lock_id
        self._handle = handle
        self._held = True

    @classmethod
    def acquire(cls, run_dir: Path) -> "SerialRunReconciliationLease":
        from .serial_monitor_lock import current_process_owner

        _require_safe_directory(run_dir, label="Monitor run directory")
        path = run_dir / ".sqlite-artifacts.lock"
        lock_id = f"sqlite_artifacts_{uuid4().hex}"
        metadata = {
            **current_process_owner(),
            "lock_id": lock_id,
            "created_at": now_utc_iso(),
        }
        descriptor: int | None = None
        handle: BinaryIO | None = None
        locked = False
        transferred = False
        try:
            descriptor = _open_reconciliation_lock_file(path)
            handle = os.fdopen(descriptor, "r+b", closefd=True)
            descriptor = None
            status = os.fstat(handle.fileno())
            if not stat.S_ISREG(status.st_mode) or bool(
                getattr(status, "st_file_attributes", 0)
                & _WINDOWS_REPARSE_POINT
            ):
                raise SerialLogStoreError(
                    "Monitor reconciliation lock is not a safe regular file."
                )
            # Windows byte-range locks may extend past EOF, so an empty lock
            # file needs no pre-lock sentinel.  Keeping every write behind the
            # lease prevents a contender from observing the owner's truncate
            # window and failing with PermissionError before it asks for the
            # non-blocking lock.
            _lock_reconciliation_file(handle)
            locked = True
            encoded = (
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
            ).encode("utf-8")
            handle.seek(0)
            handle.truncate(0)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
            lease = cls(path, lock_id, handle)
            transferred = True
            return lease
        except SerialLogReconciliationBusy:
            raise
        except (OSError, SerialLogStoreError) as exc:
            raise SerialLogStoreError(
                f"Monitor artifact reconciliation lease could not be acquired: {exc}"
            ) from exc
        finally:
            if handle is not None and not transferred:
                # A successfully returned lease owns the still-open handle.
                if locked:
                    try:
                        _unlock_reconciliation_file(handle)
                    except OSError:
                        pass
                try:
                    handle.close()
                except OSError:
                    pass
            elif descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

    def release(self) -> None:
        if not self._held:
            return
        self._held = False
        try:
            try:
                _unlock_reconciliation_file(self._handle)
            except OSError:
                pass
        finally:
            try:
                self._handle.close()
            except OSError:
                pass

    @property
    def held(self) -> bool:
        return self._held


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


def _is_reparse_point(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    )


def _require_safe_directory(path: Path, *, label: str) -> None:
    try:
        status = path.lstat()
    except OSError as exc:
        raise SerialLogStoreError(f"{label} is unavailable: {exc}") from exc
    if _is_reparse_point(path):
        raise SerialLogStoreError(f"{label} is a reparse point and is refused.")
    if not stat.S_ISDIR(status.st_mode):
        raise SerialLogStoreError(f"{label} is not a directory.")


def _require_safe_regular_file(path: Path, *, parent: Path, label: str) -> None:
    _require_safe_directory(parent, label=f"{label} parent")
    if path.parent != parent:
        raise SerialLogStoreError(f"{label} escapes its monitor run directory.")
    try:
        status = path.lstat()
    except OSError as exc:
        raise SerialLogStoreError(f"{label} is unavailable: {exc}") from exc
    if _is_reparse_point(path):
        raise SerialLogStoreError(f"{label} is a reparse point and is refused.")
    if not stat.S_ISREG(status.st_mode):
        raise SerialLogStoreError(f"{label} is not a regular file.")


def _stat_identity(status: os.stat_result) -> tuple[int, int, int, int]:
    return (
        int(status.st_dev),
        int(status.st_ino),
        int(status.st_size),
        int(status.st_mtime_ns),
    )


def _open_readonly_no_reparse(path: Path) -> int:
    if os.name != "nt":
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags)

    import ctypes
    import msvcrt

    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9
    invalid_handle_value = ctypes.c_void_p(-1).value

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", ctypes.c_uint32),
            ("ReparseTag", ctypes.c_uint32),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_information.restype = ctypes.c_int

    handle = create_file(
        str(path),
        generic_read,
        share_all,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(path))
    info = FileAttributeTagInfo()
    if not get_information(
        handle,
        file_attribute_tag_info,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        close_handle(handle)
        raise OSError(error, ctypes.FormatError(error), str(path))
    if info.FileAttributes & file_attribute_reparse_point:
        close_handle(handle)
        raise SerialLogStoreError(
            "Monitor file is a reparse point and is refused."
        )
    try:
        return msvcrt.open_osfhandle(
            int(handle),
            os.O_RDONLY | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        close_handle(handle)
        raise


def _open_reconciliation_lock_file(path: Path) -> int:
    if os.name != "nt":
        flags = os.O_RDWR | os.O_CREAT
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(path, flags, 0o600)

    import ctypes
    import msvcrt

    generic_read = 0x80000000
    generic_write = 0x40000000
    # Read/write sharing lets contenders open the same file and observe the
    # byte-range lock. Delete sharing is deliberately excluded: otherwise a
    # second process could unlink and recreate the path while this file object
    # remains locked, producing two independent leases for one run.
    share_read_write = 0x00000001 | 0x00000002
    open_always = 4
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    file_attribute_reparse_point = 0x00000400
    file_attribute_tag_info = 9
    invalid_handle_value = ctypes.c_void_p(-1).value

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", ctypes.c_uint32),
            ("ReparseTag", ctypes.c_uint32),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    get_information = kernel32.GetFileInformationByHandleEx
    get_information.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_uint32,
    ]
    get_information.restype = ctypes.c_int

    handle = create_file(
        str(path),
        generic_read | generic_write,
        share_read_write,
        None,
        open_always,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        raise OSError(error, ctypes.FormatError(error), str(path))
    info = FileAttributeTagInfo()
    if not get_information(
        handle,
        file_attribute_tag_info,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.get_last_error()
        close_handle(handle)
        raise OSError(error, ctypes.FormatError(error), str(path))
    if info.FileAttributes & file_attribute_reparse_point:
        close_handle(handle)
        raise SerialLogStoreError(
            "Monitor reconciliation lock is a reparse point and is refused."
        )
    try:
        return msvcrt.open_osfhandle(
            int(handle),
            os.O_RDWR | getattr(os, "O_BINARY", 0),
        )
    except BaseException:
        close_handle(handle)
        raise


def _lock_reconciliation_file(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as exc:
        if getattr(exc, "errno", None) in {11, 13}:
            raise SerialLogReconciliationBusy(
                "Monitor artifact reconciliation is already active."
            ) from exc
        raise


def _unlock_reconciliation_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _safe_binary_reader(
    path: Path,
    *,
    parent: Path,
    label: str,
) -> Iterator[tuple[BinaryIO, os.stat_result]]:
    _require_safe_directory(parent, label=f"{label} parent")
    if path.parent != parent:
        raise SerialLogStoreError(f"{label} escapes its monitor run directory.")
    parent_before = parent.lstat()
    try:
        descriptor = _open_readonly_no_reparse(path)
    except (OSError, SerialLogStoreError) as exc:
        raise SerialLogStoreError(f"{label} could not be safely opened: {exc}") from exc
    try:
        handle = os.fdopen(descriptor, "rb", closefd=True)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    with handle:
        before = os.fstat(handle.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise SerialLogStoreError(f"{label} is not a regular file.")
        if bool(
            getattr(before, "st_file_attributes", 0)
            & _WINDOWS_REPARSE_POINT
        ):
            raise SerialLogStoreError(
                f"{label} is a reparse point and is refused."
            )
        yield handle, before
        after = os.fstat(handle.fileno())
        if _stat_identity(after) != _stat_identity(before):
            raise SerialLogStoreError(
                f"{label} changed while it was being verified."
            )
    _require_safe_directory(parent, label=f"{label} parent")
    parent_after = parent.lstat()
    if _stat_identity(parent_after) != _stat_identity(parent_before):
        raise SerialLogStoreError(
            f"{label} parent changed while it was being verified."
        )


def _verified_file_digest(
    path: Path,
    *,
    parent: Path,
    label: str,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    with _safe_binary_reader(path, parent=parent, label=label) as (handle, status):
        while block := handle.read(1024 * 1024):
            digest.update(block)
        length = int(status.st_size)
    return length, digest.hexdigest()


def _read_safe_json_object(
    path: Path,
    *,
    parent: Path,
    label: str,
    max_bytes: int = 4 * 1024 * 1024,
) -> dict:
    value, _sha256 = _read_safe_json_object_snapshot(
        path,
        parent=parent,
        label=label,
        max_bytes=max_bytes,
    )
    return value


def _read_safe_json_object_snapshot(
    path: Path,
    *,
    parent: Path,
    label: str,
    max_bytes: int = 4 * 1024 * 1024,
) -> tuple[dict, str]:
    with _safe_binary_reader(path, parent=parent, label=label) as (handle, _status):
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise SerialLogStoreError(f"{label} exceeds the supported size.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SerialLogStoreError(f"{label} is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise SerialLogStoreError(f"{label} must be a JSON object.")
    return value, hashlib.sha256(raw).hexdigest()


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for current_root, directory_names, file_names in os.walk(
        path,
        topdown=True,
        followlinks=False,
    ):
        root = Path(current_root)
        directory_names[:] = [
            name
            for name in directory_names
            if not _is_reparse_point(root / name)
        ]
        for name in file_names:
            candidate = root / name
            if _is_reparse_point(candidate):
                continue
            try:
                status = candidate.lstat()
                if stat.S_ISREG(status.st_mode):
                    total += status.st_size
            except OSError:
                continue
    return total


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _require_safe_directory(path.parent, label="Monitor manifest directory")
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    except OSError as exc:
        raise SerialLogStoreError(
            f"Monitor JSON target is unavailable: {exc}"
        ) from exc
    if existing is not None:
        if _is_reparse_point(path):
            raise SerialLogStoreError(
                "Monitor JSON target is a reparse point and is refused."
            )
        if not stat.S_ISREG(existing.st_mode):
            raise SerialLogStoreError(
                "Monitor JSON target is not a regular file."
            )
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_name).replace(path)
    finally:
        if temporary_name is not None:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass


def _sha256(path: Path) -> str:
    _length, digest = _verified_file_digest(
        path,
        parent=path.parent,
        label="Monitor chunk",
    )
    return digest


class SerialLogStore:
    def __init__(self, log_root: Path, run_id: str, manifest: dict):
        if (
            not run_id
            or Path(run_id).name != run_id
            or run_id in {".", ".."}
        ):
            raise SerialLogStoreError("Monitor run_id is not a safe directory name.")
        self.serial_root = log_root / "serial"
        self.run_dir = self.serial_root / run_id
        self.manifest_path = self.run_dir / "manifest.json"
        self.records_path = self.run_dir / "records.jsonl"
        self.chunk_limit = _env_positive_int("ESP_MCP_MONITOR_CHUNK_BYTES", DEFAULT_CHUNK_BYTES)
        self.session_limit = _env_positive_int("ESP_MCP_MONITOR_SESSION_BYTES", DEFAULT_SESSION_BYTES)
        self.project_limit = _env_positive_int("ESP_MCP_MONITOR_PROJECT_BYTES", DEFAULT_PROJECT_BYTES)
        self.flush_bytes = _env_positive_int("ESP_MCP_MONITOR_FLUSH_BYTES", DEFAULT_FLUSH_BYTES)
        self.flush_seconds = _env_positive_float("ESP_MCP_MONITOR_FLUSH_SECONDS", DEFAULT_FLUSH_SECONDS)
        self.serial_root.mkdir(parents=True, exist_ok=True)
        _require_safe_directory(self.serial_root, label="Serial log root")
        self.project_bytes_at_start = _directory_size(self.serial_root)
        if self.project_bytes_at_start >= self.project_limit:
            raise SerialLogQuotaError("Project serial log quota is already exhausted.")

        self.run_dir.mkdir(exist_ok=False)
        _require_safe_directory(self.run_dir, label="Monitor run directory")
        self._manifest = dict(manifest)
        self._manifest.update(
            {
                "format_version": 2,
                "records_name": self.records_path.name,
                "chunks": [],
                "persisted_bytes": 0,
                "created_at": now_utc_iso(),
            }
        )
        self._records_handle: TextIO | None = self.records_path.open(
            "x",
            encoding="utf-8",
        )
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
            if self._records_handle is not None:
                self._records_handle.close()
            raise

    @property
    def persisted_bytes(self) -> int:
        return int(self._manifest.get("persisted_bytes", 0))

    @property
    def closed(self) -> bool:
        return self._closed

    def _open_chunk(self) -> None:
        self._chunk_number += 1
        self._chunk_offset = 0
        self._chunk_part_path = self.run_dir / f"chunk-{self._chunk_number:06d}.bin.part"
        self._chunk_handle = self._chunk_part_path.open("xb")

    def _finalize_chunk(self) -> None:
        if self._chunk_part_path is None:
            return
        if self._chunk_handle is not None:
            self._chunk_handle.flush()
            os.fsync(self._chunk_handle.fileno())
            self._chunk_handle.close()
            self._chunk_handle = None
        final_path = self._chunk_part_path.with_suffix("")
        try:
            self._chunk_part_path.lstat()
            part_exists = True
        except FileNotFoundError:
            part_exists = False
        if part_exists:
            _require_safe_regular_file(
                self._chunk_part_path,
                parent=self.run_dir,
                label="Monitor chunk part",
            )
            try:
                final_path.lstat()
                final_exists = True
            except FileNotFoundError:
                final_exists = False
            if final_exists:
                raise SerialLogStoreError(
                    "Monitor chunk part conflicts with an existing final chunk."
                )
            self._chunk_part_path.replace(final_path)
        else:
            try:
                final_path.lstat()
            except FileNotFoundError as exc:
                raise SerialLogStoreError(
                    "Monitor chunk disappeared during finalization."
                ) from exc
        byte_length, sha256 = _verified_file_digest(
            final_path,
            parent=self.run_dir,
            label="Monitor chunk",
        )
        entry = {
            "chunk_id": self._chunk_number,
            "name": final_path.name,
            "byte_length": byte_length,
            "sha256": sha256,
        }
        existing = [
            chunk
            for chunk in self._manifest["chunks"]
            if (
                chunk.get("chunk_id") == self._chunk_number
                or chunk.get("name") == final_path.name
            )
        ]
        if existing and existing != [entry]:
            raise SerialLogStoreError(
                "Monitor finalized chunk conflicts with its manifest metadata."
            )
        if not existing:
            self._manifest["chunks"].append(entry)
            self._manifest["chunks"].sort(
                key=lambda chunk: int(chunk["chunk_id"])
            )
        _atomic_json(self.manifest_path, self._manifest)
        self._chunk_part_path = None
        self._chunk_offset = 0

    def _flush_if_needed(self, *, force: bool = False) -> None:
        elapsed = time.monotonic() - self._last_flush_at
        if not force and self._bytes_since_flush < self.flush_bytes and elapsed < self.flush_seconds:
            return
        if self._chunk_handle is not None:
            self._chunk_handle.flush()
        if self._records_handle is not None:
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
        if self._records_handle is None:
            raise SerialLogStoreError("Serial record index is already closed.")
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
        self._manifest.update(
            {
                key: value
                for key, value in values.items()
                if key not in _SERIAL_MANIFEST_STATUS_EXCLUSIONS
            }
        )
        _atomic_json(self.manifest_path, self._manifest)

    def close(self, **values: object) -> None:
        if self._closed:
            return
        self._flush_if_needed(force=True)
        self._finalize_chunk()
        if self._records_handle is not None:
            self._records_handle.flush()
            os.fsync(self._records_handle.fileno())
            self._records_handle.close()
            self._records_handle = None
        values["log_store_closed"] = True
        self._manifest.update(
            {
                key: value
                for key, value in values.items()
                if key not in _SERIAL_MANIFEST_STATUS_EXCLUSIONS
            }
        )
        _atomic_json(self.manifest_path, self._manifest)
        self._closed = True


def _read_manifest_strict(run_dir: Path) -> dict:
    _require_safe_directory(run_dir, label="Monitor run directory")
    manifest_path = run_dir / "manifest.json"
    return _read_safe_json_object(
        manifest_path,
        parent=run_dir,
        label="Monitor manifest",
    )


def read_manifest_snapshot(run_dir: Path) -> tuple[dict, str]:
    """Read one immutable manifest snapshot and its digest from one safe fd."""

    _require_safe_directory(run_dir, label="Monitor run directory")
    manifest_path = run_dir / "manifest.json"
    return _read_safe_json_object_snapshot(
        manifest_path,
        parent=run_dir,
        label="Monitor manifest",
    )


def safe_directory_identity(
    path: Path,
    *,
    label: str,
    include_metadata: bool,
) -> tuple[int, ...]:
    """Return a no-follow directory identity suitable for before/after checks."""

    try:
        status = path.lstat()
    except OSError as exc:
        raise SerialLogStoreError(f"{label} is unavailable: {exc}") from exc
    if path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
    ):
        raise SerialLogStoreError(f"{label} is a reparse point and is refused.")
    if not stat.S_ISDIR(status.st_mode):
        raise SerialLogStoreError(f"{label} is not a directory.")
    identity: tuple[int, ...] = (
        int(status.st_dev),
        int(status.st_ino),
        int(stat.S_IFMT(status.st_mode)),
    )
    if include_metadata:
        identity += (
            int(status.st_size),
            int(status.st_mtime_ns),
        )
    return identity


def require_unowned_historical_monitor_run(
    run_dir: Path,
    manifest: dict,
) -> None:
    """Fail closed when B3 already owns a monitor run's artifact state."""

    _require_safe_directory(run_dir, label="Monitor run directory")
    if any(key in manifest for key in _LEGACY_SQLITE_ARTIFACT_KEYS):
        raise SerialLogStoreError(
            "Historical monitor run is already owned by legacy B3 artifact state."
        )
    if any(
        _SQLITE_ARTIFACT_MARKER_PATTERN.fullmatch(candidate.name)
        for candidate in run_dir.iterdir()
    ):
        raise SerialLogStoreError(
            "Historical monitor run is already owned by a B3 artifact marker."
        )


def _pending_projection(event_uuid: str | None = None) -> dict:
    return {
        "state": "pending",
        "event_uuid": event_uuid,
        "completed_at": None,
        "error": None,
    }


def _artifact_marker_path(run_dir: Path) -> Path:
    return run_dir / SQLITE_ARTIFACT_MARKER_NAME


def _new_artifact_marker_document(
    manifest: dict,
    *,
    terminal_marker: dict | None = None,
) -> dict:
    event_uuid = (
        str(terminal_marker.get("event_uuid"))
        if isinstance(terminal_marker, dict) and terminal_marker.get("event_uuid")
        else None
    )
    return {
        "format": "esp-mcp-toolchain.serial-sqlite-artifacts",
        "version": SQLITE_ARTIFACT_RECONCILIATION_VERSION,
        "project_id": manifest.get("project_id"),
        "run_id": manifest.get("run_id"),
        "terminal_marker": (
            json.loads(json.dumps(terminal_marker))
            if isinstance(terminal_marker, dict)
            else None
        ),
        "first_runtime_error": None,
        "projection": (
            _pending_projection(event_uuid)
            if event_uuid
            else {
                "state": "not_terminal",
                "event_uuid": None,
                "completed_at": None,
                "error": None,
            }
        ),
        "audit_mirror": (
            _pending_projection(event_uuid)
            if event_uuid
            else {
                "state": "not_terminal",
                "event_uuid": None,
                "completed_at": None,
                "error": None,
            }
        ),
    }


def _validate_artifact_marker_document(document: dict, run_dir: Path) -> dict:
    if not isinstance(document, dict):
        raise SerialLogStoreError("Monitor artifact marker must be a JSON object.")
    if document.get("version") != SQLITE_ARTIFACT_RECONCILIATION_VERSION:
        raise SerialLogStoreError("Monitor artifact marker version is unsupported.")
    if document.get("format") != "esp-mcp-toolchain.serial-sqlite-artifacts":
        raise SerialLogStoreError("Monitor artifact marker format is unsupported.")
    if document.get("run_id") != run_dir.name:
        raise SerialLogStoreError("Monitor artifact marker run identity conflicts.")
    project_id = document.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise SerialLogStoreError("Monitor artifact marker project identity is invalid.")
    terminal_marker = document.get("terminal_marker")
    if terminal_marker is not None and not isinstance(terminal_marker, dict):
        raise SerialLogStoreError("Monitor terminal marker is invalid.")
    projection = document.get("projection")
    if not isinstance(projection, dict):
        raise SerialLogStoreError("Monitor artifact projection is invalid.")
    if projection.get("state") not in {
        "not_terminal",
        "pending",
        "committed",
        "failed",
    }:
        raise SerialLogStoreError("Monitor artifact projection state is invalid.")
    audit_mirror = document.get("audit_mirror")
    if not isinstance(audit_mirror, dict):
        raise SerialLogStoreError("Monitor audit mirror projection is invalid.")
    if audit_mirror.get("state") not in {
        "not_terminal",
        "pending",
        "committed",
        "failed",
    }:
        raise SerialLogStoreError(
            "Monitor audit mirror projection state is invalid."
        )
    return document


def load_serial_run_artifact_marker(run_dir: Path) -> dict | None:
    _require_safe_directory(run_dir, label="Monitor run directory")
    marker_path = _artifact_marker_path(run_dir)
    unknown_markers = sorted(
        candidate.name
        for candidate in run_dir.iterdir()
        if (
            _SQLITE_ARTIFACT_MARKER_PATTERN.fullmatch(candidate.name)
            and candidate.name != SQLITE_ARTIFACT_MARKER_NAME
        )
    )
    if unknown_markers:
        raise SerialLogStoreError(
            "Monitor artifact marker version is unsupported."
        )
    try:
        marker_path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SerialLogStoreError(
            f"Monitor artifact marker is unavailable: {exc}"
        ) from exc
    document = _read_safe_json_object(
        marker_path,
        parent=run_dir,
        label="Monitor artifact marker",
    )
    return _validate_artifact_marker_document(document, run_dir)


def _strip_legacy_artifact_fields(manifest: dict) -> bool:
    changed = False
    for key in _LEGACY_SQLITE_ARTIFACT_KEYS:
        if key in manifest:
            manifest.pop(key, None)
            changed = True
    return changed


def _validate_known_legacy_artifact_fields(manifest: dict) -> None:
    version = manifest.get("sqlite_artifacts_reconciliation_version")
    if version is not None and version not in {
        0,
        SQLITE_ARTIFACT_RECONCILIATION_VERSION,
    }:
        raise SerialLogStoreError(
            "Legacy monitor artifact marker version is unsupported."
        )
    projection = manifest.get("sqlite_artifact_projection")
    if projection is not None and not isinstance(projection, dict):
        raise SerialLogStoreError(
            "Legacy monitor artifact projection is invalid."
        )
    if isinstance(projection, dict):
        projection_version = projection.get("version")
        if projection_version not in {
            None,
            SQLITE_ARTIFACT_RECONCILIATION_VERSION,
        }:
            raise SerialLogStoreError(
                "Legacy monitor artifact projection version is unsupported."
            )
    terminal_marker = manifest.get("terminal_marker")
    if terminal_marker is not None and not isinstance(terminal_marker, dict):
        raise SerialLogStoreError("Legacy monitor terminal marker is invalid.")


def _chunk_name_from_entry(chunk: dict) -> str:
    chunk_id = chunk.get("chunk_id")
    if isinstance(chunk_id, bool) or not isinstance(chunk_id, int):
        raise SerialLogStoreError("Monitor chunk_id must be an integer.")
    if chunk_id < 1 or chunk_id > 999_999:
        raise SerialLogStoreError("Monitor chunk_id is outside the supported range.")
    expected = f"chunk-{chunk_id:06d}.bin"
    supplied_name = chunk.get("name")
    if supplied_name is not None and supplied_name != expected:
        raise SerialLogStoreError("Monitor chunk name does not match its chunk_id.")
    return expected


def _recovered_chunk_entry(
    *,
    manifest: dict,
    final_path: Path,
    chunk_id: int,
) -> dict:
    byte_length, sha256 = _verified_file_digest(
        final_path,
        parent=final_path.parent,
        label="Monitor recovered chunk",
    )
    entry = {
        "chunk_id": chunk_id,
        "byte_length": byte_length,
        "sha256": sha256,
        "recovered": True,
    }
    if int(manifest.get("format_version") or 1) >= 2:
        entry["name"] = final_path.name
    else:
        entry["path"] = str(final_path)
    return entry


def recover_serial_runs(
    log_root: Path,
    *,
    skip_run_ids: set[str] | None = None,
    include_run_ids: set[str] | None = None,
    project_id: str | None = None,
    reconciliation_consumer: (
        Callable[[dict, SerialRunReconciliationLease], dict] | None
    ) = None,
) -> list[dict]:
    serial_root = log_root / "serial"
    recovered: list[dict] = []
    skipped = skip_run_ids or set()
    included = include_run_ids
    if not serial_root.exists():
        return recovered
    try:
        _require_safe_directory(serial_root, label="Serial log root")
    except SerialLogStoreError:
        return recovered

    for run_dir in sorted(serial_root.iterdir(), key=lambda path: path.name):
        manifest: dict | None = None
        lease: SerialRunReconciliationLease | None = None
        try:
            if run_dir.name in skipped or (
                included is not None and run_dir.name not in included
            ):
                continue
            _require_safe_directory(run_dir, label="Monitor run directory")
            lease = SerialRunReconciliationLease.acquire(run_dir)
            manifest = _read_manifest_strict(run_dir)
            manifest_run_id = manifest.get("run_id")
            if manifest_run_id not in {None, run_dir.name}:
                continue
            if project_id is not None and manifest.get("project_id") != project_id:
                continue
            process_owner = manifest.get("process_owner")
            if process_owner:
                from .serial_monitor_lock import process_owner_is_live

                if process_owner_is_live(process_owner):
                    continue

            entries = list(run_dir.iterdir())
            parts = sorted(
                (
                    path
                    for path in entries
                    if _CHUNK_PART_NAME_PATTERN.fullmatch(path.name)
                ),
                key=lambda path: path.name,
            )
            finals = sorted(
                (
                    path
                    for path in entries
                    if _CHUNK_NAME_PATTERN.fullmatch(path.name)
                ),
                key=lambda path: path.name,
            )
            for candidate in [*parts, *finals]:
                _require_safe_regular_file(
                    candidate,
                    parent=run_dir,
                    label="Monitor chunk",
                )

            chunks_value = manifest.get("chunks")
            if chunks_value is None:
                chunks: list[dict] = []
            elif isinstance(chunks_value, list) and all(
                isinstance(chunk, dict) for chunk in chunks_value
            ):
                chunks = list(chunks_value)
            else:
                raise SerialLogStoreError(
                    "Monitor manifest chunks must be a list of objects."
                )
            known_chunk_names: set[str] = set()
            for chunk in chunks:
                chunk_name = _chunk_name_from_entry(chunk)
                if chunk_name in known_chunk_names:
                    raise SerialLogStoreError(
                        "Monitor manifest has duplicate chunk metadata."
                    )
                known_chunk_names.add(chunk_name)

            changed = False
            unresolved_parts: list[str] = []
            stale_state = manifest.get("state") in {
                "STARTING",
                "RUNNING",
                "STOPPING",
            }
            for part in parts:
                if not stale_state:
                    unresolved_parts.append(str(part))
                    continue
                final_path = part.with_suffix("")
                if final_path.exists():
                    unresolved_parts.append(str(part))
                    continue
                part.replace(final_path)
                changed = True
                if final_path.name not in known_chunk_names:
                    chunk_id = int(
                        final_path.name.removeprefix("chunk-").removesuffix(".bin")
                    )
                    chunks.append(
                        _recovered_chunk_entry(
                            manifest=manifest,
                            final_path=final_path,
                            chunk_id=chunk_id,
                        )
                    )
                    known_chunk_names.add(final_path.name)
                    finals.append(final_path)

            for final_path in finals:
                if final_path.name in known_chunk_names:
                    continue
                if not stale_state:
                    continue
                chunk_id = int(
                    final_path.name.removeprefix("chunk-").removesuffix(".bin")
                )
                chunks.append(
                    _recovered_chunk_entry(
                        manifest=manifest,
                        final_path=final_path,
                        chunk_id=chunk_id,
                    )
                )
                known_chunk_names.add(final_path.name)
                changed = True

            if changed:
                manifest["persisted_bytes"] = sum(
                    int(chunk.get("byte_length") or 0) for chunk in chunks
                )
            terminal_state = manifest.get("state") in {
                "STOPPED",
                "FAILED",
                "DISCONNECTED",
            }
            last_error = manifest.get("last_error")
            needs_old_sqlite_reconciliation = (
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
                        "stopped_at": (
                            str(manifest.get("stopped_at"))
                            if manifest.get("stopped_at")
                            else now_utc_iso()
                        ),
                        "last_error": {
                            "error_kind": "stale_monitor_recovered",
                            "message": (
                                "A previous monitor process ended without "
                                "completing cleanup."
                            ),
                        },
                        "chunks": sorted(
                            chunks,
                            key=lambda chunk: (
                                int(chunk.get("chunk_id"))
                                if isinstance(chunk.get("chunk_id"), int)
                                else 1_000_000
                            ),
                        ),
                        "sqlite_reconciled": False,
                    }
                )
                if unresolved_parts:
                    manifest["recovery_unresolved_parts"] = unresolved_parts
                _atomic_json(run_dir / "manifest.json", manifest)
                terminal_state = True

            artifact_marker = load_serial_run_artifact_marker(run_dir)
            if (
                artifact_marker is not None
                and artifact_marker.get("project_id") != manifest.get("project_id")
            ):
                raise SerialLogStoreError(
                    "Monitor artifact marker project identity conflicts."
                )
            needs_artifact_reconciliation = terminal_state
            if (
                stale_state
                or changed
                or needs_old_sqlite_reconciliation
                or needs_artifact_reconciliation
            ):
                manifest["run_id"] = run_dir.name
                if reconciliation_consumer is None:
                    recovered.append(dict(manifest))
                else:
                    recovered.append(
                        reconciliation_consumer(dict(manifest), lease)
                    )
        except (OSError, TypeError, ValueError, SerialLogStoreError) as exc:
            error_manifest = dict(manifest) if isinstance(manifest, dict) else {}
            if project_id is not None:
                error_manifest.setdefault("project_id", project_id)
            error_manifest.setdefault("run_id", run_dir.name)
            error_manifest["_sqlite_artifact_recovery_error"] = (
                f"{type(exc).__name__}: {exc}"
            )
            if reconciliation_consumer is None:
                recovered.append(error_manifest)
            else:
                recovered.append(
                    {
                        "ok": False,
                        "error_kind": "monitor_artifact_recovery_failed",
                        "message": error_manifest[
                            "_sqlite_artifact_recovery_error"
                        ],
                        "manifest": error_manifest,
                        "artifact_marker": None,
                    }
                )
        finally:
            if lease is not None:
                lease.release()
    return recovered


def load_manifest(run_dir: Path) -> dict | None:
    try:
        return _read_manifest_strict(run_dir)
    except (FileNotFoundError, SerialLogStoreError):
        return None


def freeze_serial_run_first_runtime_error(
    log_root: Path,
    run_id: str,
    *,
    report: dict,
    detected_at: str,
) -> dict:
    if not isinstance(report, dict):
        raise SerialLogStoreError("Monitor runtime error report is invalid.")
    if not isinstance(detected_at, str) or not detected_at:
        raise SerialLogStoreError("Monitor runtime error timestamp is invalid.")
    run_dir = log_root / "serial" / run_id
    manifest = _read_manifest_strict(run_dir)
    if manifest.get("run_id") != run_id:
        raise SerialLogStoreError("Monitor manifest run identity conflicts.")
    _validate_known_legacy_artifact_fields(manifest)
    document = load_serial_run_artifact_marker(run_dir)
    if document is None:
        document = _new_artifact_marker_document(manifest)
    if document.get("project_id") != manifest.get("project_id"):
        raise SerialLogStoreError(
            "Monitor artifact marker project identity conflicts."
        )
    snapshot = {
        "detected_at": detected_at,
        "report": json.loads(json.dumps(report)),
    }
    existing = document.get("first_runtime_error")
    if existing is None:
        if document.get("terminal_marker") is not None:
            raise SerialLogStoreError(
                "Monitor terminal marker was frozen before its runtime error."
            )
        document["first_runtime_error"] = snapshot
        _atomic_json(_artifact_marker_path(run_dir), document)
        persisted = load_serial_run_artifact_marker(run_dir)
        if persisted != document:
            raise SerialLogStoreError(
                "Monitor runtime error marker could not be verified after writing."
            )
        return persisted
    if not isinstance(existing, dict):
        raise SerialLogStoreError("Monitor runtime error marker is invalid.")
    return document


def freeze_serial_run_terminal_marker(
    log_root: Path,
    run_id: str,
    marker: dict,
) -> dict:
    run_dir = log_root / "serial" / run_id
    manifest_path = run_dir / "manifest.json"
    manifest = _read_manifest_strict(run_dir)
    if manifest.get("run_id") != run_id:
        raise SerialLogStoreError("Monitor manifest run identity conflicts.")
    project_id = manifest.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise SerialLogStoreError("Monitor manifest project identity is invalid.")
    _validate_known_legacy_artifact_fields(manifest)
    frozen_marker = json.loads(json.dumps(marker))
    event_uuid = str(frozen_marker.get("event_uuid") or "")
    if not event_uuid:
        raise SerialLogStoreError("Monitor terminal marker has no event_uuid.")
    legacy_marker = manifest.get("terminal_marker")
    if legacy_marker is not None and legacy_marker != frozen_marker:
        raise SerialLogStoreError(
            "Legacy monitor terminal marker conflicts with canonical terminal facts."
        )
    document = load_serial_run_artifact_marker(run_dir)
    if document is None:
        document = _new_artifact_marker_document(manifest)
    if document.get("project_id") != project_id:
        raise SerialLogStoreError(
            "Monitor artifact marker project identity conflicts."
        )
    existing = document.get("terminal_marker")
    if existing is None:
        document["terminal_marker"] = frozen_marker
    elif existing != frozen_marker:
        raise SerialLogStoreError(
            "Monitor terminal marker conflicts with canonical terminal facts."
        )
    projection = document["projection"]
    projected_uuid = projection.get("event_uuid")
    if projected_uuid not in {None, event_uuid}:
        raise SerialLogStoreError(
            "Monitor artifact projection identifies a different terminal event."
        )
    projection["event_uuid"] = event_uuid
    if projection.get("state") != "committed":
        projection.update(
            {
                "state": "pending",
                "completed_at": None,
                "error": None,
            }
        )
    audit_mirror = document["audit_mirror"]
    mirrored_uuid = audit_mirror.get("event_uuid")
    if mirrored_uuid not in {None, event_uuid}:
        raise SerialLogStoreError(
            "Monitor audit mirror identifies a different terminal event."
        )
    audit_mirror["event_uuid"] = event_uuid
    if audit_mirror.get("state") != "committed":
        audit_mirror.update(
            {
                "state": "pending",
                "completed_at": None,
                "error": None,
            }
        )
    _atomic_json(_artifact_marker_path(run_dir), document)
    persisted_document = load_serial_run_artifact_marker(run_dir)
    if persisted_document != document:
        raise SerialLogStoreError(
            "Monitor artifact marker could not be verified after writing."
        )
    return persisted_document


def record_serial_run_artifact_reconciliation_error(
    log_root: Path,
    run_id: str,
    error: str,
) -> dict:
    run_dir = log_root / "serial" / run_id
    manifest = _read_manifest_strict(run_dir)
    document = load_serial_run_artifact_marker(run_dir)
    if document is None:
        document = _new_artifact_marker_document(manifest)
    projection = document["projection"]
    if projection.get("state") == "committed":
        document["audit_mirror"].update(
            {
                "state": "failed",
                "completed_at": None,
                "error": str(error),
            }
        )
    else:
        projection.update(
            {
                "state": "failed",
                "completed_at": None,
                "error": str(error),
            }
        )
    _atomic_json(_artifact_marker_path(run_dir), document)
    return document


def mark_serial_run_artifacts_reconciled(
    log_root: Path,
    run_id: str,
    *,
    event_uuid: str,
    expected_terminal_marker: dict,
    mark_sqlite_reconciled: bool = False,
) -> dict:
    run_dir = log_root / "serial" / run_id
    manifest_path = run_dir / "manifest.json"
    manifest = _read_manifest_strict(run_dir)
    document = load_serial_run_artifact_marker(run_dir)
    if document is None:
        raise SerialLogStoreError("Monitor artifact marker is unavailable.")
    marker = document.get("terminal_marker")
    if (
        not isinstance(marker, dict)
        or marker.get("event_uuid") != event_uuid
        or marker != expected_terminal_marker
    ):
        raise SerialLogStoreError(
            "Monitor terminal marker changed before artifact commit."
        )
    completed_at = now_utc_iso()
    document["projection"] = {
        "state": "committed",
        "event_uuid": event_uuid,
        "completed_at": completed_at,
        "error": None,
    }
    _atomic_json(_artifact_marker_path(run_dir), document)
    persisted_document = load_serial_run_artifact_marker(run_dir)
    if persisted_document != document:
        raise SerialLogStoreError(
            "Monitor artifact marker could not be verified after commit."
        )
    if mark_sqlite_reconciled:
        manifest["sqlite_reconciled"] = True
        manifest["sqlite_reconciled_at"] = completed_at
    if _strip_legacy_artifact_fields(manifest) or mark_sqlite_reconciled:
        _atomic_json(manifest_path, manifest)
    return persisted_document


def mark_serial_run_audit_mirror(
    log_root: Path,
    run_id: str,
    *,
    event_uuid: str,
    succeeded: bool,
    error: str | None,
) -> dict:
    run_dir = log_root / "serial" / run_id
    document = load_serial_run_artifact_marker(run_dir)
    if document is None:
        raise SerialLogStoreError("Monitor artifact marker is unavailable.")
    marker = document.get("terminal_marker")
    if not isinstance(marker, dict) or marker.get("event_uuid") != event_uuid:
        raise SerialLogStoreError(
            "Monitor terminal marker changed before audit mirror commit."
        )
    if document["projection"].get("state") != "committed":
        raise SerialLogStoreError(
            "Monitor SQLite projection is not committed."
        )
    document["audit_mirror"] = {
        "state": "committed" if succeeded else "failed",
        "event_uuid": event_uuid,
        "completed_at": now_utc_iso() if succeeded else None,
        "error": None if succeeded else str(error or "Audit mirror failed."),
    }
    _atomic_json(_artifact_marker_path(run_dir), document)
    persisted = load_serial_run_artifact_marker(run_dir)
    if persisted != document:
        raise SerialLogStoreError(
            "Monitor audit mirror marker could not be verified after writing."
        )
    return persisted


def mark_serial_run_sqlite_reconciled(log_root: Path, run_id: str) -> dict:
    manifest_path = log_root / "serial" / run_id / "manifest.json"
    manifest = _read_manifest_strict(manifest_path.parent)
    manifest["sqlite_reconciled"] = True
    manifest["sqlite_reconciled_at"] = now_utc_iso()
    _atomic_json(manifest_path, manifest)
    return manifest


def verified_finalized_chunks(log_root: Path, manifest: dict) -> list[dict]:
    run_id = manifest.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id
        or Path(run_id).name != run_id
        or run_id in {".", ".."}
    ):
        raise SerialLogStoreError("Monitor manifest has an invalid run_id.")
    run_dir = log_root / "serial" / run_id
    _require_safe_directory(run_dir, label="Monitor run directory")
    entries = list(run_dir.iterdir())
    parts = [
        path
        for path in entries
        if _CHUNK_PART_NAME_PATTERN.fullmatch(path.name)
    ]
    if parts:
        raise SerialLogStoreError(
            "Monitor run still contains an unfinalized chunk part."
        )
    disk_chunks = {
        path.name: path
        for path in entries
        if _CHUNK_NAME_PATTERN.fullmatch(path.name)
    }
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        raise SerialLogStoreError("Monitor manifest chunks must be a list.")
    format_version = int(manifest.get("format_version") or 1)
    verified: list[dict] = []
    seen_ids: set[int] = set()
    declared_chunks: list[tuple[dict, int, str, Path]] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise SerialLogStoreError("Monitor chunk metadata must be an object.")
        chunk_name = _chunk_name_from_entry(chunk)
        chunk_id = int(chunk["chunk_id"])
        if chunk_id in seen_ids:
            raise SerialLogStoreError("Monitor manifest has a duplicate chunk_id.")
        seen_ids.add(chunk_id)
        chunk_path = run_dir / chunk_name
        if format_version < 2:
            supplied_path = chunk.get("path")
            if not isinstance(supplied_path, str) or not supplied_path:
                raise SerialLogStoreError(
                    "Legacy monitor chunk metadata has no path."
                )
            supplied = Path(supplied_path)
            if supplied.is_absolute():
                supplied_lexical = os.path.normcase(
                    os.path.normpath(str(supplied))
                )
                expected_lexical = os.path.normcase(
                    os.path.normpath(str(chunk_path))
                )
            elif supplied_path == chunk_name:
                supplied_lexical = chunk_name
                expected_lexical = chunk_name
            else:
                raise SerialLogStoreError(
                    "Monitor chunk path escapes or conflicts with its run."
                )
            if supplied_lexical != expected_lexical:
                raise SerialLogStoreError(
                    "Monitor chunk path escapes or conflicts with its run."
                )
        elif chunk.get("path") is not None:
            raise SerialLogStoreError(
                "Format v2 monitor chunks must not contain an absolute path."
            )
        declared_chunks.append((chunk, chunk_id, chunk_name, chunk_path))

    if set(disk_chunks) != {
        chunk_name for _chunk, _chunk_id, chunk_name, _path in declared_chunks
    }:
        raise SerialLogStoreError(
            "Monitor chunk files do not exactly match the manifest."
        )

    for chunk, chunk_id, chunk_name, chunk_path in declared_chunks:
        expected_length = chunk.get("byte_length")
        if isinstance(expected_length, bool) or not isinstance(expected_length, int):
            raise SerialLogStoreError("Monitor chunk byte_length must be an integer.")
        actual_length, actual_sha = _verified_file_digest(
            chunk_path,
            parent=run_dir,
            label="Monitor chunk",
        )
        if expected_length != actual_length:
            raise SerialLogStoreError("Monitor chunk byte_length does not match.")
        expected_sha = chunk.get("sha256")
        if (
            not isinstance(expected_sha, str)
            or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha)
        ):
            raise SerialLogStoreError("Monitor chunk sha256 is invalid.")
        if expected_sha.lower() != actual_sha:
            raise SerialLogStoreError("Monitor chunk sha256 does not match.")
        verified.append(
            {
                "chunk_id": chunk_id,
                "path": f"serial/{run_id}/{chunk_name}",
                "byte_length": actual_length,
                "sha256": actual_sha,
            }
        )
    return sorted(verified, key=lambda chunk: chunk["chunk_id"])


def read_persisted_records(
    run_dir: Path,
    *,
    after_seq: int | None,
    max_bytes: int,
    representation: str,
) -> dict:
    manifest = _read_manifest_strict(run_dir)
    records_path = run_dir / "records.jsonl"
    _require_safe_regular_file(
        records_path,
        parent=run_dir,
        label="Monitor records file",
    )
    rows = read_jsonl(records_path)
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
        _require_safe_regular_file(
            chunk,
            parent=run_dir,
            label="Monitor chunk",
        )
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
