from esp_mcp_toolchain.tools.flash_tools import esp_flash_firmware


def test_flash_declared_not_implemented():
    result = esp_flash_firmware(port="COM1")
    assert result["ok"] is False
    assert result["error_kind"] == "not_implemented"

