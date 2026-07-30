# ESP-IDF UART-only Smoke Test

This example provides a bounded build, flash, and serial-monitor acceptance
target for the ESP32-D0WD-V3 board. The application emits one ready marker and
then a monotonically increasing heartbeat at one-second intervals on UART0 at
115200 baud.

This separate target is necessary because `esp_idf_key_led_buzzer` configures
LEDC and binds GPIO25 during startup even when the requested duty is zero; a
low level on GPIO34 can also start its five-pulse buzzer sequence. Reusing that
example would therefore violate a no-buzzer acceptance boundary.

The application does not configure application GPIO and does not include the
GPIO or LEDC drivers. In particular, it does not bind GPIO25, start PWM, read
KEY1, or control the onboard LED or buzzer. This source-level boundary does not
prove electrical pin levels during boot or reset; that would require external
measurement.

The tracked `sdkconfig.defaults` selects ESP32, DIO mode at 40 MHz, the physical
4 MiB flash size, the single-app partition table, and the default UART0 console
at 115200 baud. Generated files such as `build/`, `sdkconfig`,
`dependencies.lock`, and `managed_components/` remain ignored by Git.

## Verified host build

The first ESP-IDF 5.2.1 host build completed as run
`build_20260730_152205_0508e89d`. The application binary is 176,896 bytes with
SHA-256
`AA9E9AFA7036D2F78B183A4834835EBEDDD859AB3914D3509F9C00FD0AD409A9`.
Its generated flash arguments are:

```text
--flash_mode dio --flash_freq 40m --flash_size 4MB
0x1000 bootloader/bootloader.bin
0x10000 esp_idf_uart_smoke.bin
0x8000 partition_table/partition-table.bin
```

That first build encountered ESP-IDF's `set-target` dependency on `fullclean`,
but no build directory existed, so ESP-IDF reported `Nothing to clean` and did
not remove an existing artifact. The backend now uses a non-cleaning
`-D IDF_TARGET=... build` plan for an unconfigured project and blocks any
target/cache transition that requires `fullclean` unless
`confirm_target_change=True` was explicitly authorized. A later incremental
build returned `target_plan=build`, `fullclean_planned=false`,
`set_target_planned=false`, and `target_verified=true`.

All five build plans now reject a project or build directory that is a
symlink, junction, reparse point, or resolves outside the project. The check
runs before reading the target cache and again immediately before `idf.py`
starts; a linked or non-regular `CMakeCache.txt` is also refused.

Building is a host-side operation. Flash backup, firmware flashing, monitoring,
and restoring the previous full-flash image require explicit authorization and
must retain the toolchain confirmation gates. This example does not authorize
erase, full clean, target replacement, deletion, or fallback recovery actions.
Neither host build accessed COM3.
