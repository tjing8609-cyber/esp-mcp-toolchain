"""Start the MCP server with the project-owned Conda environment.

This bootstrap intentionally uses only the Python standard library. The
``python`` configured by the MCP client only locates the isolated runtime; the
actual server is always run by ``esp-mcp-toolchain`` (or an explicit override).
"""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys
from typing import Callable, Mapping


ENVIRONMENT_NAME = "esp-mcp-toolchain"
PYTHON_OVERRIDE = "ESP_MCP_PYTHON"


def _environment_name(value: str | None) -> str:
    """Return a Conda environment's final path/name component."""

    if not value:
        return ""
    return Path(value.rstrip("\\/")).name


def _python_in_environment(environment_dir: Path, os_name: str) -> Path:
    if os_name == "nt":
        return environment_dir / "python.exe"
    return environment_dir / "bin" / "python"


def _conda_base(conda_executable: str) -> Path:
    """Infer the Conda base directory from ``.../Scripts|bin/conda``."""

    return Path(conda_executable).expanduser().parent.parent


def runtime_python_candidates(
    *,
    environment: Mapping[str, str],
    current_executable: str,
    current_prefix: str,
    home: Path,
    os_name: str,
) -> list[Path]:
    """Build ordered interpreter candidates without accessing the filesystem."""

    override = environment.get(PYTHON_OVERRIDE)
    if override:
        override_path = Path(override)
        if override_path.parts and override_path.parts[0] == "~":
            override_path = home.joinpath(*override_path.parts[1:])
        return [override_path]

    current_environment = environment.get("CONDA_DEFAULT_ENV")
    if (
        _environment_name(current_environment) == ENVIRONMENT_NAME
        or Path(current_prefix).name == ENVIRONMENT_NAME
    ):
        return [Path(current_executable)]

    environment_roots: list[Path] = []
    conda_executable = environment.get("CONDA_EXE")
    if conda_executable:
        environment_roots.append(_conda_base(conda_executable) / "envs" / ENVIRONMENT_NAME)
    environment_roots.extend(
        [
            home / "anaconda3" / "envs" / ENVIRONMENT_NAME,
            home / "miniconda3" / "envs" / ENVIRONMENT_NAME,
        ]
    )

    candidates: list[Path] = []
    for root in environment_roots:
        candidate = _python_in_environment(root, os_name)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def select_runtime_python(
    *,
    environment: Mapping[str, str] | None = None,
    current_executable: str | None = None,
    current_prefix: str | None = None,
    home: Path | None = None,
    os_name: str | None = None,
    is_file: Callable[[Path], bool] | None = None,
) -> Path:
    """Select the isolated interpreter or raise a clear configuration error."""

    environment = os.environ if environment is None else environment
    current_executable = sys.executable if current_executable is None else current_executable
    current_prefix = sys.prefix if current_prefix is None else current_prefix
    home = Path.home() if home is None else home
    os_name = os.name if os_name is None else os_name
    is_file = (lambda path: path.is_file()) if is_file is None else is_file

    candidates = runtime_python_candidates(
        environment=environment,
        current_executable=current_executable,
        current_prefix=current_prefix,
        home=home,
        os_name=os_name,
    )
    for candidate in candidates:
        if is_file(candidate):
            return candidate

    searched = ", ".join(path.as_posix() for path in candidates) or "no candidate paths"
    if environment.get(PYTHON_OVERRIDE):
        raise RuntimeError(
            f"{PYTHON_OVERRIDE} does not point to an existing Python executable: {searched}"
        )
    raise RuntimeError(
        f"Conda environment '{ENVIRONMENT_NAME}' was not found. Searched: {searched}. "
        f"Create it or set {PYTHON_OVERRIDE} to its Python executable."
    )


def _same_executable(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def main() -> None:
    server_script = Path(__file__).resolve().parents[1] / "toolchain" / "mcp_server.py"
    try:
        runtime_python = select_runtime_python()
    except RuntimeError as exc:
        print(f"esp-mcp-toolchain launcher error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if _same_executable(runtime_python, Path(sys.executable)):
        runpy.run_path(str(server_script), run_name="__main__")
        return

    os.execv(str(runtime_python), [str(runtime_python), str(server_script)])


if __name__ == "__main__":
    main()
