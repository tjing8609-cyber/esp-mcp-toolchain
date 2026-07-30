from __future__ import annotations

import os
from pathlib import Path


def _source_root() -> Path:
    configured = os.environ.get("ESP_MCP_SOURCE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def _example_root() -> Path:
    return _source_root() / "examples" / "esp_idf_uart_smoke"


def test_uart_smoke_example_tracks_a_complete_espidf_project():
    example = _example_root()
    required = [
        example / "CMakeLists.txt",
        example / "main" / "CMakeLists.txt",
        example / "main" / "main.c",
        example / "sdkconfig.defaults",
        example / "README.md",
    ]

    missing = [
        path.relative_to(_source_root()).as_posix()
        for path in required
        if not path.is_file()
    ]
    assert not missing, f"main must track the UART-only ESP-IDF example: {missing}"


def test_uart_smoke_application_is_continuous_and_gpio_free():
    source = (_example_root() / "main" / "main.c").read_text(encoding="utf-8")
    lowered = source.casefold()

    assert '#include "esp_log.h"' in source
    assert '#include "freertos/freertos.h"' in lowered
    assert '#include "freertos/task.h"' in lowered
    assert "ESP_MCP_UART_ONLY_READY" in source
    assert "ESP_MCP_UART_ONLY_HEARTBEAT=" in source
    assert "vTaskDelay" in source

    blocked_tokens = {
        "driver/gpio.h",
        "driver/ledc.h",
        "gpio_num_",
        "gpio_",
        "ledc_",
        "gpio25",
        "gpio_num_25",
        "pwm",
        "buzzer",
    }
    found = sorted(token for token in blocked_tokens if token in lowered)
    assert not found, f"UART-only application must not reference GPIO/PWM: {found}"


def test_uart_smoke_defaults_are_reproducible_for_the_demo_board():
    defaults = (_example_root() / "sdkconfig.defaults").read_text(encoding="utf-8")
    active = {
        line.strip()
        for line in defaults.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert 'CONFIG_IDF_TARGET="esp32"' in active
    assert "CONFIG_ESPTOOLPY_FLASHMODE_DIO=y" in active
    assert "CONFIG_ESPTOOLPY_FLASHFREQ_40M=y" in active
    assert "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y" in active
    assert "CONFIG_PARTITION_TABLE_SINGLE_APP=y" in active
    assert "CONFIG_ESP_CONSOLE_UART_DEFAULT=y" in active
    assert "CONFIG_ESP_CONSOLE_UART_NUM=0" in active
    assert "CONFIG_ESP_CONSOLE_UART_BAUDRATE=115200" in active

    flash_size_choices = {
        line
        for line in active
        if line.startswith("CONFIG_ESPTOOLPY_FLASHSIZE_") and line.endswith("=y")
    }
    assert flash_size_choices == {"CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y"}


def test_uart_smoke_readme_states_the_no_gpio_evidence_boundary():
    readme = (_example_root() / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    assert "UART-only" in normalized
    assert "does not configure application GPIO" in normalized
    assert "does not prove electrical pin levels" in normalized
    assert "GPIO25" in normalized
    assert "LEDC" in normalized
    assert "explicit authorization" in normalized
