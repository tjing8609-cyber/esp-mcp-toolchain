from pathlib import Path
from types import SimpleNamespace

from esp_mcp_toolchain.backends import esptool_backend


def _prepare_backend(monkeypatch, tmp_path):
    idf_path = tmp_path / "esp-idf"
    idf_path.mkdir()
    python_path = tmp_path / "python.exe"
    monkeypatch.setattr(esptool_backend, "_idf_path", lambda: idf_path)
    monkeypatch.setattr(esptool_backend, "_idf_python", lambda: python_path)
    return idf_path, python_path


def test_read_flash_uses_managed_process_and_atomic_output(monkeypatch, tmp_path):
    idf_path, python_path = _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    observed = {}

    def fake_run(command, working_dir, received_idf_path, timeout_s):
        observed.update(
            command=command,
            working_dir=working_dir,
            idf_path=received_idf_path,
            timeout_s=timeout_s,
        )
        Path(command[-1]).write_bytes(b"data")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=4,
    )

    assert result["ok"] is True
    assert output.read_bytes() == b"data"
    assert not output.with_name("backup.bin.part").exists()
    assert observed["command"][0] == str(python_path)
    assert observed["command"][-1].endswith("backup.bin.part")
    assert observed["working_dir"] == tmp_path
    assert observed["idf_path"] == idf_path
    assert observed["timeout_s"] == 240


def test_read_flash_timeout_removes_partial_and_preserves_existing_target(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    output.write_bytes(b"existing")

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"partial")
        return {
            "ok": False,
            "error_kind": "idf_command_timeout",
            "message": "timed out",
            "stdout": "",
            "stderr": "",
        }

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(port="COM_TEST", output_path=output, size=16)

    assert result["ok"] is False
    assert result["error_kind"] == "backup_timeout"
    assert output.read_bytes() == b"existing"
    assert not output.with_name("backup.bin.part").exists()


def test_read_flash_rejects_incomplete_output(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"short")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(port="COM_TEST", output_path=output, size=16)

    assert result["ok"] is False
    assert result["error_kind"] == "backup_size_mismatch"
    assert result["expected_bytes"] == 16
    assert result["actual_bytes"] == 5
    assert not output.exists()
    assert not output.with_name("backup.bin.part").exists()


def test_erase_flash_backend_remains_callable(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    observed = {}

    def fake_run(command, **kwargs):
        observed.update(command=command, kwargs=kwargs)
        return SimpleNamespace(returncode=0, stdout="erased", stderr="")

    monkeypatch.setattr(esptool_backend.subprocess, "run", fake_run)

    result = esptool_backend.run_erase_flash(port="COM_TEST")

    assert result["ok"] is True
    assert "erase_flash" in observed["command"]
    assert observed["kwargs"]["capture_output"] is True
    assert observed["kwargs"]["timeout"] == 180
