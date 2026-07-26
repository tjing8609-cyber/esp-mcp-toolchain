from __future__ import annotations

import json

from esp_mcp_toolchain.tools import advanced_tools


def _raw_result(marker: str, payload: object, prefix: str = "") -> dict:
    return {
        "ok": True,
        "stdout": prefix + marker + json.dumps(payload),
        "stderr": "",
        "message": "ok",
    }


def test_gpio_status_reads_explicit_pins_without_changing_modes(monkeypatch):
    observed: dict[str, object] = {}

    def fake_execute_code(port: str, code: str, *, timeout_ms: int):
        observed.update(port=port, code=code, timeout_ms=timeout_ms)
        return _raw_result(
            advanced_tools._GPIO_MARKER,
            [
                {"pin": 25, "ok": True, "value": 1},
                {"pin": 32, "ok": True, "value": 0},
            ],
        )

    monkeypatch.setattr(advanced_tools, "execute_code", fake_execute_code)

    result = advanced_tools.esp_gpio_status(port="COM9", pins=[25, 32, 25], capture_ms=1200, allow_program_interrupt=True)

    assert result["ok"] is True
    assert result["requested_pins"] == [25, 32]
    assert result["readings"] == [
        {"pin": 25, "ok": True, "value": 1},
        {"pin": 32, "ok": True, "value": 0},
    ]
    assert result["gpio_read_only"] is True
    assert result["program_interrupted"] is True
    assert result["mode_changed"] is False
    assert observed["port"] == "COM9"
    assert observed["timeout_ms"] == 1200
    assert "machine.Pin(_pin).value()" in str(observed["code"])
    assert "Pin.IN" not in str(observed["code"])
    assert "Pin.OUT" not in str(observed["code"])
    assert ".init(" not in str(observed["code"])


def test_gpio_status_rejects_implicit_or_invalid_pin_sets(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert advanced_tools.esp_gpio_status(port="COM9", pins=[])["error_kind"] == "missing_pins"
    assert advanced_tools.esp_gpio_status(port="COM9", pins=[True])["error_kind"] == "invalid_gpio"
    assert advanced_tools.esp_gpio_status(port="COM9", pins=[49])["error_kind"] == "invalid_gpio"


def test_gpio_status_reports_partial_probe_failure(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._GPIO_MARKER,
            [
                {"pin": 1, "ok": True, "value": 1},
                {"pin": 2, "ok": False, "error": "ValueError('invalid pin')"},
            ],
        ),
    )

    result = advanced_tools.esp_gpio_status(port="COM9", pins=[1, 2], allow_program_interrupt=True)

    assert result["ok"] is False
    assert result["error_kind"] == "gpio_read_partial_failure"
    assert result["failed_count"] == 1




def test_hardware_info_passive_collects_usb_and_reviewed_evidence_without_probe(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "describe_serial_port",
        lambda port: {"enumerated": True, "port": port, "vid": "1A86", "pid": "55D4", "description": "USB UART"},
    )
    monkeypatch.setattr(
        advanced_tools,
        "hardwork_list",
        lambda: {"ok": True, "items": [{"kind": "board_summary", "confidence": 1.0}]},
    )
    monkeypatch.setattr(advanced_tools, "load_mapping", lambda: {"gpio_entries": [{"gpio": "GPIO25"}]})
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("passive mode must not probe")),
    )

    result = advanced_tools.esp_hardware_info(port="COM3", mode="passive")

    assert result["ok"] is True
    assert result["mode"] == "passive"
    assert result["bootloader_entered"] is False
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is True
    assert result["program_interrupted"] is False
    assert result["data"]["usb"]["vid"] == {
        "value": "1A86",
        "evidence": "host_usb_serial_descriptor",
    }
    assert result["data"]["hardwork"]["items"]["evidence"] == "reviewed_project_hardwork_index"
    assert result["data"]["hardwork"]["mapping"]["value"]["gpio_entries"][0]["gpio"] == "GPIO25"
    assert result["data"]["runtime"] == {}


