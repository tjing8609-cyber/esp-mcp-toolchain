from esp_mcp_toolchain.tools import flash_tools
import hashlib


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


def test_restore_flash_requires_confirmation_by_default():
    result = flash_tools.esp_restore_flash(port="COM_TEST", input_path="backup.bin")

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"
    assert result["tool"] == "esp_restore_flash"


def test_restore_flash_rejects_hash_mismatch(isolated_project_context):
    image = isolated_project_context / "backup.bin"
    image.write_bytes(b"backup-image")

    result = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=str(image),
        expected_sha256="0" * 64,
        confirm=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "restore_hash_mismatch"


def test_restore_flash_confirmed_calls_esptool_backend(monkeypatch, isolated_project_context):
    image = isolated_project_context / "backup.bin"
    payload = b"verified-backup-image"
    image.write_bytes(payload)

    def fake_run_write_flash(port: str, input_path, chip: str, address: int, baud: int):
        assert input_path == image
        return {"ok": True, "stdout": "restored", "stderr": "", "message": chip}

    monkeypatch.setattr(flash_tools, "run_write_flash", fake_run_write_flash)
    expected = hashlib.sha256(payload).hexdigest()

    result = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=str(image),
        expected_sha256=expected,
        confirm=True,
    )

    assert result["ok"] is True
    assert result["tool_name"] == "esp_restore_flash"
    assert result["bytes_written"] == len(payload)
    assert result["sha256"] == expected
