# Debug Serial Error

1. Read `esp_logs_latest`.
2. Fetch details with `esp_logs_get`.
3. Parse text with `esp_error_parse_log`.
4. Fix the source issue outside the generic toolchain.
5. Capture serial output again.