def test_hardware_info_micropython_labels_runtime_evidence_and_partial_errors(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "describe_serial_port",
        lambda port: {"enumerated": True, "port": port, "description": "CH9102"},
    )
    monkeypatch.setattr(advanced_tools, "hardwork_list", lambda: {"ok": True, "items": []})
    monkeypatch.setattr(advanced_tools, "load_mapping", lambda: None)
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._HARDWARE_MARKER,
            {
                "runtime": {
                    "implementation": "micropython",
                    "cpu_frequency_hz": 240000000,
                    "heap_free_bytes": 90112,
                },
                "probe_errors": [{"field": "flash_size_bytes", "error": "ImportError"}],
            },
        ),
    )

    result = advanced_tools.esp_hardware_info(
        port="COM3",
        mode="micropython",
        capture_ms=2000,
        allow_program_interrupt=True,
    )

    assert result["ok"] is True
    assert result["data"]["runtime"]["implementation"]["value"] == "micropython"
    assert (
        result["data"]["runtime"]["cpu_frequency_hz"]["evidence"]
        == "micropython_runtime_raw_repl"
    )
    assert result["probe_errors"] == [{"field": "flash_size_bytes", "error": "ImportError"}]
    assert result["bootloader_entered"] is False
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is False
    assert result["program_interrupted"] is True


def test_hardware_info_rejects_unknown_mode_without_touching_board(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_hardware_info(port="COM3", mode="esptool")

    assert result["ok"] is False
    assert result["error_kind"] == "invalid_hardware_info_mode"





def test_regression_test_runs_explicit_remote_files_and_aggregates(monkeypatch):
    responses = iter(
        [
            _raw_result(
                advanced_tools._REGRESSION_MARKER,
                {"ok": True, "duration_us": 110, "error": None},
                prefix="first output\n",
            ),
            _raw_result(
                advanced_tools._REGRESSION_MARKER,
                {"ok": True, "duration_us": 90, "error": None},
                prefix="second output\n",
            ),
        ]
    )
    observed_code: list[str] = []

    def fake_execute_code(_port: str, code: str, *, timeout_ms: int):
        observed_code.append(code)
        assert timeout_ms == 2500
        return next(responses)

    monkeypatch.setattr(advanced_tools, "execute_code", fake_execute_code)

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/tests/test_led.py", "/tests/test_buzzer.py"],
        capture_ms=2500,
        confirm_execution=True,
    )

    assert result["ok"] is True
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["skipped"] == 0
    assert result["duration_us"] == 200
    assert [item["stdout"] for item in result["results"]] == [
        "first output",
        "second output",
    ]
    assert "/tests/test_led.py" in observed_code[0]
    assert "/tests/test_buzzer.py" in observed_code[1]


def test_regression_test_fail_fast_reports_failed_and_skipped(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._REGRESSION_MARKER,
            {"ok": False, "duration_us": 70, "error": "AssertionError('bad')"},
        ),
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/tests/fail.py", "/tests/not-run.py"],
        fail_fast=True,
        confirm_execution=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "regression_failed"
    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["skipped"] == 1
    assert result["results"][0]["error"] == "AssertionError('bad')"


