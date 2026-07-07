from esp_mcp_toolchain.tools.file_tools import esp_file_list


def test_file_declared_not_implemented():
    result = esp_file_list(port="COM1")
    assert result["ok"] is False
    assert result["error_kind"] == "not_implemented"

