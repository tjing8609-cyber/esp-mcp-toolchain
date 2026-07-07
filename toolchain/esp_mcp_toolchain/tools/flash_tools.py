from __future__ import annotations

from ..errors import not_implemented


def esp_flash_firmware(
    port: str,
    backend: str = "espidf",
    project_dir: str = ".",
    baud: int = 460800,
    monitor_after_flash: bool = False,
) -> dict:
    return not_implemented("esp_flash_firmware")


def esp_erase_flash(port: str, chip: str = "esp32") -> dict:
    return not_implemented("esp_erase_flash")

