from __future__ import annotations

from ..errors import not_implemented


def esp_file_upload(port: str, backend: str = "mpremote", local_path: str = "", remote_path: str = "") -> dict:
    return not_implemented("esp_file_upload")


def esp_file_download(port: str, backend: str = "mpremote", remote_path: str = "", local_path: str = "") -> dict:
    return not_implemented("esp_file_download")


def esp_file_list(port: str, backend: str = "mpremote", remote_dir: str = "/") -> dict:
    return not_implemented("esp_file_list")


def esp_file_read(port: str, backend: str = "mpremote", remote_path: str = "", max_bytes: int = 20000) -> dict:
    return not_implemented("esp_file_read")


def esp_file_delete(port: str, backend: str = "mpremote", remote_path: str = "") -> dict:
    return not_implemented("esp_file_delete")

