from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from . import __version__
from .errors import execution_error
from .prompts.prompt_registry import get_prompt, list_prompts
from .resources.resource_registry import list_resources, read_resource
from .schemas import ToolSpec
from .tools import (
    build_tools,
    error_tools,
    exec_tools,
    file_tools,
    flash_tools,
    hardwork_tools,
    log_tools,
    memory_tools,
    port_tools,
    reset_tools,
    serial_tools,
)


ToolFunc = Callable[..., dict[str, Any]]


TOOL_REGISTRY: dict[str, tuple[ToolSpec, ToolFunc]] = {
    "esp_port_list": (
        ToolSpec("esp_port_list", "List local serial ports."),
        port_tools.esp_port_list,
    ),
    "esp_port_select": (
        ToolSpec(
            "esp_port_select",
            "Select the default serial port.",
            {
                "type": "object",
                "properties": {"port": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["port"],
            },
        ),
        port_tools.esp_port_select,
    ),
    "esp_port_status": (
        ToolSpec("esp_port_status", "Read selected port and check whether it can be opened."),
        port_tools.esp_port_status,
    ),
    "esp_serial_capture": (
        ToolSpec("esp_serial_capture", "Capture serial output for a fixed duration."),
        serial_tools.esp_serial_capture,
    ),
    "esp_logs_latest": (
        ToolSpec("esp_logs_latest", "Read the latest tool run summary."),
        log_tools.esp_logs_latest,
    ),
    "esp_logs_get": (
        ToolSpec("esp_logs_get", "Read log events by run_id."),
        log_tools.esp_logs_get,
    ),
    "esp_logs_query": (
        ToolSpec("esp_logs_query", "Search log events."),
        log_tools.esp_logs_query,
    ),
    "esp_error_parse_log": (
        ToolSpec("esp_error_parse_log", "Parse errors from a stored run log."),
        error_tools.esp_error_parse_log,
    ),
    "esp_error_parse_text": (
        ToolSpec("esp_error_parse_text", "Parse errors from text."),
        error_tools.esp_error_parse_text,
    ),
    "hardwork_list": (
        ToolSpec("hardwork_list", "List hardware context documents."),
        hardwork_tools.hardwork_list,
    ),
    "hardwork_get": (
        ToolSpec("hardwork_get", "Read a hardware context document."),
        hardwork_tools.hardwork_get,
    ),
    "hardwork_set": (
        ToolSpec("hardwork_set", "Write a processed hardware context document."),
        hardwork_tools.hardwork_set,
    ),
    "hardwork_search": (
        ToolSpec("hardwork_search", "Search hardware context documents."),
        hardwork_tools.hardwork_search,
    ),
    "memory_write": (
        ToolSpec("memory_write", "Write a stable project-scoped memory item."),
        memory_tools.memory_write,
    ),
    "memory_read": (
        ToolSpec("memory_read", "Read a memory item by namespace and key."),
        memory_tools.memory_read,
    ),
    "memory_search": (
        ToolSpec("memory_search", "Search project-scoped memory items."),
        memory_tools.memory_search,
    ),
    "memory_update": (
        ToolSpec("memory_update", "Update a memory item by id."),
        memory_tools.memory_update,
    ),
    "memory_delete": (
        ToolSpec("memory_delete", "Delete a memory item by id."),
        memory_tools.memory_delete,
    ),
    "esp_project_build": (
        ToolSpec("esp_project_build", "Build an ESP project."),
        build_tools.esp_project_build,
    ),
    "esp_project_clean": (
        ToolSpec("esp_project_clean", "Clean ESP build artifacts."),
        build_tools.esp_project_clean,
    ),
    "esp_flash_firmware": (
        ToolSpec("esp_flash_firmware", "Flash firmware to an ESP board."),
        flash_tools.esp_flash_firmware,
    ),
    "esp_erase_flash": (
        ToolSpec("esp_erase_flash", "Erase ESP flash."),
        flash_tools.esp_erase_flash,
    ),
    "esp_file_upload": (
        ToolSpec("esp_file_upload", "Upload a file to the board."),
        file_tools.esp_file_upload,
    ),
    "esp_file_download": (
        ToolSpec("esp_file_download", "Download a file from the board."),
        file_tools.esp_file_download,
    ),
    "esp_file_list": (
        ToolSpec("esp_file_list", "List board files."),
        file_tools.esp_file_list,
    ),
    "esp_file_read": (
        ToolSpec("esp_file_read", "Read a small board file."),
        file_tools.esp_file_read,
    ),
    "esp_file_delete": (
        ToolSpec("esp_file_delete", "Delete a board file."),
        file_tools.esp_file_delete,
    ),
    "esp_reset": (
        ToolSpec("esp_reset", "Reset the board."),
        reset_tools.esp_reset,
    ),
    "esp_exec_code": (
        ToolSpec("esp_exec_code", "Execute short code through REPL."),
        exec_tools.esp_exec_code,
    ),
    "esp_run_file": (
        ToolSpec("esp_run_file", "Run a remote file or local file on the board."),
        exec_tools.esp_run_file,
    ),
}


def list_tool_specs() -> list[dict[str, Any]]:
    return [spec.to_mcp() for spec, _func in TOOL_REGISTRY.values()]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in TOOL_REGISTRY:
        return execution_error("unknown_tool", f"Unknown tool: {name}", recoverable=False)
    _spec, func = TOOL_REGISTRY[name]
    try:
        return func(**(arguments or {}))
    except TypeError as exc:
        return execution_error("invalid_arguments", str(exc), tool=name, recoverable=True)
    except Exception as exc:  # pragma: no cover - last-resort tool boundary
        return execution_error("tool_exception", str(exc), tool=name, recoverable=True)


def initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
            "resources": {},
            "prompts": {},
            "logging": {},
        },
        "serverInfo": {
            "name": "esp-mcp-toolchain",
            "version": __version__,
        },
    }


def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result = initialize_result()
    elif method == "tools/list":
        result = {"tools": list_tool_specs()}
    elif method == "tools/call":
        structured = call_tool(params.get("name", ""), params.get("arguments") or {})
        result = {
            "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}],
            "structuredContent": structured,
            "isError": structured.get("ok") is False,
        }
    elif method == "resources/list":
        result = {"resources": list_resources()}
    elif method == "resources/read":
        result = read_resource(params.get("uri", ""))
    elif method == "prompts/list":
        result = {"prompts": list_prompts()}
    elif method == "prompts/get":
        result = get_prompt(params.get("name", ""), params.get("arguments") or {})
    elif method == "shutdown":
        result = {}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }

    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve_stdio() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()

