from __future__ import annotations

import json
import statistics
from typing import Annotated, Any, Literal

from pydantic import Field

from ..backends.pyserial_backend import describe_serial_port
from ..backends.raw_repl_backend import execute_code
from ..config import get_selected_port
from ..errors import execution_error
from ..hardwork.mapping_writer import load_mapping
from .hardwork_tools import hardwork_list
from .log_tools import logged_task


_GPIO_MARKER = "__ESP_MCP_GPIO_STATUS_V1__"
_HARDWARE_MARKER = "__ESP_MCP_HARDWARE_INFO_V1__"
_REGRESSION_MARKER = "__ESP_MCP_REGRESSION_V1__"
_PERFORMANCE_MARKER = "__ESP_MCP_PERFORMANCE_V1__"
_MAX_GPIO_COUNT = 32
_MAX_REGRESSION_ERROR_CHARS = 256
_MAX_REGRESSION_MARKER_BYTES = 16_384
_MAX_PROFILE_ERROR_CHARS = 256
_MAX_PROFILE_HEAP_BYTES = (1 << 32) - 1
_MAX_PROFILE_MARKER_BYTES = 131_072
_MIN_CAPTURE_MS = 100
_MAX_CAPTURE_MS = 30_000

RawReplBackend = Literal["raw_repl"]
HardwareInfoMode = Literal["passive", "micropython"]
CaptureMs = Annotated[int, Field(ge=_MIN_CAPTURE_MS, le=_MAX_CAPTURE_MS)]
GpioPin = Annotated[int, Field(ge=0, le=48)]
GpioPins = Annotated[list[GpioPin], Field(min_length=1, max_length=_MAX_GPIO_COUNT)] | None
RemoteTestPath = Annotated[str, Field(min_length=1, max_length=256)]
RemoteTests = Annotated[list[RemoteTestPath], Field(min_length=1, max_length=32)] | None
Iterations = Annotated[int, Field(ge=1, le=50)]


def _selected_port_or_error(port: str | None, *, tool: str) -> tuple[str | None, dict[str, Any] | None]:
    selected_port = port or get_selected_port()
    if selected_port:
        return selected_port, None
    return None, execution_error(
        "serial_port_not_selected",
        "No serial port was provided or selected.",
        tool=tool,
        suggested_next_actions=["Run esp_port_list", "Run esp_port_select with the confirmed board port"],
    )


def _validate_raw_repl(backend: str, *, tool: str) -> dict[str, Any] | None:
    if backend == "raw_repl":
        return None
    return execution_error(
        "unsupported_backend",
        f"Unsupported backend: {backend}",
        tool=tool,
        suggested_next_actions=["Use backend=raw_repl on a board running MicroPython"],
    )


def _validate_capture_ms(capture_ms: int, *, tool: str) -> dict[str, Any] | None:
    if isinstance(capture_ms, bool) or not isinstance(capture_ms, int):
        return execution_error("invalid_capture_ms", "capture_ms must be an integer.", tool=tool)
    if _MIN_CAPTURE_MS <= capture_ms <= _MAX_CAPTURE_MS:
        return None
    return execution_error(
        "invalid_capture_ms",
        f"capture_ms must be between {_MIN_CAPTURE_MS} and {_MAX_CAPTURE_MS}.",
        tool=tool,
    )


