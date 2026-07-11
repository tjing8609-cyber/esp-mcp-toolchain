# ESP-IDF Key LED Buzzer Test

Board-specific smoke test for the ESP32-D0WD-V3 board in this repository.

- KEY1: GPIO34, active low, external board pull-up.
- LED: GPIO32, active low.
- Buzzer: GPIO25, LEDC PWM at 2000 Hz.

Press KEY1 once. The firmware flashes the GPIO32 LED and sounds the GPIO25
buzzer five times, then waits for the key to be released before accepting the
next trigger.

Generated ESP-IDF files such as `build/`, `sdkconfig`, `dependencies.lock`, and
`managed_components/` are intentionally ignored by Git.
