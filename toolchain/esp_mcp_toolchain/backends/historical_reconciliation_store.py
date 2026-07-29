from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, BinaryIO, TYPE_CHECKING
from uuid import uuid4

from ..utils.time_utils import now_utc_iso

if TYPE_CHECKING:
    from ..tools.log_tools import LogScope


HISTORICAL_RECONCILIATION_LOCK_NAME = (
    ".sqlite-historical-artifacts.lock"
)
HISTORICAL_RECONCILIATION_MARKER_NAME = (
    "sqlite-historical-artifacts-v1.json"
)
HISTORICAL_RECONCILIATION_MARKER_FORMAT = (
    "esp-mcp-toolchain.historical-sqlite-artifacts"
)
HISTORICAL_RECONCILIATION_MARKER_VERSION = 1

_HISTORICAL_RECONCILIATION_LOCK_FORMAT = (
    "esp-mcp-toolchain.historical-sqlite-artifacts-lock"
)
_HISTORICAL_RECONCILIATION_LOCK_VERSION = 1
_HISTORICAL_RECONCILIATION_MARKER_PATTERN = re.compile(
    r"^sqlite-historical-artifacts-v\d+\.json$"
)
_MAX_LOCK_METADATA_BYTES = 64 * 1024
_MAX_MARKER_BYTES = 4 * 1024 * 1024
_MAX_PROJECT_CONTROL_ENTRIES = 10_000
_WINDOWS_REPARSE_POINT = 0x400


class HistoricalProjectReconciliationStoreError(RuntimeError):
    error_kind = "historical_project_reconciliation_store_failed"
    recoverable = False


class HistoricalProjectReconciliationBusy(
    HistoricalProjectReconciliationStoreError
):
    error_kind = "historical_project_reconciliation_busy"
    recoverable = True


def _is_reparse_status(path: Path, status: os.stat_result) -> bool:
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
    )


def _binding_identity(status: os.stat_result) -> tuple[int, int, int]:
    return (
        int(status.st_dev),
        int(status.st_ino),
        int(stat.S_IFMT(status.st_mode)),
    )


def _content_identity(
    status: os.stat_result,
) -> tuple[int, int, int, int, int]:
    return (
        *_binding_identity(status),
        int(status.st_size),
        int(status.st_mtime_ns),
    )


def _require_safe_directory(
    path: Path,
    *,
    label: str,
) -> os.stat_result:
    try:
        status = path.lstat()
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is unavailable: {exc}"
        ) from exc
    if _is_reparse_status(path, status):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is a reparse point and is refused."
        )
    if not stat.S_ISDIR(status.st_mode):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is not a directory."
        )
    return status


def _directory_chain_snapshot(
    project_dir: Path,
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    if not project_dir.is_absolute():
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation project directory must be absolute."
        )
    entries: list[tuple[str, tuple[int, int, int]]] = []
    current = project_dir
    while True:
        status = _require_safe_directory(
            current,
            label=(
                "Historical reconciliation project directory"
                if current == project_dir
                else "Historical reconciliation project ancestor"
            ),
        )
        entries.append((str(current), _binding_identity(status)))
        parent = current.parent
        if parent == current:
            break
        current = parent
    return tuple(entries)


def _scope_details(
    scope: LogScope,
) -> tuple[str, Path, Path, tuple[tuple[str, tuple[int, int, int]], ...]]:
    try:
        project_id = scope.project_id
        project_dir = Path(scope.project_dir)
        log_root = Path(scope.log_root)
    except (AttributeError, TypeError) as exc:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation scope is invalid."
        ) from exc
    if (
        not isinstance(project_id, str)
        or not project_id.strip()
        or project_id != project_id.strip()
    ):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation project identity is invalid."
        )
    if (
        not project_dir.is_absolute()
        or not log_root.is_absolute()
        or log_root.parent != project_dir
    ):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation scope is not project-bound."
        )
    snapshot = _directory_chain_snapshot(project_dir)
    return project_id, project_dir, log_root, snapshot


def _require_same_project_chain(
    project_dir: Path,
    expected: tuple[tuple[str, tuple[int, int, int]], ...],
) -> None:
    if _directory_chain_snapshot(project_dir) != expected:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation project directory chain changed."
        )


