from __future__ import annotations

import json
from pathlib import Path

from ..paths import hardwork_dir
from ..utils.time_utils import now_iso
from .attachment_store import load_attachment_manifest
from .hardwork_store import set_item
from .review_state import save_review_state


VALID_EVIDENCE = {"schematic_confirmed", "board_test_confirmed", "model_inference", "unconfirmed"}
EVIDENCE_PRIORITY = {
    "unconfirmed": 0,
    "model_inference": 1,
    "schematic_confirmed": 2,
    "board_test_confirmed": 3,
}
GPIO_CONFLICT_FIELDS = {"direction", "active_level"}
SERIAL_CONFLICT_FIELDS = {"tx_gpio", "rx_gpio", "usb_bridge", "default_baudrate"}


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def mapping_path() -> Path:
    return hardwork_dir() / "index" / "hardware_mapping.json"


def load_mapping() -> dict | None:
    path = mapping_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _identity(entry: dict, kind: str) -> tuple[str, ...]:
    if kind == "gpio_entries":
        gpio = str(entry.get("gpio", "")).strip().lower()
        function = str(entry.get("function", "")).strip().lower()
        if not gpio or not function:
            raise ValueError("GPIO patch entries require gpio and function")
        return gpio, function
    interface = str(entry.get("interface", "")).strip().lower()
    if not interface:
        raise ValueError("Serial patch entries require interface")
    return (interface,)


