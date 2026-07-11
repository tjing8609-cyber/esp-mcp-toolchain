from __future__ import annotations

import json
from pathlib import Path

from ..paths import hardwork_dir
from ..utils.time_utils import now_iso
from .attachment_store import load_attachment_manifest
from .hardwork_store import set_item
from .review_state import save_review_state


VALID_EVIDENCE = {"schematic_confirmed", "board_test_confirmed", "model_inference", "unconfirmed"}


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _validate_entries(entries: list[dict], kind: str) -> None:
    for index, entry in enumerate(entries):
        evidence = entry.get("evidence", "unconfirmed")
        if evidence not in VALID_EVIDENCE:
            raise ValueError(f"{kind}[{index}].evidence must be one of {sorted(VALID_EVIDENCE)}")
        confidence = float(entry.get("confidence", 0.0))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{kind}[{index}].confidence must be between 0 and 1")


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
    mapping_path = hardwork_dir() / "index" / "hardware_mapping.json"
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    state = save_review_state(
        {
            "status": "ready",
            "attachment_count": len(load_attachment_manifest().get("attachments", [])),
            "reviewed_attachment_ids": source_attachment_ids,
            "mapping_path": str(mapping_path),
        }
    )
    return {
        "gpio_item": gpio_item,
        "serial_item": serial_item,
        "mapping_path": str(mapping_path),
        "review_state": state,
    }

