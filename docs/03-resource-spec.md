# Resource Spec

Recommended resources:

```text
esp://project/config
esp://project/status
esp://ports/selected
esp://logs/latest
esp://hardwork/index
esp://hardwork/gpio-map
esp://hardwork/serial-interface
esp://hardwork/attachments
esp://memory/recent
```

Project-scoped resources return `project_context_required` until `project_context_select` binds the MCP session to a Codex workspace.
