from esp_mcp_toolchain.tools.file_tools import (
    esp_file_delete,
    esp_file_download,
    esp_file_list,
    esp_file_read,
    esp_file_upload,
)


def test_file_declared_not_implemented():
    result = esp_file_list(port="COM1")
    assert result["ok"] is True
    assert result["implemented"] is False
    assert result["tool_name"] == "esp_file_list"
    assert any(key.startswith("tools") and value == "esp_file_list" for key, value in result.items())
    assert result["error_kind"] == "not_implemented"


def test_file_list_raw_repl_parses_files(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {"ok": True, "stdout": "['boot.py', 'main.py']\r\n", "stderr": "", "message": code}

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    result = file_tools.esp_file_list(port="COM_TEST", backend="raw_repl")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_list"
    assert result["files"] == ["boot.py", "main.py"]


def test_file_read_raw_repl_parses_bytes(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {"ok": True, "stdout": "b'abc\\n'\r\n", "stderr": "", "message": code}

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    result = esp_file_read(port="COM_TEST", backend="raw_repl", remote_path="/boot.py", max_bytes=20)

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_read"
    assert result["content"] == "abc\n"
    assert result["bytes_read"] == 4
    assert result["truncated"] is False


def test_file_upload_raw_repl_writes_bytes(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    source = tmp_path / "probe.txt"
    source.write_text("probe", encoding="utf-8")

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {"ok": True, "stdout": "5\r\n", "stderr": "", "message": code}

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    result = esp_file_upload(
        port="COM_TEST",
        backend="raw_repl",
        local_path=str(source),
        remote_path="/probe.txt",
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_upload"
    assert result["bytes_written"] == 5


def test_file_download_raw_repl_writes_local_file(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    def fake_file_read(port: str, backend: str, remote_path: str, max_bytes: int):
        return {"ok": True, "content": "downloaded", "port": port}

    monkeypatch.setattr(file_tools, "esp_file_read", fake_file_read)
    target = tmp_path / "downloaded.txt"

    result = esp_file_download(
        port="COM_TEST",
        backend="raw_repl",
        remote_path="/probe.txt",
        local_path=str(target),
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_download"
    assert target.read_text(encoding="utf-8") == "downloaded"


def test_file_delete_requires_confirmation_by_default():
    result = esp_file_delete(port="COM_TEST", remote_path="/probe.txt")

    assert result["ok"] is False
    assert result["error_kind"] == "confirmation_required"
    assert result["tool"] == "esp_file_delete"


def test_file_delete_confirmed_uses_raw_repl(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {"ok": True, "stdout": "deleted\r\n", "stderr": "", "message": code}

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)

    result = esp_file_delete(
        port="COM_TEST",
        backend="raw_repl",
        remote_path="/probe.txt",
        confirm=True,
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_delete"
    assert result["remote_path"] == "/probe.txt"
