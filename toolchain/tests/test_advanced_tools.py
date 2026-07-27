from __future__ import annotations

import json

import pytest

from esp_mcp_toolchain.tools import advanced_tools, log_tools


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
        tests=["/tests/test_led.py", "/tests/test_key_read.py"],
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
    assert "/tests/test_key_read.py" in observed_code[1]
    assert f"[:{advanced_tools._MAX_REGRESSION_ERROR_CHARS}]" in observed_code[0]


def test_regression_persists_stdout_free_summaries_and_conservative_reset_boundary(
    monkeypatch,
):
    responses = iter(
        [
            _raw_result(
                advanced_tools._REGRESSION_MARKER,
                {"ok": True, "duration_us": 110, "error": None},
                prefix="safe output that must not enter SQLite\n",
            ),
            _raw_result(
                advanced_tools._REGRESSION_MARKER,
                {
                    "ok": False,
                    "duration_us": 70,
                    "error": "AssertionError('intentional regression failure')",
                },
                prefix="negative output that must not enter SQLite\n",
            ),
        ]
    )
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: next(responses),
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=[
            "/esp_mcp_reg_safe_runtime_smoke.py",
            "/esp_mcp_reg_negative_intentional_failure.py",
        ],
        fail_fast=False,
        confirm_execution=True,
    )

    assert result["result_summaries"] == [
        {
            "path": "/esp_mcp_reg_safe_runtime_smoke.py",
            "ok": True,
            "duration_us": 110,
            "error_kind": None,
        },
        {
            "path": "/esp_mcp_reg_negative_intentional_failure.py",
            "ok": False,
            "duration_us": 70,
            "error_kind": "test_failed",
        },
    ]
    assert result["program_interrupted"] is True
    assert result["reset_command_sent"] is False
    assert result["physical_reset_excluded"] is False

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    persisted = complete["payload_json"]
    assert persisted["result_summaries"] == result["result_summaries"]
    assert "results" not in persisted
    assert "stdout" not in persisted
    assert all(
        set(summary) == {"path", "ok", "duration_us", "error_kind"}
        for summary in persisted["result_summaries"]
    )


def test_regression_bounds_failure_text_before_returning(monkeypatch):
    oversized_error = "AssertionError('" + ("x" * 1000) + "-sensitive-tail')"
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._REGRESSION_MARKER,
            {"ok": False, "duration_us": 70, "error": oversized_error},
        ),
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/esp_mcp_reg_negative_intentional_failure.py"],
        confirm_execution=True,
    )

    assert result["ok"] is False
    assert len(result["results"][0]["error"]) == advanced_tools._MAX_REGRESSION_ERROR_CHARS
    assert "sensitive-tail" not in result["results"][0]["error"]
    assert result["result_summaries"][0]["error_kind"] == "test_failed"

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    serialized = json.dumps(complete["payload_json"], ensure_ascii=False)
    assert "sensitive-tail" not in serialized
    assert oversized_error not in serialized


def test_regression_rejects_oversized_marker_before_json_decode(monkeypatch):
    oversized_payload = json.dumps(
        {
            "ok": False,
            "duration_us": 1,
            "error": "oversized-regression-secret"
            + ("x" * advanced_tools._MAX_REGRESSION_MARKER_BYTES),
        }
    )
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: {
            "ok": True,
            "stdout": advanced_tools._REGRESSION_MARKER + oversized_payload,
            "stderr": "",
            "message": "ok",
        },
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/esp_mcp_reg_safe_runtime_smoke.py"],
        confirm_execution=True,
    )

    assert result["ok"] is False
    assert result["result_summaries"][0]["error_kind"] == "probe_result_too_large"

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    serialized = json.dumps(complete["payload_json"], ensure_ascii=False)
    assert "oversized-regression-secret" not in serialized
    assert "results" not in complete["payload_json"]


