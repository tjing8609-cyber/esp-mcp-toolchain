from __future__ import annotations

from ..errors import not_implemented


def esp_exec_code(port: str, backend: str = "raw_repl", code: str = "", capture_ms: int = 3000) -> dict:
    return not_implemented("esp_exec_code")


def esp_run_file(port: str, backend: str = "raw_repl", path: str = "", path_type: str = "remote", capture_ms: int = 5000) -> dict:
    return not_implemented("esp_run_file")

