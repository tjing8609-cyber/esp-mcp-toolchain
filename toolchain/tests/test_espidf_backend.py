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

    def fake_run(command, project_dir, actual_idf_path, timeout_s):
        captured["command"] = command
        assert project_dir == project
        assert actual_idf_path == idf_path
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is True
    assert captured["command"][-1] == "build"
    assert "set-target" not in captured["command"]


def test_build_sets_target_when_sdkconfig_is_missing(tmp_path, monkeypatch):
    _prepare_idf(tmp_path, monkeypatch)
    project = tmp_path / "project"
    project.mkdir()
    captured = {}

    def fake_run(command, project_dir, idf_path, timeout_s):
        captured["command"] = command
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(espidf_backend, "_run_idf_command", fake_run)

    result = espidf_backend.run_idf_build(project, target="esp32")

    assert result["ok"] is True
    assert captured["command"][-3:] == ["set-target", "esp32", "build"]


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

