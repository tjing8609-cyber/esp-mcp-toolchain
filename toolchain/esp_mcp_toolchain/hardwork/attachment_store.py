from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

from ..paths import hardwork_dir
from ..project_context import get_project_context
from ..utils.time_utils import now_iso
from .review_state import load_review_state, save_review_state


MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
ALLOWED_DOCUMENT_TYPES = {"schematic", "pcb", "pinout", "bom", "datasheet", "serial", "other"}
FILE_TYPES = {
    "png": {"extensions": {".png"}, "mime_type": "image/png"},
    "jpeg": {"extensions": {".jpg", ".jpeg"}, "mime_type": "image/jpeg"},
    "pdf": {"extensions": {".pdf"}, "mime_type": "application/pdf"},
}


class AttachmentError(ValueError):
    def __init__(self, error_kind: str, message: str):
        super().__init__(message)
        self.error_kind = error_kind


def attachment_manifest_path() -> Path:
    return hardwork_dir() / "index" / "attachments.json"


def load_attachment_manifest() -> dict:
    path = attachment_manifest_path()
    if not path.exists():
        return {"attachments": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_attachment_manifest(manifest: dict) -> None:
    path = attachment_manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _is_within(path: Path, root: Path) -> bool:
    return root == path or root in path.parents


def _allowed_source_roots() -> list[Path]:
    context = get_project_context()
    roots = [Path(context["workspace_root"]).resolve(), Path(tempfile.gettempdir()).resolve()]
    configured = os.environ.get("ESP_MCP_ATTACHMENT_ROOTS", "")
    for value in configured.split(os.pathsep):
        if value.strip():
            roots.append(Path(value).expanduser().resolve())
    return roots


def _detect_file_type(path: Path) -> str:
    with path.open("rb") as handle:
        header = handle.read(8)
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if header.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if header.startswith(b"%PDF-"):
        return "pdf"
    raise AttachmentError("unsupported_attachment_type", "Attachment is not a valid PNG, JPEG, or PDF file.")


def _safe_filename(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-.") or "hardware"
    return stem[:80]


def store_attachment(attachment_path: str, document_type: str, title: str = "") -> dict:
    if document_type not in ALLOWED_DOCUMENT_TYPES:
        raise AttachmentError(
            "invalid_document_type",
            f"Unsupported document_type: {document_type}. Allowed: {', '.join(sorted(ALLOWED_DOCUMENT_TYPES))}",
        )
    source = Path(attachment_path).expanduser().resolve()
    if not source.exists() or not source.is_file():
        raise AttachmentError("attachment_unavailable", f"Attachment is unavailable: {source}")
    if not any(_is_within(source, root) for root in _allowed_source_roots()):
        raise AttachmentError(
            "attachment_path_not_allowed",
            "Attachment must be inside the current workspace, system temporary directory, or an explicitly allowed root.",
        )
    size = source.stat().st_size
    if size <= 0:
        raise AttachmentError("empty_attachment", "Attachment is empty.")
    if size > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("attachment_too_large", f"Attachment exceeds {MAX_ATTACHMENT_BYTES} bytes.")

    file_type = _detect_file_type(source)
    extension = source.suffix.lower()
    if extension not in FILE_TYPES[file_type]["extensions"]:
        raise AttachmentError("attachment_extension_mismatch", "Attachment extension does not match its file content.")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = load_attachment_manifest()
    for existing in manifest.get("attachments", []):
        if existing.get("sha256") == digest:
            state = load_review_state()
            return {**existing, "duplicate": True, "review_required": state.get("status") == "pending"}

    attachment_id = f"att_{digest[:16]}"
    destination_dir = hardwork_dir() / "raw"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{_safe_filename(source.name)}-{digest[:12]}{extension}"
    if destination.exists():
        destination = destination_dir / f"{_safe_filename(source.name)}-{uuid4().hex[:12]}{extension}"
    shutil.copy2(source, destination)

    item = {
        "attachment_id": attachment_id,
        "title": title or source.stem,
        "document_type": document_type,
        "file_type": file_type,
        "mime_type": FILE_TYPES[file_type]["mime_type"],
        "original_name": source.name,
        "stored_path": str(destination),
        "sha256": digest,
        "size": size,
        "uploaded_at": now_iso(),
    }
    attachments = [*manifest.get("attachments", []), item]
    save_attachment_manifest({"attachments": attachments, "updated_at": now_iso()})

    state = load_review_state()
    first_upload = state.get("attachment_count", 0) == 0
    next_state = {
        **state,
        "status": "pending" if first_upload else state.get("status", "pending"),
        "attachment_count": len(attachments),
        "first_attachment_id": state.get("first_attachment_id") or attachment_id,
    }
    state = save_review_state(next_state)
    return {
        **item,
        "duplicate": False,
        "first_upload": first_upload,
        "review_required": state.get("status") == "pending",
        "review_recommended": not first_upload,
    }

