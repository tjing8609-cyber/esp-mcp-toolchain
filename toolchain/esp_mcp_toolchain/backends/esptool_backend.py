from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from typing import Any
from uuid import uuid4

from .espidf_backend import _idf_path, _idf_python, _run_idf_command
from ..utils.subprocess_utils import redact_command, run_managed_command


def _remove_owned_partial(path: Path) -> str | None:
    try:
        if path.is_symlink():
            return f"refused to remove a symbolic-link partial: {path}"
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"could not inspect backup partial before cleanup: {exc}"
    if not stat.S_ISREG(metadata.st_mode):
        return f"refused to remove a non-regular partial: {path}"
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return str(exc)
    return None


def _record_partial_cleanup(result: dict[str, Any], partial_path: Path) -> dict[str, Any]:
    cleanup_error = _remove_owned_partial(partial_path)
    result["partial_path"] = str(partial_path)
    result["partial_cleanup_completed"] = cleanup_error is None
    if cleanup_error is not None:
        result["partial_cleanup_error"] = cleanup_error
    return result


def _directory_signature(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return (metadata.st_dev, metadata.st_ino)


def _directory_unchanged(
    path: Path,
    expected_path: Path,
    expected_signature: tuple[int, int],
) -> bool:
    try:
        return (
            path.resolve(strict=True) == expected_path
            and path.is_dir()
            and _directory_signature(path) == expected_signature
        )
    except OSError:
        return False


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _link_no_replace(source: Path, target: Path) -> None:
    try:
        os.link(source, target, follow_symlinks=False)
    except NotImplementedError:
        # Some Windows Python builds expose the keyword but do not implement it.
        # The caller has already revalidated the private UUID staging file.
        os.link(source, target)


def run_read_flash(
    *,
    port: str,
    output_path: Path,
    chip: str = "esp32",
    address: int = 0,
    size: int = 0x400000,
    baud: int = 460800,
    timeout_s: int = 240,
    staging_dir: Path | None = None,
) -> dict[str, Any]:
    if not output_path.parent.exists() or not output_path.parent.is_dir():
        return {
            "ok": False,
            "error_kind": "backup_parent_missing",
            "message": f"Backup parent directory does not exist: {output_path.parent}",
        }
    if output_path.exists() or output_path.is_symlink():
        return {
            "ok": False,
            "error_kind": "backup_output_exists",
            "message": f"Backup output already exists and will not be overwritten: {output_path}",
        }
    legacy_partial = output_path.with_name(f"{output_path.name}.part")
    if legacy_partial.exists() or legacy_partial.is_symlink():
        return {
            "ok": False,
            "error_kind": "backup_partial_exists",
            "message": f"Existing partial backup will not be deleted: {legacy_partial}",
        }
    try:
        output_parent = output_path.parent.resolve(strict=True)
        output_parent_signature = _directory_signature(output_path.parent)
    except OSError as exc:
        return {
            "ok": False,
            "error_kind": "backup_parent_unreadable",
            "message": f"Backup parent directory could not be validated: {exc}",
        }
    staging_root = staging_dir or output_path.parent
    if not staging_root.exists() or not staging_root.is_dir():
        return {
            "ok": False,
            "error_kind": "backup_staging_missing",
            "message": f"Backup staging directory does not exist: {staging_root}",
        }
    try:
        canonical_staging_root = staging_root.resolve(strict=True)
        staging_root_signature = _directory_signature(staging_root)
    except OSError as exc:
        return {
            "ok": False,
            "error_kind": "backup_staging_unreadable",
            "message": f"Backup staging directory could not be validated: {exc}",
        }
    partial_path = staging_root / f"{output_path.name}.{uuid4().hex}.part"
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found for flash backup.",
        }
    command = [
        str(_idf_python()),
        "-m",
        "esptool",
        "--chip",
        chip,
        "-p",
        port,
        "-b",
        str(baud),
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "read_flash",
        hex(address),
        hex(size),
        str(partial_path),
    ]
    try:
        result = _run_idf_command(command, staging_root, idf_path, timeout_s)
    except Exception as exc:
        if not _directory_unchanged(
            staging_root,
            canonical_staging_root,
            staging_root_signature,
        ):
            return {
                "ok": False,
                "error_kind": "backup_staging_changed",
                "message": "Backup staging directory changed while esptool was running.",
                "partial_path": str(partial_path),
                "partial_cleanup_completed": False,
            }
        return _record_partial_cleanup(
            {
                "ok": False,
                "error_kind": "backup_spawn_failed",
                "message": str(exc),
                "command": redact_command(command),
            },
            partial_path,
        )

    if not _directory_unchanged(
        staging_root,
        canonical_staging_root,
        staging_root_signature,
    ):
        return {
            **result,
            "ok": False,
            "error_kind": "backup_staging_changed",
            "message": "Backup staging directory changed while esptool was running.",
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
        }
    if not result.get("ok"):
        if result.get("error_kind") == "idf_command_timeout":
            result["error_kind"] = "backup_timeout"
            result["message"] = f"esptool read_flash timed out after {timeout_s} seconds."
        else:
            result["message"] = result.get("message", "Flash backup failed.")
        return _record_partial_cleanup(result, partial_path)

    if not partial_path.exists():
        return _record_partial_cleanup(
            {
                **result,
                "ok": False,
                "error_kind": "backup_output_missing",
                "message": "esptool completed without creating a backup file.",
            },
            partial_path,
        )
    if not partial_path.is_file() or partial_path.is_symlink():
        return _record_partial_cleanup(
            {
                **result,
                "ok": False,
                "error_kind": "backup_partial_invalid",
                "message": "esptool backup output is not a regular file.",
            },
            partial_path,
        )

    try:
        partial_signature = _file_signature(partial_path)
        partial_digest = _sha256_file(partial_path)
    except OSError as exc:
        return _record_partial_cleanup(
            {
                **result,
                "ok": False,
                "error_kind": "backup_partial_unreadable",
                "message": f"Flash backup partial could not be validated: {exc}",
            },
            partial_path,
        )
    actual_size = partial_signature[2]
    if actual_size != size:
        return _record_partial_cleanup(
            {
                **result,
                "ok": False,
                "error_kind": "backup_size_mismatch",
                "message": "Flash backup size does not match the requested size.",
                "expected_bytes": size,
                "actual_bytes": actual_size,
            },
            partial_path,
        )
    result["sha256"] = partial_digest

    if not _directory_unchanged(
        output_path.parent,
        output_parent,
        output_parent_signature,
    ):
        return {
            **result,
            "ok": False,
            "error_kind": "backup_output_parent_changed",
            "message": "Backup output parent changed during capture; output was not published.",
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
            "recovery_path": str(partial_path),
        }
    try:
        revalidated_signature = _file_signature(partial_path)
        revalidated_digest = _sha256_file(partial_path)
    except OSError:
        revalidated_signature = None
        revalidated_digest = None
    if (
        revalidated_signature != partial_signature
        or revalidated_digest != partial_digest
        or not partial_path.is_file()
        or partial_path.is_symlink()
    ):
        return {
            **result,
            "ok": False,
            "error_kind": "backup_partial_changed",
            "message": "Flash backup partial changed before publication.",
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
        }
    try:
        _link_no_replace(partial_path, output_path)
    except FileExistsError:
        return {
            **result,
            "ok": False,
            "error_kind": "backup_publish_conflict",
            "message": (
                "Backup output appeared during capture and was not overwritten; "
                "the complete captured image was preserved at recovery_path."
            ),
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
            "recovery_path": str(partial_path),
        }
    except (NotImplementedError, OSError) as exc:
        return {
            **result,
            "ok": False,
            "error_kind": "backup_publish_failed",
            "message": (
                f"Backup could not be published atomically: {exc}. "
                "The complete captured image was preserved at recovery_path."
            ),
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
            "recovery_path": str(partial_path),
        }
    try:
        final_signature = _file_signature(output_path)
        final_digest = _sha256_file(output_path)
    except OSError:
        final_signature = None
        final_digest = None
    if (
        final_signature is None
        or final_signature[2] != size
        or final_digest != partial_digest
        or not output_path.is_file()
        or output_path.is_symlink()
    ):
        return {
            **result,
            "ok": False,
            "error_kind": "backup_publish_verification_failed",
            "message": (
                "Published backup could not be verified; the validated staging "
                "image was preserved at recovery_path."
            ),
            "partial_path": str(partial_path),
            "partial_cleanup_completed": False,
            "recovery_path": str(partial_path),
        }
    result["output_path"] = str(output_path)
    result["bytes_read"] = size
    result = _record_partial_cleanup(result, partial_path)
    result["message"] = (
        "Flash backup completed."
        if result["partial_cleanup_completed"]
        else "Flash backup completed, but its temporary partial file could not be removed."
    )
    return result


