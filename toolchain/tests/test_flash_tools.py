from esp_mcp_toolchain.tools.flash_tools import esp_flash_firmware


def test_flash_declared_not_implemented():
    result = esp_flash_firmware(port="COM1")
    assert result["ok"] is True
    assert result["implemented"] is False
    assert result["tool_name"] == "esp_flash_firmware"
    assert result["tools名称"] == "esp_flash_firmware"
    assert result["error_kind"] == "not_implemented"
