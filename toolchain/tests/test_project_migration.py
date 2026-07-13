import asyncio
import json

from esp_mcp_toolchain.paths import data_dir
from esp_mcp_toolchain.server import create_mcp_server
from esp_mcp_toolchain.tools import project_tools


def _write_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _legacy_tree(tmp_path):
    source = tmp_path / "legacy-plugin"
    _write_text(source / "hardwork" / "processed" / "gpio_map.md", "legacy gpio")
    _write_text(source / "data" / "memory" / "memory.jsonl", "legacy memory\n")
    _write_text(source / "data" / "logs" / "latest.json", '{"legacy": true}\n')
    _write_bytes(source / "data" / "artifacts" / "flash" / "backup.bin", b"legacy-bin")
    _write_text(source / "data" / "project_config.json", '{"port": "COM9"}\n')
    _write_bytes(source / "data" / "esp_mcp.sqlite", b"legacy-sqlite")
    _write_text(source / "data" / "projects" / "ignored" / "project.json", "must not migrate")
    return source


def test_project_migration_defaults_to_read_only_preview(tmp_path):
    source = _legacy_tree(tmp_path)
    target = data_dir()

    result = project_tools.project_migrate_legacy_data(str(source))

    assert result["ok"] is True
    assert result["implemented"] is True
    assert result["dry_run"] is True
    assert result["summary"] == {
        "discovered_files": 6,
        "planned_copy_files": 6,
        "identical_files": 0,
        "conflict_files": 0,
        "copied_files": 0,
    }
    assert not (target / "hardwork" / "processed" / "gpio_map.md").exists()
    assert not (target / "memory" / "memory.jsonl").exists()
    assert not (target / "migration_audit.jsonl").exists()
    assert all("data\\projects" not in item["source_path"] for item in result["preview"])


def test_confirmed_project_migration_copies_missing_and_preserves_conflicts(tmp_path):
    source = _legacy_tree(tmp_path)
    target = data_dir()
    _write_text(target / "memory" / "memory.jsonl", "stable memory\n")
    _write_text(target / "logs" / "latest.json", '{"legacy": true}\n')

    result = project_tools.project_migrate_legacy_data(str(source), confirm=True)

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["status"] == "completed_with_conflicts"
    assert result["summary"] == {
        "discovered_files": 6,
        "planned_copy_files": 4,
        "identical_files": 1,
        "conflict_files": 1,
        "copied_files": 4,
    }
    assert (target / "hardwork" / "processed" / "gpio_map.md").read_text(encoding="utf-8") == "legacy gpio"
    assert (target / "memory" / "memory.jsonl").read_text(encoding="utf-8") == "stable memory\n"
    assert (target / "artifacts" / "flash" / "backup.bin").read_bytes() == b"legacy-bin"
    assert not (target / "projects" / "ignored" / "project.json").exists()

    audit_path = target / "migration_audit.jsonl"
    assert result["audit_path"] == str(audit_path)
    audit = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert audit["source_root"] == str(source.resolve())
    assert audit["summary"] == result["summary"]
    assert len(audit["rollback_manifest"]["files"]) == 4
    assert audit["conflicts"][0]["destination_relative"] == "memory/memory.jsonl"


def test_confirmed_project_migration_rolls_back_when_audit_write_fails(tmp_path, monkeypatch):
    from esp_mcp_toolchain import project_migration

    source = _legacy_tree(tmp_path)
    target = data_dir()

    def fail_audit(_path, _record):
        raise OSError("audit unavailable")

    monkeypatch.setattr(project_migration, "_append_audit", fail_audit)

    result = project_tools.project_migrate_legacy_data(str(source), confirm=True)

    assert result["ok"] is False
    assert result["error_kind"] == "legacy_migration_failed"
    assert result["status"] == "rolled_back"
    assert len(result["rolled_back_files"]) == 6
    assert not (target / "hardwork" / "processed" / "gpio_map.md").exists()
    assert not (target / "artifacts" / "flash" / "backup.bin").exists()


def test_project_migration_rejects_missing_empty_and_active_project_sources(tmp_path):
    missing = project_tools.project_migrate_legacy_data(str(tmp_path / "missing"))
    empty_source = tmp_path / "empty"
    empty_source.mkdir()
    empty = project_tools.project_migrate_legacy_data(str(empty_source))
    active = project_tools.project_migrate_legacy_data(str(data_dir()))

    assert missing["error_kind"] == "invalid_legacy_source"
    assert empty["error_kind"] == "legacy_data_not_found"
    assert active["error_kind"] == "invalid_legacy_source"


def test_project_migration_mcp_schema_requires_source_and_defaults_to_preview():
    tools = asyncio.run(create_mcp_server().list_tools())
    schema = next(tool.inputSchema for tool in tools if tool.name == "project_migrate_legacy_data")

    assert schema["required"] == ["source_root"]
    assert schema["properties"]["source_root"]["type"] == "string"
    assert schema["properties"]["confirm"] == {
        "default": False,
        "title": "Confirm",
        "type": "boolean",
    }
