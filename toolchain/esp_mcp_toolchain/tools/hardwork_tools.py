from __future__ import annotations

from ..hardwork.hardwork_store import get_item, list_items, search_items, set_item
from ..hardwork.attachment_store import AttachmentError, load_attachment_manifest, store_attachment
from ..hardwork.mapping_writer import commit_mapping, patch_mapping
from ..errors import execution_error


def hardwork_list(kind: str = "all") -> dict:
    return {"ok": True, "items": list_items(kind)}


def hardwork_get(hardwork_id: str) -> dict:
    item = get_item(hardwork_id)
    if item is None:
        return {"ok": False, "error_kind": "hardwork_not_found", "message": f"No hardwork item: {hardwork_id}"}
    return {"ok": True, "item": item}


def hardwork_set(kind: str, title: str, content: str, source: str, confidence: float) -> dict:
    return {"ok": True, "item": set_item(kind=kind, title=title, content=content, source=source, confidence=confidence)}


def hardwork_search(query: str, limit: int = 10) -> dict:
    return {"ok": True, "matches": search_items(query=query, limit=limit)}


def hardwork_upload_attachment(attachment_path: str, document_type: str, title: str = "") -> dict:
    try:
        attachment = store_attachment(attachment_path, document_type, title)
    except AttachmentError as exc:
        return execution_error(exc.error_kind, str(exc), tool="hardwork_upload_attachment")
    return {
        "ok": True,
        "attachment": attachment,
        "review_required": attachment["review_required"],
        "required_action": "review_hardware" if attachment["review_required"] else None,
        "message": "Attachment archived. Read it and submit GPIO and serial mappings." if attachment["review_required"] else "Attachment archived.",
    }


def hardwork_attachment_list() -> dict:
    return {"ok": True, **load_attachment_manifest()}


def hardwork_commit_mapping(
    gpio_entries: list[dict],
    serial_interfaces: list[dict],
    source_attachment_ids: list[str],
    board_summary: str = "",
    unresolved_items: list[str] | None = None,
    confidence: float = 0.8,
) -> dict:
    try:
        result = commit_mapping(
            gpio_entries=gpio_entries,
            serial_interfaces=serial_interfaces,
            source_attachment_ids=source_attachment_ids,
            board_summary=board_summary,
            unresolved_items=unresolved_items,
            confidence=confidence,
        )
    except (TypeError, ValueError) as exc:
        return execution_error("invalid_hardware_mapping", str(exc), tool="hardwork_commit_mapping")
    return {"ok": True, **result, "message": "Hardware mapping committed and hardware tools unlocked."}


def hardwork_mapping_patch(
    gpio_entries: list[dict] | None = None,
    serial_interfaces: list[dict] | None = None,
    source_attachment_ids: list[str] | None = None,
    unresolved_items: list[str] | None = None,
    observation_source: str = "",
) -> dict:
    try:
        result = patch_mapping(
            gpio_entries=gpio_entries,
            serial_interfaces=serial_interfaces,
            source_attachment_ids=source_attachment_ids,
            unresolved_items=unresolved_items,
            observation_source=observation_source,
        )
    except (TypeError, ValueError) as exc:
        return execution_error("invalid_hardware_mapping_patch", str(exc), tool="hardwork_mapping_patch")
    if not result.get("ok"):
        return result
    return {**result, "message": "Hardware mapping updated incrementally without replacing unrelated facts."}