def run_erase_flash(*, port: str, chip: str = "esp32", timeout_s: int = 180) -> dict[str, Any]:
    command = [
        str(_idf_python()),
        "-m",
        "esptool",
        "--chip",
        chip,
        "-p",
        port,
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "erase_flash",
    ]
    result = run_managed_command(
        command,
        cwd=Path.cwd(),
        timeout_s=timeout_s,
    )
    if not result.get("ok"):
        if result.get("error_kind") == "managed_command_timeout":
            result["error_kind"] = "erase_timeout"
            result["message"] = (
                f"esptool erase_flash timed out after {timeout_s} seconds."
            )
        elif result.get("error_kind") == "managed_command_spawn_failed":
            result["error_kind"] = "erase_spawn_failed"
        else:
            result["message"] = result.get("message", "Flash erase failed.")
        return result

    result["message"] = "Flash erase completed."
    return result


def run_write_flash(
    *,
    port: str,
    input_path: Path,
    chip: str = "esp32",
    address: int = 0,
    baud: int = 460800,
    timeout_s: int = 300,
) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found for esptool.",
        }
    command = [
        str(_idf_python()),
        "-m",
        "esptool",
        "--chip",
        chip,
        "-p",
        port,
        "-b",
        str(baud),
        "write_flash",
        hex(address),
        str(input_path),
    ]
    try:
        result = _run_idf_command(command, input_path.parent, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "restore_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }
    result["message"] = "Flash image restored." if result.get("ok") else result.get("message", "Flash restore failed.")
    return result
