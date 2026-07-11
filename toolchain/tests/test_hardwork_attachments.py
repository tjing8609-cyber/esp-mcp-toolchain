from pathlib import Path

from esp_mcp_toolchain.server import call_tool
from esp_mcp_toolchain.tools.hardwork_tools import (
    hardwork_attachment_list,
    hardwork_commit_mapping,
    hardwork_mapping_patch,
    hardwork_upload_attachment,
)


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-hardware-image"


def _write_png(path: Path, content: bytes = PNG_BYTES) -> Path:
    path.write_bytes(content)
    return path


def test_first_attachment_requires_review_and_blocks_hardware_tools(isolated_project_context):
    source = _write_png(isolated_project_context / "board.png")

    uploaded = hardwork_upload_attachment(str(source), "schematic", "Board schematic")
    blocked = call_tool("esp_port_status")

    assert uploaded["ok"] is True
    assert uploaded["review_required"] is True
    assert uploaded["required_action"] == "review_hardware"
    assert Path(uploaded["attachment"]["stored_path"]).exists()
    assert blocked["error_kind"] == "hardware_context_required"


def test_attachment_deduplicates_by_sha256(isolated_project_context):
    first = _write_png(isolated_project_context / "first.png")
    second = _write_png(isolated_project_context / "second.png")

    first_result = hardwork_upload_attachment(str(first), "schematic")
    second_result = hardwork_upload_attachment(str(second), "schematic")

    assert first_result["attachment"]["attachment_id"] == second_result["attachment"]["attachment_id"]
    assert second_result["attachment"]["duplicate"] is True
    assert len(hardwork_attachment_list()["attachments"]) == 1


def test_attachment_rejects_extension_content_mismatch(isolated_project_context):
    source = isolated_project_context / "fake.pdf"
    source.write_bytes(PNG_BYTES)

    result = hardwork_upload_attachment(str(source), "schematic")

    assert result["ok"] is False
    assert result["error_kind"] == "attachment_extension_mismatch"


def test_commit_mapping_generates_files_and_unlocks_tools(isolated_project_context):
    source = _write_png(isolated_project_context / "pinout.png")
    uploaded = hardwork_upload_attachment(str(source), "pinout")
    attachment_id = uploaded["attachment"]["attachment_id"]

    committed = hardwork_commit_mapping(
        gpio_entries=[
            {
                "gpio": 32,
                "function": "LED",
                "direction": "output",
                "active_level": "low",
                "constraints": "",
                "evidence": "schematic_confirmed",
                "source_location": "pinout.png",
                "confidence": 0.95,
            }
        ],
        serial_interfaces=[
            {
                "interface": "UART0",
                "tx_gpio": 1,
                "rx_gpio": 3,
                "usb_bridge": "unconfirmed",
                "default_baudrate": 115200,
                "constraints": "",
                "evidence": "schematic_confirmed",
                "source_location": "pinout.png",
                "confidence": 0.9,
            }
        ],
        source_attachment_ids=[attachment_id],
        unresolved_items=["USB bridge model"],
        confidence=0.9,
    )

    status = call_tool("esp_port_status")
    assert committed["ok"] is True
    assert Path(committed["mapping_path"]).exists()
    assert committed["review_state"]["status"] == "ready"
    assert status.get("error_kind") != "hardware_context_required"


def test_later_attachment_does_not_reset_ready_review(isolated_project_context):
    first = _write_png(isolated_project_context / "first.png")
    uploaded = hardwork_upload_attachment(str(first), "pinout")
    attachment_id = uploaded["attachment"]["attachment_id"]
    hardwork_commit_mapping(
        gpio_entries=[{"gpio": 1, "function": "TX", "evidence": "schematic_confirmed", "confidence": 0.9}],
        serial_interfaces=[],
        source_attachment_ids=[attachment_id],
    )
    second = _write_png(isolated_project_context / "second.png", PNG_BYTES + b"-second")

    result = hardwork_upload_attachment(str(second), "schematic")

    assert result["review_required"] is False
    assert result["attachment"]["review_recommended"] is True
    assert call_tool("esp_port_status").get("error_kind") != "hardware_context_required"


def test_incremental_patch_adds_gpio_without_replacing_serial(isolated_project_context):
    source = _write_png(isolated_project_context / "base.png")
    uploaded = hardwork_upload_attachment(str(source), "schematic")
    attachment_id = uploaded["attachment"]["attachment_id"]
    hardwork_commit_mapping(
        gpio_entries=[],
        serial_interfaces=[
            {
                "interface": "UART0",
                "tx_gpio": 1,
                "rx_gpio": 3,
                "evidence": "schematic_confirmed",
                "confidence": 0.9,
            }
        ],
        source_attachment_ids=[attachment_id],
    )

    result = hardwork_mapping_patch(
        gpio_entries=[
            {
                "gpio": 32,
                "function": "LED_GREEN",
                "direction": "output",
                "active_level": "low",
                "evidence": "schematic_confirmed",
                "source_location": "base.png LED section",
                "confidence": 0.9,
            }
        ],
        source_attachment_ids=[attachment_id],
        observation_source="later LED question",
    )

    assert result["ok"] is True
    assert result["mapping"]["serial_interfaces"][0]["interface"] == "UART0"
    assert result["mapping"]["gpio_entries"][0]["gpio"] == 32


def test_incremental_patch_upgrades_board_test_evidence(isolated_project_context):
    source = _write_png(isolated_project_context / "base.png")
    uploaded = hardwork_upload_attachment(str(source), "schematic")
    attachment_id = uploaded["attachment"]["attachment_id"]
    hardwork_commit_mapping(
        gpio_entries=[
            {
                "gpio": 25,
                "function": "BUZZER",
                "active_level": "PWM",
                "evidence": "schematic_confirmed",
                "confidence": 0.8,
            }
        ],
        serial_interfaces=[],
        source_attachment_ids=[attachment_id],
    )

    result = hardwork_mapping_patch(
        gpio_entries=[
            {
                "gpio": 25,
                "function": "BUZZER",
                "active_level": "PWM",
                "evidence": "board_test_confirmed",
                "confidence": 1.0,
                "source_location": "real-board three-beep test",
            }
        ],
        observation_source="esp_exec_code run_id test",
    )

    entry = result["mapping"]["gpio_entries"][0]
    assert entry["evidence"] == "board_test_confirmed"
    assert entry["confidence"] == 1.0
    assert "real-board three-beep test" in entry["source_location"]


def test_incremental_patch_rejects_conflict_atomically(isolated_project_context):
    source = _write_png(isolated_project_context / "base.png")
    uploaded = hardwork_upload_attachment(str(source), "schematic")
    attachment_id = uploaded["attachment"]["attachment_id"]
    hardwork_commit_mapping(
        gpio_entries=[
            {
                "gpio": 32,
                "function": "LED_GREEN",
                "active_level": "low",
                "evidence": "schematic_confirmed",
                "confidence": 0.9,
            }
        ],
        serial_interfaces=[],
        source_attachment_ids=[attachment_id],
    )

    result = hardwork_mapping_patch(
        gpio_entries=[
            {
                "gpio": 32,
                "function": "LED_GREEN",
                "active_level": "high",
                "evidence": "board_test_confirmed",
                "confidence": 1.0,
            }
        ],
        observation_source="conflicting observation",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "hardware_mapping_conflict"
    assert result["mapping"]["gpio_entries"][0]["active_level"] == "low"
