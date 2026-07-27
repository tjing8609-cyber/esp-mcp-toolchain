from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from uuid import uuid4

from ..backends.espidf_backend import run_idf_flash
from ..backends.esptool_backend import run_erase_flash, run_read_flash, run_write_flash
from ..errors import execution_error
from ..paths import data_dir, safe_project_path
from ..utils.time_utils import now_compact
from .log_tools import logged_task


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _flash_artifact_root() -> Path:
    project_data = data_dir()
    canonical_project_data = project_data.resolve()
    artifacts = project_data / "artifacts"
    flash_root = artifacts / "flash"
    for component in (artifacts, flash_root):
        if _is_reparse_point(component):
            raise ValueError(
                f"project flash artifact path contains a symbolic link or junction: {component}"
            )
    canonical_flash_root = flash_root.resolve()
    if not _is_within(canonical_flash_root, canonical_project_data):
        raise ValueError(
            "project flash artifact path is outside the current project data directory: "
            f"{canonical_flash_root}"
        )
    return canonical_flash_root


def _prepare_flash_artifact_subdir(name: str) -> Path:
    flash_root = _flash_artifact_root()
    flash_root.mkdir(parents=True, exist_ok=True)
    flash_root = _flash_artifact_root()
    subdir = flash_root / name
    if _is_reparse_point(subdir):
        raise ValueError(
            f"project flash staging path contains a symbolic link or junction: {subdir}"
        )
    subdir.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(subdir):
        raise ValueError(
            f"project flash staging path became a symbolic link or junction: {subdir}"
        )
    canonical_subdir = subdir.resolve()
    if not _is_within(canonical_subdir, flash_root):
        raise ValueError(
            f"project flash staging path is outside the flash artifact directory: {canonical_subdir}"
        )
    return canonical_subdir


def _prepare_flash_run_dir(kind: str) -> Path:
    staging_root = _prepare_flash_artifact_subdir(f"{kind}-staging")
    run_dir = staging_root / f"run_{uuid4().hex}"
    run_dir.mkdir(mode=0o700)
    if _is_reparse_point(run_dir):
        raise ValueError(f"flash run staging directory is a reparse point: {run_dir}")
    canonical_run_dir = run_dir.resolve()
    if not _is_within(canonical_run_dir, staging_root):
        raise ValueError(
            f"flash run staging directory is outside its project staging root: {canonical_run_dir}"
        )
    return canonical_run_dir


def _remove_flash_run_dir(path: Path) -> str | None:
    try:
        is_reparse = _is_reparse_point(path)
    except OSError as exc:
        return f"could not inspect flash staging directory before cleanup: {exc}"
    if is_reparse:
        return f"refused to remove reparse staging directory: {path}"
    try:
        path.rmdir()
    except OSError as exc:
        return str(exc)
    return None


def _resolve_flash_artifact_path(value: str | Path) -> Path:
    workspace_root = safe_project_path(".").resolve()
    artifact_root = _flash_artifact_root()
    requested = Path(value).expanduser()
    candidate = (
        (workspace_root / requested).resolve()
        if not requested.is_absolute()
        else requested.resolve()
    )
    if not any(
        _is_within(candidate, allowed_root)
        for allowed_root in (workspace_root, artifact_root)
    ):
        raise ValueError(
            "flash image path must stay inside the selected workspace or "
            f"the current project's flash artifact directory: {candidate}"
        )
    return candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_signature(path: Path) -> tuple[int, int, int, int]:
    metadata = path.stat()
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _copy_restore_image(source: Path, staging_dir: Path) -> tuple[Path, str, int]:
    staged = staging_dir / f"restore_{uuid4().hex}.bin"
    digest = hashlib.sha256()
    copied_bytes = 0
    created = False
    try:
        with source.open("rb") as input_stream, staged.open("xb") as output_stream:
            created = True
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output_stream.write(chunk)
                digest.update(chunk)
                copied_bytes += len(chunk)
    except Exception:
        if created:
            _remove_restore_staging(staged)
        raise
    return staged, digest.hexdigest(), copied_bytes


