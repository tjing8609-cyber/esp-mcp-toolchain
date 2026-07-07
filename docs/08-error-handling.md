# Error Handling

Protocol errors are JSON-RPC errors.

Execution errors are returned as structured tool results:

```json
{
  "ok": false,
  "error_kind": "serial_port_busy",
  "recoverable": true,
  "message": "COM3 is already opened."
}
```

