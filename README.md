# ESP MCP Toolchain

A generic stdio MCP toolchain for ESP development.

This repository is intentionally scoped to local ESP development operations:

- list and select serial ports
- capture serial output
- store and query JSONL logs
- parse common error traces
- manage hardware context under `hardwork/`
- manage project-scoped memory under `data/memory/`
- expose tools, resources, and prompts through a stdio MCP entrypoint

It does not contain product firmware or business-specific application code.

## Current Phase

Phase 1 initializes the repository and provides a safe Python CLI foundation:

```powershell
python toolchain/cli.py port-list
python toolchain/cli.py port-select COM3
python toolchain/cli.py port-status
python toolchain/cli.py logs-latest
python toolchain/cli.py hardwork-list
python toolchain/cli.py memory-search baudrate
```

The MCP entrypoint is:

```powershell
python toolchain/mcp_server.py
```

## Repository Layout

See `docs/00-overview.md` and `docs/10-development-roadmap.md`.