def _remove_restore_staging(path: Path) -> str | None:
    try:
        is_reparse = _is_reparse_point(path)
    except OSError as exc:
        return f"could not inspect restore staging path before cleanup: {exc}"
    if is_reparse:
        return f"refused to clean a reparse restore staging path: {path}"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return f"could not inspect restore staging file before cleanup: {exc}"
    if not stat.S_ISREG(metadata.st_mode):
        return f"refused to clean a non-regular restore staging path: {path}"
    if os.name != "nt":
        try:
            os.chmod(
                path,
                stat.S_IREAD | stat.S_IWRITE,
                follow_symlinks=False,
            )
        except (NotImplementedError, OSError):
            # Unlink below never follows a file symlink. If a platform cannot
            # safely clear the mode, retain the file and report the error.
            pass
    try:
        changed_to_reparse = _is_reparse_point(path)
    except OSError as exc:
        return f"could not revalidate restore staging path before cleanup: {exc}"
    if changed_to_reparse:
        return f"restore staging path changed before cleanup: {path}"
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        return str(exc)
    return None


def _record_restore_staging_cleanup(result: dict, staged: Path) -> dict:
    file_cleanup_error = _remove_restore_staging(staged)
    directory_cleanup_error = (
        _remove_flash_run_dir(staged.parent)
        if file_cleanup_error is None
        else "run directory retained because staged file cleanup did not complete"
    )
    cleanup_errors = [
        error
        for error in (file_cleanup_error, directory_cleanup_error)
        if error is not None
    ]
    result["staging_cleanup_completed"] = not cleanup_errors
    if cleanup_errors:
        result["staging_cleanup_errors"] = cleanup_errors
        result["staging_path"] = str(staged)
    return result


def _default_backup_path(prefix: str = "flash_backup") -> Path:
    return data_dir() / "artifacts" / "flash" / f"{prefix}_{now_compact()}.bin"


@logged_task(
    task_type="backup_flash",
    selected_port_arg="port",
    payload_args=("chip", "size", "address", "baud"),
)
def esp_backup_flash(
    port: str,
    chip: str = "esp32",
    size: int = 0x400000,
    address: int = 0,
    baud: int = 460800,
    output_path: str = "",
) -> dict:
    if size <= 0:
        return execution_error("invalid_size", "Backup size must be greater than zero.", tool="esp_backup_flash")
    using_default_path = not output_path
    try:
        if using_default_path:
            flash_root = _flash_artifact_root()
            flash_root.mkdir(parents=True, exist_ok=True)
            _flash_artifact_root()
        target = _resolve_flash_artifact_path(
            _default_backup_path() if using_default_path else output_path
        )
    except (OSError, ValueError) as exc:
        return execution_error(
            "unsafe_output_path",
            str(exc),
            tool="esp_backup_flash",
        )
    if not target.parent.exists() or not target.parent.is_dir():
        return execution_error(
            "backup_parent_missing",
            f"Backup parent directory does not exist: {target.parent}",
            tool="esp_backup_flash",
        )
    if target.exists() or target.is_symlink():
        return execution_error(
            "backup_output_exists",
            f"Backup output already exists and will not be overwritten: {target}",
            tool="esp_backup_flash",
        )
    legacy_partial = target.with_name(f"{target.name}.part")
    if legacy_partial.exists() or legacy_partial.is_symlink():
        return execution_error(
            "backup_partial_exists",
            f"Existing partial backup will not be deleted: {legacy_partial}",
            tool="esp_backup_flash",
        )
    try:
        staging_dir = _prepare_flash_run_dir("backup")
    except (OSError, ValueError) as exc:
        return execution_error(
            "unsafe_output_path",
            str(exc),
            tool="esp_backup_flash",
        )
    result = run_read_flash(
        port=port,
        chip=chip,
        address=address,
        size=size,
        baud=baud,
        output_path=target,
        staging_dir=staging_dir,
    )
    if result.get("error_kind") == "backup_staging_changed":
        result["staging_cleanup_completed"] = False
        result["staging_cleanup_errors"] = [
            "staging directory identity changed; automatic cleanup was refused"
        ]
    elif result.get("recovery_path"):
        result["staging_cleanup_completed"] = False
        result["staging_preserved_for_recovery"] = True
    else:
        staging_cleanup_error = _remove_flash_run_dir(staging_dir)
        result["staging_cleanup_completed"] = staging_cleanup_error is None
        if staging_cleanup_error is not None:
            result["staging_cleanup_errors"] = [staging_cleanup_error]
    result.update(
        {
            "tool": "esp_backup_flash",
            "tool_name": "esp_backup_flash",
            "tools鍚嶇О": "esp_backup_flash",
            "implemented": True,
            "port": port,
            "chip": chip,
            "address": address,
            "size": size,
            "baud": baud,
            "output_path": str(target),
        }
    )
    if result.get("ok"):
        bytes_read = result.get("bytes_read")
        digest = result.get("sha256")
        if bytes_read != size or not isinstance(digest, str) or len(digest) != 64:
            return execution_error(
                "backup_backend_contract_invalid",
                "Backup backend reported success without validated size and SHA-256 evidence.",
                tool="esp_backup_flash",
            )
        result["data"] = {
            "output_path": str(target),
            "bytes_read": bytes_read,
            "sha256": digest,
        }
    return result