def _require_safe_regular_status(
    status: os.stat_result,
    *,
    label: str,
) -> None:
    if not stat.S_ISREG(status.st_mode):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is not a regular file."
        )
    if bool(
        getattr(status, "st_file_attributes", 0)
        & _WINDOWS_REPARSE_POINT
    ):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is a reparse point and is refused."
        )
    if int(getattr(status, "st_nlink", 1)) != 1:
        raise HistoricalProjectReconciliationStoreError(
            f"{label} has multiple hard links and is refused."
        )


def _require_path_matches_handle(
    path: Path,
    handle: BinaryIO,
    *,
    label: str,
) -> os.stat_result:
    try:
        path_status = path.lstat()
        handle_status = os.fstat(handle.fileno())
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"{label} identity is unavailable: {exc}"
        ) from exc
    if _is_reparse_status(path, path_status):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is a reparse point and is refused."
        )
    _require_safe_regular_status(path_status, label=label)
    _require_safe_regular_status(handle_status, label=label)
    if _binding_identity(path_status) != _binding_identity(handle_status):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} path changed while it was being opened."
        )
    return handle_status


def _windows_open_file(
    path: Path,
    *,
    writable: bool,
    create: bool,
    share_delete: bool,
) -> int:
    import ctypes
    import msvcrt

    generic_read = 0x80000000
    generic_write = 0x40000000
    file_share_read = 0x00000001
    file_share_write = 0x00000002
    file_share_delete = 0x00000004
    open_existing = 3
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

    desired_access = generic_read | (generic_write if writable else 0)
    sharing = file_share_read | file_share_write
    if share_delete:
        sharing |= file_share_delete
    handle = create_file(
        str(path),
        desired_access,
        sharing,
        None,
        open_always if create else open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle_value:
        error = ctypes.get_last_error()
        if not create and error in {2, 3}:
            raise FileNotFoundError(
                error,
                ctypes.FormatError(error),
                str(path),
            )
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
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation control file is a reparse point "
            "and is refused."
        )
    flags = os.O_RDONLY
    if writable:
        flags = os.O_RDWR
    flags |= getattr(os, "O_BINARY", 0)
    try:
        return msvcrt.open_osfhandle(int(handle), flags)
    except BaseException:
        close_handle(handle)
        raise


def _open_lock_file(path: Path, *, create: bool) -> int:
    if os.name == "nt":
        return _windows_open_file(
            path,
            writable=True,
            create=create,
            share_delete=False,
        )
    flags = os.O_RDWR
    if create:
        flags |= os.O_CREAT
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    if create:
        return os.open(path, flags, 0o600)
    return os.open(path, flags)


