from esp_mcp_toolchain.tools.file_tools import (
    esp_file_delete,
    esp_file_download,
    esp_file_list,
    esp_file_read,
    esp_file_upload,
)


def test_file_list_mpremote_parses_files(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_list_files(port: str, remote_dir: str):
        return {"ok": True, "stdout": "ls :/\r\n         139 boot.py\r\n        1024 lib/\r\n", "stderr": ""}

    monkeypatch.setattr(file_tools.mpremote_backend, "list_files", fake_list_files)

    result = esp_file_list(port="COM_TEST")
    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_list"
    assert result["backend"] == "mpremote"
    assert result["files"] == ["boot.py", "lib"]


def test_file_read_mpremote_returns_content(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_read_file(port: str, remote_path: str):
        return {"ok": True, "stdout": "hello\n", "stderr": ""}

    monkeypatch.setattr(file_tools.mpremote_backend, "read_file", fake_read_file)

    result = esp_file_read(port="COM_TEST", remote_path="/boot.py", max_bytes=20)

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["backend"] == "mpremote"
    assert result["content"] == "hello\n"


def test_file_upload_mpremote_calls_backend(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    source = tmp_path / "probe.txt"
    source.write_text("probe", encoding="utf-8")

    def fake_upload_file(port: str, local_path, remote_path: str):
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(file_tools.mpremote_backend, "upload_file", fake_upload_file)

    result = esp_file_upload(port="COM_TEST", local_path=str(source), remote_path="/probe.txt")

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["backend"] == "mpremote"
    assert result["bytes_written"] == 5


def test_file_download_mpremote_calls_backend(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    target = tmp_path / "downloaded.txt"

    def fake_download_file(port: str, remote_path: str, local_path):
        local_path.write_text("probe", encoding="utf-8")
        return {"ok": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(file_tools.mpremote_backend, "download_file", fake_download_file)

    result = esp_file_download(port="COM_TEST", remote_path="/probe.txt", local_path=str(target))

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["backend"] == "mpremote"
    assert result["bytes_written"] == 5


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
    assert isinstance(result["content"], str)
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


def test_file_download_raw_repl_preserves_binary_bytes(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    payload = b"\x00\xff\n\r\n"

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {
            "ok": True,
            "stdout": f"{payload!r}\r\n",
            "stderr": "",
            "message": code,
        }

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)
    target = tmp_path / "downloaded.bin"
    result = esp_file_download(
        port="COM_TEST",
        backend="raw_repl",
        remote_path="/probe.bin",
        local_path=str(target),
    )

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["tool_name"] == "esp_file_download"
    assert result["bytes_written"] == len(payload)
    assert target.read_bytes() == payload


def test_file_download_raw_repl_rejects_truncated_content(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    payload = b"x" * 20001

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {
            "ok": True,
            "stdout": f"{payload!r}\r\n",
            "stderr": "",
            "message": code,
        }

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)
    target = tmp_path / "not-created" / "downloaded.bin"

    result = esp_file_download(
        port="COM_TEST",
        backend="raw_repl",
        remote_path="/large.bin",
        local_path=str(target),
    )

    assert result["ok"] is False
    assert result["error_kind"] == "file_download_truncated"
    assert result["truncated"] is True
    assert target.exists() is False
    assert target.parent.exists() is False


def test_file_download_raw_repl_accepts_exact_size_limit(monkeypatch, tmp_path):
    from esp_mcp_toolchain.tools import file_tools

    payload = bytes(range(256)) * 78 + bytes(range(32))
    assert len(payload) == 20000

    def fake_execute_code(port: str, code: str, timeout_ms: int):
        return {
            "ok": True,
            "stdout": f"{payload!r}\r\n",
            "stderr": "",
            "message": code,
        }

    monkeypatch.setattr(file_tools, "execute_code", fake_execute_code)
    target = tmp_path / "limit.bin"

    result = esp_file_download(
        port="COM_TEST",
        backend="raw_repl",
        remote_path="/limit.bin",
        local_path=str(target),
    )

    assert result["ok"] is True
    assert result["truncated"] is False
    assert result["bytes_written"] == 20000
    assert target.read_bytes() == payload


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


def test_file_delete_confirmed_uses_mpremote(monkeypatch):
    from esp_mcp_toolchain.tools import file_tools

    def fake_run_mpremote(args: list[str], port: str):
        return {"ok": True, "stdout": "", "stderr": "", "args": args}

    monkeypatch.setattr(file_tools.mpremote_backend, "run_mpremote", fake_run_mpremote)

    result = esp_file_delete(port="COM_TEST", remote_path="/probe.txt", confirm=True)

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["backend"] == "mpremote"
    assert result["remote_path"] == "/probe.txt"
