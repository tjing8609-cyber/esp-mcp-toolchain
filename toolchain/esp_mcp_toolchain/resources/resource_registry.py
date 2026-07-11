from __future__ import annotations

import json

from ..config import get_selected_port
from ..paths import project_root
from ..project_context import get_project_context, project_context_status
from ..hardwork.attachment_store import load_attachment_manifest
from ..hardwork.review_state import load_review_state
from ..tools.hardwork_tools import hardwork_get
from ..tools.log_tools import esp_logs_latest
from ..tools.memory_tools import memory_search


RESOURCES = [
    {"uri": "esp://project/config", "name": "Project config", "mimeType": "application/json"},
    {"uri": "esp://project/status", "name": "Project status", "mimeType": "application/json"},
    {"uri": "esp://ports/selected", "name": "Selected port", "mimeType": "application/json"},
    {"uri": "esp://logs/latest", "name": "Latest run log", "mimeType": "application/json"},
    {"uri": "esp://hardwork/index", "name": "Hardware context index", "mimeType": "application/json"},
    {"uri": "esp://hardwork/gpio-map", "name": "GPIO map", "mimeType": "text/markdown"},
    {"uri": "esp://hardwork/serial-interface", "name": "Serial interface", "mimeType": "text/markdown"},
    {"uri": "esp://hardwork/attachments", "name": "Hardware attachments", "mimeType": "application/json"},
    {"uri": "esp://memory/recent", "name": "Recent memory", "mimeType": "application/json"},
    {"uri": "esp://tools/directory", "name": "Tools directory", "mimeType": "application/json"},
    {"uri": "esp://tools/registry", "name": "Registered tools", "mimeType": "application/json"},
]


def list_resources() -> list[dict]:
    return RESOURCES


def text_result(uri: str, text: str, mime_type: str = "text/plain") -> dict:
    return {"contents": [{"uri": uri, "mimeType": mime_type, "text": text}]}


def tools_directory_manifest() -> dict:
    tools_dir = project_root() / "toolchain" / "esp_mcp_toolchain" / "tools"
    files = []
    for path in sorted(tools_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        files.append({"name": path.name, "path": str(path.relative_to(project_root())).replace("\\", "/")})
    return {"ok": True, "tools_dir": str(tools_dir.relative_to(project_root())).replace("\\", "/"), "files": files}


def registered_tools_manifest() -> dict:
    from ..server import TOOL_REGISTRY

    tools = []
    for name, (spec, _func) in sorted(TOOL_REGISTRY.items()):
        tools.append({"name": name, "description": spec.description, "inputSchema": spec.input_schema})
    return {"ok": True, "count": len(tools), "tools": tools}


def read_resource(uri: str) -> dict:
    context_free = {"esp://project/status", "esp://tools/directory", "esp://tools/registry"}
    if uri not in context_free and get_project_context(required=False) is None:
        return text_result(
            uri,
            json.dumps(
                {
                    "ok": False,
                    "error_kind": "project_context_required",
                    "message": "Call project_context_select with the current Codex workspace root.",
                },
                ensure_ascii=False,
            ),
            "application/json",
        )
    if uri == "esp://ports/selected":
        return text_result(uri, json.dumps({"selected_port": get_selected_port()}, ensure_ascii=False), "application/json")
    if uri == "esp://logs/latest":
        return text_result(uri, json.dumps(esp_logs_latest(), ensure_ascii=False), "application/json")
    if uri == "esp://memory/recent":
        return text_result(uri, json.dumps(memory_search("", limit=20), ensure_ascii=False), "application/json")
    if uri == "esp://hardwork/gpio-map":
        item = hardwork_get("gpio_map")
        return text_result(uri, item.get("item", {}).get("content", ""), "text/markdown")
    if uri == "esp://hardwork/serial-interface":
        item = hardwork_get("serial_interface")
        return text_result(uri, item.get("item", {}).get("content", ""), "text/markdown")
    if uri == "esp://project/config":
        return text_result(uri, json.dumps({"selected_port": get_selected_port()}, ensure_ascii=False), "application/json")
    if uri == "esp://project/status":
        status = project_context_status()
        if status.get("ok"):
            status["hardware_review"] = load_review_state()
        return text_result(uri, json.dumps(status, ensure_ascii=False), "application/json")
    if uri == "esp://hardwork/attachments":
        return text_result(uri, json.dumps(load_attachment_manifest(), ensure_ascii=False), "application/json")
    if uri == "esp://hardwork/index":
        return text_result(uri, json.dumps(hardwork_get("index"), ensure_ascii=False), "application/json")
    if uri == "esp://tools/directory":
        return text_result(uri, json.dumps(tools_directory_manifest(), ensure_ascii=False), "application/json")
    if uri == "esp://tools/registry":
        return text_result(uri, json.dumps(registered_tools_manifest(), ensure_ascii=False), "application/json")
    return text_result(uri, json.dumps({"ok": False, "error_kind": "resource_not_found"}, ensure_ascii=False), "application/json")
