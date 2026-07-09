from __future__ import annotations

from pathlib import Path

from ..backends.espidf_backend import run_idf_flash
from ..backends.esptool_backend import run_erase_flash, run_read_flash
from ..errors import execution_error, not_implemented
from ..paths import data_dir
from ..paths import safe_project_path
from ..utils.time_utils import now_compact


def _default_backup_path(prefix: str = "flash_backup") -> Path:
    return data_dir() / "artifacts" / "flash" / f"{prefix}_{now_compact()}.bin"


def esp_backup_flash(
    port: str,
    chip: str = "esp32",
    size: int = 0x400000,
    address: int = 0,
    baud: int = 460800,
    output_path: str = "",
) -> dict:
    if size <= 0:
        return execution_error("invalid_size", "Backup size must be greater than zero.", tool="esp_backup_flash")
    target = Path(output_path) if output_path else _default_backup_path()
    if not target.is_absolute():
        target = safe_project_path(target)
    result = run_read_flash(port=port, chip=chip, address=address, size=size, baud=baud, output_path=target)
    result.update(
        {
            "tool": "esp_backup_flash",
            "tool_name": "esp_backup_flash",
            "tools鍚嶇О": "esp_backup_flash",
            "implemented": True,
            "port": port,
            "chip": chip,
            "address": address,
            "size": size,
            "baud": baud,
            "output_path": str(target),
        }
    )
    if result.get("ok"):
        result["bytes_read"] = target.stat().st_size if target.exists() else 0
        result["data"] = {"output_path": str(target), "bytes_read": result["bytes_read"]}
    return result


def esp_flash_firmware(
    port: str,
    backend: str = "espidf",
    project_dir: str = ".",
    baud: int = 460800,
    monitor_after_flash: bool = False,
    confirm: bool = False,
) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Flashing firmware is a high-risk action and requires confirm=True.",
            tool="esp_flash_firmware",
            recoverable=True,
            suggested_next_actions=["Review the port and project_dir", "Call again with confirm=True only after user approval"],
        )
    if backend != "espidf":
        return execution_error(
            "unsupported_backend",
            f"Unsupported flash backend: {backend}",
            tool="esp_flash_firmware",
            suggested_next_actions=["Use backend=espidf"],
        )
    if monitor_after_flash:
        return execution_error(
            "unsupported_option",
            "monitor_after_flash is not implemented yet.",
            tool="esp_flash_firmware",
            suggested_next_actions=["Flash first, then run esp_serial_capture"],
        )
    try:
        path = safe_project_path(project_dir)
    except ValueError as exc:
        return execution_error("unsafe_project_path", str(exc), tool="esp_flash_firmware")
    if not path.exists():
        return execution_error("project_dir_missing", f"Project directory does not exist: {path}", tool="esp_flash_firmware")

    result = run_idf_flash(path, port=port, baud=baud)
    result.update(
        {
            "tool": "esp_flash_firmware",
            "tool_name": "esp_flash_firmware",
            "tools鍚嶇О": "esp_flash_firmware",
            "implemented": True,
            "backend": backend,
            "project_dir": str(path),
            "port": port,
            "baud": baud,
        }
    )
    return result


def esp_erase_flash(port: str, chip: str = "esp32", confirm: bool = False) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Erasing flash is a destructive high-risk action and requires confirm=True.",
            tool="esp_erase_flash",
            recoverable=True,
            suggested_next_actions=["Back up flash first", "Review port and chip", "Call again with confirm=True only after user approval"],
        )

    result = run_erase_flash(port=port, chip=chip)
    result.update(
        {
            "tool": "esp_erase_flash",
            "tool_name": "esp_erase_flash",
            "tools鍚嶇О": "esp_erase_flash",
            "implemented": True,
            "port": port,
            "chip": chip,
        }
    )
    return result
