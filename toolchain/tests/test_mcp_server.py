import asyncio

from esp_mcp_toolchain.server import create_mcp_server


def test_sdk_tools_list():
    tools = asyncio.run(create_mcp_server().list_tools())
    assert any(tool.name == "esp_port_list" for tool in tools)


def test_sdk_resources_list():
    resources = asyncio.run(create_mcp_server().list_resources())
    assert any(str(resource.uri) == "esp://logs/latest" for resource in resources)


def test_sdk_prompts_list():
    prompts = asyncio.run(create_mcp_server().list_prompts())
    assert any(prompt.name == "debug_error" for prompt in prompts)
