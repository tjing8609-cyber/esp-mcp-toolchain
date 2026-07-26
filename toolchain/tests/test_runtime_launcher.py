from __future__ import annotations

import importlib.util
import os
from pathlib import Path


SOURCE_ROOT = Path(
    os.environ.get(
        "ESP_MCP_SOURCE_ROOT",
        Path(__file__).resolve().parents[2],
    )
).resolve()
LAUNCHER_PATH = SOURCE_ROOT / "scripts" / "run_mcp_server.py"


def _load_launcher():
    spec = importlib.util.spec_from_file_location("esp_mcp_runtime_launcher", LAUNCHER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _select(module, existing: set[Path], **kwargs) -> Path:
    return module.select_runtime_python(is_file=lambda path: path in existing, **kwargs)


def test_explicit_override_has_highest_priority() -> None:
    launcher = _load_launcher()
    override = Path("D:/isolated/python.exe")

    selected = _select(
        launcher,
        {override},
        environment={
            "ESP_MCP_PYTHON": str(override),
            "CONDA_DEFAULT_ENV": "esp-mcp-toolchain",
            "CONDA_EXE": "C:/Users/example/anaconda3/Scripts/conda.exe",
        },
        current_executable="C:/current/python.exe",
        current_prefix="C:/current/esp-mcp-toolchain",
        home=Path("C:/Users/example"),
        os_name="nt",
    )

    assert selected == override


def test_invalid_explicit_override_does_not_fall_back() -> None:
    launcher = _load_launcher()
    conda_python = Path("C:/Users/example/anaconda3/envs/esp-mcp-toolchain/python.exe")

    try:
        _select(
            launcher,
            {conda_python},
            environment={
                "ESP_MCP_PYTHON": "D:/missing/python.exe",
                "CONDA_EXE": "C:/Users/example/anaconda3/Scripts/conda.exe",
            },
            current_executable="C:/global/python.exe",
            current_prefix="C:/global",
            home=Path("C:/Users/example"),
            os_name="nt",
        )
    except RuntimeError as exc:
        assert "ESP_MCP_PYTHON" in str(exc)
        assert "D:/missing/python.exe" in str(exc)
    else:
        raise AssertionError("an invalid explicit override must fail")


def test_current_named_conda_environment_runs_directly() -> None:
    launcher = _load_launcher()
    current = Path("C:/conda/envs/esp-mcp-toolchain/python.exe")

    selected = _select(
        launcher,
        {current},
        environment={"CONDA_DEFAULT_ENV": "esp-mcp-toolchain"},
        current_executable=str(current),
        current_prefix="C:/conda/envs/another-name",
        home=Path("C:/Users/example"),
        os_name="nt",
    )

    assert selected == current


def test_windows_conda_exe_base_is_checked_before_home_fallbacks() -> None:
    launcher = _load_launcher()
    conda_python = Path("D:/Conda/envs/esp-mcp-toolchain/python.exe")
    home_python = Path("C:/Users/example/anaconda3/envs/esp-mcp-toolchain/python.exe")

    selected = _select(
        launcher,
        {conda_python, home_python},
        environment={"CONDA_EXE": "D:/Conda/Scripts/conda.exe"},
        current_executable="C:/global/python.exe",
        current_prefix="C:/global",
        home=Path("C:/Users/example"),
        os_name="nt",
    )

    assert selected == conda_python


def test_posix_miniconda_home_layout_is_supported() -> None:
    launcher = _load_launcher()
    miniconda_python = Path("/home/example/miniconda3/envs/esp-mcp-toolchain/bin/python")

    selected = _select(
        launcher,
        {miniconda_python},
        environment={},
        current_executable="/usr/bin/python3",
        current_prefix="/usr",
        home=Path("/home/example"),
        os_name="posix",
    )

    assert selected == miniconda_python


def test_missing_environment_fails_instead_of_using_global_python() -> None:
    launcher = _load_launcher()

    try:
        _select(
            launcher,
            set(),
            environment={},
            current_executable="C:/Python/python.exe",
            current_prefix="C:/Python",
            home=Path("C:/Users/example"),
            os_name="nt",
        )
    except RuntimeError as exc:
        message = str(exc)
        assert "esp-mcp-toolchain" in message
        assert "ESP_MCP_PYTHON" in message
    else:
        raise AssertionError("the launcher must not silently use global Python")


def test_mcp_config_routes_through_bootstrap_script() -> None:
    config = (SOURCE_ROOT / ".mcp.json").read_text(encoding="utf-8")
    assert '"./scripts/run_mcp_server.py"' in config
    assert '"./toolchain/mcp_server.py"' not in config
