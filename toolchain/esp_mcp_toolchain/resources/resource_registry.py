from __future__ import annotations

import json

from ..config import get_selected_port
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
    {"uri": "esp://memory/recent", "name": "Recent memory", "mimeType": "application/json"},
]


def list_resources() -> list[dict]:
    return RESOURCES


def text_result(uri: str, text: str, mime_type: str = "text/plain") -> dict:
    return {"contents": [{"uri": uri, "mimeType": mime_type, "text": text}]}


def read_resource(uri: str) -> dict:
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
        return text_result(uri, json.dumps({"ok": True, "phase": "initialization"}, ensure_ascii=False), "application/json")
    if uri == "esp://hardwork/index":
        return text_result(uri, json.dumps(hardwork_get("index"), ensure_ascii=False), "application/json")
    return text_result(uri, json.dumps({"ok": False, "error_kind": "resource_not_found"}, ensure_ascii=False), "application/json")

