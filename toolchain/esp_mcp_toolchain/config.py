from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .paths import data_dir


def config_path() -> Path:
    return data_dir() / "project_config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def get_selected_port() -> str | None:
    config = load_config()
    return config.get("selected_port") or os.environ.get("ESP_MCP_DEFAULT_PORT")


def set_selected_port(port: str, reason: str = "manual") -> None:
    config = load_config()
    config["selected_port"] = port
    config["selected_port_reason"] = reason
    save_config(config)


def get_default_baudrate() -> int:
    raw = os.environ.get("ESP_MCP_DEFAULT_BAUDRATE", "115200")
    try:
        return int(raw)
    except ValueError:
        return 115200
