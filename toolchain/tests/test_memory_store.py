from uuid import uuid4

from esp_mcp_toolchain.tools.memory_tools import memory_read, memory_write


def test_memory_write_read():
    key = f"default_baudrate_{uuid4().hex}"
    result = memory_write("project", key, "115200", "device_profile", "test", 0.9)
    assert result["ok"] is True
    read = memory_read("project", key)
    assert read["ok"] is True
    assert read["memory"]["value"] == "115200"

