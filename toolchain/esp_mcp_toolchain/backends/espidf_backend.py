from __future__ import annotations

import os
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..utils.subprocess_utils import redact_command


DEFAULT_IDF_PATH = Path(r"C:\Espressif\frameworks\esp-idf-v5.2.1")
DEFAULT_IDF_PYTHON = Path(r"C:\Espressif\python_env\idf5.2_py3.11_env\Scripts\python.exe")
DEFAULT_TOOL_DIRS = [
    Path(r"C:\Espressif\tools\xtensa-esp-elf\esp-13.2.0_20230928\xtensa-esp-elf\bin"),
    Path(r"C:\Espressif\tools\cmake\3.24.0\bin"),
    Path(r"C:\Espressif\tools\ninja\1.11.1"),
    Path(r"C:\Espressif\tools\idf-git\2.43.0\cmd"),
    Path(r"C:\Espressif\tools\ccache\4.8\ccache-4.8-windows-x86_64"),
]


def _idf_path() -> Path | None:
    env_path = os.environ.get("IDF_PATH") or os.environ.get("ESP_MCP_IDF_PATH")
    if env_path:
        path = Path(env_path)
        return path if path.exists() else None
    return DEFAULT_IDF_PATH if DEFAULT_IDF_PATH.exists() else None


def _idf_python() -> Path:
    env_python = os.environ.get("ESP_MCP_IDF_PYTHON")
    if env_python and Path(env_python).exists():
        return Path(env_python)
    if DEFAULT_IDF_PYTHON.exists():
        return DEFAULT_IDF_PYTHON
    return Path(sys.executable)


