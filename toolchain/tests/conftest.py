from pathlib import Path

import pytest

from esp_mcp_toolchain.project_context import clear_project_context, select_project_context


@pytest.fixture(autouse=True)
def isolated_project_context(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "examples" / "esp_idf_key_led_buzzer").mkdir(parents=True)
    monkeypatch.setenv("ESP_MCP_DATA_ROOT", str(tmp_path / "project-data"))
    select_project_context(workspace)
    yield workspace
    clear_project_context()
