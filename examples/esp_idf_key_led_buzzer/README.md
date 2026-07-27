# ESP-IDF Key LED Buzzer Test

Board-specific smoke test for the ESP32-D0WD-V3 board in this repository.

- KEY1: GPIO34, active low, external board pull-up.
- LED: GPIO32, active low.
- Buzzer: GPIO25, LEDC PWM at 2000 Hz.
- Flash: physical 4 MiB flash, DIO mode at 40 MHz.

Press KEY1 once. The firmware flashes the GPIO32 LED and sounds the GPIO25
buzzer five times, then waits for the key to be released before accepting the
next trigger.

Generated ESP-IDF files such as `build/`, `sdkconfig`, `dependencies.lock`, and
`managed_components/` are intentionally ignored by Git.

The tracked `sdkconfig.defaults` makes a newly configured build describe the
physical 4 MiB flash while retaining the single-app partition layout. This
corrects the bootloader image header and flash command size; it does not expand
the application partition.

ESP-IDF does not overwrite an existing ignored `sdkconfig` with
`sdkconfig.defaults`. An existing local build therefore needs an explicit,
reviewed configuration update before rebuilding. Do not delete `sdkconfig` just
to apply these defaults through this toolchain: the build backend would run
`idf.py set-target esp32`, which is equivalent to a full clean and requires
separate confirmation.
