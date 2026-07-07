---
name: esp-mcp
description: Use the local ESP MCP toolchain for serial ports, logs, hardwork context, project memory, and safe ESP development workflows.
---

# ESP MCP Skill

Use this skill when working with the ESP MCP toolchain in this repository.

## Rules

- Read hardwork context before making GPIO, serial, flash, or board assumptions.
- Use low-risk tools first: port list, port status, log read, hardwork read, memory read.
- Ask for explicit confirmation before high-risk actions such as flash erase, firmware flashing, clean, or delete.
- Write project memory only for stable facts with source and confidence.
- Do not use the toolchain as a generic shell executor.

## Typical Flow

1. Check `esp_port_list` and `esp_port_status`.
2. Read `hardwork_get` for board context.
3. Run build or upload only through declared tools.
4. Capture serial logs.
5. Parse errors and store stable learnings in project memory.