def _open_readonly_file(path: Path) -> int:
    if os.name == "nt":
        return _windows_open_file(
            path,
            writable=False,
            create=False,
            share_delete=True,
        )
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _lock_file(handle: BinaryIO) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(
                handle.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
    except (BlockingIOError, OSError) as exc:
        if getattr(exc, "errno", None) in {errno.EAGAIN, errno.EACCES}:
            raise HistoricalProjectReconciliationBusy(
                "Historical project reconciliation is already active."
            ) from exc
        raise


def _unlock_file(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _lock_metadata(
    *,
    project_id: str,
    lock_id: str,
) -> dict[str, Any]:
    from .serial_monitor_lock import current_process_owner

    return {
        "format": _HISTORICAL_RECONCILIATION_LOCK_FORMAT,
        "version": _HISTORICAL_RECONCILIATION_LOCK_VERSION,
        "project_id": project_id,
        "lock_id": lock_id,
        "created_at": now_utc_iso(),
        **current_process_owner(),
    }


def _encode_json_document(
    value: dict[str, Any],
    *,
    label: str,
    max_bytes: int,
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(value, dict):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} must be a JSON object."
        )
    try:
        encoded = (
            json.dumps(
                value,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        normalized = json.loads(encoded.decode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"{label} is not valid JSON: {exc}"
        ) from exc
    if len(encoded) > max_bytes:
        raise HistoricalProjectReconciliationStoreError(
            f"{label} exceeds the supported size."
        )
    if not isinstance(normalized, dict):
        raise HistoricalProjectReconciliationStoreError(
            f"{label} must be a JSON object."
        )
    return normalized, encoded


def _read_handle_json_metadata(
    handle: BinaryIO,
    *,
    project_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        handle.seek(0)
        raw = handle.read(_MAX_LOCK_METADATA_BYTES + 1)
    except OSError as exc:
        return None, f"Lock metadata could not be read: {exc}"
    if len(raw) > _MAX_LOCK_METADATA_BYTES:
        return None, "Lock metadata exceeds the supported size."
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"Lock metadata is invalid: {exc}"
    if not isinstance(value, dict):
        return None, "Lock metadata must be a JSON object."
    if (
        value.get("format") != _HISTORICAL_RECONCILIATION_LOCK_FORMAT
        or value.get("version")
        != _HISTORICAL_RECONCILIATION_LOCK_VERSION
        or value.get("project_id") != project_id
        or not isinstance(value.get("lock_id"), str)
        or not value["lock_id"]
    ):
        return None, "Lock metadata identity is invalid."
    return value, None


class HistoricalProjectReconciliationLease:
    def __init__(
        self,
        *,
        path: Path,
        lock_id: str,
        project_id: str,
        handle: BinaryIO,
        project_chain: tuple[
            tuple[str, tuple[int, int, int]],
            ...,
        ],
        lock_identity: tuple[int, int, int, int, int],
    ):
        self.path = path
        self.lock_id = lock_id
        self.project_id = project_id
        self._handle = handle
        self._project_chain = project_chain
        self._lock_identity = lock_identity
        self._held = True

    @classmethod
    def acquire(
        cls,
        scope: LogScope,
    ) -> HistoricalProjectReconciliationLease:
        project_id, project_dir, _log_root, project_chain = (
            _scope_details(scope)
        )
        path = project_dir / HISTORICAL_RECONCILIATION_LOCK_NAME
        lock_id = f"historical_project_{uuid4().hex}"
        metadata = _lock_metadata(
            project_id=project_id,
            lock_id=lock_id,
        )
        _normalized, encoded = _encode_json_document(
            metadata,
            label="Historical reconciliation lock metadata",
            max_bytes=_MAX_LOCK_METADATA_BYTES,
        )
        descriptor: int | None = None
        handle: BinaryIO | None = None
        locked = False
        transferred = False
        try:
            descriptor = _open_lock_file(path, create=True)
            handle = os.fdopen(descriptor, "r+b", closefd=True)
            descriptor = None
            _require_path_matches_handle(
                path,
                handle,
                label="Historical reconciliation lock",
            )
            _lock_file(handle)
            locked = True
            _require_same_project_chain(project_dir, project_chain)
            _require_path_matches_handle(
                path,
                handle,
                label="Historical reconciliation lock",
            )
            handle.seek(0)
            handle.truncate(0)
            written = handle.write(encoded)
            if written != len(encoded):
                raise OSError(
                    "Historical reconciliation lock metadata write "
                    "was incomplete."
                )
            handle.flush()
            os.fsync(handle.fileno())
            handle.seek(0)
            lock_status = _require_path_matches_handle(
                path,
                handle,
                label="Historical reconciliation lock",
            )
            _require_same_project_chain(project_dir, project_chain)
            lease = cls(
                path=path,
                lock_id=lock_id,
                project_id=project_id,
                handle=handle,
                project_chain=project_chain,
                lock_identity=_content_identity(lock_status),
            )
            transferred = True
            return lease
        except HistoricalProjectReconciliationBusy:
            raise
        except HistoricalProjectReconciliationStoreError:
            raise
        except OSError as exc:
            raise HistoricalProjectReconciliationStoreError(
                "Historical project reconciliation lease could not be "
                f"acquired: {exc}"
            ) from exc
        finally:
            if handle is not None and not transferred:
                if locked:
                    try:
                        _unlock_file(handle)
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
                _unlock_file(self._handle)
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


def _assert_lease_covers_scope(
    scope: LogScope,
    lease: HistoricalProjectReconciliationLease,
) -> tuple[
    str,
    Path,
    tuple[tuple[str, tuple[int, int, int]], ...],
]:
    project_id, project_dir, _log_root, project_chain = _scope_details(
        scope
    )
    if not isinstance(lease, HistoricalProjectReconciliationLease):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker requires a project lease."
        )
    if (
        not lease.held
        or lease.project_id != project_id
        or lease.path
        != project_dir / HISTORICAL_RECONCILIATION_LOCK_NAME
        or lease._handle.closed
    ):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation lease does not cover this project."
        )
    if project_chain != lease._project_chain:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation project directory chain changed "
            "after lease acquisition."
        )
    status = _require_path_matches_handle(
        lease.path,
        lease._handle,
        label="Historical reconciliation lock",
    )
    if _content_identity(status) != lease._lock_identity:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation lock metadata changed while held."
        )
    return project_id, project_dir, project_chain


