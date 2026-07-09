from esp_mcp_toolchain.tools.build_tools import esp_project_build


def test_build_declared_not_implemented():
    result = esp_project_build()
    assert result["ok"] is True
    assert result["implemented"] is False
    assert result["tool_name"] == "esp_project_build"
    assert result["tools名称"] == "esp_project_build"
    assert result["error_kind"] == "not_implemented"
