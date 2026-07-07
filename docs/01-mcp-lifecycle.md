# MCP Lifecycle

The intended stdio MCP lifecycle is:

```text
initialize
tools/list, resources/list, prompts/list
tools/call, resources/read, prompts/get
shutdown
```

The current server provides a minimal JSON-RPC adapter for initialization and
tool registry development. A production MCP SDK transport can replace the
adapter without changing tool implementations.

