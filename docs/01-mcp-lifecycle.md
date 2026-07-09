# MCP Lifecycle

The MCP server uses the official MCP Python SDK with `FastMCP` and stdio
transport. The SDK owns protocol parsing, initialization, capability
negotiation, request routing, and shutdown behavior.

The intended stdio MCP lifecycle is:

```text
initialize
tools/list, resources/list, prompts/list
tools/call, resources/read, prompts/get
close stdio connection
```

Project code owns only the tool, resource, and prompt implementations:

- tools are registered from `esp_mcp_toolchain.server.TOOL_REGISTRY`
- resources are registered from `resources/resource_registry.py`
- prompts are registered from `prompts/prompt_registry.py`

Do not print ordinary logs to `stdout`; stdio `stdout` is reserved for MCP
protocol messages. Use files or `stderr` for diagnostics.
