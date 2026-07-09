# ESP-IDF Key LED Buzzer Test

Board-specific smoke test for the ESP32-D0WD-V3 board in this repository.

- BOOT: GPIO0, active low, internal pull-up enabled.
- LED: GPIO32, active low.
- Buzzer: GPIO25, LEDC PWM at 2000 Hz.

Press BOOT once. The firmware flashes the GPIO32 LED and sounds the GPIO25
buzzer three times, then waits for the key to be released before accepting the
next trigger.

Generated ESP-IDF files such as `build/`, `sdkconfig`, `dependencies.lock`, and
`managed_components/` are intentionally ignored by Git.
