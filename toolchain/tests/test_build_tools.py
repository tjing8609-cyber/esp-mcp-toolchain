from esp_mcp_toolchain.tools import build_tools


def test_project_build_calls_espidf_backend(monkeypatch):
    def fake_run_idf_build(project_dir, target: str):
        return {"ok": True, "stdout": "built", "stderr": "", "message": str(project_dir)}

    monkeypatch.setattr(build_tools, "run_idf_build", fake_run_idf_build)

    result = build_tools.esp_project_build(project_dir="examples/esp_idf_key_led_buzzer", target="esp32")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_project_build"
    assert result["backend"] == "espidf"
    assert result["target"] == "esp32"
    assert result["stdout"] == "built"


def test_project_build_rejects_unsupported_backend():
    result = build_tools.esp_project_build(backend="unknown")

    assert result["ok"] is False
    assert result["error_kind"] == "unsupported_backend"


def test_project_clean_requires_confirmation_by_default():
    result = build_tools.esp_project_clean(project_dir="examples/esp_idf_key_led_buzzer")

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"
    assert result["tool"] == "esp_project_clean"


def test_project_clean_confirmed_calls_espidf_backend(monkeypatch):
    def fake_run_idf_clean(project_dir, mode: str):
        return {"ok": True, "stdout": "cleaned", "stderr": "", "message": str(project_dir)}

    monkeypatch.setattr(build_tools, "run_idf_clean", fake_run_idf_clean)

    result = build_tools.esp_project_clean(
        project_dir="examples/esp_idf_key_led_buzzer",
        mode="clean",
        confirm=True,
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_project_clean"
    assert result["mode"] == "clean"
    assert result["stdout"] == "cleaned"