def _marked_payload(
    execution: dict[str, Any],
    marker: str,
    *,
    tool: str,
    max_payload_bytes: int | None = None,
) -> tuple[Any | None, str, dict[str, Any] | None]:
    stdout = str(execution.get("stdout") or "")
    stderr = str(execution.get("stderr") or "")
    if execution.get("ok") is False:
        return None, stdout, execution_error(
            str(execution.get("error_kind") or "raw_repl_execution_failed"),
            str(execution.get("message") or "MicroPython raw REPL execution failed."),
            tool=tool,
            stdout=stdout[-16_000:],
            stderr=stderr[-16_000:],
        )
    marker_position = stdout.rfind(marker)
    if marker_position < 0:
        return None, stdout, execution_error(
            "probe_result_missing",
            "MicroPython completed without the expected structured result marker.",
            tool=tool,
            stdout=stdout[-16_000:],
            stderr=stderr[-16_000:],
        )
    human_output = stdout[:marker_position].rstrip("\r\n")
    encoded_payload = stdout[marker_position + len(marker) :].strip()
    encoded_payload_bytes = len(encoded_payload.encode("utf-8"))
    if (
        max_payload_bytes is not None
        and encoded_payload_bytes > max_payload_bytes
    ):
        return None, human_output, execution_error(
            "probe_result_too_large",
            f"Structured probe result exceeds {max_payload_bytes} UTF-8 bytes.",
            tool=tool,
            structured_payload_bytes=encoded_payload_bytes,
            structured_payload_limit_bytes=max_payload_bytes,
            stdout=human_output[-16_000:],
            stderr=stderr[-16_000:],
        )
    try:
        payload = json.loads(encoded_payload)
    except (RecursionError, TypeError, ValueError) as exc:
        return None, human_output, execution_error(
            "probe_result_invalid",
            f"MicroPython returned an invalid structured result: {exc}",
            tool=tool,
            stdout=(
                human_output[-16_000:]
                if max_payload_bytes is not None
                else stdout[-16_000:]
            ),
            stderr=stderr[-16_000:],
        )
    return payload, human_output, None


@logged_task(
    task_type="gpio_status",
    selected_port_arg="port",
    payload_args=("backend", "pins", "capture_ms", "allow_program_interrupt"),
)
def esp_gpio_status(
    port: str | None = None,
    backend: RawReplBackend = "raw_repl",
    pins: GpioPins = None,
    capture_ms: CaptureMs = 3000,
    allow_program_interrupt: bool = False,
) -> dict[str, Any]:
    """Read requested GPIO levels after explicit permission to interrupt the program."""

    tool = "esp_gpio_status"
    backend_error = _validate_raw_repl(backend, tool=tool)
    if backend_error:
        return backend_error
    timeout_error = _validate_capture_ms(capture_ms, tool=tool)
    if timeout_error:
        return timeout_error
    if not pins:
        return execution_error(
            "missing_pins",
            "pins must contain at least one explicit GPIO number.",
            tool=tool,
        )
    if len(pins) > _MAX_GPIO_COUNT:
        return execution_error(
            "too_many_pins",
            f"At most {_MAX_GPIO_COUNT} GPIO numbers may be read in one call.",
            tool=tool,
        )

    normalized_pins: list[int] = []
    for pin in pins:
        if isinstance(pin, bool) or not isinstance(pin, int) or not 0 <= pin <= 48:
            return execution_error(
                "invalid_gpio",
                "Every GPIO number must be an integer between 0 and 48.",
                tool=tool,
                invalid_value=pin,
            )
        if pin not in normalized_pins:
            normalized_pins.append(pin)

    if not allow_program_interrupt:
        return execution_error(
            "program_interrupt_confirmation_required",
            "GPIO runtime inspection enters raw REPL and interrupts the current MicroPython program.",
            tool=tool,
            confirmation_required=True,
            requested_pins=normalized_pins,
            suggested_next_actions=[
                "Confirm the exact GPIO list",
                "Call again with allow_program_interrupt=true only after accepting the interruption",
            ],
        )

    selected_port, port_error = _selected_port_or_error(port, tool=tool)
    if port_error:
        return port_error
    code = (
        "import machine, ujson\n"
        f"_pins = {normalized_pins!r}\n"
        "_items = []\n"
        "for _pin in _pins:\n"
        "    try:\n"
        "        _items.append({'pin': _pin, 'ok': True, 'value': int(machine.Pin(_pin).value())})\n"
        "    except Exception as _exc:\n"
        "        _items.append({'pin': _pin, 'ok': False, 'error': repr(_exc)})\n"
        f"print({_GPIO_MARKER!r} + ujson.dumps(_items))\n"
    )
    execution = execute_code(selected_port, code, timeout_ms=capture_ms)
    payload, _human_output, probe_error = _marked_payload(execution, _GPIO_MARKER, tool=tool)
    if probe_error:
        probe_error.update({"port": selected_port, "backend": backend})
        return probe_error
    if not isinstance(payload, list):
        return execution_error(
            "probe_result_invalid",
            "GPIO probe result must be a list.",
            tool=tool,
            port=selected_port,
            backend=backend,
        )

    readings = [item for item in payload if isinstance(item, dict)]
    failed_count = sum(1 for item in readings if item.get("ok") is not True)
    complete = len(readings) == len(normalized_pins) and failed_count == 0
    result: dict[str, Any] = {
        "ok": complete,
        "tool": tool,
        "tool_name": tool,
        "tools名称": tool,
        "implemented": True,
        "port": selected_port,
        "backend": backend,
        "gpio_read_only": True,
        "mode_changed": False,
        "program_interrupted": True,
        "requested_pins": normalized_pins,
        "readings": readings,
        "failed_count": failed_count,
        "evidence": "micropython_runtime_raw_repl",
        "message": (
            f"Read {len(readings)} GPIO level(s) after interrupting the current program; GPIO modes were not changed."
            if complete
            else "One or more requested GPIO levels could not be read."
        ),
    }
    if not complete:
        result["error_kind"] = "gpio_read_partial_failure"
        result["recoverable"] = True
        result["suggested_next_actions"] = [
            "Verify the GPIO number against the reviewed hardware mapping",
            "Confirm the board is running MicroPython and retry",
        ]
    return result



