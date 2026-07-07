from esp_mcp_toolchain.tools.log_tools import esp_logs_get, write_event


def test_write_and_read_log():
    event = write_event("test_tool", "info", "hello")
    result = esp_logs_get(event["run_id"])
    assert result["ok"] is True
    assert result["events"][-1]["message"] == "hello"

