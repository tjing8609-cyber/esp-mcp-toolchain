from esp_mcp_toolchain.tools.port_tools import esp_port_list


def test_port_list_shape():
    result = esp_port_list()
    assert result["ok"] is True
    assert "ports" in result

