import subprocess

from esp_mcp_toolchain.backends import espidf_backend


def _prepare_idf(tmp_path, monkeypatch):
    idf_path = tmp_path / "idf"
    idf_py = idf_path / "tools" / "idf.py"
    idf_py.parent.mkdir(parents=True)
    idf_py.write_text("# test", encoding="utf-8")
    python_path = tmp_path / "python.exe"
    python_path.write_text("", encoding="utf-8")
    monkeypatch.setattr(espidf_backend, "_idf_path", lambda: idf_path)
    monkeypatch.setattr(espidf_backend, "_idf_python", lambda: python_path)
    return idf_path


def test_build_skips_set_target_when_sdkconfig_matches(tmp_path, monkeypatch):
    idf_path = _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32"\n', encoding="utf-8")
    captured = {}

    def fake_run(command, project_dir, actual_idf_path, timeout_s, **kwargs):
        captured["command"] = command
        assert project_dir == project
        assert actual_idf_path == idf_path
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is True
    assert captured["command"][-1] == "build"
    assert "set-target" not in captured["command"]


def test_build_without_sdkconfig_uses_cache_target_without_set_target(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    captured = {}

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        captured["command"] = command
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is True
    assert captured["command"][-3:] == ["-D", "IDF_TARGET=esp32", "build"]
    assert "set-target" not in captured["command"]
    assert "fullclean" not in captured["command"]
    assert result["configured_target_before"] is None
    assert result["sdkconfig_exists_before"] is False
    assert result["target_change_required"] is False
    assert result["target_change_confirmed"] is False
    assert result["set_target_planned"] is False
    assert result["fullclean_planned"] is False
    assert result["fullclean_authorized"] is False
    assert result["destructive_command_requested"] is False
    assert result["target_change_applied"] is False
    assert result["target_change_may_be_partial"] is False


def test_build_target_mismatch_requires_confirmation_without_spawning(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("target mismatch must be rejected before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "target_change_confirmation_required"
    assert result["configured_target_before"] == "esp32s3"
    assert result["sdkconfig_exists_before"] is True
    assert result["target_change_required"] is True
    assert result["target_change_confirmed"] is False
    assert result["set_target_planned"] is True
    assert result["fullclean_planned"] is True
    assert result["fullclean_authorized"] is False
    assert result["destructive_command_requested"] is False
    assert result["sdkconfig_old_exists_before"] is False
    assert result["sdkconfig_old_overwrite_risk"] is False
    assert "command" not in result


def test_build_malformed_sdkconfig_requires_confirmation_without_spawning(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text("CONFIG_FREERTOS_HZ=1000\n", encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("unknown existing target must be rejected before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "target_change_confirmation_required"
    assert result["configured_target_before"] is None
    assert result["sdkconfig_exists_before"] is True
    assert result["target_change_required"] is True
    assert result["target_change_confirmed"] is False
    assert result["set_target_planned"] is True
    assert result["fullclean_planned"] is True
    assert result["fullclean_authorized"] is False
    assert result["destructive_command_requested"] is False


def test_build_target_change_uses_set_target_only_when_confirmed(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
    captured = {}

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        captured["command"] = command
        (project_dir / "sdkconfig").write_text(
            'CONFIG_IDF_TARGET="esp32"\n',
            encoding="utf-8",
        )
        build = project_dir / "build"
        build.mkdir()
        (build / "CMakeCache.txt").write_text(
            "IDF_TARGET:STRING=esp32\n",
            encoding="utf-8",
        )
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is True
    assert captured["command"][-3:] == ["set-target", "esp32", "build"]
    assert result["configured_target_before"] == "esp32s3"
    assert result["sdkconfig_exists_before"] is True
    assert result["target_change_required"] is True
    assert result["target_change_confirmed"] is True
    assert result["set_target_planned"] is True
    assert result["fullclean_planned"] is True
    assert result["fullclean_authorized"] is True
    assert result["destructive_command_requested"] is True
    assert result["command_started"] is True
    assert result["target_change_applied"] is True
    assert result["target_change_may_be_partial"] is False
    assert result["configured_target_after"] == "esp32"
    assert result["cmake_cache_target_after"] == "esp32"


def test_build_target_change_failure_reports_possible_partial_state(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        return {
            "ok": False,
            "returncode": 2,
            "stdout": "set-target started",
            "stderr": "build failed",
        }

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is False
    assert result["set_target_planned"] is True
    assert result["fullclean_authorized"] is True
    assert result["destructive_command_requested"] is True
    assert result["command_started"] is True
    assert result["target_change_applied"] is False
    assert result["target_change_may_be_partial"] is True
    assert result["configured_target_after"] == "esp32s3"


def test_build_warns_before_set_target_can_replace_existing_sdkconfig_old(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
    (project / "sdkconfig.old").write_text("user backup\n", encoding="utf-8")

    def unexpected_run(*args, **kwargs):
        raise AssertionError("unconfirmed set-target must not start")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["sdkconfig_old_exists_before"] is True
    assert result["sdkconfig_old_overwrite_risk"] is True
    assert any("sdkconfig.old" in item for item in result["target_change_warnings"])


def test_build_missing_sdkconfig_with_stale_cache_requires_fullclean_confirmation(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    build = project / "build"
    build.mkdir()
    (build / "CMakeCache.txt").write_text(
        "IDF_TARGET:STRING=esp32s3\n",
        encoding="utf-8",
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("stale target cache must be rejected before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "target_change_confirmation_required"
    assert result["sdkconfig_exists_before"] is False
    assert result["cmake_cache_exists_before"] is True
    assert result["cmake_cache_target_before"] == "esp32s3"
    assert result["sdkconfig_replacement_required"] is False
    assert result["build_cache_reset_required"] is True
    assert result["target_change_required"] is True
    assert result["set_target_planned"] is False
    assert result["fullclean_planned"] is True
    assert result["fullclean_authorized"] is False


def test_build_confirmed_stale_cache_fullcleans_without_set_target(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    build = project / "build"
    build.mkdir()
    cache = build / "CMakeCache.txt"
    cache.write_text("IDF_TARGET:STRING=esp32s3\n", encoding="utf-8")
    captured = {}

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        captured["command"] = command
        (project_dir / "sdkconfig").write_text(
            'CONFIG_IDF_TARGET="esp32"\n',
            encoding="utf-8",
        )
        cache.write_text("IDF_TARGET:STRING=esp32\n", encoding="utf-8")
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is True
    assert "-D" in captured["command"]
    assert "IDF_TARGET=esp32" in captured["command"]
    assert "fullclean" in captured["command"]
    assert "build" in captured["command"]
    assert "set-target" not in captured["command"]
    assert result["set_target_planned"] is False
    assert result["fullclean_planned"] is True
    assert result["destructive_command_requested"] is True
    assert result["command_started"] is True
    assert result["target_change_applied"] is True


def test_build_matching_sdkconfig_with_stale_cache_fullcleans_without_set_target(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32"\n', encoding="utf-8")
    build = project / "build"
    build.mkdir()
    cache = build / "CMakeCache.txt"
    cache.write_text("IDF_TARGET:STRING=esp32s3\n", encoding="utf-8")
    captured = {}

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        captured["command"] = command
        captured["env_overrides"] = kwargs["env_overrides"]
        cache.write_text("IDF_TARGET:STRING=esp32\n", encoding="utf-8")
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    blocked = espidf_backend.run_idf_build(project, target="esp32")
    assert blocked["ok"] is False
    assert blocked["target_plan"] == "fullclean_build"
    assert blocked["confirmation_required"] is True
    assert "command" not in blocked

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is True
    assert captured["command"][-2:] == ["fullclean", "build"]
    assert "set-target" not in captured["command"]
    assert captured["env_overrides"] == {"IDF_TARGET": "esp32"}
    assert result["set_target_planned"] is False
    assert result["fullclean_planned"] is True
    assert result["target_verified"] is True


def test_build_cache_parser_handles_crlf_and_missing_target(tmp_path):
    project = tmp_path / "project"
    cache = project / "build" / "CMakeCache.txt"
    cache.parent.mkdir(parents=True)
    cache.write_text("OTHER:STRING=x\r\nIDF_TARGET:STRING=esp32\r\n", encoding="utf-8")

    assert espidf_backend._configured_build_target(project) == "esp32"

    cache.write_text("OTHER:STRING=x\r\n", encoding="utf-8")
    assert espidf_backend._configured_build_target(project) is None


def test_non_regular_build_cache_stops_before_target_inspection(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    cache = project / "build" / "CMakeCache.txt"
    cache.mkdir(parents=True)

    def unexpected_run(*args, **kwargs):
        raise AssertionError("inspection failure must stop before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_destructive_build_path"
    assert result["target_plan"] == "inspection_failed"
    assert result["build_path_safety_checked"] is True
    assert result["build_path_safe"] is False
    assert result["build_path_check_error"] == (
        "CMake cache exists but is not a regular file."
    )
    assert result["command_started"] is False
    assert result["side_effects_partial_possible"] is False


def test_build_confirmed_target_change_spawn_error_is_conservatively_partial(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")

    def failed_spawn(*args, **kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", failed_spawn)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "build_spawn_failed"
    assert result["command_started"] is False
    assert result["destructive_command_requested"] is True
    assert result["side_effects_partial_possible"] is True
    assert result["target_change_may_be_partial"] is True


def test_build_success_without_target_postcondition_fails_closed(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")

    def fake_run(command, project_dir, idf_path, timeout_s, **kwargs):
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "target_change_postcondition_failed"
    assert result["target_verified"] is False
    assert result["target_change_applied"] is False
    assert result["side_effects_partial_possible"] is True


def test_destructive_build_refuses_reparse_build_directory_before_spawn(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
    (project / "build").mkdir()
    original_is_reparse = espidf_backend._path_is_reparse
    monkeypatch.setattr(
        espidf_backend,
        "_path_is_reparse",
        lambda path: path.name == "build" or original_is_reparse(path),
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("unsafe build path must be rejected before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_destructive_build_path"
    assert result["build_path_safety_checked"] is True
    assert result["build_path_safe"] is False
    assert result["build_path_reparse_detected"] is True
    assert result["build_path_within_project"] is False
    assert result["command_started"] is False


def test_plain_build_refuses_reparse_build_directory_before_target_inspection(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text(
        'CONFIG_IDF_TARGET="esp32"\n',
        encoding="utf-8",
    )
    build = project / "build"
    build.mkdir()
    (build / "CMakeCache.txt").write_text(
        "IDF_TARGET:STRING=esp32\n",
        encoding="utf-8",
    )
    original_is_reparse = espidf_backend._path_is_reparse
    monkeypatch.setattr(
        espidf_backend,
        "_path_is_reparse",
        lambda path: path.name == "build" or original_is_reparse(path),
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("plain build must reject an unsafe build path before spawn")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_destructive_build_path"
    assert result["target_plan"] == "inspection_failed"
    assert result["build_path_safety_checked"] is True
    assert result["build_path_safe"] is False
    assert result["build_path_reparse_detected"] is True
    assert result["build_path_within_project"] is False
    assert result["command_started"] is False


def test_define_target_build_refuses_reparse_build_directory_before_spawn(
    tmp_path,
    monkeypatch,
):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "build").mkdir()
    original_is_reparse = espidf_backend._path_is_reparse
    monkeypatch.setattr(
        espidf_backend,
        "_path_is_reparse",
        lambda path: path.name == "build" or original_is_reparse(path),
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError(
            "define-target build must reject an unsafe build path before spawn"
        )

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_destructive_build_path"
    assert result["target_plan"] == "inspection_failed"
    assert result["build_path_safety_checked"] is True
    assert result["build_path_safe"] is False
    assert result["build_path_reparse_detected"] is True
    assert result["build_path_within_project"] is False
    assert result["command_started"] is False


def test_destructive_build_rechecks_path_immediately_before_spawn(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    (project / "sdkconfig").write_text('CONFIG_IDF_TARGET="esp32s3"\n', encoding="utf-8")
    calls = 0

    def changing_inspection(project_dir):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "build_path_safety_checked": True,
                "build_path_safe": True,
                "build_path_exists_before": False,
                "build_path_reparse_detected": False,
                "build_path_within_project": True,
                "resolved_build_dir": str(project_dir / "build"),
                "build_path_check_error": None,
            }
        raise ValueError("Build directory is a symbolic link, junction, or reparse point.")

    monkeypatch.setattr(
        espidf_backend,
        "_inspect_destructive_build_path",
        changing_inspection,
    )

    def unexpected_run(*args, **kwargs):
        raise AssertionError("changed build path must be rejected before spawning idf.py")

    monkeypatch.setattr(espidf_backend, "_run_idf_command", unexpected_run)

    result = espidf_backend.run_idf_build(
        project,
        target="esp32",
        confirm_target_change=True,
    )

    assert calls == 2
    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_destructive_build_path"
    assert result["build_path_safe"] is False
    assert result["command_started"] is False


def test_timeout_terminates_process_tree(tmp_path, monkeypatch):
    class FakeProcess:
        pid = 1234
        returncode = None

        def communicate(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("idf.py", timeout)
            self.returncode = -1
            return "partial stdout", "partial stderr"

        def poll(self):
            return self.returncode

    terminated = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(espidf_backend, "_terminate_process_tree", lambda process: terminated.append(process.pid))

    result = espidf_backend._run_idf_command(["python", "idf.py", "build"], tmp_path, tmp_path, 1)

    assert result["ok"] is False
    assert result["error_kind"] == "idf_command_timeout"
    assert terminated == [1234]
    assert result["stdout"] == "partial stdout"
    assert result["command_started"] is True
    assert result["command_completed"] is False


def test_windows_build_env_restores_platform_variables(tmp_path, monkeypatch):
    if espidf_backend.os.name != "nt":
        return
    monkeypatch.delenv("OS", raising=False)
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE", raising=False)
    monkeypatch.delenv("IDF_TOOLS_PATH", raising=False)
    monkeypatch.delenv("IDF_PYTHON_ENV_PATH", raising=False)
    monkeypatch.setenv("WINDIR", r"C:\Windows")

    env = espidf_backend._build_env(tmp_path)

    assert env["OS"] == "Windows_NT"
    assert env["SYSTEMROOT"] == r"C:\Windows"
    assert env["PROCESSOR_ARCHITECTURE"] in {"AMD64", "x86"}
    assert env["IDF_TOOLS_PATH"] == str(tmp_path.parents[1])
    assert env["IDF_PYTHON_ENV_PATH"] == str(espidf_backend._idf_python().parents[1])

