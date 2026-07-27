import hashlib
from pathlib import Path

import pytest

from esp_mcp_toolchain.tools import flash_tools


def test_backup_flash_calls_esptool_backend(monkeypatch, isolated_project_context):
    output = isolated_project_context / "backup.bin"
    observed: dict[str, Path] = {}

    def fake_run_read_flash(
        port: str,
        chip: str,
        address: int,
        size: int,
        baud: int,
        output_path,
        staging_dir,
    ):
        observed["staging_dir"] = Path(staging_dir)
        output_path.write_bytes(b"1234")
        return {
            "ok": True,
            "stdout": "read",
            "stderr": "",
            "message": chip,
            "bytes_read": 4,
            "sha256": hashlib.sha256(b"1234").hexdigest(),
        }

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
    assert observed["staging_dir"].parent.name == "backup-staging"
    assert observed["staging_dir"].name.startswith("run_")
    assert not observed["staging_dir"].exists()
    assert result["staging_cleanup_completed"] is True


def test_backup_flash_rejects_external_absolute_path_before_backend(
    monkeypatch,
    isolated_project_context,
):
    outside_parent = isolated_project_context.parent / "outside-backup"
    outside = outside_parent / "backup.bin"
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run for an unsafe output path")
        ),
    )

    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        output_path=str(outside),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_output_path"
    assert not outside_parent.exists()


def test_backup_flash_rejects_parent_escape_before_backend(
    monkeypatch,
    isolated_project_context,
):
    outside = isolated_project_context.parent / "escape.bin"
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run for a parent escape")
        ),
    )

    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        output_path="../escape.bin",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_output_path"
    assert not outside.exists()


def test_backup_flash_rejects_missing_user_parent_without_creating_it(
    monkeypatch,
    isolated_project_context,
):
    target = isolated_project_context / "missing" / "backup.bin"
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not create a user-selected parent")
        ),
    )

    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        output_path=str(target),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_parent_missing"
    assert not target.parent.exists()
    assert not (flash_tools._flash_artifact_root() / "backup-staging").exists()


def test_backup_flash_preserves_existing_final_and_partial_before_backend(
    monkeypatch,
    isolated_project_context,
):
    target = isolated_project_context / "backup.bin"
    partial = target.with_name(f"{target.name}.part")
    target.write_bytes(b"existing-final")
    partial.write_bytes(b"existing-partial")
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run when backup files already exist")
        ),
    )

    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        output_path=str(target),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_output_exists"
    assert target.read_bytes() == b"existing-final"
    assert partial.read_bytes() == b"existing-partial"
    assert not (flash_tools._flash_artifact_root() / "backup-staging").exists()

    target.unlink()
    result = flash_tools.esp_backup_flash(
        port="COM_TEST",
        output_path=str(target),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "backup_partial_exists"
    assert partial.read_bytes() == b"existing-partial"
    assert not (flash_tools._flash_artifact_root() / "backup-staging").exists()


def test_default_backup_rejects_reparse_artifact_root(
    monkeypatch,
    isolated_project_context,
):
    project_data = flash_tools.data_dir()
    outside = isolated_project_context.parent / "outside-artifacts"
    outside.mkdir()
    artifacts = project_data / "artifacts"
    try:
        artifacts.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlink is unavailable on this platform: {exc}")
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run through a reparse artifact root")
        ),
    )

    result = flash_tools.esp_backup_flash(port="COM_TEST", size=4)

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_output_path"
    assert not (outside / "flash").exists()


def test_default_backup_rejects_detected_reparse_component(monkeypatch):
    real_detector = flash_tools._is_reparse_point

    def detect_artifacts(path):
        return path.name == "artifacts" or real_detector(path)

    monkeypatch.setattr(flash_tools, "_is_reparse_point", detect_artifacts)
    monkeypatch.setattr(
        flash_tools,
        "run_read_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run through a reparse component")
        ),
    )

    result = flash_tools.esp_backup_flash(port="COM_TEST", size=4)

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_output_path"
    assert "symbolic link or junction" in result["message"]


def test_flash_run_staging_is_unique_and_project_owned():
    first = flash_tools._prepare_flash_run_dir("restore")
    second = flash_tools._prepare_flash_run_dir("restore")

    try:
        assert first != second
        assert first.parent == second.parent
        assert first.parent.name == "restore-staging"
        assert first.name.startswith("run_")
        assert second.name.startswith("run_")
    finally:
        assert flash_tools._remove_flash_run_dir(first) is None
        assert flash_tools._remove_flash_run_dir(second) is None


def test_restore_cleanup_refuses_reparse_without_following_chmod(monkeypatch, tmp_path):
    staged = tmp_path / "restore.bin"
    staged.write_bytes(b"owned")
    monkeypatch.setattr(
        flash_tools,
        "_is_reparse_point",
        lambda path: path == staged,
    )
    monkeypatch.setattr(
        flash_tools.os,
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("chmod must not follow a reparse staging path")
        ),
    )

    error = flash_tools._remove_restore_staging(staged)

    assert "reparse" in error
    assert staged.read_bytes() == b"owned"


