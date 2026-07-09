from esp_mcp_toolchain.backends import mpremote_backend


class FakeCompleted:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_run_remote_file_uses_exec_open(monkeypatch):
    calls = []

    def fake_run_mpremote(args: list[str], port: str, timeout_s: int = 30):
        calls.append((args, port, timeout_s))
        return {"ok": True, "stdout": "ok\n", "stderr": ""}

    monkeypatch.setattr(mpremote_backend, "run_mpremote", fake_run_mpremote)

    result = mpremote_backend.run_remote_file(port="COM_TEST", remote_path="/main.py", timeout_s=7)

    assert result["ok"] is True
    assert calls == [(["exec", "exec(open('/main.py').read())"], "COM_TEST", 7)]


def test_run_mpremote_retries_raw_repl_entry_failure(monkeypatch):
    calls = []

    def fake_run(command, capture_output: bool, text: bool, timeout: int, check: bool):
        calls.append(command)
        if len(calls) == 1:
            return FakeCompleted(1, stderr="mpremote.transport.TransportError: could not enter raw repl")
        return FakeCompleted(0, stdout="ok\n")

    monkeypatch.setattr(mpremote_backend.subprocess, "run", fake_run)
    monkeypatch.setattr(mpremote_backend.time, "sleep", lambda _seconds: None)

    result = mpremote_backend.run_mpremote(["fs", "ls", "/"], port="COM_TEST")

    assert result["ok"] is True
    assert result["stdout"] == "ok\n"
    assert len(calls) == 2