def _evidence_fields(values: dict[str, Any], evidence: str) -> dict[str, dict[str, Any]]:
    return {
        key: {"value": value, "evidence": evidence}
        for key, value in values.items()
    }


@logged_task(
    task_type="hardware_info",
    selected_port_arg="port",
    payload_args=("mode", "backend", "capture_ms", "allow_program_interrupt"),
)
def esp_hardware_info(
    port: str | None = None,
    mode: HardwareInfoMode = "passive",
    backend: RawReplBackend = "raw_repl",
    capture_ms: CaptureMs = 3000,
    allow_program_interrupt: bool = False,
) -> dict[str, Any]:
    """Collect USB/reviewed context and optionally query a MicroPython runtime."""

    tool = "esp_hardware_info"
    if mode not in {"passive", "micropython"}:
        return execution_error(
            "invalid_hardware_info_mode",
            "mode must be passive or micropython.",
            tool=tool,
        )
    timeout_error = _validate_capture_ms(capture_ms, tool=tool)
    if timeout_error:
        return timeout_error
    selected_port, port_error = _selected_port_or_error(port, tool=tool)
    if port_error:
        return port_error

    usb_descriptor = describe_serial_port(selected_port)
    if usb_descriptor.get("enumerated") is not True:
        return execution_error(
            "serial_port_not_enumerated",
            f"The requested serial port is not currently enumerated: {selected_port}",
            tool=tool,
            port=selected_port,
            suggested_next_actions=["Run esp_port_list", "Reconnect the board and retry"],
        )
    usb = _evidence_fields(usb_descriptor, "host_usb_serial_descriptor")
    reviewed_items = hardwork_list().get("items", [])
    reviewed_mapping = load_mapping()
    hardwork = {
        "items": {
            "value": reviewed_items,
            "evidence": "reviewed_project_hardwork_index",
        },
        "mapping": {
            "value": reviewed_mapping,
            "evidence": "reviewed_project_hardware_mapping",
        },
    }
    data: dict[str, Any] = {
        "usb": usb,
        "hardwork": hardwork,
        "runtime": {},
    }
    probe_errors: list[dict[str, str]] = []

    if mode == "micropython":
        if not allow_program_interrupt:
            return execution_error(
                "program_interrupt_confirmation_required",
                "MicroPython runtime collection enters raw REPL and interrupts the current program.",
                tool=tool,
                confirmation_required=True,
                suggested_next_actions=[
                    "Use mode=passive if interruption is not acceptable",
                    "Call again with allow_program_interrupt=true only after accepting the interruption",
                ],
            )
        backend_error = _validate_raw_repl(backend, tool=tool)
        if backend_error:
            return backend_error
        code = (
            "import sys, os, gc, machine, ujson, ubinascii\n"
            "_runtime = {}\n"
            "_errors = []\n"
            "try:\n"
            "    _runtime['implementation'] = getattr(sys.implementation, 'name', str(sys.implementation))\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'implementation', 'error': repr(_exc)})\n"
            "try:\n"
            "    _runtime['firmware_version'] = str(getattr(sys.implementation, 'version', ''))\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'firmware_version', 'error': repr(_exc)})\n"
            "try:\n"
            "    _runtime['uname'] = [str(_part) for _part in os.uname()]\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'uname', 'error': repr(_exc)})\n"
            "try:\n"
            "    _runtime['cpu_frequency_hz'] = int(machine.freq())\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'cpu_frequency_hz', 'error': repr(_exc)})\n"
            "try:\n"
            "    _runtime['unique_id_hex'] = ubinascii.hexlify(machine.unique_id()).decode()\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'unique_id_hex', 'error': repr(_exc)})\n"
            "try:\n"
            "    _runtime['heap_free_bytes'] = int(gc.mem_free())\n"
            "    _runtime['heap_alloc_bytes'] = int(gc.mem_alloc())\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'heap', 'error': repr(_exc)})\n"
            "try:\n"
            "    import esp\n"
            "    _runtime['flash_size_bytes'] = int(esp.flash_size())\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'flash_size_bytes', 'error': repr(_exc)})\n"
            "try:\n"
            "    _fs = os.statvfs('/')\n"
            "    _runtime['filesystem_total_bytes'] = int(_fs[0] * _fs[2])\n"
            "    _runtime['filesystem_free_bytes'] = int(_fs[0] * _fs[3])\n"
            "except Exception as _exc:\n"
            "    _errors.append({'field': 'filesystem', 'error': repr(_exc)})\n"
            f"print({_HARDWARE_MARKER!r} + ujson.dumps({{'runtime': _runtime, 'probe_errors': _errors}}))\n"
        )
        execution = execute_code(selected_port, code, timeout_ms=capture_ms)
        payload, _human_output, probe_error = _marked_payload(
            execution,
            _HARDWARE_MARKER,
            tool=tool,
        )
        if probe_error:
            probe_error.update(
                {
                    "port": selected_port,
                    "backend": backend,
                    "mode": mode,
                    "bootloader_entered": False,
                }
            )
            return probe_error
        if not isinstance(payload, dict) or not isinstance(payload.get("runtime"), dict):
            return execution_error(
                "probe_result_invalid",
                "Hardware runtime probe must return an object.",
                tool=tool,
                port=selected_port,
            )
        data["runtime"] = _evidence_fields(
            payload["runtime"],
            "micropython_runtime_raw_repl",
        )
        if isinstance(payload.get("probe_errors"), list):
            probe_errors = [
                item for item in payload["probe_errors"] if isinstance(item, dict)
            ]

    return {
        "ok": True,
        "tool": tool,
        "tool_name": tool,
        "tools名称": tool,
        "implemented": True,
        "port": selected_port,
        "mode": mode,
        "backend": backend if mode == "micropython" else "passive",
        "data": data,
        "probe_errors": probe_errors,
        "bootloader_entered": False,
        "reset_command_sent": False,
        "physical_reset_excluded": mode == "passive",
        "program_interrupted": mode == "micropython",
        "message": (
            "Collected passive host and reviewed hardware information."
            if mode == "passive"
            else "Collected host, reviewed, and MicroPython runtime hardware information."
        ),
    }


