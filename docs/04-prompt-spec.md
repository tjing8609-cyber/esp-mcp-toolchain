# Prompt Spec

The source-registered public prompt surface contains exactly 12 task-book workflows; the installed
personal Marketplace cache must be verified separately after Codex restarts:

1. `file_transfer`
2. `program_execution_control`
3. `microcontroller_reset`
4. `serial_monitor`
5. `runtime_log_search`
6. `debug_error`
7. `build_flash_monitor`
8. `remote_file_management`
9. `gpio_status_query`
10. `review_hardware_context`
11. `automated_regression_test`
12. `performance_analysis`

Every public prompt must contain:

- goal;
- project and hardware prechecks;
- recommended tool order;
- success evidence;
- safety and confirmation boundary;
- failure handling;
- final report fields.

The prompt list is the final 6-basic + 6-advanced capability set. It is not the former four prompts plus twelve more.

The three task-book names that already existed publicly remain unchanged: `debug_error`,
`build_flash_monitor`, and `review_hardware_context`. Other historical names, including
`hardware_context_review`, `memory_write_policy`, and `write_project_memory`, are not registered
and are not compatibility aliases. Clients must use one of the 12 names listed above.

Safety fields are part of the workflow contract, not optional wording:

- `esp_program_stop` only proves that no reset command was sent
  (`reset_command_sent=false`). Opening a serial port cannot exclude driver- or
  hardware-level control-line effects, so it reports `physical_reset_excluded=false`.
  A stop is confirmed only after the friendly REPL prompt `>>>` is observed.
- GPIO queries and MicroPython runtime hardware probes can interrupt the running program.
  The corresponding prompt may set `allow_program_interrupt=true` only after the user accepts
  that interruption.
- Board regression execution requires `confirm_execution=true` for the exact test paths.
- Performance profiling requires `confirm_repeated_execution=true` because the target is run
  repeatedly and may repeat its side effects.
- Flashing and deletion preserve their existing explicit confirmations. No prompt may silently
  expand into flashing, erasing, deletion, bootloader entry, reset, or full clean.
