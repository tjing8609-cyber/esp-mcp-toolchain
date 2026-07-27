from __future__ import annotations

import ast
import json
import os
from pathlib import Path


_EXPECTED_PROFILES = {
    "safe": ["safe.runtime_smoke"],
    "hardware_readonly": [
        "safe.runtime_smoke",
        "hardware_readonly.gpio34_key_read",
    ],
    "stateful": [
        "safe.runtime_smoke",
        "stateful.gpio32_led_latch",
    ],
    "all_positive": [
        "safe.runtime_smoke",
        "hardware_readonly.gpio34_key_read",
        "stateful.gpio32_led_latch",
    ],
    "negative_contract": ["negative.intentional_failure"],
}
_EXPECTED_CASE_PATHS = {
    "safe.runtime_smoke": (
        "safe/runtime_smoke.py",
        "/esp_mcp_reg_safe_runtime_smoke.py",
    ),
    "hardware_readonly.gpio34_key_read": (
        "hardware_readonly/gpio34_key_read.py",
        "/esp_mcp_reg_hardware_readonly_gpio34_key_read.py",
    ),
    "stateful.gpio32_led_latch": (
        "stateful/gpio32_led_latch.py",
        "/esp_mcp_reg_stateful_gpio32_led_latch.py",
    ),
    "negative.intentional_failure": (
        "negative/intentional_failure.py",
        "/esp_mcp_reg_negative_intentional_failure.py",
    ),
}
_EXCLUDED_HARDWARE = ["GPIO25", "buzzer", "PWM"]
_SAFE_BLOCKED_IMPORTS = {
    "machine",
    "network",
    "socket",
    "usocket",
    "urequests",
    "requests",
    "os",
    "uos",
}
_SAFE_BLOCKED_CALLS = {"open", "exec", "eval", "compile", "__import__"}
_SAFE_BLOCKED_METHODS = {
    "write",
    "writelines",
    "remove",
    "rename",
    "mkdir",
    "rmdir",
    "unlink",
}


def _source_root() -> Path:
    configured = os.environ.get("ESP_MCP_SOURCE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _resolved_integer_names(tree: ast.AST) -> dict[str, int]:
    resolved: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
            and not isinstance(node.value.value, bool)
        ):
            resolved[node.targets[0].id] = node.value.value
    return resolved


def _pin_numbers(tree: ast.AST) -> set[int]:
    constants = _resolved_integer_names(tree)
    pins: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "Pin":
            first = node.args[0]
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "Pin":
            first = node.args[0]
        else:
            continue
        if (
            isinstance(first, ast.Constant)
            and isinstance(first.value, int)
            and not isinstance(first.value, bool)
        ):
            pins.add(first.value)
        elif isinstance(first, ast.Name) and first.id in constants:
            pins.add(constants[first.id])
        else:
            raise AssertionError("Every curated Pin target must resolve to a static integer.")
    return pins


def _assert_safe_script(tree: ast.AST) -> None:
    assert _import_roots(tree).isdisjoint(_SAFE_BLOCKED_IMPORTS)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in _SAFE_BLOCKED_CALLS
        elif isinstance(node.func, ast.Attribute):
            assert node.func.attr not in _SAFE_BLOCKED_METHODS
    assert _pin_numbers(tree) == set()


def _assert_hardware_readonly_script(tree: ast.AST) -> None:
    assert _import_roots(tree).isdisjoint(_SAFE_BLOCKED_IMPORTS - {"machine"})
    assert _pin_numbers(tree) == {34}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            assert node.func.id not in _SAFE_BLOCKED_CALLS
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        assert node.func.attr not in _SAFE_BLOCKED_METHODS
        assert node.func.attr != "init"
        if node.func.attr == "value":
            assert node.args == []
            assert node.keywords == []
    assert not any(
        isinstance(node, ast.Attribute) and node.attr in {"OUT", "PWM"}
        for node in ast.walk(tree)
    )


