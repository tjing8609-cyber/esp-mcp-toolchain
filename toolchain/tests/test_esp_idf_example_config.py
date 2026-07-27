from __future__ import annotations

import os
from pathlib import Path


def _source_root() -> Path:
    configured = os.environ.get("ESP_MCP_SOURCE_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[2]


def test_key_led_buzzer_defaults_match_the_physical_4mib_flash():
    example = _source_root() / "examples" / "esp_idf_key_led_buzzer"
    defaults_path = example / "sdkconfig.defaults"

    assert defaults_path.is_file(), "the tracked example must declare reproducible sdkconfig defaults"
    active = {
        line.strip()
        for line in defaults_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert 'CONFIG_IDF_TARGET="esp32"' in active
    assert "CONFIG_ESPTOOLPY_FLASHMODE_DIO=y" in active
    assert "CONFIG_ESPTOOLPY_FLASHFREQ_40M=y" in active
    assert "CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y" in active
    assert "CONFIG_PARTITION_TABLE_SINGLE_APP=y" in active

    flash_size_choices = {
        line
        for line in active
        if line.startswith("CONFIG_ESPTOOLPY_FLASHSIZE_") and line.endswith("=y")
    }
    assert flash_size_choices == {"CONFIG_ESPTOOLPY_FLASHSIZE_4MB=y"}
    assert 'CONFIG_ESPTOOLPY_FLASHSIZE="4MB"' not in active
    assert "CONFIG_ESPTOOLPY_HEADER_FLASHSIZE_UPDATE=y" not in active


def test_key_led_buzzer_readme_explains_defaults_application_boundary():
    readme = (
        _source_root() / "examples" / "esp_idf_key_led_buzzer" / "README.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    assert "physical 4 MiB flash" in normalized
    assert "`sdkconfig.defaults`" in normalized
    assert "does not overwrite an existing ignored `sdkconfig`" in normalized
    assert "single-app partition layout" in normalized
    assert "does not expand the application partition" in normalized
