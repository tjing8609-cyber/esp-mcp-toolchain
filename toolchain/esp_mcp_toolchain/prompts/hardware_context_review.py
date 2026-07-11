from __future__ import annotations

PROMPT = (
    "Select the current Codex workspace with project_context_select. When the user attaches a schematic, PCB, "
    "pinout, BOM, datasheet, or serial document, call hardwork_upload_attachment with the attachment's local "
    "temporary path. Read the attachment itself, distinguish confirmed facts from inference, and call "
    "hardwork_commit_mapping with a bounded base mapping before using hardware-dependent tools. Do not attempt "
    "an exhaustive first-pass archive for a large hardware project. During later questions or real-board operations, "
    "read esp://hardwork/mapping first. If you discover a stable hardware fact that is missing or gains stronger "
    "evidence, call hardwork_mapping_patch before finishing the answer or operation. Preserve unrelated existing "
    "facts and report conflicts instead of overwriting them. Never guess "
    "a port, GPIO, chip, flash layout, active level, or electrical constraint."
)
