from esp_mcp_toolchain.tools.hardwork_tools import hardwork_get, hardwork_set


def test_hardwork_set_get(tmp_path, monkeypatch):
    from esp_mcp_toolchain.hardwork import hardwork_store

    monkeypatch.setattr(hardwork_store, "hardwork_dir", lambda: tmp_path)
    hardwork_set("gpio_map", "GPIO Map", "# GPIO Map\n", "test", 0.9)
    result = hardwork_get("gpio_map")
    assert result["ok"] is True
    assert "GPIO Map" in result["item"]["content"]