@logged_task(
    task_type="flash",
    selected_port_arg="port",
    payload_args=("backend", "project_dir", "baud", "monitor_after_flash"),
)
def esp_flash_firmware(
    port: str,
    backend: str = "espidf",
    project_dir: str = ".",
    baud: int = 460800,
    monitor_after_flash: bool = False,
    confirm: bool = False,
) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Flashing firmware is a high-risk action and requires confirm=True.",
            tool="esp_flash_firmware",
            recoverable=True,
            suggested_next_actions=["Review the port and project_dir", "Call again with confirm=True only after user approval"],
        )
    if backend != "espidf":
        return execution_error(
            "unsupported_backend",
            f"Unsupported flash backend: {backend}",
            tool="esp_flash_firmware",
            suggested_next_actions=["Use backend=espidf"],
        )
    if monitor_after_flash:
        return execution_error(
            "unsupported_option",
            "monitor_after_flash is not implemented yet.",
            tool="esp_flash_firmware",
            suggested_next_actions=["Flash first, then run esp_serial_capture"],
        )
    try:
        path = safe_project_path(project_dir)
    except ValueError as exc:
        return execution_error("unsafe_project_path", str(exc), tool="esp_flash_firmware")
    if not path.exists():
        return execution_error("project_dir_missing", f"Project directory does not exist: {path}", tool="esp_flash_firmware")

    result = run_idf_flash(path, port=port, baud=baud)
    result.update(
        {
            "tool": "esp_flash_firmware",
            "tool_name": "esp_flash_firmware",
            "tools鍚嶇О": "esp_flash_firmware",
            "implemented": True,
            "backend": backend,
            "project_dir": str(path),
            "port": port,
            "baud": baud,
        }
    )
    return result


@logged_task(task_type="erase_flash", selected_port_arg="port", payload_args=("chip",))
def esp_erase_flash(port: str, chip: str = "esp32", confirm: bool = False) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Erasing flash is a destructive high-risk action and requires confirm=True.",
            tool="esp_erase_flash",
            recoverable=True,
            suggested_next_actions=["Back up flash first", "Review port and chip", "Call again with confirm=True only after user approval"],
        )

    result = run_erase_flash(port=port, chip=chip)
    result.update(
        {
            "tool": "esp_erase_flash",
            "tool_name": "esp_erase_flash",
            "tools鍚嶇О": "esp_erase_flash",
            "implemented": True,
            "port": port,
            "chip": chip,
        }
    )
    return result