def _assert_no_unknown_markers(project_dir: Path) -> None:
    try:
        entries = list(project_dir.iterdir())
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation project directory is unavailable: "
            f"{exc}"
        ) from exc
    if len(entries) > _MAX_PROJECT_CONTROL_ENTRIES:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation project directory has too many "
            "control entries."
        )
    unknown = sorted(
        candidate.name
        for candidate in entries
        if (
            _HISTORICAL_RECONCILIATION_MARKER_PATTERN.fullmatch(
                candidate.name
            )
            and candidate.name
            != HISTORICAL_RECONCILIATION_MARKER_NAME
        )
    )
    if unknown:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker version is unsupported: "
            + ", ".join(unknown)
        )


def _validate_marker(
    document: dict[str, Any],
    *,
    project_id: str,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker must be a JSON object."
        )
    if (
        document.get("format")
        != HISTORICAL_RECONCILIATION_MARKER_FORMAT
    ):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker format is unsupported."
        )
    if (
        document.get("version")
        != HISTORICAL_RECONCILIATION_MARKER_VERSION
    ):
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker version is unsupported."
        )
    if document.get("project_id") != project_id:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker project identity conflicts."
        )
    return document


def _read_marker_file(
    marker_path: Path,
    *,
    project_id: str,
) -> dict[str, Any]:
    descriptor: int | None = None
    try:
        descriptor = _open_readonly_file(marker_path)
        handle = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = None
        with handle:
            before = os.fstat(handle.fileno())
            _require_safe_regular_status(
                before,
                label="Historical reconciliation marker",
            )
            raw = handle.read(_MAX_MARKER_BYTES + 1)
            after = os.fstat(handle.fileno())
            if _content_identity(after) != _content_identity(before):
                raise HistoricalProjectReconciliationStoreError(
                    "Historical reconciliation marker changed while read."
                )
    except HistoricalProjectReconciliationStoreError:
        raise
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation marker could not be safely read: "
            f"{exc}"
        ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(raw) > _MAX_MARKER_BYTES:
        raise HistoricalProjectReconciliationStoreError(
            "Historical reconciliation marker exceeds the supported size."
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation marker is invalid: {exc}"
        ) from exc
    return _validate_marker(document, project_id=project_id)


def load_historical_reconciliation_marker(
    scope: LogScope,
) -> dict[str, Any] | None:
    project_id, project_dir, _log_root, project_chain = _scope_details(
        scope
    )
    _assert_no_unknown_markers(project_dir)
    marker_path = project_dir / HISTORICAL_RECONCILIATION_MARKER_NAME
    try:
        marker_path.lstat()
    except FileNotFoundError:
        _require_same_project_chain(project_dir, project_chain)
        _assert_no_unknown_markers(project_dir)
        return None
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation marker is unavailable: {exc}"
        ) from exc
    document = _read_marker_file(
        marker_path,
        project_id=project_id,
    )
    _require_same_project_chain(project_dir, project_chain)
    _assert_no_unknown_markers(project_dir)
    return document


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError as exc:
        unsupported = {
            errno.EBADF,
            errno.EINVAL,
            getattr(errno, "ENOTSUP", errno.EINVAL),
            getattr(errno, "EOPNOTSUPP", errno.EINVAL),
        }
        if exc.errno not in unsupported:
            raise HistoricalProjectReconciliationStoreError(
                "Historical reconciliation marker directory could not "
                f"be synchronized: {exc}"
            ) from exc
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass


def _replace_marker_file(source: Path, target: Path) -> None:
    deadline = time.monotonic() + 1.0
    while True:
        try:
            source.replace(target)
            return
        except OSError as exc:
            transient_windows_sharing_error = (
                os.name == "nt"
                and (
                    getattr(exc, "winerror", None) in {5, 32}
                    or getattr(exc, "errno", None) == errno.EACCES
                )
            )
            if (
                not transient_windows_sharing_error
                or time.monotonic() >= deadline
            ):
                raise
            time.sleep(0.005)


