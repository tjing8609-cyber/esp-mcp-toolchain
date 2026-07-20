from pathlib import Path
import os
import sys


_SOURCE_ROOT = os.environ.get("ESP_MCP_SOURCE_ROOT")
if _SOURCE_ROOT:
    source_toolchain = str((Path(_SOURCE_ROOT).resolve() / "toolchain"))
    if source_toolchain in sys.path:
        sys.path.remove(source_toolchain)
    sys.path.insert(0, source_toolchain)

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