@logged_task(
    task_type="restore_flash",
    selected_port_arg="port",
    payload_args=("chip", "address", "baud"),
)
def esp_restore_flash(
    port: str,
    input_path: str,
    chip: str = "esp32",
    address: int = 0,
    baud: int = 460800,
    expected_sha256: str = "",
    confirm: bool = False,
) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Restoring a flash image overwrites board flash and requires confirm=True.",
            tool="esp_restore_flash",
            recoverable=True,
            suggested_next_actions=["Verify port, input_path, address, and backup hash", "Call again with confirm=True only after user approval"],
        )
    try:
        source = _resolve_flash_artifact_path(input_path)
    except (OSError, ValueError) as exc:
        return execution_error("unsafe_input_path", str(exc), tool="esp_restore_flash")
    if not source.exists():
        return execution_error("restore_image_missing", f"Flash image does not exist: {source}", tool="esp_restore_flash")
    if not source.is_file():
        return execution_error(
            "restore_image_not_regular",
            f"Flash image is not a regular file: {source}",
            tool="esp_restore_flash",
        )
    staged_source: Path | None = None
    staging_dir: Path | None = None
    try:
        initial_signature = _file_signature(source)
        staging_dir = _prepare_flash_run_dir("restore")
        staged_source, digest, copied_bytes = _copy_restore_image(source, staging_dir)
        revalidated_source = _resolve_flash_artifact_path(input_path)
        revalidated_signature = _file_signature(revalidated_source)
        revalidated_digest = _sha256_file(revalidated_source)
    except (OSError, ValueError) as exc:
        result = execution_error(
            "restore_image_unreadable",
            f"Flash image could not be staged safely: {exc}",
            tool="esp_restore_flash",
        )
        if staged_source is not None:
            return _record_restore_staging_cleanup(result, staged_source)
        if staging_dir is not None:
            directory_cleanup_error = _remove_flash_run_dir(staging_dir)
            result["staging_cleanup_completed"] = directory_cleanup_error is None
            if directory_cleanup_error is not None:
                result["staging_cleanup_errors"] = [directory_cleanup_error]
        return result
    assert staged_source is not None
    size = initial_signature[2]
    if size <= 0:
        return _record_restore_staging_cleanup(
            execution_error(
                "restore_image_empty",
                "Flash image is empty.",
                tool="esp_restore_flash",
            ),
            staged_source,
        )
    if (
        revalidated_source != source
        or revalidated_signature != initial_signature
        or revalidated_digest != digest
        or copied_bytes != size
    ):
        return _record_restore_staging_cleanup(
            execution_error(
                "restore_source_changed",
                "Flash image changed while creating the validated restore staging copy.",
                tool="esp_restore_flash",
            ),
            staged_source,
        )
    if expected_sha256 and digest.lower() != expected_sha256.lower():
        return _record_restore_staging_cleanup(
            execution_error(
                "restore_hash_mismatch",
                "Flash image SHA-256 does not match expected_sha256.",
                tool="esp_restore_flash",
                expected_sha256=expected_sha256.lower(),
                actual_sha256=digest,
            ),
            staged_source,
        )
    try:
        if os.name != "nt":
            os.chmod(staged_source, stat.S_IREAD, follow_symlinks=False)
        staged_signature = _file_signature(staged_source)
        staged_digest = _sha256_file(staged_source)
    except OSError as exc:
        return _record_restore_staging_cleanup(
            execution_error(
                "restore_staging_invalid",
                f"Validated restore staging copy could not be protected: {exc}",
                tool="esp_restore_flash",
            ),
            staged_source,
        )
    if staged_signature[2] != size or staged_digest != digest:
        return _record_restore_staging_cleanup(
            execution_error(
                "restore_staging_changed",
                "Validated restore staging copy changed before restore started.",
                tool="esp_restore_flash",
            ),
            staged_source,
        )
    try:
        result = run_write_flash(
            port=port,
            input_path=staged_source,
            chip=chip,
            address=address,
            baud=baud,
        )
    except Exception:
        _record_restore_staging_cleanup({}, staged_source)
        raise
    result = _record_restore_staging_cleanup(result, staged_source)
    result.update(
        {
            "tool": "esp_restore_flash",
            "tool_name": "esp_restore_flash",
            "tools名称": "esp_restore_flash",
            "implemented": True,
            "port": port,
            "chip": chip,
            "address": address,
            "baud": baud,
            "input_path": str(source),
            "bytes_written": size if result.get("ok") else 0,
            "sha256": digest,
        }
    )
    if result.get("ok"):
        result["data"] = {"input_path": str(source), "bytes_written": size, "sha256": digest}
    return result
