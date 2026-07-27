from __future__ import annotations

from pathlib import Path

import pytest

from esp_mcp_toolchain.tools import exec_tools, file_tools


@pytest.mark.parametrize("backend", ["mpremote", "raw_repl"])
def test_file_download_relative_path_uses_selected_workspace(
    monkeypatch,
    tmp_path,
    isolated_project_context,
    backend,
):
    payload = b"workspace-download"
    foreign_cwd = tmp_path / "plugin-cache-cwd"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    if backend == "mpremote":

        def fake_download_file(port: str, remote_path: str, local_path: Path):
            local_path.write_bytes(payload)
            return {"ok": True, "stdout": "", "stderr": ""}

        monkeypatch.setattr(file_tools.mpremote_backend, "download_file", fake_download_file)
    else:

        def fake_execute_code(port: str, code: str, timeout_ms: int):
            return {
                "ok": True,
                "stdout": f"{payload!r}\r\n",
                "stderr": "",
                "message": code,
            }

        monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    relative_target = Path("exports") / "downloaded.bin"
    result = file_tools.esp_file_download(
        port="COM_TEST",
        backend=backend,
        remote_path="/probe.bin",
        local_path=str(relative_target),
    )

    expected_target = (isolated_project_context / relative_target).resolve()
    assert result["ok"] is True
    assert Path(result["local_path"]) == expected_target
    assert expected_target.read_bytes() == payload
    assert (foreign_cwd / relative_target).exists() is False


@pytest.mark.parametrize("backend", ["mpremote", "raw_repl"])
def test_file_upload_relative_path_uses_selected_workspace(
    monkeypatch,
    tmp_path,
    isolated_project_context,
    backend,
):
    relative_source = Path("inputs") / "probe.txt"
    workspace_source = isolated_project_context / relative_source
    workspace_source.parent.mkdir(parents=True)
    workspace_source.write_text("workspace-source", encoding="utf-8")

    foreign_cwd = tmp_path / "plugin-cache-cwd"
    foreign_source = foreign_cwd / relative_source
    foreign_source.parent.mkdir(parents=True)
    foreign_source.write_text("foreign-source", encoding="utf-8")
    monkeypatch.chdir(foreign_cwd)

    observed: dict[str, object] = {}
    if backend == "mpremote":

        def fake_upload_file(port: str, local_path: Path, remote_path: str):
            observed["local_path"] = local_path
            observed["payload"] = local_path.read_text(encoding="utf-8")
            return {"ok": True, "stdout": "", "stderr": ""}

        monkeypatch.setattr(file_tools.mpremote_backend, "upload_file", fake_upload_file)
    else:

        def fake_execute_code(port: str, code: str, timeout_ms: int):
            observed["code"] = code
            return {"ok": True, "stdout": "16\r\n", "stderr": "", "message": code}

        monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    result = file_tools.esp_file_upload(
        port="COM_TEST",
        backend=backend,
        local_path=str(relative_source),
        remote_path="/probe.txt",
    )

    assert result["ok"] is True
    assert Path(result["local_path"]) == workspace_source.resolve()
    if backend == "mpremote":
        assert observed["local_path"] == workspace_source.resolve()
        assert observed["payload"] == "workspace-source"
    else:
        assert "_data=b'workspace-source'" in str(observed["code"])


@pytest.mark.parametrize("backend", ["mpremote", "raw_repl"])
@pytest.mark.parametrize("operation", ["upload", "download"])
@pytest.mark.parametrize("path_kind", ["parent_escape", "outside_absolute"])
def test_file_transfer_rejects_paths_outside_selected_workspace(
    monkeypatch,
    tmp_path,
    isolated_project_context,
    backend,
    operation,
    path_kind,
):
    foreign_cwd = tmp_path / "plugin-cache-cwd"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    outside_path = tmp_path / f"{operation}-{backend}-{path_kind}.bin"
    local_path = str(outside_path) if path_kind == "outside_absolute" else f"../{outside_path.name}"
    if operation == "upload":
        outside_path.write_bytes(b"outside-workspace")

    calls: list[str] = []

    def fake_upload_file(port: str, local_path: Path, remote_path: str):
        calls.append("mpremote-upload")
        return {"ok": True, "stdout": "", "stderr": ""}

    def fake_download_file(port: str, remote_path: str, local_path: Path):
        calls.append("mpremote-download")
        local_path.write_bytes(b"unexpected")
        return {"ok": True, "stdout": "", "stderr": ""}

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        calls.append("raw-repl")
        return {
            "ok": True,
            "stdout": "b'unexpected'\r\n" if operation == "download" else "18\r\n",
            "stderr": "",
            "message": code,
        }

    monkeypatch.setattr(file_tools.mpremote_backend, "upload_file", fake_upload_file)
    monkeypatch.setattr(file_tools.mpremote_backend, "download_file", fake_download_file)
    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    if operation == "upload":
        result = file_tools.esp_file_upload(
            port="COM_TEST",
            backend=backend,
            local_path=local_path,
            remote_path="/probe.bin",
        )
    else:
        result = file_tools.esp_file_download(
            port="COM_TEST",
            backend=backend,
            remote_path="/probe.bin",
            local_path=local_path,
        )

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_local_path"
    assert calls == []
    if operation == "download":
        assert outside_path.exists() is False


def test_run_local_file_relative_path_uses_selected_workspace(
    monkeypatch,
    tmp_path,
    isolated_project_context,
):
    relative_source = Path("scripts") / "probe.py"
    workspace_source = isolated_project_context / relative_source
    workspace_source.parent.mkdir(parents=True)
    workspace_source.write_text("print('workspace-source')", encoding="utf-8")

    foreign_cwd = tmp_path / "plugin-cache-cwd"
    foreign_source = foreign_cwd / relative_source
    foreign_source.parent.mkdir(parents=True)
    foreign_source.write_text("print('foreign-source')", encoding="utf-8")
    monkeypatch.chdir(foreign_cwd)

    observed: dict[str, str] = {}

    def fake_exec_code(port: str, backend: str, code: str, capture_ms: int):
        observed["code"] = code
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(exec_tools, "esp_exec_code", fake_exec_code)

    result = exec_tools.esp_run_file(
        port="COM_TEST",
        backend="raw_repl",
        path=str(relative_source),
        path_type="local",
    )

    assert result["ok"] is True
    assert Path(result["path"]) == workspace_source.resolve()
    assert observed["code"] == "print('workspace-source')"


@pytest.mark.parametrize("path_kind", ["parent_escape", "outside_absolute"])
def test_run_local_file_rejects_paths_outside_selected_workspace(
    monkeypatch,
    tmp_path,
    isolated_project_context,
    path_kind,
):
    foreign_cwd = tmp_path / "plugin-cache-cwd"
    foreign_cwd.mkdir()
    monkeypatch.chdir(foreign_cwd)

    outside_path = tmp_path / f"outside-{path_kind}.py"
    outside_path.write_text("print('outside-workspace')", encoding="utf-8")
    local_path = str(outside_path) if path_kind == "outside_absolute" else f"../{outside_path.name}"
    calls: list[str] = []

    def fake_exec_code(port: str, backend: str, code: str, capture_ms: int):
        calls.append(code)
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(exec_tools, "esp_exec_code", fake_exec_code)

    result = exec_tools.esp_run_file(
        port="COM_TEST",
        backend="raw_repl",
        path=local_path,
        path_type="local",
    )

    assert result["ok"] is False
    assert result["error_kind"] == "unsafe_local_path"
    assert calls == []