def _build_env(idf_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["IDF_PATH"] = str(idf_path)
    env.setdefault("IDF_TOOLS_PATH", str(idf_path.parents[1]))
    env.setdefault("IDF_PYTHON_ENV_PATH", str(_idf_python().parents[1]))
    if os.name == "nt":
        env.setdefault("OS", "Windows_NT")
        env.setdefault("SYSTEMROOT", env.get("WINDIR", r"C:\Windows"))
        env.setdefault("PROCESSOR_ARCHITECTURE", "AMD64" if sys.maxsize > 2**32 else "x86")
    tool_paths = [str(path) for path in DEFAULT_TOOL_DIRS if path.exists()]
    env["PATH"] = os.pathsep.join([*tool_paths, env.get("PATH", "")])
    return env


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _run_idf_command(
    command: list[str],
    project_dir: Path,
    idf_path: Path,
    timeout_s: int,
    *,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    env = _build_env(idf_path)
    if env_overrides:
        env.update(env_overrides)
    popen_kwargs: dict[str, Any] = {
        "cwd": str(project_dir),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _terminate_process_tree(process)
        stdout, stderr = process.communicate()
        return {
            "ok": False,
            "error_kind": "idf_command_timeout",
            "message": f"ESP-IDF command timed out after {timeout_s} seconds.",
            "command": redact_command(command),
            "command_started": True,
            "command_completed": False,
            "stdout": stdout,
            "stderr": stderr,
        }
    return {
        "ok": process.returncode == 0,
        "returncode": process.returncode,
        "command": redact_command(command),
        "command_started": True,
        "command_completed": True,
        "stdout": stdout,
        "stderr": stderr,
    }


def _configured_target(project_dir: Path) -> str | None:
    sdkconfig = project_dir / "sdkconfig"
    if not sdkconfig.exists():
        return None
    match = re.search(r'^CONFIG_IDF_TARGET="([^"]+)"$', sdkconfig.read_text(encoding="utf-8"), re.MULTILINE)
    return match.group(1) if match else None


def _configured_build_target(project_dir: Path) -> str | None:
    cache = project_dir / "build" / "CMakeCache.txt"
    if not cache.exists():
        return None
    match = re.search(
        r"^IDF_TARGET:[^=\r\n]+=(.+)$",
        cache.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    return match.group(1).strip() if match else None


def _path_is_reparse(path: Path) -> bool:
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _inspect_destructive_build_path(project_dir: Path) -> dict[str, Any]:
    if _path_is_reparse(project_dir):
        raise ValueError("Project directory is a symbolic link, junction, or reparse point.")
    project_status = os.lstat(project_dir)
    if not stat.S_ISDIR(project_status.st_mode):
        raise ValueError("Project path is not a directory.")

    resolved_project = project_dir.resolve(strict=True)
    build_dir = project_dir / "build"
    build_exists = os.path.lexists(str(build_dir))
    if build_exists:
        if _path_is_reparse(build_dir):
            raise ValueError("Build directory is a symbolic link, junction, or reparse point.")
        build_status = os.lstat(build_dir)
        if not stat.S_ISDIR(build_status.st_mode):
            raise ValueError("Build path exists but is not a directory.")
        cmake_cache = build_dir / "CMakeCache.txt"
        if os.path.lexists(str(cmake_cache)):
            if _path_is_reparse(cmake_cache):
                raise ValueError(
                    "CMake cache is a symbolic link, junction, or reparse point."
                )
            cache_status = os.lstat(cmake_cache)
            if not stat.S_ISREG(cache_status.st_mode):
                raise ValueError("CMake cache exists but is not a regular file.")
        resolved_build = build_dir.resolve(strict=True)
    else:
        resolved_build = (resolved_project / "build").resolve(strict=False)

    try:
        relative_build = resolved_build.relative_to(resolved_project)
    except ValueError as exc:
        raise ValueError("Resolved build directory escapes the project directory.") from exc
    if not relative_build.parts:
        raise ValueError("Resolved build directory must be a strict child of the project directory.")

    return {
        "build_path_safety_checked": True,
        "build_path_safe": True,
        "build_path_exists_before": build_exists,
        "build_path_reparse_detected": False,
        "build_path_within_project": True,
        "resolved_build_dir": str(resolved_build),
        "build_path_check_error": None,
    }


def run_idf_build(
    project_dir: Path,
    *,
    target: str = "esp32",
    timeout_s: int = 600,
    confirm_target_change: bool = False,
) -> dict[str, Any]:
    try:
        initial_build_path_safety = _inspect_destructive_build_path(project_dir)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "error_kind": "unsafe_destructive_build_path",
            "message": f"Build path refused before target inspection: {exc}",
            "recoverable": True,
            "suggested_next_actions": [
                "Use a real build directory strictly inside the project",
                "Remove symbolic-link or junction indirection before building",
            ],
            "sdkconfig_state": "not_inspected",
            "configured_target_before": None,
            "sdkconfig_exists_before": False,
            "cmake_cache_state": "not_inspected",
            "cmake_cache_target_before": None,
            "cmake_cache_exists_before": False,
            "target_plan": "inspection_failed",
            "confirmation_required": False,
            "target_change_required": False,
            "target_change_confirmed": False,
            "sdkconfig_replacement_required": False,
            "build_cache_reset_required": False,
            "set_target_planned": False,
            "fullclean_planned": False,
            "sdkconfig_rename_planned": False,
            "sdkconfig_old_exists_before": False,
            "sdkconfig_old_overwrite_risk": False,
            "fullclean_authorized": False,
            "destructive_command_requested": False,
            "build_path_safety_checked": True,
            "build_path_safe": False,
            "build_path_exists_before": False,
            "build_path_reparse_detected": "reparse point" in str(exc),
            "build_path_within_project": False,
            "resolved_build_dir": None,
            "build_path_check_error": str(exc),
            "command_started": False,
            "command_completed": False,
            "target_change_applied": False,
            "target_verified": False,
            "side_effects_partial_possible": False,
            "target_change_may_be_partial": False,
        }

    sdkconfig = project_dir / "sdkconfig"
    sdkconfig_old = project_dir / "sdkconfig.old"
    cmake_cache = project_dir / "build" / "CMakeCache.txt"
    sdkconfig_exists = sdkconfig.exists()
    cmake_cache_exists = cmake_cache.exists()
    try:
        configured_target = _configured_target(project_dir)
        cmake_cache_target = _configured_build_target(project_dir)
    except (OSError, UnicodeError) as exc:
        return {
            "ok": False,
            "error_kind": "target_configuration_inspection_failed",
            "message": f"Could not inspect sdkconfig or the CMake target cache: {exc}",
            "sdkconfig_state": "inspection_failed",
            "configured_target_before": None,
            "sdkconfig_exists_before": sdkconfig_exists,
            "cmake_cache_state": "inspection_failed",
            "cmake_cache_target_before": None,
            "cmake_cache_exists_before": cmake_cache_exists,
            "target_plan": "inspection_failed",
            "confirmation_required": False,
            "target_change_required": False,
            "target_change_confirmed": False,
            "sdkconfig_replacement_required": False,
            "build_cache_reset_required": False,
            "set_target_planned": False,
            "fullclean_planned": False,
            "sdkconfig_rename_planned": False,
            "sdkconfig_old_exists_before": sdkconfig_old.exists(),
            "sdkconfig_old_overwrite_risk": False,
            "fullclean_authorized": False,
            "destructive_command_requested": False,
            "build_path_safety_checked": False,
            "build_path_safe": False,
            "build_path_exists_before": False,
            "build_path_reparse_detected": False,
            "build_path_within_project": False,
            "resolved_build_dir": None,
            "build_path_check_error": None,
            "command_started": False,
            "command_completed": False,
            "target_change_applied": False,
            "target_verified": False,
            "side_effects_partial_possible": False,
            "target_change_may_be_partial": False,
        }

    sdkconfig_state = (
        "missing"
        if not sdkconfig_exists
        else "configured"
        if configured_target is not None
        else "target_missing"
    )
    cmake_cache_state = (
        "missing"
        if not cmake_cache_exists
        else "configured"
        if cmake_cache_target is not None
        else "target_missing"
    )
    sdkconfig_replacement_required = sdkconfig_exists and configured_target != target
    build_cache_reset_required = cmake_cache_exists and cmake_cache_target != target
    if sdkconfig_replacement_required:
        target_plan = "set_target_build"
    elif build_cache_reset_required and sdkconfig_exists:
        target_plan = "fullclean_build"
    elif build_cache_reset_required:
        target_plan = "fullclean_define_target_build"
    elif sdkconfig_exists:
        target_plan = "build"
    else:
        target_plan = "define_target_build"

    confirmation_required = target_plan in {
        "set_target_build",
        "fullclean_build",
        "fullclean_define_target_build",
    }
    target_change_required = confirmation_required
    target_change_confirmed = bool(confirmation_required and confirm_target_change)
    set_target_planned = target_plan == "set_target_build"
    fullclean_planned = confirmation_required
    sdkconfig_old_exists = sdkconfig_old.exists()
    sdkconfig_old_overwrite_risk = set_target_planned and sdkconfig_old_exists
    target_change_warnings: list[str] = []
    if set_target_planned:
        target_change_warnings.append(
            "ESP-IDF set-target depends on fullclean and renames sdkconfig to sdkconfig.old."
        )
    if sdkconfig_old_overwrite_risk:
        target_change_warnings.append(
            "sdkconfig.old already exists and may be replaced by the confirmed set-target operation."
        )
    if build_cache_reset_required and not set_target_planned:
        target_change_warnings.append(
            "The existing CMake target cache conflicts with the requested target and requires fullclean."
        )

    target_metadata = {
        "sdkconfig_state": sdkconfig_state,
        "configured_target_before": configured_target,
        "sdkconfig_exists_before": sdkconfig_exists,
        "cmake_cache_state": cmake_cache_state,
        "cmake_cache_target_before": cmake_cache_target,
        "cmake_cache_exists_before": cmake_cache_exists,
        "target_plan": target_plan,
        "confirmation_required": confirmation_required,
        "target_change_required": target_change_required,
        "target_change_confirmed": target_change_confirmed,
        "sdkconfig_replacement_required": sdkconfig_replacement_required,
        "build_cache_reset_required": build_cache_reset_required,
        "set_target_planned": set_target_planned,
        "fullclean_planned": fullclean_planned,
        "sdkconfig_rename_planned": set_target_planned,
        "sdkconfig_old_exists_before": sdkconfig_old_exists,
        "sdkconfig_old_overwrite_risk": sdkconfig_old_overwrite_risk,
        "fullclean_authorized": target_change_confirmed,
        "destructive_command_requested": target_change_confirmed,
        "target_change_warnings": target_change_warnings,
        **initial_build_path_safety,
        "command_started": False,
        "command_completed": False,
        "target_change_applied": False,
        "target_verified": False,
        "side_effects_partial_possible": False,
        "target_change_may_be_partial": False,
    }
    if confirmation_required and not confirm_target_change:
        configured_label = configured_target or "missing/unknown"
        cache_label = cmake_cache_target or "missing/unknown"
        return {
            "ok": False,
            "error_kind": "target_change_confirmation_required",
            "message": (
                f"Target plan {target_plan} is destructive: sdkconfig target is {configured_label}, "
                f"CMake cache target is {cache_label}, and the requested target is {target}. "
                "The plan requires fullclean"
                + (" and sdkconfig replacement via set-target." if set_target_planned else ".")
            ),
            "recoverable": True,
            "suggested_next_actions": [
                "Review and preserve sdkconfig, sdkconfig.old, and existing build artifacts",
                "Call again with confirm_target_change=True only after explicit user approval",
            ],
            **target_metadata,
        }

    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
            **target_metadata,
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
            **target_metadata,
        }

    if target_plan == "set_target_build":
        actions = ["set-target", target, "build"]
    elif target_plan == "fullclean_build":
        actions = ["fullclean", "build"]
    elif target_plan == "fullclean_define_target_build":
        actions = ["-D", f"IDF_TARGET={target}", "fullclean", "build"]
    elif target_plan == "define_target_build":
        actions = ["-D", f"IDF_TARGET={target}", "build"]
    else:
        actions = ["build"]
    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), *actions]
    try:
        pre_spawn_safety = _inspect_destructive_build_path(project_dir)
    except (OSError, ValueError) as exc:
        return {
            "ok": False,
            "error_kind": "unsafe_destructive_build_path",
            "message": f"Build path changed before process start: {exc}",
            "command": redact_command(command),
            **target_metadata,
            "build_path_safety_checked": True,
            "build_path_safe": False,
            "build_path_reparse_detected": "reparse point" in str(exc),
            "build_path_within_project": False,
            "build_path_check_error": str(exc),
        }
    if pre_spawn_safety["resolved_build_dir"] != target_metadata["resolved_build_dir"]:
        return {
            "ok": False,
            "error_kind": "unsafe_destructive_build_path",
            "message": "Resolved build directory changed before process start.",
            "command": redact_command(command),
            **target_metadata,
            "build_path_safe": False,
            "build_path_check_error": "resolved build directory changed",
        }
    target_metadata.update(pre_spawn_safety)
    try:
        result = _run_idf_command(
            command,
            project_dir,
            idf_path,
            timeout_s,
            env_overrides={"IDF_TARGET": target},
        )
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "build_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
            **target_metadata,
            "side_effects_partial_possible": confirmation_required,
            "target_change_may_be_partial": confirmation_required,
        }

    command_started = bool(result.get("command_started", True))
    command_completed = bool(result.get("command_completed", True))
    try:
        configured_target_after = _configured_target(project_dir)
        cmake_cache_target_after = _configured_build_target(project_dir)
        postflight_error = None
    except (OSError, UnicodeError) as exc:
        configured_target_after = None
        cmake_cache_target_after = None
        postflight_error = str(exc)

    target_verified = (
        result.get("ok") is True
        and configured_target_after == target
        and cmake_cache_target_after == target
    )
    target_change_applied = confirmation_required and target_verified
    side_effects_partial_possible = (
        confirmation_required and command_started and not target_change_applied
    )
    result.update(
        target_metadata,
        command_started=command_started,
        command_completed=command_completed,
        configured_target_after=configured_target_after,
        cmake_cache_target_after=cmake_cache_target_after,
        target_verified=target_verified,
        target_change_applied=target_change_applied,
        postflight_error=postflight_error,
        side_effects_partial_possible=side_effects_partial_possible,
        target_change_may_be_partial=side_effects_partial_possible,
    )
    if confirmation_required and result.get("ok") and not target_verified:
        result["ok"] = False
        result["error_kind"] = "target_change_postcondition_failed"
        result["message"] = (
            "ESP-IDF returned success, but postflight did not verify both sdkconfig and "
            "the CMake cache at the requested target."
        )
        return result

    result["message"] = "ESP-IDF build completed." if result.get("ok") else result.get("message", "ESP-IDF build failed.")
    return result


