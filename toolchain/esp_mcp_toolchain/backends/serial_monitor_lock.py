from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from ..project_context import storage_root
from ..utils.time_utils import now_utc_iso


def _windows_process_info(pid: int) -> tuple[bool, str | None]:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False, None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False, None
        if exit_code.value != 259:
            return False, None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return True, None
        marker = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return True, str(marker)
    finally:
        kernel32.CloseHandle(handle)


def _process_info(pid: int) -> tuple[bool, str | None]:
    if pid <= 0:
        return False, None
    if os.name == "nt":
        return _windows_process_info(pid)
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.exists():
        try:
            fields = proc_stat.read_text(encoding="utf-8").split()
            return True, fields[21]
        except (OSError, IndexError):
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None
    except OSError:
        return False, None
    return True, None


_PROCESS_STARTED = _process_info(os.getpid())[1]
_PROCESS_TOKEN = f"{os.getpid()}-{time.time_ns()}-{uuid4().hex}"


class PortLockError(RuntimeError):
    def __init__(self, message: str, owner: dict | None = None):
        super().__init__(message)
        self.owner = owner or {}


def identity_key(identity: dict) -> str:
    vid = identity.get("vid") or ""
    pid = identity.get("pid") or ""
    serial_number = identity.get("serial_number") or ""
    location = identity.get("location") or ""
    device = identity.get("device_path") or identity.get("port") or "unknown"
    if serial_number:
        stable = f"usb:{vid}:{pid}:{serial_number}"
    elif location:
        stable = f"location:{vid}:{pid}:{location}"
    else:
        stable = f"device:{str(device).casefold()}"
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def lock_directory() -> Path:
    return storage_root().parent / "locks" / "serial"


def _read_owner(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def current_process_owner() -> dict:
    return {
        "pid": os.getpid(),
        "process_token": _PROCESS_TOKEN,
        "process_started": _PROCESS_STARTED,
    }


def process_owner_is_live(owner: dict) -> bool:
    try:
        pid = int(owner.get("pid", 0))
    except (TypeError, ValueError):
        return False
    if pid == os.getpid() and owner.get("process_token") != _PROCESS_TOKEN:
        return False
    alive, started = _process_info(pid)
    expected_started = owner.get("process_started")
    if not alive:
        return False
    if expected_started and started and str(expected_started) != str(started):
        return False
    return True


@dataclass
class PortLease:
    path: Path
    lock_id: str
    metadata: dict
    stale_owner: dict | None = None

    @classmethod
    def acquire(cls, identity: dict, *, run_id: str, project_id: str) -> "PortLease":
        root = lock_directory()
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{identity_key(identity)}.json"
        lock_id = f"lock_{uuid4().hex}"
        metadata = {
            "lock_id": lock_id,
            "pid": os.getpid(),
            "process_token": _PROCESS_TOKEN,
            "process_started": _PROCESS_STARTED,
            "run_id": run_id,
            "project_id": project_id,
            "started_at": now_utc_iso(),
            "port_identity": identity,
        }
        stale_owner = None

        for _attempt in range(3):
            try:
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(metadata, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return cls(path=path, lock_id=lock_id, metadata=metadata, stale_owner=stale_owner)
            except FileExistsError:
                owner = _read_owner(path)
                if process_owner_is_live(owner):
                    raise PortLockError("Serial port is reserved by another monitor process.", owner)
                stale_owner = owner
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise PortLockError(f"Stale serial lock could not be removed: {exc}", owner) from exc

        raise PortLockError("Serial port lock could not be acquired after stale-lock recovery.", stale_owner)

    def release(self) -> None:
        owner = _read_owner(self.path)
        if owner.get("lock_id") != self.lock_id:
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
