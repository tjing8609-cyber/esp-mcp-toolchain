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

Project code also owns bounded cleanup for background serial monitors. Cleanup is requested when
stdin reaches EOF, the normal server runner returns, a supported `SIGINT` / `SIGTERM` is caught,
`atexit` runs, or an internal monitor/main-thread exception is handled. The cleanup path stops
workers, flushes writable logs, closes serial handles, and releases locks without waiting forever.

This is not an absolute guarantee for `TerminateProcess`, interpreter crashes, or power loss. In
those cases the operating system is expected to release serial handles, and the next server start
recovers stale monitor state and lock files.

The lifecycle test suite launches a real MCP child process with a fake serial monitor, closes the
child stdin, and checks bounded exit, port reopen, thread exit, and stale-lock cleanup. A separate
forced-termination test checks recovery on the next process start.

Do not print ordinary logs to `stdout`; stdio `stdout` is reserved for MCP
protocol messages. Use files or `stderr` for diagnostics.
