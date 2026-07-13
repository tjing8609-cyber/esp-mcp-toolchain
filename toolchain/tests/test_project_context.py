from esp_mcp_toolchain.config import get_selected_port, set_selected_port
from esp_mcp_toolchain.database.db import database_path
from esp_mcp_toolchain.paths import data_dir, hardwork_dir, logs_dir, memory_dir
from esp_mcp_toolchain import project_context
from esp_mcp_toolchain.project_context import clear_project_context, project_id_for, select_project_context
from esp_mcp_toolchain.resources.resource_registry import read_resource
from esp_mcp_toolchain.server import call_tool
from esp_mcp_toolchain.tools.hardwork_tools import hardwork_get, hardwork_set
from esp_mcp_toolchain.tools.log_tools import esp_logs_get, write_event
from esp_mcp_toolchain.tools.memory_tools import memory_read, memory_write


def test_project_context_isolates_paths_and_port_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_MCP_DATA_ROOT", str(tmp_path / "storage"))
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_context = select_project_context(first)
    first_data = data_dir()
    first_hardwork = hardwork_dir()
    first_memory = memory_dir()
    first_logs = logs_dir()
    first_database = database_path()
    set_selected_port("COM11", "test")
    hardwork_set("known_issues", "Known Issues", "first-project-only", "test", 1.0)
    memory_write("project", "isolation", "first-project-only", "fact", "test", 1.0)
    first_event = write_event("test", "info", "first-project-only")

    second_context = select_project_context(second)
    second_data = data_dir()
    second_hardwork = hardwork_dir()

    assert first_context["project_id"] == project_id_for(first)
    assert second_context["project_id"] == project_id_for(second)
    assert first_context["project_id"] != second_context["project_id"]
    assert first_data != second_data
    assert first_hardwork != second_hardwork
    assert first_memory != memory_dir()
    assert first_logs != logs_dir()
    assert first_database != database_path()
    assert get_selected_port() is None
    assert hardwork_get("known_issues")["ok"] is False
    assert memory_read("project", "isolation")["ok"] is False
    assert esp_logs_get(first_event["run_id"])["ok"] is False

    select_project_context(first)
    assert get_selected_port() == "COM11"
    assert hardwork_get("known_issues")["item"]["content"] == "first-project-only"
    assert memory_read("project", "isolation")["memory"]["value"] == "first-project-only"
    assert esp_logs_get(first_event["run_id"])["ok"] is True


def test_project_scoped_tools_and_resources_require_context():
    clear_project_context()

    tool_result = call_tool("hardwork_list")
    resource_result = read_resource("esp://hardwork/index")

    assert tool_result["error_kind"] == "project_context_required"
    assert "project_context_required" in resource_result["contents"][0]["text"]


def test_select_context_migrates_legacy_project_without_overwrite(tmp_path, monkeypatch):
    monkeypatch.delenv("ESP_MCP_DATA_ROOT", raising=False)
    workspace = tmp_path / "migration-workspace"
    workspace.mkdir()
    stable = tmp_path / "stable"
    legacy = tmp_path / "legacy"
    project_id = project_id_for(workspace)
    legacy_file = legacy / project_id / "hardwork" / "processed" / "gpio_map.md"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("legacy gpio", encoding="utf-8")
    existing = stable / project_id / "memory" / "memory.jsonl"
    existing.parent.mkdir(parents=True)
    existing.write_text("stable memory", encoding="utf-8")
    legacy_memory = legacy / project_id / "memory" / "memory.jsonl"
    legacy_memory.parent.mkdir(parents=True)
    legacy_memory.write_text("legacy memory", encoding="utf-8")
    monkeypatch.setattr(project_context, "storage_root", lambda: stable)
    monkeypatch.setattr(project_context, "legacy_storage_roots", lambda: [legacy])

    context = select_project_context(workspace)

    assert context["migration"]["copied_files"] == 1
    assert (stable / project_id / "hardwork" / "processed" / "gpio_map.md").read_text(encoding="utf-8") == "legacy gpio"
    assert existing.read_text(encoding="utf-8") == "stable memory"


def test_active_context_survives_in_memory_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("ESP_MCP_DATA_ROOT", str(tmp_path / "stable"))
    workspace = tmp_path / "restart-workspace"
    workspace.mkdir()
    selected = select_project_context(workspace)
    monkeypatch.setattr(project_context, "_ACTIVE_CONTEXT", None)

    restored = project_context.get_project_context()

    assert restored["project_id"] == selected["project_id"]
    assert restored["project_dir"] == selected["project_dir"]
