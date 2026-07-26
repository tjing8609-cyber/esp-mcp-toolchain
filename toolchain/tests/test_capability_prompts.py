from __future__ import annotations

import asyncio

from esp_mcp_toolchain.prompts.prompt_registry import (
    PROMPTS,
    get_prompt,
    list_prompts,
)
from esp_mcp_toolchain.server import create_mcp_server


EXPECTED_PROMPTS = {
    "file_transfer": ("esp_file_upload", "esp_file_read"),
    "program_execution_control": ("esp_exec_code", "esp_program_stop"),
    "microcontroller_reset": ("esp_reset", "esp_serial_capture"),
    "serial_monitor": ("esp_serial_monitor_start", "esp_serial_monitor_stop"),
    "runtime_log_search": ("esp_logs_query", "esp_logs_get"),
    "debug_error": ("esp_error_parse_log", "esp_error_parse_text"),
    "build_flash_monitor": ("esp_project_build", "esp_flash_firmware"),
    "remote_file_management": ("esp_file_list", "esp_file_delete"),
    "gpio_status_query": ("esp_gpio_status",),
    "review_hardware_context": ("esp_hardware_info", "hardwork_mapping_patch"),
    "automated_regression_test": ("esp_regression_test",),
    "performance_analysis": ("esp_performance_profile",),
}


REQUIRED_SECTIONS = (
    "目标",
    "前置检查",
    "工具顺序",
    "成功证据",
    "安全边界",
    "失败处理",
    "最终报告",
)


def _text(name: str) -> str:
    return get_prompt(name)["messages"][0]["content"]["text"]


def test_public_prompt_registry_contains_exactly_12_taskbook_workflows():
    assert set(PROMPTS) == set(EXPECTED_PROMPTS)
    assert len(PROMPTS) == 12
    assert [item["name"] for item in list_prompts()] == list(PROMPTS)

    sdk_prompts = asyncio.run(create_mcp_server().list_prompts())
    assert len(sdk_prompts) == 12
    assert {prompt.name for prompt in sdk_prompts} == set(EXPECTED_PROMPTS)
    assert all(len(prompt.description or "") < 100 for prompt in sdk_prompts)


def test_every_prompt_has_seven_sections_and_its_capability_tools():
    for name, tool_names in EXPECTED_PROMPTS.items():
        text = _text(name)
        for section in REQUIRED_SECTIONS:
            assert section in text, (name, section)
        assert "project_context" in text, name
        for tool_name in tool_names:
            assert tool_name in text, (name, tool_name)


def test_high_risk_prompts_keep_confirmation_and_evidence_boundaries():
    flash = _text("build_flash_monitor")
    files = _text("remote_file_management")
    stop = _text("program_execution_control")
    reset = _text("microcontroller_reset")
    gpio = _text("gpio_status_query")
    hardware = _text("review_hardware_context")
    regression = _text("automated_regression_test")
    performance = _text("performance_analysis")

    assert "明确确认" in flash
    assert "confirm=True" in flash
    assert "confirm=True" in files
    assert "Ctrl-D" in stop and "复位命令" in stop
    assert "physical_reset_excluded=False" in stop
    assert "同一串口句柄" in reset
    assert "独立串口会话" in reset
    assert "不能反向证明原复位" in reset
    assert "physical_reset_excluded=False" in reset
    assert "pre_action_text" in reset
    assert "output_causality_confirmed=False" in reset
    assert "allow_program_interrupt=True" in gpio
    assert "allow_program_interrupt=True" in hardware
    assert "confirm_execution=True" in regression
    assert "confirm_repeated_execution=True" in performance
    assert "sampling_profiler=False" in performance



def test_non_taskbook_legacy_names_do_not_expand_public_prompt_count():
    for legacy_name in (
        "write_project_memory",
        "hardware_context_review",
        "memory_write_policy",
    ):
        assert get_prompt(legacy_name)["messages"] == []

    sdk_prompts = asyncio.run(create_mcp_server().list_prompts())
    assert len(sdk_prompts) == 12
    assert "write_project_memory" not in {prompt.name for prompt in sdk_prompts}
