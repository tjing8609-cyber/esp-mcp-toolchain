from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any
from uuid import uuid4

from .project_context import get_project_context
from .utils.time_utils import now_compact, now_iso


class LegacyMigrationError(RuntimeError):
    def __init__(self, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.error_kind = error_kind


_DIRECTORY_MAPPINGS = (
    ("hardwork", Path("hardwork"), Path("hardwork")),
    ("memory", Path("data") / "memory", Path("memory")),
    ("logs", Path("data") / "logs", Path("logs")),
    ("artifacts", Path("data") / "artifacts", Path("artifacts")),
)

_FILE_MAPPINGS = (
    ("config", Path("data") / "project_config.json", Path("project_config.json")),
    ("database", Path("data") / "esp_mcp.sqlite", Path("esp_mcp.sqlite")),
)


def _is_within(path: Path, root: Path) -> bool:
    return root in (path, *path.parents)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_source(source_root: str | Path, target_root: Path) -> Path:
    source = Path(source_root).expanduser().resolve()
    if not source.exists() or not source.is_dir():
        raise LegacyMigrationError("invalid_legacy_source", f"Legacy source is not an existing directory: {source}")
    if _is_within(source, target_root) or _is_within(target_root, source):
        raise LegacyMigrationError(
            "invalid_legacy_source",
            "Legacy source and the active project data directory must not contain one another.",
        )
    return source


def _source_files(source_root: Path, target_root: Path) -> list[dict[str, Any]]:
    candidates: list[tuple[str, Path, Path]] = []
    for category, source_relative, destination_relative in _DIRECTORY_MAPPINGS:
        source_dir = source_root / source_relative
        if not source_dir.exists() or not source_dir.is_dir():
            continue
        for source_path in sorted(source_dir.rglob("*")):
            if source_path.is_symlink():
                raise LegacyMigrationError(
                    "unsafe_legacy_source",
                    f"Legacy source contains a symbolic link: {source_path}",
                )
            if source_path.is_file():
                relative = source_path.relative_to(source_dir)
                candidates.append((category, source_path, target_root / destination_relative / relative))

    for category, source_relative, destination_relative in _FILE_MAPPINGS:
        source_path = source_root / source_relative
        if source_path.is_symlink():
            raise LegacyMigrationError(
                "unsafe_legacy_source",
                f"Legacy source contains a symbolic link: {source_path}",
            )
        if source_path.is_file():
            candidates.append((category, source_path, target_root / destination_relative))

    if not candidates:
        raise LegacyMigrationError(
            "legacy_data_not_found",
            "No supported legacy hardwork, memory, logs, artifacts, configuration, or database files were found.",
        )

    records: list[dict[str, Any]] = []
    seen_destinations: set[Path] = set()
    for category, source_path, destination in candidates:
        resolved_source = source_path.resolve()
        resolved_destination = destination.resolve()
        if not _is_within(resolved_source, source_root) or not _is_within(resolved_destination, target_root):
            raise LegacyMigrationError("unsafe_legacy_source", "A migration path escaped its permitted root.")
        if resolved_destination in seen_destinations:
            raise LegacyMigrationError(
                "legacy_destination_collision",
                f"Multiple legacy files map to the same destination: {resolved_destination}",
            )
        seen_destinations.add(resolved_destination)

        source_hash = _sha256(resolved_source)
        action = "copy"
        destination_hash = ""
        if resolved_destination.exists():
            if not resolved_destination.is_file() or resolved_destination.is_symlink():
                action = "conflict"
            else:
                destination_hash = _sha256(resolved_destination)
                action = "identical" if destination_hash == source_hash else "conflict"
        records.append(
            {
                "category": category,
                "source_path": str(resolved_source),
                "destination_path": str(resolved_destination),
                "destination_relative": resolved_destination.relative_to(target_root).as_posix(),
                "bytes": resolved_source.stat().st_size,
                "sha256": source_hash,
                "destination_sha256": destination_hash,
                "action": action,
            }
        )
    return records


def _copy_no_overwrite(source: Path, destination: Path, expected_hash: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with source.open("rb") as source_stream, destination.open("xb") as destination_stream:
            created = True
            shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
        try:
            shutil.copystat(source, destination)
        except OSError:
            pass
        if _sha256(destination) != expected_hash:
            raise OSError(f"Copied file failed SHA-256 verification: {destination}")
    except Exception:
        if created:
            destination.unlink(missing_ok=True)
        raise


def _summary(records: list[dict[str, Any]], copied_files: int) -> dict[str, int]:
    return {
        "discovered_files": len(records),
        "planned_copy_files": sum(record["action"] == "copy" for record in records),
        "identical_files": sum(record["action"] == "identical" for record in records),
        "conflict_files": sum(record["action"] == "conflict" for record in records),
        "copied_files": copied_files,
    }


def _append_audit(path: Path, record: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with temporary.open("xb") as output:
            if path.exists():
                with path.open("rb") as existing:
                    shutil.copyfileobj(existing, output, length=1024 * 1024)
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate_legacy_data(source_root: str | Path, *, confirm: bool = False) -> dict[str, Any]:
    context = get_project_context()
    target_root = Path(context["project_dir"]).resolve()
    source = _normalize_source(source_root, target_root)
    records = _source_files(source, target_root)
    preview_limit = 200

    base_result: dict[str, Any] = {
        "ok": True,
        "source_root": str(source),
        "project_id": context["project_id"],
        "workspace_root": context["workspace_root"],
        "project_dir": str(target_root),
        "dry_run": not confirm,
        "preview": records[:preview_limit],
        "preview_truncated": max(0, len(records) - preview_limit),
    }
    if not confirm:
        base_result.update(
            {
                "status": "preview",
                "summary": _summary(records, 0),
                "audit_path": "",
                "message": "Legacy migration preview completed; no files were written.",
            }
        )
        return base_result

    copied: list[dict[str, Any]] = []
    migration_id = f"migration_{now_compact()}_{uuid4().hex[:8]}"
    audit_path = target_root / "migration_audit.jsonl"
    try:
        for record in records:
            if record["action"] != "copy":
                continue
            destination = Path(record["destination_path"])
            try:
                _copy_no_overwrite(Path(record["source_path"]), destination, record["sha256"])
            except FileExistsError:
                record["action"] = "conflict"
                record["destination_sha256"] = _sha256(destination) if destination.is_file() else ""
                continue
            copied.append(
                {
                    "destination_relative": record["destination_relative"],
                    "destination_path": record["destination_path"],
                    "bytes": record["bytes"],
                    "sha256": record["sha256"],
                }
            )

        summary = _summary(records, len(copied))
        conflicts = [record for record in records if record["action"] == "conflict"]
        identical = [record for record in records if record["action"] == "identical"]
        status = "completed_with_conflicts" if conflicts else "completed"
        audit = {
            "migration_id": migration_id,
            "created_at": now_iso(),
            "status": status,
            "source_root": str(source),
            "project_id": context["project_id"],
            "workspace_root": context["workspace_root"],
            "summary": summary,
            "rollback_manifest": {"files": copied},
            "identical_files": identical,
            "conflicts": conflicts,
        }
        _append_audit(audit_path, audit)
    except Exception as exc:
        rolled_back: list[str] = []
        for copied_record in reversed(copied):
            destination = Path(copied_record["destination_path"])
            destination.unlink(missing_ok=True)
            rolled_back.append(copied_record["destination_relative"])
        return {
            **base_result,
            "ok": False,
            "error_kind": "legacy_migration_failed",
            "recoverable": True,
            "status": "rolled_back",
            "summary": _summary(records, 0),
            "rolled_back_files": rolled_back,
            "message": str(exc),
        }

    base_result.update(
        {
            "migration_id": migration_id,
            "status": status,
            "summary": summary,
            "audit_path": str(audit_path),
            "rollback_manifest": audit["rollback_manifest"],
            "identical_files": identical,
            "conflicts": conflicts,
            "message": "Legacy migration completed without overwriting existing files.",
        }
    )
    return base_result
