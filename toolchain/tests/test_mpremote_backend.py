from esp_mcp_toolchain.backends import mpremote_backend


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
    responses = iter(
        [
            {
                "ok": False,
                "returncode": 1,
                "stdout": "",
                "stderr": "mpremote.transport.TransportError: could not enter raw repl",
            },
            {"ok": True, "returncode": 0, "stdout": "ok\n", "stderr": ""},
        ]
    )

    def fake_run_managed(command: list[str], *, timeout_s: int):
        calls.append((command, timeout_s))
        return next(responses)

    monkeypatch.setattr(mpremote_backend, "run_managed_command", fake_run_managed)
    monkeypatch.setattr(mpremote_backend.time, "sleep", lambda _seconds: None)

    result = mpremote_backend.run_mpremote(
        ["fs", "ls", "/"],
        port="COM_TEST",
        timeout_s=9,
    )

    assert result["ok"] is True
    assert result["stdout"] == "ok\n"
    assert len(calls) == 2
    assert all(timeout_s == 9 for _command, timeout_s in calls)
