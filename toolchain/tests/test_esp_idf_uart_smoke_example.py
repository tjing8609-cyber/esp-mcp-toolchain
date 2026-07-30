from __future__ import annotations

import os
import re
from pathlib import Path


def _source_root() -> Path:
    configured = os.environ.get("ESP_MCP_SOURCE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def _example_root() -> Path:
    return _source_root() / "examples" / "esp_idf_uart_smoke"


def _without_c_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"//[^\r\n]*", "", source)


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


def test_uart_smoke_application_has_one_locked_source_file():
    example = _example_root()
    application_sources = sorted(
        path.relative_to(example).as_posix()
        for path in example.rglob("*")
        if path.is_file()
        and "build" not in path.relative_to(example).parts
        and "managed_components" not in path.relative_to(example).parts
        and path.suffix.casefold() in {".c", ".cc", ".cpp", ".h", ".hpp", ".s"}
    )

    assert application_sources == ["main/main.c"]
    assert not (example / "components").exists()

    component_cmake = " ".join(
        (example / "main" / "CMakeLists.txt").read_text(encoding="utf-8").split()
    )
    assert component_cmake == 'idf_component_register(SRCS "main.c" INCLUDE_DIRS ".")'


def test_uart_smoke_application_is_continuous_and_gpio_free():
    source = (_example_root() / "main" / "main.c").read_text(encoding="utf-8")
    code = _without_c_comments(source)
    lowered = code.casefold()

    assert '#include "esp_log.h"' in code
    assert '#include "freertos/freertos.h"' in lowered
    assert '#include "freertos/task.h"' in lowered
    assert re.search(
        r'ESP_LOGI\s*\(\s*TAG\s*,\s*"ESP_MCP_UART_ONLY_READY"\s*\)\s*;',
        code,
    )
    assert re.search(
        r'ESP_LOGI\s*\(\s*TAG\s*,\s*"ESP_MCP_UART_ONLY_HEARTBEAT=%lu"\s*,\s*heartbeat\+\+\s*\)\s*;',
        code,
    )
    assert re.search(r"while\s*\(\s*true\s*\)", code)
    assert re.search(
        r"vTaskDelay\s*\(\s*pdMS_TO_TICKS\s*\(\s*1000\s*\)\s*\)\s*;",
        code,
    )

    blocked_tokens = {
        "dac_",
        "driver/gpio.h",
        "driver/ledc.h",
        "driver/rmt",
        "gpio_num_",
        "gpio_",
        "ledc_",
        "mcpwm",
        "reg_write",
        "rmt_",
        "soc/gpio",
        "write_peri_reg",
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

    assert active == {
        'CONFIG_IDF_TARGET="esp32"',
        "CONFIG_ESPTOOLPY_FLASHMODE_DIO=y",
        "CONFIG_ESPTOOLPY_FLASHFREQ_40M=y",
        "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y",
        "CONFIG_PARTITION_TABLE_SINGLE_APP=y",
        "CONFIG_ESP_CONSOLE_UART_DEFAULT=y",
        "CONFIG_ESP_CONSOLE_UART_NUM=0",
        "CONFIG_ESP_CONSOLE_UART_BAUDRATE=115200",
        "CONFIG_LOG_DEFAULT_LEVEL_INFO=y",
        "CONFIG_APP_COMPILE_TIME_DATE=n",
    }


def test_uart_smoke_readme_states_the_no_gpio_evidence_boundary():
    readme = (_example_root() / "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    assert "UART-only" in normalized
    assert "does not configure application GPIO" in normalized
    assert "does not prove electrical pin levels" in normalized
    assert "GPIO25" in normalized
    assert "LEDC" in normalized
    assert "CONFIG_APP_COMPILE_TIME_DATE=n" in normalized
    assert "`__DATE__`/`__TIME__`" in readme
    assert "explicit authorization" in normalized
