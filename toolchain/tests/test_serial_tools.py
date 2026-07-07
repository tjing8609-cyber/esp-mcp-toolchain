from esp_mcp_toolchain.tools.serial_tools import esp_serial_monitor_status


def test_serial_monitor_status_empty():
    result = esp_serial_monitor_status()
    assert result == {"ok": True, "monitors": []}

