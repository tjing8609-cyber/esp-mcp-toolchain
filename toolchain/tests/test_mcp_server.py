import asyncio
import json

from esp_mcp_toolchain.resources.resource_registry import read_resource
from esp_mcp_toolchain.server import call_tool, create_mcp_server


def test_sdk_tools_list():
    tools = asyncio.run(create_mcp_server().list_tools())
    assert any(tool.name == "esp_port_list" for tool in tools)


def test_sdk_resources_list():
    resources = asyncio.run(create_mcp_server().list_resources())
    assert any(str(resource.uri) == "esp://logs/latest" for resource in resources)
    assert any(str(resource.uri) == "esp://tools/directory" for resource in resources)
    assert any(str(resource.uri) == "esp://tools/registry" for resource in resources)


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