def test_regression_rejects_deeply_nested_marker_without_escaping_tool(monkeypatch):
    deeply_nested_payload = ("[" * 5000) + "0" + ("]" * 5000)
    assert len(deeply_nested_payload.encode("utf-8")) < advanced_tools._MAX_REGRESSION_MARKER_BYTES
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: {
            "ok": True,
            "stdout": advanced_tools._REGRESSION_MARKER + deeply_nested_payload,
            "stderr": "",
            "message": "ok",
        },
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/esp_mcp_reg_safe_runtime_smoke.py"],
        confirm_execution=True,
    )

    assert result["ok"] is False
    assert result["result_summaries"][0]["error_kind"] == "probe_result_invalid"

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    serialized = json.dumps(complete["payload_json"], ensure_ascii=False)
    assert "results" not in complete["payload_json"]
    assert "stdout" not in complete["payload_json"]
    assert deeply_nested_payload not in serialized


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": "yes", "duration_us": 1, "error": None},
        {"ok": True, "duration_us": True, "error": None},
        {"ok": True, "duration_us": 1_000_001, "error": None},
        {"ok": False, "duration_us": 1, "error": None},
    ],
)
def test_regression_rejects_invalid_probe_contract(monkeypatch, payload):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._REGRESSION_MARKER,
            payload,
        ),
    )

    result = advanced_tools.esp_regression_test(
        port="COM3",
        tests=["/esp_mcp_reg_safe_runtime_smoke.py"],
        capture_ms=1000,
        confirm_execution=True,
    )

    assert result["ok"] is False
    assert result["result_summaries"] == [
        {
            "path": "/esp_mcp_reg_safe_runtime_smoke.py",
            "ok": False,
            "duration_us": None,
            "error_kind": "probe_result_invalid",
        }
    ]


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
            "error_truncated": False,
        },
        {
            "iteration": 2,
            "ok": True,
            "duration_us": 300,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 970,
            "memory_delta_bytes": -30,
            "error": None,
            "error_truncated": False,
        },
        {
            "iteration": 3,
            "ok": True,
            "duration_us": 200,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 980,
            "memory_delta_bytes": -20,
            "error": None,
            "error_truncated": False,
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
    assert "error_truncated" in str(observed["code"])
    assert f"[:{advanced_tools._MAX_PROFILE_ERROR_CHARS}]" in str(observed["code"])

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert complete["payload_json"]["samples"] == samples
    assert complete["payload_json"]["timing_us"] == result["timing_us"]
    assert (
        complete["payload_json"]["memory_delta_bytes"]
        == result["memory_delta_bytes"]
    )
    assert complete["payload_json"]["sampling_profiler"] is False
    assert "stdout" not in complete["payload_json"]
    assert "code" not in complete["payload_json"]


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
                    "error_truncated": False,
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

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    assert complete["payload_json"]["samples"] == result["samples"]
    assert complete["payload_json"]["timing_us"] is None
    assert complete["payload_json"]["memory_delta_bytes"] is None
    assert complete["payload_json"]["sampling_profiler"] is False


def test_performance_profile_bounds_sample_errors_before_persisting(monkeypatch):
    oversized_error = ("\x00" * 300) + "💥-sensitive-tail"
    samples = [
        {
            "iteration": index,
            "ok": False,
            "duration_us": 50,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 1000,
            "memory_delta_bytes": 0,
            "error": oversized_error,
            "error_truncated": False,
            "unexpected": "drop-me",
        }
        for index in range(1, 51)
    ]
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._PERFORMANCE_MARKER,
            samples,
        ),
    )

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        code="raise ValueError('x')",
        iterations=50,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is False
    assert len(result["samples"]) == 50
    assert all(
        len(item["error"]) == advanced_tools._MAX_PROFILE_ERROR_CHARS
        for item in result["samples"]
    )
    assert all(item["error_truncated"] is True for item in result["samples"])
    assert all("sensitive-tail" not in item["error"] for item in result["samples"])
    assert all("unexpected" not in item for item in result["samples"])

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    serialized = json.dumps(
        complete["payload_json"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(serialized) <= advanced_tools._MAX_PROFILE_MARKER_BYTES
    assert b"sensitive-tail" not in serialized
    assert b"drop-me" not in serialized


def test_performance_profile_rejects_unbounded_integer_fields(monkeypatch):
    samples = [
        {
            "iteration": 1,
            "ok": True,
            "duration_us": 10**4000,
            "memory_before_bytes": 1000,
            "memory_after_bytes": 990,
            "memory_delta_bytes": -10,
            "error": None,
            "error_truncated": False,
        }
    ]
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._PERFORMANCE_MARKER,
            samples,
        ),
    )

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        code="pass",
        iterations=1,
        capture_ms=1000,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "probe_result_invalid"
    assert "samples" not in result


def test_performance_profile_rejects_oversized_marker_before_json_decode(monkeypatch):
    oversized_payload = json.dumps(
        {"secret": "oversized-marker-secret" + ("x" * advanced_tools._MAX_PROFILE_MARKER_BYTES)}
    )
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: {
            "ok": True,
            "stdout": advanced_tools._PERFORMANCE_MARKER + oversized_payload,
            "stderr": "",
            "message": "ok",
        },
    )

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        code="pass",
        iterations=1,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "probe_result_too_large"
    assert result["structured_payload_limit_bytes"] == advanced_tools._MAX_PROFILE_MARKER_BYTES

    logs = log_tools.esp_logs_get(result["run_id"], tail=10)
    complete = next(event for event in logs["events"] if event["phase"] == "complete")
    serialized = json.dumps(complete["payload_json"], ensure_ascii=False)
    assert complete["payload_json"]["error_kind"] == "probe_result_too_large"
    assert "samples" not in complete["payload_json"]
    assert "oversized-marker-secret" not in serialized


@pytest.mark.parametrize(
    ("samples", "iterations"),
    [
        ([{"iteration": 1, "ok": True}], 1),
        (
            [
                {
                    "iteration": True,
                    "ok": True,
                    "duration_us": 1,
                    "memory_before_bytes": 10,
                    "memory_after_bytes": 9,
                    "memory_delta_bytes": -1,
                    "error": None,
                    "error_truncated": False,
                }
            ],
            1,
        ),
        (
            [
                {
                    "iteration": 1,
                    "ok": True,
                    "duration_us": 1,
                    "memory_before_bytes": 10,
                    "memory_after_bytes": 9,
                    "memory_delta_bytes": 0,
                    "error": None,
                    "error_truncated": False,
                }
            ],
            1,
        ),
        (
            [
                {
                    "iteration": 1,
                    "ok": True,
                    "duration_us": 1,
                    "memory_before_bytes": 10,
                    "memory_after_bytes": 9,
                    "memory_delta_bytes": -1,
                    "error": None,
                    "error_truncated": False,
                }
            ],
            2,
        ),
    ],
)
def test_performance_profile_rejects_invalid_sample_contract(
    monkeypatch,
    samples,
    iterations,
):
    monkeypatch.setattr(
        advanced_tools,
        "execute_code",
        lambda *_args, **_kwargs: _raw_result(
            advanced_tools._PERFORMANCE_MARKER,
            samples,
        ),
    )

    result = advanced_tools.esp_performance_profile(
        port="COM3",
        code="pass",
        iterations=iterations,
        confirm_repeated_execution=True,
    )

    assert result["ok"] is False
    assert result["error_kind"] == "probe_result_invalid"
    assert "samples" not in result

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
