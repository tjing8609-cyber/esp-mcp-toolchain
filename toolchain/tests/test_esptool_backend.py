import hashlib
from pathlib import Path

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
    partial = Path(observed["command"][-1])
    assert partial.parent == tmp_path
    assert partial.name.startswith("backup.bin.")
    assert partial.name.endswith(".part")
    assert not partial.exists()
    assert result["partial_path"] == str(partial)
    assert result["partial_cleanup_completed"] is True
    assert result["sha256"] == hashlib.sha256(b"data").hexdigest()
    assert result["bytes_read"] == 4
    assert result["output_path"] == str(output)
    assert observed["working_dir"] == tmp_path
    assert observed["idf_path"] == idf_path
    assert observed["timeout_s"] == 240


def test_read_flash_rejects_existing_target_before_command(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    output.write_bytes(b"existing")

    def fake_run(*_args, **_kwargs):
        raise AssertionError("command must not run over an existing target")

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(port="COM_TEST", output_path=output, size=16)

    assert result["ok"] is False
    assert result["error_kind"] == "backup_output_exists"
    assert output.read_bytes() == b"existing"
    assert not output.with_name("backup.bin.part").exists()


def test_read_flash_rejects_missing_parent_before_command(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "missing" / "backup.bin"
    monkeypatch.setattr(
        esptool_backend,
        "_run_idf_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not create a caller-selected parent")
        ),
    )

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=16,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_parent_missing"
    assert not output.parent.exists()


def test_read_flash_rejects_existing_partial_without_deleting_it(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    partial = output.with_name("backup.bin.part")
    partial.write_bytes(b"existing-partial")
    monkeypatch.setattr(
        esptool_backend,
        "_run_idf_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("command must not run over an existing partial")
        ),
    )

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=16,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_partial_exists"
    assert partial.read_bytes() == b"existing-partial"


def test_read_flash_timeout_removes_only_its_partial(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    observed_partial: dict[str, Path] = {}

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        partial = Path(command[-1])
        observed_partial["path"] = partial
        partial.write_bytes(b"partial")
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
    assert not output.exists()
    assert not observed_partial["path"].exists()


def test_read_flash_publish_conflict_does_not_overwrite_racing_target(
    monkeypatch,
    tmp_path,
):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"new-backup")
        output.write_bytes(b"racing-existing-target")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=len(b"new-backup"),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_publish_conflict"
    assert output.read_bytes() == b"racing-existing-target"
    assert Path(result["partial_path"]).read_bytes() == b"new-backup"
    assert result["recovery_path"] == result["partial_path"]
    assert result["partial_cleanup_completed"] is False


def test_read_flash_preserves_complete_image_when_atomic_publish_is_unsupported(
    monkeypatch,
    tmp_path,
):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"new-backup")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)
    monkeypatch.setattr(
        esptool_backend.os,
        "link",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("hard links are unsupported")
        ),
    )

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=len(b"new-backup"),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_publish_failed"
    assert not output.exists()
    assert Path(result["recovery_path"]).read_bytes() == b"new-backup"
    assert result["partial_cleanup_completed"] is False


def test_read_flash_falls_back_when_follow_symlinks_keyword_is_unsupported(
    monkeypatch,
    tmp_path,
):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    real_link = esptool_backend.os.link

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"new-backup")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    def compatibility_link(source, target, **kwargs):
        if kwargs:
            raise NotImplementedError("follow_symlinks is unavailable")
        return real_link(source, target)

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)
    monkeypatch.setattr(esptool_backend.os, "link", compatibility_link)

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=len(b"new-backup"),
    )

    assert result["ok"] is True
    assert output.read_bytes() == b"new-backup"
    assert result["partial_cleanup_completed"] is True


def test_read_flash_uses_project_staging_and_revalidates_output_parent(
    monkeypatch,
    tmp_path,
):
    _prepare_backend(monkeypatch, tmp_path)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    output = output_dir / "backup.bin"
    staging_dir = tmp_path / "project-staging"
    staging_dir.mkdir()
    moved_output_dir = tmp_path / "moved-output"

    def fake_run(command, working_dir, _idf_path, _timeout_s):
        assert working_dir == staging_dir
        Path(command[-1]).write_bytes(b"new-backup")
        output_dir.rename(moved_output_dir)
        output_dir.mkdir()
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        staging_dir=staging_dir,
        size=len(b"new-backup"),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_output_parent_changed"
    assert not output.exists()
    recovery = Path(result["recovery_path"])
    assert recovery.parent == staging_dir
    assert recovery.read_bytes() == b"new-backup"


def test_read_flash_reports_cleanup_failure_after_safe_publish(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        Path(command[-1]).write_bytes(b"new-backup")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)
    monkeypatch.setattr(
        esptool_backend,
        "_remove_owned_partial",
        lambda _path: "temporary file is locked",
        raising=False,
    )

    result = esptool_backend.run_read_flash(
        port="COM_TEST",
        output_path=output,
        size=len(b"new-backup"),
    )

    assert result["ok"] is True
    assert output.read_bytes() == b"new-backup"
    assert result["partial_cleanup_completed"] is False
    assert result["partial_cleanup_error"] == "temporary file is locked"
    assert Path(result["partial_path"]).exists()


