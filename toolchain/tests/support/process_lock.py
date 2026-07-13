from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


def acquire_process_lock(path: str | Path) -> BinaryIO:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a+b", buffering=0)
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        handle.close()
        raise
    return handle


def release_process_lock(handle: BinaryIO) -> None:
    if handle.closed:
        return
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()


def can_acquire_process_lock(path: str | Path) -> bool:
    try:
        handle = acquire_process_lock(path)
    except OSError:
        return False
    release_process_lock(handle)
    return True
