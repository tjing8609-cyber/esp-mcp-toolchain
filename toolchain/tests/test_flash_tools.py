from esp_mcp_toolchain.tools import flash_tools


def test_backup_flash_calls_esptool_backend(monkeypatch, tmp_path):
    output = tmp_path / "backup.bin"

    def fake_run_read_flash(port: str, chip: str, address: int, size: int, baud: int, output_path):
        output_path.write_bytes(b"1234")
        return {"ok": True, "stdout": "read", "stderr": "", "message": chip}

    monkeypatch.setattr(flash_tools, "run_read_flash", fake_run_read_flash)

    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        chip="esp32",
        size=4,
        address=0,
        output_path=str(output),
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_backup_flash"
    assert result["bytes_read"] == 4
    assert result["output_path"] == str(output)


def test_flash_requires_confirmation_by_default():
    result = flash_tools.esp_flash_firmware(port="COM1")

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"
    assert result["tool"] == "esp_flash_firmware"


def test_flash_confirmed_calls_espidf_backend(monkeypatch):
    def fake_run_idf_flash(project_dir, port: str, baud: int):
        return {"ok": True, "stdout": "flashed", "stderr": "", "message": str(project_dir)}

    monkeypatch.setattr(flash_tools, "run_idf_flash", fake_run_idf_flash)

    result = flash_tools.esp_flash_firmware(
        port="COM_TEST",
        project_dir="examples/esp_idf_key_led_buzzer",
        confirm=True,
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_flash_firmware"
    assert result["port"] == "COM_TEST"
    assert result["stdout"] == "flashed"


def test_erase_flash_requires_confirmation_by_default():
    result = flash_tools.esp_erase_flash(port="COM_TEST")

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"
    assert result["tool"] == "esp_erase_flash"


def test_erase_flash_confirmed_calls_esptool_backend(monkeypatch):
    def fake_run_erase_flash(port: str, chip: str):
        return {"ok": True, "stdout": "erased", "stderr": "", "message": chip}

    monkeypatch.setattr(flash_tools, "run_erase_flash", fake_run_erase_flash)

    result = flash_tools.esp_erase_flash(port="COM_TEST", chip="esp32", confirm=True)

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_erase_flash"
    assert result["port"] == "COM_TEST"
    assert result["stdout"] == "erased"
