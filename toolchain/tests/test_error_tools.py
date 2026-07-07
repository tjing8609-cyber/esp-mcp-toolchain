from esp_mcp_toolchain.tools.error_tools import esp_error_parse_text


def test_parse_traceback():
    text = 'Traceback (most recent call last):\n  File "main.py", line 12, in <module>\nNameError: name PWM is not defined'
    result = esp_error_parse_text(text)
    assert result["ok"] is True
    assert result["has_error"] is True
    assert result["file"] == "main.py"
    assert result["line"] == 12
    assert result["exception_type"] == "NameError"

