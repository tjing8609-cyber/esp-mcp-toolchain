from __future__ import annotations

import ast
from pathlib import Path

from ..backends import mpremote_backend
from ..backends.raw_repl_backend import execute_code
from ..config import get_selected_port
from ..errors import execution_error, not_implemented


def _quote_micro_python(value: str) -> str:
    return repr(value)


def _resolve_port(tool: str, port: str | None) -> dict | str:
    selected_port = port or get_selected_port()
    if not selected_port:
        return execution_error(
            "serial_port_not_selected",
            "No serial port was provided or selected.",
            tool=tool,
            suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
        )
    return selected_port


def _mpremote_metadata(tool: str, result: dict, *, port: str, **extra: object) -> dict:
    result.update(
        {
            "tool": tool,
            "tool_name": tool,
            "tools鍚嶇О": tool,
            "implemented": True,
            "port": port,
            "backend": "mpremote",
            **extra,
        }
    )
    return result


def _parse_mpremote_ls(stdout: str) -> list[str]:
    files: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line or line.endswith(":") or line.startswith("ls "):
            continue
        parts = line.split()
        if parts:
            files.append(parts[-1].rstrip("/"))
    return files


def esp_file_upload(
    port: str | None = None,
    backend: str = "mpremote",
    local_path: str = "",
    remote_path: str = "",
) -> dict:
    if backend == "mpremote":
        selected_port = _resolve_port("esp_file_upload", port)
        if isinstance(selected_port, dict):
            return selected_port
        if not local_path:
            return execution_error("missing_local_path", "No local path was provided.", tool="esp_file_upload")
        if not remote_path:
            return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_upload")
        source_path = Path(local_path)
        if not source_path.exists():
            return execution_error("path_not_found", f"Local file does not exist: {local_path}", tool="esp_file_upload")
        result = mpremote_backend.upload_file(port=selected_port, local_path=source_path, remote_path=remote_path)
        _mpremote_metadata(
            "esp_file_upload",
            result,
            port=selected_port,
            local_path=str(source_path),
            remote_path=remote_path,
            bytes_written=source_path.stat().st_size,
        )
        if result.get("ok"):
            result["data"] = {"bytes_written": source_path.stat().st_size}
        return result
    if backend != "raw_repl":
        return not_implemented("esp_file_upload")
    if not local_path:
        return execution_error("missing_local_path", "No local path was provided.", tool="esp_file_upload")
    if not remote_path:
        return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_upload")
    selected_port = _resolve_port("esp_file_upload", port)
    if isinstance(selected_port, dict):
        return selected_port

    source_path = Path(local_path)
    if not source_path.exists():
        return execution_error("path_not_found", f"Local file does not exist: {local_path}", tool="esp_file_upload")
    payload = source_path.read_bytes()
    if len(payload) > 20000:
        return execution_error(
            "file_too_large",
            "raw_repl upload is limited to 20000 bytes.",
            tool="esp_file_upload",
            size=len(payload),
        )

    code = (
        f"_p={_quote_micro_python(remote_path)}\n"
        f"_data={payload!r}\n"
        "with open(_p, 'wb') as _f:\n"
        "    _n = _f.write(_data)\n"
        "print(_n)"
    )
    result = execute_code(selected_port, code, timeout_ms=5000)
    result.update(
        {
            "tool": "esp_file_upload",
            "tool_name": "esp_file_upload",
            "tools鍚嶇О": "esp_file_upload",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
            "local_path": str(source_path),
            "remote_path": remote_path,
            "bytes_written": len(payload),
        }
    )
    if result.get("ok"):
        result["data"] = {"bytes_written": len(payload)}
    return result


