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

## Background serial monitor

The background monitor exposes:

```text
esp_serial_monitor_start(port=None, baudrate=115200, session_name="default")
esp_serial_monitor_stop(run_id, timeout_ms=5000)
esp_serial_monitor_status(run_id=None)
esp_serial_monitor_read(
    run_id,
    after_seq=None,
    max_bytes=65536,
    wait_ms=0,
    representation="text",
)
```

`start` captures an immutable project and log-path snapshot. A later active-project switch cannot
redirect that worker. The session state is one of `STARTING`, `RUNNING`, `STOPPING`, `STOPPED`,
`FAILED`, or `DISCONNECTED`.

`read` has cursor semantics rather than an undefined "latest text" behavior:

- Records are serial read chunks, not lines, and each record has a monotonically increasing `seq`.
- `after_seq=None` reads from the oldest record still available. Passing the returned
  `next_after_seq` advances the caller cursor. Reusing the same cursor repeats records that remain
  available.
- `max_bytes` must be 4096 through 65536 and bounds one response. `wait_ms=0` returns immediately;
  values through 30000 wait only until data, a terminal state, or the deadline.
- The in-memory ring is bounded. When older records were evicted, `dropped_before_seq` identifies
  the newest sequence no longer available from memory. Persisted terminal sessions remain readable
  from disk.
- UTF-8 byte sequences split across serial reads are reassembled before record creation. Invalid
  bytes use replacement characters in `text`; `representation="base64"` or `"both"` preserves an
  exact binary representation in `raw_base64`.
- `next_seq` is the next sequence the producer will assign; it is not the caller cursor. Empty
  reads return an empty `records` list and leave `next_after_seq` at the supplied cursor.

The monitor writes raw bytes to `logs/serial/<run_id>/` as checksummed chunks. Active chunks use a
`.part` suffix. Default limits are a 1 MiB in-memory ring, 8 MiB chunks, 256 MiB per session, and a
2 GiB per-project raw-log safety limit. These defaults are configurable by environment variables;
the project limit is not claimed to be an atomic quota across unrelated concurrently writing
processes.

Only one monitor may own a port in a process, and a PID/process-creation lock coordinates multiple
MCP processes. Operating-system serial open remains the final authority. Port identity records all
available device path, VID/PID, serial number, location, manufacturer, product, interface, and HWID
fields instead of trusting a `COM` name alone.

Flash tools include `esp_backup_flash` for reading an image and `esp_restore_flash` for writing a verified local BIN image back to the board. Backup runs through the managed ESP-IDF subprocess wrapper, writes to a `.part` file, deletes partial output after failure or timeout, validates the exact requested byte count, and only then atomically replaces the final BIN. Restore requires `confirm=True` and supports an expected SHA-256 guard.

`esp_reset` supports two explicit modes:

- `soft`: send MicroPython Ctrl-C/Ctrl-D over the selected serial port.
- `hard`: keep GPIO0 high with DTR inactive, pulse RTS for 100 ms to reset EN, then capture two seconds of boot output.

Unsupported reset modes return `unsupported_reset_mode` instead of an unimplemented placeholder.

`project_migrate_legacy_data(source_root, confirm=False)` migrates recognized shared data from an explicitly named legacy plugin or repository root into the active project:

- Preview is the default and performs no migration or audit writes.
- Recognized inputs are top-level `hardwork/`, plus `data/memory`, `data/logs`, `data/artifacts`, `data/project_config.json`, and `data/esp_mcp.sqlite`.
- Legacy `data/projects/` is never traversed by this tool.
- Existing files with the same SHA-256 are skipped; different existing files are reported as conflicts and are never overwritten.
- Confirmed copies use exclusive destination creation, verify SHA-256, and roll back files created by the run if copying or audit persistence fails. Audit updates use a temporary file and atomic replacement to preserve the previous JSONL file and append a rollback manifest without partial lines.

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
