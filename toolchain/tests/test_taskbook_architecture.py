from __future__ import annotations

import asyncio

from esp_mcp_toolchain.server import (
    HARDWARE_GATED_TOOLS,
    TOOL_REGISTRY,
    create_mcp_server,
    list_tool_specs,
)


NEW_TASKBOOK_TOOLS = {
    "esp_program_stop",
    "esp_gpio_status",
    "esp_hardware_info",
    "esp_regression_test",
    "esp_performance_profile",
}


def test_taskbook_tool_architecture_exposes_exactly_48_tools():
    tools = asyncio.run(create_mcp_server().list_tools())
    by_name = {tool.name: tool for tool in tools}

    assert len(tools) == 48
    assert NEW_TASKBOOK_TOOLS <= set(by_name)
    assert len(TOOL_REGISTRY) == 48
    assert len(list_tool_specs()) == 48


def _non_null_variant(schema: dict) -> dict:
    return next(item for item in schema.get("anyOf", [schema]) if item.get("type") != "null")


def test_new_board_tools_are_hardware_gated_and_have_structured_schemas():
    assert NEW_TASKBOOK_TOOLS <= HARDWARE_GATED_TOOLS
    tools = asyncio.run(create_mcp_server().list_tools())
    by_name = {tool.name: tool for tool in tools}

    gpio = by_name["esp_gpio_status"].inputSchema["properties"]
    pins = _non_null_variant(gpio["pins"])
    assert gpio["backend"]["const"] == "raw_repl"
    assert pins["minItems"] == 1
    assert pins["maxItems"] == 32
    assert pins["items"]["minimum"] == 0
    assert pins["items"]["maximum"] == 48
    assert gpio["capture_ms"]["minimum"] == 100
    assert gpio["capture_ms"]["maximum"] == 30000
    assert gpio["allow_program_interrupt"]["default"] is False

    hardware = by_name["esp_hardware_info"].inputSchema["properties"]
    assert hardware["mode"]["enum"] == ["passive", "micropython"]
    assert hardware["allow_program_interrupt"]["default"] is False

    regression = by_name["esp_regression_test"].inputSchema["properties"]
    tests = _non_null_variant(regression["tests"])
    assert tests["minItems"] == 1
    assert tests["maxItems"] == 32
    assert tests["items"]["maxLength"] == 256
    assert regression["confirm_execution"]["default"] is False

    performance = by_name["esp_performance_profile"].inputSchema["properties"]
    assert performance["iterations"]["minimum"] == 1
    assert performance["iterations"]["maximum"] == 50
    assert performance["confirm_repeated_execution"]["default"] is False

    stop = by_name["esp_program_stop"].inputSchema["properties"]
    assert stop["baudrate"]["minimum"] == 1
    assert stop["timeout_ms"]["minimum"] == 100
    assert stop["timeout_ms"]["maximum"] == 30000


def test_static_registry_preserves_tool_bounds_for_directory_resources():
    specs = {entry["name"]: entry["inputSchema"] for entry in list_tool_specs()}

    assert specs["esp_gpio_status"]["properties"]["pins"]["maxItems"] == 32
    assert specs["esp_regression_test"]["properties"]["tests"]["maxItems"] == 32
    assert specs["esp_performance_profile"]["properties"]["iterations"]["maximum"] == 50
    assert specs["esp_error_parse_log"]["properties"]["max_bytes"]["maximum"] == 1048576
    assert specs["esp_gpio_status"]["properties"]["allow_program_interrupt"]["default"] is False
    assert specs["esp_regression_test"]["properties"]["confirm_execution"]["default"] is False
    assert specs["esp_performance_profile"]["properties"]["confirm_repeated_execution"]["default"] is False




def test_final_mcp_surface_is_48_tools_12_resources_12_prompts():
    server = create_mcp_server()

    assert len(asyncio.run(server.list_tools())) == 48
    assert len(asyncio.run(server.list_resources())) == 12
    assert len(asyncio.run(server.list_prompts())) == 12