def esp_file_download(
    port: str | None = None,
    backend: str = "mpremote",
    remote_path: str = "",
    local_path: str = "",
) -> dict:
    if backend == "mpremote":
        selected_port = _resolve_port("esp_file_download", port)
        if isinstance(selected_port, dict):
            return selected_port
        if not remote_path:
            return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_download")
        if not local_path:
            return execution_error("missing_local_path", "No local path was provided.", tool="esp_file_download")
        target_path = Path(local_path)
        if target_path.exists():
            return execution_error(
                "local_path_exists",
                f"Local path already exists: {local_path}",
                tool="esp_file_download",
                suggested_next_actions=["Choose a new output path"],
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        result = mpremote_backend.download_file(port=selected_port, remote_path=remote_path, local_path=target_path)
        _mpremote_metadata(
            "esp_file_download",
            result,
            port=selected_port,
            remote_path=remote_path,
            local_path=str(target_path),
        )
        if result.get("ok"):
            result["bytes_written"] = target_path.stat().st_size if target_path.exists() else 0
            result["data"] = {"local_path": str(target_path), "bytes_written": result["bytes_written"]}
        return result
    if backend != "raw_repl":
        return not_implemented("esp_file_download")
    if not local_path:
        return execution_error("missing_local_path", "No local path was provided.", tool="esp_file_download")
    target_path = Path(local_path)
    if target_path.exists():
        return execution_error(
            "local_path_exists",
            f"Local path already exists: {local_path}",
            tool="esp_file_download",
            suggested_next_actions=["Choose a new output path"],
        )

    read_result = esp_file_read(port=port, backend=backend, remote_path=remote_path, max_bytes=20000)
    if not read_result.get("ok"):
        read_result["tool"] = "esp_file_download"
        read_result["tool_name"] = "esp_file_download"
        read_result["tools鍚嶇О"] = "esp_file_download"
        return read_result

    target_path.parent.mkdir(parents=True, exist_ok=True)
    content = read_result.get("content", "")
    target_path.write_text(content, encoding="utf-8")
    return {
        "ok": True,
        "tool": "esp_file_download",
        "tool_name": "esp_file_download",
        "tools鍚嶇О": "esp_file_download",
        "implemented": True,
        "port": read_result.get("port"),
        "backend": backend,
        "remote_path": remote_path,
        "local_path": str(target_path),
        "bytes_written": len(content.encode("utf-8")),
        "data": {"local_path": str(target_path), "bytes_written": len(content.encode("utf-8"))},
        "message": "Downloaded file through MicroPython raw REPL.",
    }


def esp_file_list(port: str | None = None, backend: str = "mpremote", remote_dir: str = "/") -> dict:
    if backend == "mpremote":
        selected_port = _resolve_port("esp_file_list", port)
        if isinstance(selected_port, dict):
            return selected_port
        result = mpremote_backend.list_files(port=selected_port, remote_dir=remote_dir)
        _mpremote_metadata("esp_file_list", result, port=selected_port, remote_dir=remote_dir)
        if result.get("ok"):
            files = _parse_mpremote_ls(result.get("stdout", ""))
            result["files"] = files
            result["data"] = {"files": files}
        return result
    if backend != "raw_repl":
        return not_implemented("esp_file_list")
    selected_port = _resolve_port("esp_file_list", port)
    if isinstance(selected_port, dict):
        return selected_port

    code = f"import os\nprint(repr(os.listdir({_quote_micro_python(remote_dir)})))"
    result = execute_code(selected_port, code, timeout_ms=3000)
    result.update(
        {
            "tool": "esp_file_list",
            "tool_name": "esp_file_list",
            "tools鍚嶇О": "esp_file_list",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
            "remote_dir": remote_dir,
        }
    )
    if not result.get("ok"):
        return result
    try:
        files = ast.literal_eval(result.get("stdout", "").strip())
    except (SyntaxError, ValueError) as exc:
        return execution_error(
            "file_list_parse_failed",
            str(exc),
            tool="esp_file_list",
            stdout=result.get("stdout", ""),
            port=selected_port,
            backend=backend,
        )
    result["files"] = files
    result["data"] = {"files": files}
    return result


def esp_file_read(
    port: str | None = None,
    backend: str = "mpremote",
    remote_path: str = "",
    max_bytes: int = 20000,
) -> dict:
    if backend == "mpremote":
        selected_port = _resolve_port("esp_file_read", port)
        if isinstance(selected_port, dict):
            return selected_port
        if not remote_path:
            return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_read")
        limit = max(0, int(max_bytes))
        result = mpremote_backend.read_file(port=selected_port, remote_path=remote_path)
        _mpremote_metadata(
            "esp_file_read",
            result,
            port=selected_port,
            remote_path=remote_path,
            max_bytes=limit,
        )
        if result.get("ok"):
            content = result.get("stdout", "")
            truncated = len(content.encode("utf-8")) > limit if limit else False
            if truncated:
                content = content.encode("utf-8")[:limit].decode("utf-8", errors="replace")
            result["content"] = content
            result["bytes_read"] = len(content.encode("utf-8"))
            result["truncated"] = truncated
            result["data"] = {"content": content, "bytes_read": result["bytes_read"], "truncated": truncated}
        return result
    if backend != "raw_repl":
        return not_implemented("esp_file_read")
    if not remote_path:
        return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_read")
    selected_port = _resolve_port("esp_file_read", port)
    if isinstance(selected_port, dict):
        return selected_port

    limit = max(0, int(max_bytes))
    code = (
        f"_p={_quote_micro_python(remote_path)}\n"
        f"_n={limit}\n"
        "with open(_p, 'rb') as _f:\n"
        "    _data = _f.read(_n + 1)\n"
        "print(repr(_data))"
    )
    result = execute_code(selected_port, code, timeout_ms=3000)
    result.update(
        {
            "tool": "esp_file_read",
            "tool_name": "esp_file_read",
            "tools鍚嶇О": "esp_file_read",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
            "remote_path": remote_path,
            "max_bytes": limit,
        }
    )
    if not result.get("ok"):
        return result
    try:
        payload = ast.literal_eval(result.get("stdout", "").strip())
    except (SyntaxError, ValueError) as exc:
        return execution_error(
            "file_read_parse_failed",
            str(exc),
            tool="esp_file_read",
            stdout=result.get("stdout", ""),
            port=selected_port,
            backend=backend,
        )
    if not isinstance(payload, (bytes, bytearray)):
        return execution_error("file_read_unexpected_payload", "Expected bytes from board.", tool="esp_file_read")

    raw = bytes(payload)
    truncated = len(raw) > limit
    if truncated:
        raw = raw[:limit]
    text = raw.decode("utf-8", errors="replace")
    result["content"] = text
    result["bytes_read"] = len(raw)
    result["truncated"] = truncated
    result["data"] = {"content": text, "bytes_read": len(raw), "truncated": truncated}
    return result


def esp_file_delete(
    port: str | None = None,
    backend: str = "mpremote",
    remote_path: str = "",
    confirm: bool = False,
) -> dict:
    if not confirm:
        return execution_error(
            "confirmation_required",
            "Deleting a board file is a high-risk action and requires confirm=True.",
            tool="esp_file_delete",
            recoverable=True,
            suggested_next_actions=["Review remote_path", "Call again with confirm=True only after user approval"],
        )
    if backend == "mpremote":
        selected_port = _resolve_port("esp_file_delete", port)
        if isinstance(selected_port, dict):
            return selected_port
        if not remote_path:
            return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_delete")
        result = mpremote_backend.run_mpremote(["fs", "rm", remote_path], port=selected_port)
        return _mpremote_metadata("esp_file_delete", result, port=selected_port, remote_path=remote_path)
    if backend != "raw_repl":
        return not_implemented("esp_file_delete")
    if not remote_path:
        return execution_error("missing_remote_path", "No remote path was provided.", tool="esp_file_delete")
    selected_port = _resolve_port("esp_file_delete", port)
    if isinstance(selected_port, dict):
        return selected_port

    code = f"import os\nos.remove({_quote_micro_python(remote_path)})\nprint('deleted')"
    result = execute_code(selected_port, code, timeout_ms=3000)
    result.update(
        {
            "tool": "esp_file_delete",
            "tool_name": "esp_file_delete",
            "tools鍚嶇О": "esp_file_delete",
            "implemented": True,
            "port": selected_port,
            "backend": backend,
            "remote_path": remote_path,
        }
    )
    return result
