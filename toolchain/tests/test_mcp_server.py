import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from esp_mcp_toolchain.resources.resource_registry import read_resource
from esp_mcp_toolchain.server import call_tool, create_mcp_server


def test_sdk_tools_list():
    tools = asyncio.run(create_mcp_server().list_tools())
    assert any(tool.name == "esp_port_list" for tool in tools)
    assert any(tool.name == "project_context_select" for tool in tools)
    assert any(tool.name == "hardwork_upload_attachment" for tool in tools)
    assert any(tool.name == "hardwork_commit_mapping" for tool in tools)
    assert any(tool.name == "esp_serial_monitor_start" for tool in tools)
    assert any(tool.name == "esp_serial_monitor_stop" for tool in tools)
    assert any(tool.name == "esp_serial_monitor_status" for tool in tools)
    assert any(tool.name == "esp_serial_monitor_read" for tool in tools)
    assert any(tool.name == "hardwork_mapping_patch" for tool in tools)
    assert any(tool.name == "esp_restore_flash" for tool in tools)


def _resolve_schema(schema: dict, root: dict) -> dict:
    reference = schema.get("$ref")
    if not reference:
        return schema
    assert reference.startswith("#/$defs/")
    return root["$defs"][reference.rsplit("/", 1)[-1]]


def test_hardware_mapping_tools_expose_structured_entry_schemas():
    tools = asyncio.run(create_mcp_server().list_tools())
    by_name = {tool.name: tool for tool in tools}

    commit_schema = by_name["hardwork_commit_mapping"].inputSchema
    gpio_schema = _resolve_schema(commit_schema["properties"]["gpio_entries"]["items"], commit_schema)
    serial_schema = _resolve_schema(commit_schema["properties"]["serial_interfaces"]["items"], commit_schema)

    assert {"gpio", "function"} <= set(gpio_schema["required"])
    assert "interface" in serial_schema["required"]
    assert gpio_schema["properties"]["evidence"]["enum"] == [
        "schematic_confirmed",
        "board_test_confirmed",
        "model_inference",
        "unconfirmed",
    ]

    patch_schema = by_name["hardwork_mapping_patch"].inputSchema
    patch_gpio_union = patch_schema["properties"]["gpio_entries"]["anyOf"]
    patch_gpio_array = next(option for option in patch_gpio_union if option.get("type") == "array")
    patch_gpio_schema = _resolve_schema(patch_gpio_array["items"], patch_schema)
    assert {"gpio", "function"} <= set(patch_gpio_schema["required"])


def test_sdk_resources_list():
    resources = asyncio.run(create_mcp_server().list_resources())
    assert any(str(resource.uri) == "esp://logs/latest" for resource in resources)
    assert any(str(resource.uri) == "esp://tools/directory" for resource in resources)
    assert any(str(resource.uri) == "esp://tools/registry" for resource in resources)
    assert any(str(resource.uri) == "esp://hardwork/attachments" for resource in resources)
    assert any(str(resource.uri) == "esp://hardwork/mapping" for resource in resources)


def test_sdk_prompts_list():
    prompts = asyncio.run(create_mcp_server().list_prompts())
    assert any(prompt.name == "debug_error" for prompt in prompts)


def test_unimplemented_tool_returns_name_placeholder():
    result = call_tool("esp_file_upload", {"port": "COM_TEST", "backend": "unknown"})

    assert result["ok"] is True
    assert result["implemented"] is False
    assert result["tool_name"] == "esp_file_upload"
    assert any(key.startswith("tools") and value == "esp_file_upload" for key, value in result.items())


def test_tools_resources_describe_directory_and_registry():
    directory = read_resource("esp://tools/directory")
    registry = read_resource("esp://tools/registry")

    directory_payload = json.loads(directory["contents"][0]["text"])
    registry_payload = json.loads(registry["contents"][0]["text"])

    assert directory_payload["ok"] is True
    assert any(file["name"] == "build_tools.py" for file in directory_payload["files"])
    assert registry_payload["ok"] is True
    assert any(tool["name"] == "esp_project_build" for tool in registry_payload["tools"])
    assert any(tool["name"] == "esp_backup_flash" for tool in registry_payload["tools"])


def test_stdio_project_context_persists_across_tool_calls(isolated_project_context):
    async def scenario():
        repository_root = Path(
            os.environ.get("ESP_MCP_SOURCE_ROOT", Path(__file__).resolve().parents[2])
        ).resolve()
        source_toolchain = repository_root / "toolchain"
        server_script = source_toolchain / "mcp_server.py"
        assert server_script.is_file()
        child_env = {
            **os.environ,
            "ESP_MCP_SOURCE_ROOT": str(repository_root),
            "ESP_MCP_DATA_ROOT": str(isolated_project_context.parent / "stdio-project-data"),
        }
        inherited_pythonpath = child_env.get("PYTHONPATH")
        child_env["PYTHONPATH"] = (
            str(source_toolchain)
            if not inherited_pythonpath
            else os.pathsep.join((str(source_toolchain), inherited_pythonpath))
        )
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[str(server_script)],
            cwd=str(repository_root),
            env=child_env,
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                selected = await session.call_tool(
                    "project_context_select",
                    {"workspace_root": str(isolated_project_context)},
                )
                status = await session.call_tool("project_context_status", {})
                hardwork = await session.call_tool("hardwork_list", {"kind": "all"})
                return selected, status, hardwork

    selected, status, hardwork = asyncio.run(scenario())
    selected_payload = json.loads(selected.content[0].text)
    status_payload = json.loads(status.content[0].text)
    hardwork_payload = json.loads(hardwork.content[0].text)

    assert selected_payload["ok"] is True
    assert status_payload["ok"] is True
    assert hardwork_payload["ok"] is True
    assert status_payload["project_id"] == selected_payload["project_id"]
