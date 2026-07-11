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

Raw files are preserved and never overwritten. Processed conclusions must include evidence type, source location, confidence, and unresolved items. MCP can enforce this call order, but it cannot guarantee that a model interpreted a low-quality or ambiguous image correctly.