def test_regression_test_rejects_missing_or_control_character_paths(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert advanced_tools.esp_regression_test(port="COM3", tests=[])["error_kind"] == "missing_tests"
    assert (
        advanced_tools.esp_regression_test(port="COM3", tests=["/bad\npath.py"])["error_kind"]
        == "invalid_remote_path"
    )





def test_performance_profile_returns_wall_time_and_heap_statistics(monkeypatch):
    samples = [
        {
            "iteration": 1,
            "ok": True,
            "duration_us": 100,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 990,
            "memory_delta_bytes": -10,
            "error": None,
        },
        {
            "iteration": 2,
            "ok": True,
            "duration_us": 300,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 970,
            "memory_delta_bytes": -30,
            "error": None,
        },
        {
            "iteration": 3,
            "ok": True,
            "duration_us": 200,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 980,
            "memory_delta_bytes": -20,
            "error": None,
        },
    ]
    observed: dict[str, object] = {}

    def fake_execute_code(port: str, code: str, *, timeout_ms: int):
        observed.update(port=port, code=code, timeout_ms=timeout_ms)
        return _raw_result(
            advanced_tools._PERFORMANCE_MARKER,
            samples,
            prefix="profile output\n",
        )

    monkeypatch.setattr(advanced_tools, "execute_code", fake_execute_code)

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        code="x = sum(range(10))",
        iterations=3,
        capture_ms=4000,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is True
    assert result["profile_kind"] == "instrumented_wall_time"
    assert result["sampling_profiler"] is False
    assert result["timing_us"] == {
        "min": 100,
        "median": 200,
        "mean": 200.0,
        "max": 300,
    }
    assert result["memory_delta_bytes"]["median"] == -20
    assert result["stdout"] == "profile output"
    assert "time.ticks_us()" in str(observed["code"])
    assert "gc.mem_free()" in str(observed["code"])


def test_performance_profile_rejects_ambiguous_target_and_iteration_bounds(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    assert (
        advanced_tools.esp_performance_profile(port="COM3")["error_kind"]
        == "invalid_profile_target"
    )
    assert (
        advanced_tools.esp_performance_profile(
            port="COM3",
            code="pass",
            remote_path="/main.py",
        )["error_kind"]
        == "invalid_profile_target"
    )
    assert (
        advanced_tools.esp_performance_profile(
            port="COM3",
            code="pass",
            iterations=0,
        )["error_kind"]
        == "invalid_iterations"
    )


def test_performance_profile_reports_failed_samples_without_hiding_results(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._PERFORMANCE_MARKER,
            [
                {
                    "iteration": 1,
                    "ok": False,
                    "duration_us": 50,
                    "memory_before_bytes": 1000,
                    "memory_after_bytes": 1000,
                    "memory_delta_bytes": 0,
                    "error": "ValueError('bad')",
                }
            ],
        ),
    )

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        remote_path="/bench.py",
        iterations=1,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "performance_sample_failed"
    assert result["samples"][0]["error"] == "ValueError('bad')"
    assert result["timing_us"] is None

def test_gpio_status_requires_interrupt_confirmation_before_board_access(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_gpio_status(port="COM9", pins=[25])

    assert result["ok"] is False
    assert result["error_kind"] == "program_interrupt_confirmation_required"
    assert result["confirmation_required"] is True


def test_hardware_info_rejects_non_enumerated_port_without_fabricating_usb_evidence(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "describe_serial_port",
        lambda port: {"enumerated": False, "port": port, "description": ""},
    )
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_hardware_info(port="COM404", mode="passive")

    assert result["ok"] is False
    assert result["error_kind"] == "serial_port_not_enumerated"
    assert "data" not in result


def test_hardware_runtime_requires_interrupt_confirmation(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "describe_serial_port",
        lambda port: {"enumerated": True, "port": port, "description": "CH9102"},
    )
    monkeypatch.setattr(advanced_tools, "hardwork_list", lambda: {"ok": True, "items": []})
    monkeypatch.setattr(advanced_tools, "load_mapping", lambda: None)
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_hardware_info(port="COM3", mode="micropython")

    assert result["error_kind"] == "program_interrupt_confirmation_required"
    assert result["confirmation_required"] is True


def test_regression_requires_exact_execution_confirmation(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_regression_test(port="COM3", tests=["/tests/test_led.py"])

    assert result["error_kind"] == "execution_confirmation_required"
    assert result["tests"] == ["/tests/test_led.py"]


def test_performance_requires_repeated_execution_confirmation(monkeypatch):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    result = advanced_tools.esp_performance_profile(port="COM3", code="buzzer.on()", iterations=3)

    assert result["error_kind"] == "repeated_execution_confirmation_required"
    assert result["iterations"] == 3
    assert result["target"] == "inline_code"