def test_owned_partial_cleanup_reports_symlink_inspection_failure(
    monkeypatch,
    tmp_path,
):
    partial = tmp_path / "backup.part"
    partial.write_bytes(b"backup")
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda _path: (_ for _ in ()).throw(PermissionError("inspection denied")),
    )

    error = esptool_backend._remove_owned_partial(partial)

    assert "inspection denied" in error
    assert partial.exists()


def test_read_flash_rejects_incomplete_output(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)
    output = tmp_path / "backup.bin"
    observed_partial: dict[str, Path] = {}

    def fake_run(command, _working_dir, _idf_path, _timeout_s):
        partial = Path(command[-1])
        observed_partial["path"] = partial
        partial.write_bytes(b"short")
        return {"ok": True, "returncode": 0, "stdout": "read", "stderr": ""}

    monkeypatch.setattr(esptool_backend, "_run_idf_command", fake_run)

    result = esptool_backend.run_read_flash(port="COM_TEST", output_path=output, size=16)

    assert result["ok"] is False
    assert result["error_kind"] == "backup_size_mismatch"
    assert result["expected_bytes"] == 16
    assert result["actual_bytes"] == 5
    assert not output.exists()
    assert not output.with_name("backup.bin.part").exists()
    assert not observed_partial["path"].exists()


def test_erase_flash_uses_managed_process_and_explicit_reset(monkeypatch, tmp_path):
    _idf_path, python_path = _prepare_backend(monkeypatch, tmp_path)
    observed = {}

    def fake_run(command, *, cwd, timeout_s):
        observed.update(command=command, cwd=cwd, timeout_s=timeout_s)
        return {
            "ok": True,
            "returncode": 0,
            "stdout": "erased",
            "stderr": "",
            "process_tree_termination_attempted": False,
            "process_tree_terminated": None,
            "cleanup_completed": True,
            "cleanup_errors": [],
        }

    monkeypatch.setattr(esptool_backend, "run_managed_command", fake_run)

    result = esptool_backend.run_erase_flash(port="COM_TEST")

    assert result["ok"] is True
    assert observed["command"] == [
        str(python_path),
        "-m",
        "esptool",
        "--chip",
        "esp32",
        "-p",
        "COM_TEST",
        "--before",
        "default_reset",
        "--after",
        "hard_reset",
        "erase_flash",
    ]
    assert observed["cwd"] == Path.cwd()
    assert observed["timeout_s"] == 180
    assert result["cleanup_completed"] is True
    assert result["message"] == "Flash erase completed."


def test_erase_flash_maps_managed_timeout_and_preserves_cleanup(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)

    def fake_run(_command, *, cwd, timeout_s):
        assert cwd == Path.cwd()
        assert timeout_s == 7
        return {
            "ok": False,
            "error_kind": "managed_command_timeout",
            "message": "Command timed out after 7 seconds.",
            "returncode": None,
            "stdout": "partial stdout",
            "stderr": "partial stderr",
            "process_tree_termination_attempted": True,
            "process_tree_terminated": False,
            "process_tree_termination_error": "taskkill timed out",
            "cleanup_completed": False,
            "cleanup_errors": ["taskkill timed out"],
        }

    monkeypatch.setattr(esptool_backend, "run_managed_command", fake_run)

    result = esptool_backend.run_erase_flash(
        port="COM_TEST",
        timeout_s=7,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "erase_timeout"
    assert result["message"] == "esptool erase_flash timed out after 7 seconds."
    assert result["stdout"] == "partial stdout"
    assert result["stderr"] == "partial stderr"
    assert result["process_tree_termination_attempted"] is True
    assert result["process_tree_terminated"] is False
    assert result["cleanup_completed"] is False
    assert result["cleanup_errors"] == ["taskkill timed out"]


def test_erase_flash_maps_managed_spawn_failure(monkeypatch, tmp_path):
    _prepare_backend(monkeypatch, tmp_path)

    def fake_run(_command, *, cwd, timeout_s):
        assert cwd == Path.cwd()
        assert timeout_s == 180
        return {
            "ok": False,
            "error_kind": "managed_command_spawn_failed",
            "message": "Access is denied",
            "stdout": "",
            "stderr": "",
            "process_tree_termination_attempted": False,
            "process_tree_terminated": None,
            "cleanup_completed": True,
            "cleanup_errors": [],
        }

    monkeypatch.setattr(esptool_backend, "run_managed_command", fake_run)

    result = esptool_backend.run_erase_flash(port="COM_TEST")

    assert result["ok"] is False
    assert result["error_kind"] == "erase_spawn_failed"
    assert result["message"] == "Access is denied"
    assert result["process_tree_termination_attempted"] is False
    assert result["cleanup_completed"] is True
    assert result["cleanup_errors"] == []
