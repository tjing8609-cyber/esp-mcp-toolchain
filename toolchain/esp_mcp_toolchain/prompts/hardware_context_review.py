from __future__ import annotations

PROMPT = (
    "Select the current Codex workspace with project_context_select. When the user attaches a schematic, PCB, "
    "pinout, BOM, datasheet, or serial document, call hardwork_upload_attachment with the attachment's local "
    "temporary path. Read the attachment itself, distinguish confirmed facts from inference, and call "
    "hardwork_commit_mapping with GPIO and serial mappings before using hardware-dependent tools. Never guess "
    "a port, GPIO, chip, flash layout, active level, or electrical constraint."
)