def run_idf_flash(project_dir: Path, *, port: str, baud: int = 460800, timeout_s: int = 300) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
        }

    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), "-p", port, "-b", str(baud), "flash"]
    try:
        result = _run_idf_command(command, project_dir, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "flash_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    result["message"] = "ESP-IDF flash completed." if result.get("ok") else result.get("message", "ESP-IDF flash failed.")
    return result


def run_idf_clean(project_dir: Path, *, mode: str = "clean", timeout_s: int = 180) -> dict[str, Any]:
    idf_path = _idf_path()
    if idf_path is None:
        return {
            "ok": False,
            "error_kind": "idf_path_missing",
            "message": "ESP-IDF path was not found.",
            "suggested_next_actions": ["Set IDF_PATH or ESP_MCP_IDF_PATH"],
        }

    idf_py = idf_path / "tools" / "idf.py"
    if not idf_py.exists():
        return {
            "ok": False,
            "error_kind": "idf_py_missing",
            "message": f"idf.py was not found at {idf_py}.",
        }

    command = [str(_idf_python()), str(idf_py), "-C", str(project_dir), mode]
    try:
        result = _run_idf_command(command, project_dir, idf_path, timeout_s)
    except Exception as exc:
        return {
            "ok": False,
            "error_kind": "clean_spawn_failed",
            "message": str(exc),
            "command": redact_command(command),
        }

    result["message"] = f"ESP-IDF {mode} completed." if result.get("ok") else result.get("message", f"ESP-IDF {mode} failed.")
    return result