def publish_historical_reconciliation_marker(
    scope: LogScope,
    lease: HistoricalProjectReconciliationLease,
    document: dict[str, Any],
) -> dict[str, Any]:
    project_id, project_dir, project_chain = (
        _assert_lease_covers_scope(scope, lease)
    )
    normalized, encoded = _encode_json_document(
        document,
        label="Historical reconciliation marker",
        max_bytes=_MAX_MARKER_BYTES,
    )
    normalized = _validate_marker(
        normalized,
        project_id=project_id,
    )
    _assert_no_unknown_markers(project_dir)
    # Refuse to overwrite a malformed, foreign, or unsupported marker.
    load_historical_reconciliation_marker(scope)
    marker_path = project_dir / HISTORICAL_RECONCILIATION_MARKER_NAME
    temporary_path: Path | None = None
    replaced = False
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=project_dir,
            prefix=f".{HISTORICAL_RECONCILIATION_MARKER_NAME}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            written = handle.write(encoded)
            if written != len(encoded):
                raise OSError(
                    "Historical reconciliation marker write was incomplete."
                )
            handle.flush()
            os.fsync(handle.fileno())
        temporary_status = temporary_path.lstat()
        if _is_reparse_status(temporary_path, temporary_status):
            raise HistoricalProjectReconciliationStoreError(
                "Historical reconciliation marker temporary file is a "
                "reparse point and is refused."
            )
        _require_safe_regular_status(
            temporary_status,
            label="Historical reconciliation marker temporary file",
        )
        _assert_lease_covers_scope(scope, lease)
        _assert_no_unknown_markers(project_dir)
        try:
            existing = marker_path.lstat()
        except FileNotFoundError:
            existing = None
        except OSError as exc:
            raise HistoricalProjectReconciliationStoreError(
                f"Historical reconciliation marker target is unavailable: "
                f"{exc}"
            ) from exc
        if existing is not None:
            if _is_reparse_status(marker_path, existing):
                raise HistoricalProjectReconciliationStoreError(
                    "Historical reconciliation marker target is a reparse "
                    "point and is refused."
                )
            _require_safe_regular_status(
                existing,
                label="Historical reconciliation marker target",
            )
        _replace_marker_file(temporary_path, marker_path)
        replaced = True
        temporary_path = None
        _fsync_directory(project_dir)
        _require_same_project_chain(project_dir, project_chain)
        persisted = load_historical_reconciliation_marker(scope)
        if persisted != normalized:
            raise HistoricalProjectReconciliationStoreError(
                "Historical reconciliation marker could not be verified "
                "after writing."
            )
        _assert_lease_covers_scope(scope, lease)
        return persisted
    except HistoricalProjectReconciliationStoreError:
        raise
    except OSError as exc:
        action = "published" if replaced else "written"
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation marker could not be {action}: "
            f"{exc}"
        ) from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def probe_historical_reconciliation_lease(
    scope: LogScope,
) -> dict[str, Any]:
    project_id, project_dir, _log_root, project_chain = _scope_details(
        scope
    )
    path = project_dir / HISTORICAL_RECONCILIATION_LOCK_NAME
    descriptor: int | None = None
    handle: BinaryIO | None = None
    locked = False
    active = False
    metadata: dict[str, Any] | None = None
    metadata_error: str | None = None
    try:
        try:
            descriptor = _open_lock_file(path, create=False)
        except FileNotFoundError:
            _require_same_project_chain(project_dir, project_chain)
            return {
                "ok": True,
                "project_id": project_id,
                "path": str(path),
                "lock_present": False,
                "active": False,
                "owner": None,
                "last_owner": None,
                "metadata_error": None,
            }
        handle = os.fdopen(descriptor, "r+b", closefd=True)
        descriptor = None
        _require_path_matches_handle(
            path,
            handle,
            label="Historical reconciliation lock",
        )
        try:
            _lock_file(handle)
            locked = True
        except HistoricalProjectReconciliationBusy:
            active = True
        metadata, metadata_error = _read_handle_json_metadata(
            handle,
            project_id=project_id,
        )
        _require_path_matches_handle(
            path,
            handle,
            label="Historical reconciliation lock",
        )
        _require_same_project_chain(project_dir, project_chain)
        return {
            "ok": True,
            "project_id": project_id,
            "path": str(path),
            "lock_present": True,
            "active": active,
            "owner": metadata if active else None,
            "last_owner": None if active else metadata,
            "metadata_error": metadata_error,
        }
    except HistoricalProjectReconciliationStoreError:
        raise
    except OSError as exc:
        raise HistoricalProjectReconciliationStoreError(
            f"Historical reconciliation lease could not be probed: {exc}"
        ) from exc
    finally:
        if handle is not None:
            if locked:
                try:
                    _unlock_file(handle)
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
