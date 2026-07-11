# Hardwork Module

The hardwork module stores hardware context:

- schematic summaries
- PCB notes
- BOM notes
- GPIO map
- serial interface
- flash layout
- known issues

Each Codex workspace is bound through `project_context_select`. Hardwork data is stored under the resulting project directory and is never shared implicitly with another workspace.

Conversation attachment workflow:

1. The user attaches a PNG, JPEG, or PDF in the Codex conversation.
2. Codex passes the attachment's temporary local path to `hardwork_upload_attachment`.
3. The tool validates and archives the original file under the active project's `hardwork/raw/` directory.
4. The first upload marks hardware review as pending and blocks hardware-dependent tools.
5. Codex reads the attachment and calls `hardwork_commit_mapping`.
6. The tool writes `gpio_map.md`, `serial_interface.md`, and `hardware_mapping.json`, then unlocks hardware tools.

The first review is intentionally bounded to the base facts needed for safe development. It is not an exhaustive archive requirement for large hardware projects.

Incremental workflow:

1. Read `esp://hardwork/mapping` before a later hardware question or board operation.
2. Reuse existing high-confidence facts without reopening the source document.
3. If the task reveals a missing stable fact or stronger evidence, call `hardwork_mapping_patch` before finishing.
4. Merge GPIO facts by `gpio + function` and serial facts by `interface`.
5. Preserve unrelated mappings and reject conflicting critical fields atomically.
6. Upgrade schematic evidence to `board_test_confirmed` after a successful real-board test.

Raw files are preserved and never overwritten. Processed conclusions must include evidence type, source location, confidence, and unresolved items. MCP can enforce this call order, but it cannot guarantee that a model interpreted a low-quality or ambiguous image correctly.