def _assert_stateful_led_script(tree: ast.AST) -> None:
    assert _pin_numbers(tree) == {32}
    assert any(isinstance(node, ast.Try) and node.finalbody for node in ast.walk(tree))
    written_values = {
        node.args[0].id
        if isinstance(node.args[0], ast.Name)
        else node.args[0].value
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "value"
            and len(node.args) == 1
            and (
                isinstance(node.args[0], ast.Name)
                or isinstance(node.args[0], ast.Constant)
            )
        )
    }
    assert {"LED_ON", "LED_OFF"}.issubset(written_values)


def test_tracked_micropython_regression_suite_has_bounded_layered_contract():
    regression_root = (
        _source_root() / "examples" / "micropython_project" / "regression"
    )
    required_paths = [
        regression_root / "manifest.json",
        *(
            regression_root / local_path
            for local_path, _remote_path in _EXPECTED_CASE_PATHS.values()
        ),
    ]
    missing = [
        path.relative_to(_source_root()).as_posix()
        for path in required_paths
        if not path.is_file()
    ]
    assert not missing, f"main must track the curated regression assets: {missing}"

    manifest = json.loads(required_paths[0].read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    assert manifest["suite_id"] == "esp-mcp-micropython-regression"
    assert manifest["default_profile"] == "safe"
    assert manifest["profiles"] == _EXPECTED_PROFILES
    assert manifest["excluded_hardware"] == _EXCLUDED_HARDWARE
    assert manifest["runner"] == {
        "backend": "raw_repl",
        "capture_ms": 5000,
        "fail_fast": True,
    }

    cases = manifest["cases"]
    assert isinstance(cases, list)
    assert 1 <= len(cases) <= 32
    by_id = {case["id"]: case for case in cases}
    assert len(by_id) == len(cases)
    assert set(by_id) == set(_EXPECTED_CASE_PATHS)
    assert "negative.intentional_failure" not in manifest["profiles"]["all_positive"]
    assert manifest["profiles"]["negative_contract"] == [
        "negative.intentional_failure"
    ]

    source_by_id: dict[str, str] = {}
    remote_paths: set[str] = set()
    for case_id, (local_path, remote_path) in _EXPECTED_CASE_PATHS.items():
        case = by_id[case_id]
        assert case["tier"] == case_id.split(".", 1)[0]
        assert case["local_path"] == local_path
        assert case["remote_path"] == remote_path
        assert case["requires_execution_confirmation"] is True
        assert case["selected_by_default"] is (case_id == "safe.runtime_smoke")
        assert case["requires_explicit_selection"] is (
            case_id != "safe.runtime_smoke"
        )
        assert case["expected_tool_ok"] is (
            case_id != "negative.intentional_failure"
        )
        assert isinstance(case["side_effects"], list) and case["side_effects"]
        assert isinstance(case["claim_limits"], list) and case["claim_limits"]
        assert remote_path.startswith("/")
        assert remote_path.count("/") == 1
        assert len(remote_path) <= 256
        assert all(control not in remote_path for control in ("\x00", "\r", "\n"))
        assert remote_path not in remote_paths
        remote_paths.add(remote_path)

        source = (regression_root / local_path).read_text(encoding="utf-8")
        compile(source, local_path, "exec")
        source_by_id[case_id] = source

    for case_id, source in source_by_id.items():
        lowered = source.casefold()
        assert "buzzer" not in lowered
        assert "pwm" not in lowered
        tree = ast.parse(source, filename=case_id)
        assert 25 not in _pin_numbers(tree)

    _assert_safe_script(ast.parse(source_by_id["safe.runtime_smoke"]))
    _assert_hardware_readonly_script(
        ast.parse(source_by_id["hardware_readonly.gpio34_key_read"])
    )
    _assert_stateful_led_script(
        ast.parse(source_by_id["stateful.gpio32_led_latch"])
    )
    _assert_safe_script(ast.parse(source_by_id["negative.intentional_failure"]))
