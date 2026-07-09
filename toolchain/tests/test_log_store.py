from esp_mcp_toolchain.tools.log_tools import esp_logs_get, esp_logs_query, write_event


def test_write_and_read_log():
    event = write_event("test_tool", "info", "hello")
    result = esp_logs_get(event["run_id"])
    assert result["ok"] is True
    assert result["events"][-1]["message"] == "hello"


def test_logs_query_matches_terms_across_event_fields():
    event = write_event(
        "esp_serial_capture",
        "serial",
        "Captured 0 characters from COM3",
        {"raw_path": "data/logs/raw/low_risk_probe_115200.log"},
        source="esp32",
    )

    result = esp_logs_query("low_risk_probe COM3 Captured", limit=5)

    assert result["ok"] is True
    assert result["terms"] == ["low_risk_probe", "COM3", "Captured"]
    assert any(match["run_id"] == event["run_id"] for match in result["matches"])