def _validate_remote_paths(paths: list[str] | None, *, tool: str) -> tuple[list[str] | None, dict[str, Any] | None]:
    if not paths:
        return None, execution_error(
            "missing_tests",
            "tests must contain at least one remote MicroPython file path.",
            tool=tool,
        )
    if len(paths) > 32:
        return None, execution_error(
            "too_many_tests",
            "At most 32 remote test files may run in one regression call.",
            tool=tool,
        )
    normalized: list[str] = []
    for path in paths:
        if (
            not isinstance(path, str)
            or not path.strip()
            or len(path) > 256
            or "\x00" in path
            or "\n" in path
            or "\r" in path
        ):
            return None, execution_error(
                "invalid_remote_path",
                "Every remote test path must be a non-empty string of at most 256 characters.",
                tool=tool,
            )
        if path not in normalized:
            normalized.append(path)
    return normalized, None


def _normalize_regression_payload(
    payload: Any,
    *,
    capture_ms: int,
    tool: str,
    port: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, execution_error(
            "probe_result_invalid",
            "Regression probe must return an object.",
            tool=tool,
            port=port,
        )

    ok = payload.get("ok")
    duration_us = payload.get("duration_us")
    error = payload.get("error")
    if not isinstance(ok, bool):
        return None, execution_error(
            "probe_result_invalid",
            "Regression probe ok must be a boolean.",
            tool=tool,
            port=port,
        )
    if (
        isinstance(duration_us, bool)
        or not isinstance(duration_us, int)
        or not 0 <= duration_us <= capture_ms * 1000
    ):
        return None, execution_error(
            "probe_result_invalid",
            "Regression probe duration_us is outside the accepted range.",
            tool=tool,
            port=port,
        )
    if (ok and error is not None) or (not ok and not isinstance(error, str)):
        return None, execution_error(
            "probe_result_invalid",
            "Regression probe error does not match the test status.",
            tool=tool,
            port=port,
        )

    normalized_error = error
    if isinstance(error, str) and len(error) > _MAX_REGRESSION_ERROR_CHARS:
        normalized_error = error[:_MAX_REGRESSION_ERROR_CHARS]
    return {
        "ok": ok,
        "duration_us": duration_us,
        "error": normalized_error,
        "error_kind": None if ok else "test_failed",
    }, None


@logged_task(
    task_type="regression_test",
    selected_port_arg="port",
    payload_args=("backend", "tests", "fail_fast", "capture_ms", "confirm_execution"),
    result_payload_keys=("result_summaries",),
)
def esp_regression_test(
    port: str | None = None,
    backend: RawReplBackend = "raw_repl",
    tests: RemoteTests = None,
    fail_fast: bool = True,
    capture_ms: CaptureMs = 5000,
    confirm_execution: bool = False,
) -> dict[str, Any]:
    """Run explicit remote MicroPython test files and return per-test evidence."""

    tool = "esp_regression_test"
    backend_error = _validate_raw_repl(backend, tool=tool)
    if backend_error:
        return backend_error
    timeout_error = _validate_capture_ms(capture_ms, tool=tool)
    if timeout_error:
        return timeout_error
    normalized_tests, tests_error = _validate_remote_paths(tests, tool=tool)
    if tests_error:
        return tests_error
    if not confirm_execution:
        return execution_error(
            "execution_confirmation_required",
            "Regression tests execute the listed remote files and may have hardware side effects.",
            tool=tool,
            confirmation_required=True,
            tests=normalized_tests,
            suggested_next_actions=[
                "Review and confirm the exact remote test paths",
                "Call again with confirm_execution=true",
            ],
        )
    selected_port, port_error = _selected_port_or_error(port, tool=tool)
    if port_error:
        return port_error

    results: list[dict[str, Any]] = []
    for remote_path in normalized_tests or []:
        code = (
            "import time, ujson\n"
            "_ok = True\n"
            "_error = None\n"
            "_start = time.ticks_us()\n"
            "try:\n"
            f"    exec(compile(open({remote_path!r}).read(), {remote_path!r}, 'exec'), {{'__name__': '__main__'}})\n"
            "except Exception as _exc:\n"
            "    _ok = False\n"
            "    _error = repr(_exc)\n"
            f"    if len(_error) > {_MAX_REGRESSION_ERROR_CHARS}:\n"
            f"        _error = _error[:{_MAX_REGRESSION_ERROR_CHARS}]\n"
            "_duration = time.ticks_diff(time.ticks_us(), _start)\n"
            f"print({_REGRESSION_MARKER!r} + ujson.dumps({{'ok': _ok, 'duration_us': _duration, 'error': _error}}))\n"
        )
        execution = execute_code(selected_port, code, timeout_ms=capture_ms)
        payload, human_output, probe_error = _marked_payload(
            execution,
            _REGRESSION_MARKER,
            tool=tool,
            max_payload_bytes=_MAX_REGRESSION_MARKER_BYTES,
        )
        if probe_error:
            item = {
                "path": remote_path,
                "ok": False,
                "duration_us": None,
                "stdout": human_output,
                "error": probe_error.get("message"),
                "error_kind": probe_error.get("error_kind"),
            }
        else:
            normalized_payload, payload_error = _normalize_regression_payload(
                payload,
                capture_ms=capture_ms,
                tool=tool,
                port=selected_port,
            )
            if payload_error:
                item = {
                    "path": remote_path,
                    "ok": False,
                    "duration_us": None,
                    "stdout": human_output,
                    "error": payload_error.get("message"),
                    "error_kind": payload_error.get("error_kind"),
                }
            else:
                assert normalized_payload is not None
                item = {
                    "path": remote_path,
                    "ok": normalized_payload["ok"],
                    "duration_us": normalized_payload["duration_us"],
                    "stdout": human_output,
                    "error_kind": normalized_payload["error_kind"],
                }
                if normalized_payload["error"] is not None:
                    item["error"] = normalized_payload["error"]
        results.append(item)
        if fail_fast and item["ok"] is False:
            break

    result_summaries = [
        {
            "path": item["path"],
            "ok": item["ok"] is True,
            "duration_us": (
                item["duration_us"]
                if isinstance(item.get("duration_us"), int)
                and not isinstance(item.get("duration_us"), bool)
                else None
            ),
            "error_kind": item.get("error_kind"),
        }
        for item in results
    ]
    passed = sum(1 for item in results if item["ok"] is True)
    failed = sum(1 for item in results if item["ok"] is False)
    skipped = len(normalized_tests or []) - len(results)
    duration_us = sum(
        int(item["duration_us"])
        for item in results
        if isinstance(item.get("duration_us"), int)
    )
    response: dict[str, Any] = {
        "ok": failed == 0 and skipped == 0,
        "tool": tool,
        "tool_name": tool,
        "tools名称": tool,
        "implemented": True,
        "port": selected_port,
        "backend": backend,
        "execution_confirmed": True,
        "program_interrupted": True,
        "reset_command_sent": False,
        "physical_reset_excluded": False,
        "results": results,
        "result_summaries": result_summaries,
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_us": duration_us,
        "evidence": "micropython_runtime_raw_repl",
        "message": f"Regression run completed: {passed} passed, {failed} failed, {skipped} skipped.",
    }
    if response["ok"] is False:
        response["error_kind"] = "regression_failed"
        response["recoverable"] = True
        response["suggested_next_actions"] = [
            "Inspect the first failed result and its stdout",
            "Fix or re-upload the remote test file",
            "Run the regression set again",
        ]
    return response


def _summary(values: list[int]) -> dict[str, float | int] | None:
    if not values:
        return None
    return {
        "min": min(values),
        "median": statistics.median(values),
        "mean": statistics.fmean(values),
        "max": max(values),
    }


def _normalize_performance_samples(
    payload: Any,
    *,
    iterations: int,
    capture_ms: int,
    tool: str,
    port: str,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
    if not isinstance(payload, list) or len(payload) != iterations:
        return None, execution_error(
            "probe_result_invalid",
            "Performance probe sample count must equal iterations.",
            tool=tool,
            port=port,
        )

    samples: list[dict[str, Any]] = []
    for expected_iteration, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            return None, execution_error(
                "probe_result_invalid",
                "Every performance sample must be an object.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )

        iteration = item.get("iteration")
        ok = item.get("ok")
        duration_us = item.get("duration_us")
        memory_before_bytes = item.get("memory_before_bytes")
        memory_after_bytes = item.get("memory_after_bytes")
        memory_delta_bytes = item.get("memory_delta_bytes")
        error = item.get("error")
        error_truncated = item.get("error_truncated")

        integer_values = (
            iteration,
            duration_us,
            memory_before_bytes,
            memory_after_bytes,
            memory_delta_bytes,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in integer_values
        ):
            return None, execution_error(
                "probe_result_invalid",
                "Performance sample counters, timing, and memory fields must be integers.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )
        if iteration != expected_iteration:
            return None, execution_error(
                "probe_result_invalid",
                "Performance sample iteration numbers must be consecutive.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )
        if not isinstance(ok, bool) or not isinstance(error_truncated, bool):
            return None, execution_error(
                "probe_result_invalid",
                "Performance sample ok and error_truncated fields must be booleans.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )
        if (
            not 0 <= duration_us <= capture_ms * 1000
            or not 0 <= memory_before_bytes <= _MAX_PROFILE_HEAP_BYTES
            or not 0 <= memory_after_bytes <= _MAX_PROFILE_HEAP_BYTES
        ):
            return None, execution_error(
                "probe_result_invalid",
                "Performance timing or heap byte count is outside the accepted range.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )
        if memory_delta_bytes != memory_after_bytes - memory_before_bytes:
            return None, execution_error(
                "probe_result_invalid",
                "Performance sample memory delta does not match after minus before.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )
        if (ok and (error is not None or error_truncated)) or (
            not ok and not isinstance(error, str)
        ):
            return None, execution_error(
                "probe_result_invalid",
                "Performance sample error fields do not match the sample status.",
                tool=tool,
                port=port,
                invalid_sample_index=expected_iteration,
            )

        normalized_error = error
        normalized_error_truncated = error_truncated
        if isinstance(error, str) and len(error) > _MAX_PROFILE_ERROR_CHARS:
            normalized_error = error[:_MAX_PROFILE_ERROR_CHARS]
            normalized_error_truncated = True

        samples.append(
            {
                "iteration": iteration,
                "ok": ok,
                "duration_us": duration_us,
                "memory_before_bytes": memory_before_bytes,
                "memory_after_bytes": memory_after_bytes,
                "memory_delta_bytes": memory_delta_bytes,
                "error": normalized_error,
                "error_truncated": normalized_error_truncated,
            }
        )
    return samples, None


@logged_task(
    task_type="performance_profile",
    selected_port_arg="port",
    payload_args=("backend", "remote_path", "iterations", "capture_ms", "confirm_repeated_execution"),
    result_payload_keys=(
        "samples",
        "timing_us",
        "memory_delta_bytes",
        "sampling_profiler",
    ),
)
def esp_performance_profile(
    port: str | None = None,
    backend: RawReplBackend = "raw_repl",
    code: str = "",
    remote_path: str = "",
    iterations: Iterations = 5,
    capture_ms: CaptureMs = 10_000,
    confirm_repeated_execution: bool = False,
) -> dict[str, Any]:
    """Instrument wall time and heap delta; this is not a sampling profiler."""

    tool = "esp_performance_profile"
    backend_error = _validate_raw_repl(backend, tool=tool)
    if backend_error:
        return backend_error
    timeout_error = _validate_capture_ms(capture_ms, tool=tool)
    if timeout_error:
        return timeout_error
    if bool(code.strip()) == bool(remote_path.strip()):
        return execution_error(
            "invalid_profile_target",
            "Provide exactly one of code or remote_path.",
            tool=tool,
        )
    if isinstance(iterations, bool) or not isinstance(iterations, int) or not 1 <= iterations <= 50:
        return execution_error(
            "invalid_iterations",
            "iterations must be an integer between 1 and 50.",
            tool=tool,
        )
    if len(code.encode("utf-8")) > 20_000:
        return execution_error(
            "profile_code_too_large",
            "Inline profile code is limited to 20000 UTF-8 bytes.",
            tool=tool,
        )
    if remote_path and (
        len(remote_path) > 256
        or "\x00" in remote_path
        or "\n" in remote_path
        or "\r" in remote_path
    ):
        return execution_error(
            "invalid_remote_path",
            "remote_path must be at most 256 characters and contain no control characters.",
            tool=tool,
        )
    if not confirm_repeated_execution:
        return execution_error(
            "repeated_execution_confirmation_required",
            "Performance profiling executes the selected target repeatedly and may repeat hardware side effects.",
            tool=tool,
            confirmation_required=True,
            target=remote_path or "inline_code",
            iterations=iterations,
            suggested_next_actions=[
                "Review the target and iteration count",
                "Call again with confirm_repeated_execution=true only if repeated side effects are acceptable",
            ],
        )
    selected_port, port_error = _selected_port_or_error(port, tool=tool)
    if port_error:
        return port_error

    source_expression = repr(code) if code else f"open({remote_path!r}).read()"
    probe_code = (
        "import time, gc, ujson\n"
        f"_source = {source_expression}\n"
        f"_iterations = {iterations}\n"
        "_samples = []\n"
        "for _index in range(_iterations):\n"
        "    gc.collect()\n"
        "    _before = int(gc.mem_free())\n"
        "    _ok = True\n"
        "    _error = None\n"
        "    _error_truncated = False\n"
        "    _start = time.ticks_us()\n"
        "    try:\n"
        "        exec(_source, {'__name__': '__main__'})\n"
        "    except Exception as _exc:\n"
        "        _ok = False\n"
        "        _error = repr(_exc)\n"
        f"        if len(_error) > {_MAX_PROFILE_ERROR_CHARS}:\n"
        f"            _error = _error[:{_MAX_PROFILE_ERROR_CHARS}]\n"
        "            _error_truncated = True\n"
        "    _duration = time.ticks_diff(time.ticks_us(), _start)\n"
        "    _after = int(gc.mem_free())\n"
        "    _samples.append({'iteration': _index + 1, 'ok': _ok, 'duration_us': _duration, 'memory_before_bytes': _before, 'memory_after_bytes': _after, 'memory_delta_bytes': _after - _before, 'error': _error, 'error_truncated': _error_truncated})\n"
        f"print({_PERFORMANCE_MARKER!r} + ujson.dumps(_samples))\n"
    )
    execution = execute_code(selected_port, probe_code, timeout_ms=capture_ms)
    payload, human_output, probe_error = _marked_payload(
        execution,
        _PERFORMANCE_MARKER,
        tool=tool,
        max_payload_bytes=_MAX_PROFILE_MARKER_BYTES,
    )
    if probe_error:
        probe_error.update({"port": selected_port, "backend": backend})
        return probe_error
    samples, normalization_error = _normalize_performance_samples(
        payload,
        iterations=iterations,
        capture_ms=capture_ms,
        tool=tool,
        port=selected_port,
    )
    if normalization_error:
        return normalization_error
    assert samples is not None
    successful = [item for item in samples if item.get("ok") is True]
    durations = [
        int(item["duration_us"])
        for item in successful
        if isinstance(item.get("duration_us"), int)
    ]
    deltas = [
        int(item["memory_delta_bytes"])
        for item in successful
        if isinstance(item.get("memory_delta_bytes"), int)
    ]
    failed = len(samples) - len(successful)
    response: dict[str, Any] = {
        "ok": failed == 0 and len(samples) == iterations,
        "tool": tool,
        "tool_name": tool,
        "tools名称": tool,
        "implemented": True,
        "port": selected_port,
        "backend": backend,
        "repeated_execution_confirmed": True,
        "program_interrupted": True,
        "target": remote_path or "inline_code",
        "iterations": iterations,
        "profile_kind": "instrumented_wall_time",
        "sampling_profiler": False,
        "samples": samples,
        "timing_us": _summary(durations),
        "memory_delta_bytes": _summary(deltas),
        "stdout": human_output,
        "evidence": "micropython_ticks_us_and_gc_mem_free",
        "message": f"Collected {len(samples)} instrumented performance sample(s); {failed} failed.",
    }
    if response["ok"] is False:
        response["error_kind"] = "performance_sample_failed"
        response["recoverable"] = True
    return response
