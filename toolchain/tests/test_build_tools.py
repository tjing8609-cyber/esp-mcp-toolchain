from esp_mcp_toolchain.tools.build_tools import esp_project_build


def test_build_declared_not_implemented():
    result = esp_project_build()
    assert result["ok"] is False
    assert result["error_kind"] == "not_implemented"