def test_flash_run_cleanup_reports_reparse_inspection_failure(monkeypatch, tmp_path):
    run_dir = tmp_path / "run_locked"
    run_dir.mkdir()
    monkeypatch.setattr(
        flash_tools,
        "_is_reparse_point",
        lambda _path: (_ for _ in ()).throw(PermissionError("inspection denied")),
    )

    error = flash_tools._remove_flash_run_dir(run_dir)

    assert "inspection denied" in error
    assert run_dir.exists()


def test_restore_staging_cleanup_reports_reparse_inspection_failure(
    monkeypatch,
    tmp_path,
):
    staged = tmp_path / "restore.bin"
    staged.write_bytes(b"restore")
    monkeypatch.setattr(
        flash_tools,
        "_is_reparse_point",
        lambda _path: (_ for _ in ()).throw(PermissionError("inspection denied")),
    )

    error = flash_tools._remove_restore_staging(staged)

    assert "inspection denied" in error
    assert staged.exists()


def test_default_backup_path_can_be_restored_with_shared_resolver(monkeypatch):
    payload = b"project-artifact-backup"
    observed: dict[str, object] = {}

    def fake_read_flash(**kwargs):
        target = Path(kwargs["output_path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        return {
            "ok": True,
            "stdout": "read",
            "stderr": "",
            "message": "backup",
            "bytes_read": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }

    def fake_write_flash(**kwargs):
        observed["input_path"] = Path(kwargs["input_path"])
        observed["payload"] = observed["input_path"].read_bytes()
        return {"ok": True, "stdout": "write", "stderr": "", "message": "restore"}

    monkeypatch.setattr(flash_tools, "run_read_flash", fake_read_flash)
    monkeypatch.setattr(flash_tools, "run_write_flash", fake_write_flash)

    backup = flash_tools.esp_backup_flash(port="COM_TEST", size=len(payload))
    restored = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=backup["output_path"],
        expected_sha256=hashlib.sha256(payload).hexdigest(),
        confirm=True,
    )

    assert backup["ok"] is True
    assert restored["ok"] is True
    assert observed["input_path"] != Path(backup["output_path"])
    assert observed["payload"] == payload
    assert not observed["input_path"].exists()
    assert not observed["input_path"].parent.exists()
    assert restored["staging_cleanup_completed"] is True


def test_restore_confirmation_precedes_unsafe_path_resolution(monkeypatch, tmp_path):
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"do-not-read")
    monkeypatch.setattr(
        flash_tools,
        "_resolve_flash_artifact_path",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resolver must not run before confirmation")
        ),
        raising=False,
    )
    monkeypatch.setattr(
        flash_tools,
        "run_write_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run before confirmation")
        ),
    )

    result = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=str(outside),
        confirm=False,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"


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


def test_erase_flash_requires_confirmation_by_default(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("erase backend must not run without confirmation")

    monkeypatch.setattr(flash_tools, "run_erase_flash", fail_if_called)

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


def test_restore_flash_rejects_non_regular_image(isolated_project_context):
    directory = isolated_project_context / "backup-directory"
    directory.mkdir()

    result = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=str(directory),
        confirm=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "restore_image_not_regular"


def test_restore_flash_revalidates_source_before_backend(
    monkeypatch,
    isolated_project_context,
):
    image = isolated_project_context / "backup.bin"
    payload = b"verified-backup-image"
    image.write_bytes(payload)
    initial_digest = hashlib.sha256(payload).hexdigest()
    staged: dict[str, Path] = {}
    real_copy = flash_tools._copy_restore_image

    def copy_then_change_source(source, staging_dir):
        result = real_copy(source, staging_dir)
        staged["path"] = result[0]
        source.write_bytes(b"changed-after-staging")
        return result

    monkeypatch.setattr(
        flash_tools,
        "_copy_restore_image",
        copy_then_change_source,
    )
    monkeypatch.setattr(
        flash_tools,
        "run_write_flash",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("backend must not run after the source changes")
        ),
    )

    result = flash_tools.esp_restore_flash(
        port="COM_TEST",
        input_path=str(image),
        expected_sha256=initial_digest,
        confirm=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "restore_source_changed"
    assert result["staging_cleanup_completed"] is True
    assert not staged["path"].exists()
    assert not staged["path"].parent.exists()


def test_restore_flash_confirmed_calls_esptool_backend(monkeypatch, isolated_project_context):
    image = isolated_project_context / "backup.bin"
    payload = b"verified-backup-image"
    image.write_bytes(payload)
    observed: dict[str, object] = {}

    def fake_run_write_flash(port: str, input_path, chip: str, address: int, baud: int):
        observed["input_path"] = Path(input_path)
        observed["payload"] = observed["input_path"].read_bytes()
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
    assert observed["input_path"] != image
    assert observed["payload"] == payload
    assert not observed["input_path"].exists()
    assert not observed["input_path"].parent.exists()
    assert result["staging_cleanup_completed"] is True
