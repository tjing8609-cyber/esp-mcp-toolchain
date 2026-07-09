from __future__ import annotations

from collections.abc import Callable
from functools import wraps
import inspect
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import __version__
from .errors import execution_error
from .prompts.prompt_registry import PROMPTS, get_prompt
from .resources.resource_registry import RESOURCES, read_resource
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
SERVER_NAME = "esp-mcp-toolchain"
SERVER_INSTRUCTIONS = (
    "Generic ESP development MCP toolchain. Use low-risk inspection tools first, "
    "read hardwork context before hardware decisions, and require explicit user "
    "confirmation for high-risk operations such as flashing, erasing, deleting, "
    "or full clean."
)


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
    "esp_backup_flash": (
        ToolSpec(
            "esp_backup_flash",
            "Back up ESP flash to a local artifact file.",
            {
                "type": "object",
                "properties": {
                    "port": {"type": "string"},
                    "chip": {"type": "string"},
                    "size": {"type": "integer"},
                    "address": {"type": "integer"},
                    "baud": {"type": "integer"},
                    "output_path": {"type": "string"},
                },
                "required": ["port"],
            },
        ),
        flash_tools.esp_backup_flash,
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


def normalize_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    normalized.setdefault("tool", name)
    normalized.setdefault("tool_name", name)
    normalized.setdefault("tools名称", name)
    normalized.setdefault("implemented", normalized.get("error_kind") != "not_implemented")
    normalized.setdefault("data", {})
    normalized.setdefault("message", "")
    normalized.setdefault("suggested_next_actions", [])
    return normalized


def list_tool_specs() -> list[dict[str, Any]]:
    return [spec.to_mcp() for spec, _func in TOOL_REGISTRY.values()]


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if name not in TOOL_REGISTRY:
        return execution_error("unknown_tool", f"Unknown tool: {name}", recoverable=False)
    _spec, func = TOOL_REGISTRY[name]
    try:
        return normalize_tool_result(name, func(**(arguments or {})))
    except TypeError as exc:
        return normalize_tool_result(name, execution_error("invalid_arguments", str(exc), tool=name, recoverable=True))
    except Exception as exc:  # pragma: no cover - last-resort tool boundary
        return normalize_tool_result(name, execution_error("tool_exception", str(exc), tool=name, recoverable=True))


def _resource_text(uri: str) -> str:
    result = read_resource(uri)
    contents = result.get("contents", [])
    if not contents:
        return ""
    return contents[0].get("text", "")


def _prompt_text(name: str) -> str:
    result = get_prompt(name, {})
    messages = result.get("messages", [])
    if not messages:
        return ""
    content = messages[0].get("content", {})
    return content.get("text", "")


def _safe_function_name(prefix: str, value: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in value)
    return f"{prefix}_{safe}".strip("_")


def register_tools(mcp: FastMCP) -> None:
    for name, (spec, func) in TOOL_REGISTRY.items():
        def make_tool(tool_name: str, tool_func: ToolFunc) -> ToolFunc:
            @wraps(tool_func)
            def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
                return normalize_tool_result(tool_name, tool_func(*args, **kwargs))

            wrapper.__signature__ = inspect.signature(tool_func)  # type: ignore[attr-defined]
            return wrapper

        mcp.tool(name=name, description=spec.description)(make_tool(name, func))


def register_resources(mcp: FastMCP) -> None:
    for resource in RESOURCES:
        uri = resource["uri"]

        def make_reader(resource_uri: str) -> Callable[[], str]:
            def reader() -> str:
                return _resource_text(resource_uri)

            reader.__name__ = _safe_function_name("read", resource_uri)
            return reader

        mcp.resource(
            uri,
            name=resource.get("name"),
            description=resource.get("name"),
            mime_type=resource.get("mimeType"),
        )(make_reader(uri))


def register_prompts(mcp: FastMCP) -> None:
    for prompt_name, description in PROMPTS.items():

        def make_prompt(name: str) -> Callable[[], str]:
            def prompt() -> str:
                return _prompt_text(name)

            prompt.__name__ = _safe_function_name("prompt", name)
            return prompt

        mcp.prompt(name=prompt_name, description=description)(make_prompt(prompt_name))


def create_mcp_server() -> FastMCP:
    mcp = FastMCP(
        SERVER_NAME,
        instructions=SERVER_INSTRUCTIONS,
        log_level="WARNING",
    )
    register_tools(mcp)
    register_resources(mcp)
    register_prompts(mcp)
    return mcp


def serve_stdio() -> None:
    create_mcp_server().run(transport="stdio")
