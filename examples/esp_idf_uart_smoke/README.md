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
at 115200 baud. It also disables compile date/time metadata so an `idf.py
flash` incremental rebuild cannot change the application image solely because
`esp_app_desc.c` was compiled at a different time. Generated files such as
`build/`, `sdkconfig`,
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

## Deterministic flash input

The first authorized UART-only flash attempt exposed a reproducibility defect
before serial acceptance. `idf.py flash` recompiled ESP-IDF's
`esp_app_desc.c`, while `CONFIG_APP_COMPILE_TIME_DATE=y` embedded
`__DATE__`/`__TIME__` in the application image. The live tool output showed the
preflight application hash changing from `AA9E9AFA...09A9` to
`C0B4DE4F...1745`; that abbreviated observation was not persisted in the
SQLite/JSONL completion payload. The safety gate skipped serial acceptance and,
under the authorization already granted for that loop, restored the fresh
4 MiB MicroPython backup instead of treating the rebuilt image as reviewed.

The tracked defaults now set `CONFIG_APP_COMPILE_TIME_DATE=n`. Builds
`build_20260730_192625_8ef67c38` and
`build_20260730_192812_b9042a6e` completed without full clean or target
replacement. The final preflash review after the second incremental build
established these inputs:

```text
0x1000  bootloader.bin       26720 bytes
        1BFB7F309DB6C232FB20AF613B3A2E0E0570C615DD41DEADFF213F6C5015ABE8
0x8000  partition-table.bin   3072 bytes
        7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820
0x10000 esp_idf_uart_smoke.bin 176816 bytes
        4017628FA6BDFD2453C6518299F60D0ACF2A15BD3C43D466DE7CA8EF365D8CA2
```

The application image contains the ready/heartbeat strings and the Git-derived
project version, but no compile date or time string. A later flash still must
recompute all three hashes immediately before writing. If any reviewed input
changes, serial acceptance must stop; a fresh full-flash backup may be restored
only when the current exact authorization includes that recovery action.

## Verified COM3 closed loop

The second authorized attempt used
`backup_flash_20260730_193625_9ba0d34b` to read a fresh 4,194,304-byte image.
The backup SHA-256 was
`F28649C0194A67C951E5DFCB8BC690B526ABD1CFDA50D94BE2027F5DCA66CE89`.
Run `flash_20260730_193819_dfa2a884` wrote the reviewed target segments without
calling the full-chip erase tool.

Run `reset_20260730_193902_3172efe8` captured the ready marker and heartbeats
0 and 1. Run `serial_capture_20260730_193904_5fdcba4c` then captured seven
seconds at 115200 baud, including consecutive heartbeats 2 through 8. The
476-byte raw log has SHA-256
`C0411F143FDF459800DCB06C335ADA57FABBF285EC4C6B09E248DE672F9ED50C`
and no structured error.

Run `restore_flash_20260730_193928_f67fad17` subsequently wrote the complete
4,194,304-byte backup from address zero. MicroPython Raw REPL and mpremote file
access were then verified again. This proves ordered UART application output
and a completed controlled restore. It does not prove reset causality,
post-restore full-flash read-back equality, or electrical GPIO25 behavior
during boot and reset.
