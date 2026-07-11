---
name: esp-mcp
description: Use the local ESP MCP toolchain plugin for Codex-visible MCP tools, serial ports, logs, hardwork context, project memory, and safe ESP development workflows.
---

# ESP MCP Skill

Use this skill when working with the ESP MCP toolchain in this repository.

## Rules

- Call `project_context_select` with the current Codex workspace root before project-scoped operations. Do not use the plugin installation directory as the workspace.
- Read hardwork context before making GPIO, serial, flash, or board assumptions.
- When the user attaches hardware documentation, pass its local attachment path to `hardwork_upload_attachment`, read the attachment, and call `hardwork_commit_mapping` before hardware-dependent operations.
- Treat `schematic_confirmed`, `board_test_confirmed`, `model_inference`, and `unconfirmed` as distinct evidence levels. Never promote inference to a confirmed fact.
- Use low-risk tools first: port list, port status, log read, hardwork read, memory read.
- Ask for explicit confirmation before high-risk actions such as flash erase, firmware flashing, clean, or delete.
- Write project memory only for stable facts with source and confidence.
- Do not use the toolchain as a generic shell executor.

## Typical Flow

1. Call `project_context_select` with the current Codex workspace root and verify it with `project_context_status`.
2. If hardware documents are attached, archive and review them, then commit GPIO and serial mappings.
3. Check `esp_port_list` and `esp_port_status` without guessing the port.
4. Read `hardwork_get` for board context.
5. Run build or upload only through declared tools.
6. Capture serial logs.
7. Parse errors and store stable learnings in project memory.
