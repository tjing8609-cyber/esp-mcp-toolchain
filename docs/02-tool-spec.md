# Tool Spec

Initial tools are grouped into:

- project context
- ports
- build
- flash
- file transfer
- reset
- exec
- serial
- logs
- error parsing
- hardwork
- memory

High-risk tools must require explicit confirmation in clients.

Flash tools include `esp_backup_flash` for reading an image and `esp_restore_flash` for writing a verified local BIN image back to the board. Backup runs through the managed ESP-IDF subprocess wrapper, writes to a `.part` file, deletes partial output after failure or timeout, validates the exact requested byte count, and only then atomically replaces the final BIN. Restore requires `confirm=True` and supports an expected SHA-256 guard.

`esp_reset` supports two explicit modes:

- `soft`: send MicroPython Ctrl-C/Ctrl-D over the selected serial port.
- `hard`: keep GPIO0 high with DTR inactive, pulse RTS for 100 ms to reset EN, then capture two seconds of boot output.

Unsupported reset modes return `unsupported_reset_mode` instead of an unimplemented placeholder.

Project-scoped tools require `project_context_select(workspace_root)` first. Hardware attachments use:

- `hardwork_upload_attachment`
- `hardwork_attachment_list`
- `hardwork_commit_mapping`
- `hardwork_mapping_patch`

After the first hardware attachment is uploaded, hardware-dependent tools remain blocked until a GPIO or serial mapping is committed.
The first commit is a bounded base initialization. Later questions and board operations must use `hardwork_mapping_patch` to persist newly discovered facts without replacing unrelated mappings.

Hardware mapping entries use structured FastMCP schemas:

- Every GPIO entry requires `gpio` and `function`.
- Every serial entry requires `interface`.
- Optional evidence must be one of `schematic_confirmed`, `board_test_confirmed`, `model_inference`, or `unconfirmed`.
- Invalid entries are rejected atomically before Markdown, JSON, or review state is written.
