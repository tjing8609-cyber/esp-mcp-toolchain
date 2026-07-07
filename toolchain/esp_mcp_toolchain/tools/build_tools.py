from __future__ import annotations

from ..errors import not_implemented


def esp_project_build(project_dir: str = ".", backend: str = "espidf", target: str = "esp32", log_name: str = "build_default") -> dict:
    return not_implemented("esp_project_build")


def esp_project_clean(project_dir: str = ".", mode: str = "clean") -> dict:
    return not_implemented("esp_project_clean")