def _validate_entries(entries: list[dict], kind: str) -> None:
    for index, entry in enumerate(entries):
        try:
            _identity(entry, kind)
        except ValueError as exc:
            raise ValueError(f"{kind}[{index}]: {exc}") from exc
        evidence = entry.get("evidence", "unconfirmed")
        if evidence not in VALID_EVIDENCE:
            raise ValueError(f"{kind}[{index}].evidence must be one of {sorted(VALID_EVIDENCE)}")
        confidence = float(entry.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{kind}[{index}].confidence must be between 0 and 1")


def _meaningful(value: object) -> bool:
    return value not in (None, "", [])


def _merge_source_location(existing: object, incoming: object) -> str:
    values = []
    for raw in (existing, incoming):
        for value in str(raw or "").split("; "):
            if value and value not in values:
                values.append(value)
    return "; ".join(values)


def _merge_entry(existing: dict, incoming: dict, kind: str, observation_source: str) -> tuple[dict, list[dict]]:
    conflicts = []
    conflict_fields = GPIO_CONFLICT_FIELDS if kind == "gpio_entries" else SERIAL_CONFLICT_FIELDS
    for field in conflict_fields:
        old_value = existing.get(field)
        new_value = incoming.get(field)
        if _meaningful(old_value) and _meaningful(new_value) and str(old_value).lower() != str(new_value).lower():
            conflicts.append(
                {
                    "kind": kind,
                    "identity": _identity(existing, kind),
                    "field": field,
                    "existing": old_value,
                    "incoming": new_value,
                }
            )
    if conflicts:
        return existing, conflicts

    merged = dict(existing)
    for key, value in incoming.items():
        if key in {"evidence", "confidence", "source_location"} or not _meaningful(value):
            continue
        if not _meaningful(merged.get(key)):
            merged[key] = value
    old_evidence = existing.get("evidence", "unconfirmed")
    new_evidence = incoming.get("evidence", "unconfirmed")
    merged["evidence"] = max((old_evidence, new_evidence), key=lambda value: EVIDENCE_PRIORITY[value])
    merged["confidence"] = max(float(existing.get("confidence", 0.0)), float(incoming.get("confidence", 0.0)))
    merged["source_location"] = _merge_source_location(existing.get("source_location"), incoming.get("source_location"))
    if observation_source:
        merged["observation_source"] = _merge_source_location(existing.get("observation_source"), observation_source)
    merged["updated_at"] = now_iso()
    return merged, []


def _merge_entries(existing_entries: list[dict], incoming_entries: list[dict], kind: str, observation_source: str):
    merged = [dict(entry) for entry in existing_entries]
    positions = {_identity(entry, kind): index for index, entry in enumerate(merged)}
    conflicts = []
    for incoming in incoming_entries:
        identity = _identity(incoming, kind)
        position = positions.get(identity)
        if position is None:
            item = dict(incoming)
            item["updated_at"] = now_iso()
            if observation_source:
                item["observation_source"] = observation_source
            positions[identity] = len(merged)
            merged.append(item)
            continue
        updated, entry_conflicts = _merge_entry(merged[position], incoming, kind, observation_source)
        conflicts.extend(entry_conflicts)
        merged[position] = updated
    return merged, conflicts


def _gpio_markdown(entries: list[dict], sources: list[str], unresolved: list[str]) -> str:
    lines = [
        "# GPIO Map",
        "",
        f"Sources: {', '.join(sources) or 'not specified'}",
        "",
        "| GPIO | Function | Direction | Active level | Boot or mux constraints | Evidence | Source location | Confidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(
            "| " + " | ".join(
                _cell(entry.get(key, ""))
                for key in (
                    "gpio",
                    "function",
                    "direction",
                    "active_level",
                    "constraints",
                    "evidence",
                    "source_location",
                    "confidence",
                )
            ) + " |"
        )
    lines.extend(["", "## Unresolved", ""])
    lines.extend(f"- {_cell(item)}" for item in unresolved)
    if not unresolved:
        lines.append("- None recorded.")
    return "\n".join(lines) + "\n"


def _serial_markdown(entries: list[dict], sources: list[str], unresolved: list[str]) -> str:
    lines = [
        "# Serial Interface",
        "",
        f"Sources: {', '.join(sources) or 'not specified'}",
        "",
        "| Interface | TX GPIO | RX GPIO | USB bridge | Default baudrate | Constraints | Evidence | Source location | Confidence |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for entry in entries:
        lines.append(
            "| " + " | ".join(
                _cell(entry.get(key, ""))
                for key in (
                    "interface",
                    "tx_gpio",
                    "rx_gpio",
                    "usb_bridge",
                    "default_baudrate",
                    "constraints",
                    "evidence",
                    "source_location",
                    "confidence",
                )
            ) + " |"
        )
    lines.extend(["", "## Unresolved", ""])
    lines.extend(f"- {_cell(item)}" for item in unresolved)
    if not unresolved:
        lines.append("- None recorded.")
    return "\n".join(lines) + "\n"


def commit_mapping(
    gpio_entries: list[dict],
    serial_interfaces: list[dict],
    source_attachment_ids: list[str],
    board_summary: str = "",
    unresolved_items: list[str] | None = None,
    confidence: float = 0.8,
) -> dict:
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    if not gpio_entries and not serial_interfaces:
        raise ValueError("At least one GPIO or serial mapping entry is required")
    _validate_entries(gpio_entries, "gpio_entries")
    _validate_entries(serial_interfaces, "serial_interfaces")

    known_ids = {item["attachment_id"] for item in load_attachment_manifest().get("attachments", [])}
    missing = sorted(set(source_attachment_ids) - known_ids)
    if missing:
        raise ValueError(f"Unknown source attachment ids: {', '.join(missing)}")
    if not source_attachment_ids:
        raise ValueError("source_attachment_ids must contain at least one uploaded attachment")

    unresolved = unresolved_items or []
    source = ",".join(source_attachment_ids)
    gpio_item = set_item(
        "gpio_map", "GPIO Map", _gpio_markdown(gpio_entries, source_attachment_ids, unresolved), source, confidence
    )
    serial_item = set_item(
        "serial_interface",
        "Serial Interface",
        _serial_markdown(serial_interfaces, source_attachment_ids, unresolved),
        source,
        confidence,
    )
    if board_summary:
        set_item("board_summary", "Board Summary", f"# Board Summary\n\n{board_summary.strip()}\n", source, confidence)

    mapping = {
        "schema_version": 1,
        "source_attachment_ids": source_attachment_ids,
        "gpio_entries": gpio_entries,
        "serial_interfaces": serial_interfaces,
        "board_summary": board_summary,
        "unresolved_items": unresolved,
        "confidence": confidence,
        "updated_at": now_iso(),
    }
    target_path = mapping_path()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = save_review_state(
        {
            "status": "ready",
            "attachment_count": len(load_attachment_manifest().get("attachments", [])),
            "reviewed_attachment_ids": source_attachment_ids,
            "mapping_path": str(target_path),
        }
    )
    return {
        "gpio_item": gpio_item,
        "serial_item": serial_item,
        "mapping_path": str(target_path),
        "review_state": state,
    }


def patch_mapping(
    gpio_entries: list[dict] | None = None,
    serial_interfaces: list[dict] | None = None,
    source_attachment_ids: list[str] | None = None,
    unresolved_items: list[str] | None = None,
    observation_source: str = "",
) -> dict:
    current = load_mapping()
    if current is None:
        raise ValueError("Hardware mapping is not initialized; call hardwork_commit_mapping after the first attachment review")
    gpio_patch = gpio_entries or []
    serial_patch = serial_interfaces or []
    if not gpio_patch and not serial_patch and not unresolved_items:
        raise ValueError("At least one GPIO, serial, or unresolved item patch is required")
    _validate_entries(gpio_patch, "gpio_entries")
    _validate_entries(serial_patch, "serial_interfaces")

    attachment_ids = source_attachment_ids or []
    known_ids = {item["attachment_id"] for item in load_attachment_manifest().get("attachments", [])}
    missing = sorted(set(attachment_ids) - known_ids)
    if missing:
        raise ValueError(f"Unknown source attachment ids: {', '.join(missing)}")

    merged_gpio, gpio_conflicts = _merge_entries(
        current.get("gpio_entries", []), gpio_patch, "gpio_entries", observation_source
    )
    merged_serial, serial_conflicts = _merge_entries(
        current.get("serial_interfaces", []), serial_patch, "serial_interfaces", observation_source
    )
    conflicts = gpio_conflicts + serial_conflicts
    if conflicts:
        return {"ok": False, "error_kind": "hardware_mapping_conflict", "conflicts": conflicts, "mapping": current}

    sources = list(current.get("source_attachment_ids", []))
    for attachment_id in attachment_ids:
        if attachment_id not in sources:
            sources.append(attachment_id)
    unresolved = list(current.get("unresolved_items", []))
    for item in unresolved_items or []:
        if item not in unresolved:
            unresolved.append(item)
    merged_mapping = {
        **current,
        "source_attachment_ids": sources,
        "gpio_entries": merged_gpio,
        "serial_interfaces": merged_serial,
        "unresolved_items": unresolved,
        "updated_at": now_iso(),
    }
    confidence = float(current.get("confidence", 0.8))
    source = ",".join(sources) or observation_source or "incremental_update"
    set_item("gpio_map", "GPIO Map", _gpio_markdown(merged_gpio, sources, unresolved), source, confidence)
    set_item(
        "serial_interface",
        "Serial Interface",
        _serial_markdown(merged_serial, sources, unresolved),
        source,
        confidence,
    )
    target_path = mapping_path()
    target_path.write_text(json.dumps(merged_mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "mapping_path": str(target_path),
        "mapping": merged_mapping,
        "added_or_updated": {"gpio_entries": len(gpio_patch), "serial_interfaces": len(serial_patch)},
    }

