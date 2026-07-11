# Tool Spec

Initial tools are grouped into:

- project context
- ports
- build
- flash
- file transfer
- reset
- exec
- serial
- logs
- error parsing
- hardwork
- memory

High-risk tools must require explicit confirmation in clients.

Project-scoped tools require `project_context_select(workspace_root)` first. Hardware attachments use:

- `hardwork_upload_attachment`
- `hardwork_attachment_list`
- `hardwork_commit_mapping`
- `hardwork_mapping_patch`

After the first hardware attachment is uploaded, hardware-dependent tools remain blocked until a GPIO or serial mapping is committed.
The first commit is a bounded base initialization. Later questions and board operations must use `hardwork_mapping_patch` to persist newly discovered facts without replacing unrelated mappings.
