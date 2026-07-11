from __future__ import annotations


PROMPTS = {
    "debug_error": "Read latest logs, parse the error, propose a fix, and verify by running again when safe.",
    "build_flash_monitor": "Build the project, flash firmware after confirmation, reset, capture serial output, and parse errors.",
    "review_hardware_context": "Create a bounded base hardware mapping, then incrementally persist facts discovered by later questions or real-board operations.",
    "write_project_memory": "Write only stable project facts with source and confidence.",
}


def list_prompts() -> list[dict]:
    return [{"name": name, "description": description} for name, description in PROMPTS.items()]


def get_prompt(name: str, arguments: dict | None = None) -> dict:
    if name not in PROMPTS:
        return {"description": "Unknown prompt", "messages": []}
    return {
        "description": PROMPTS[name],
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": PROMPTS[name],
                },
            }
        ],
    }
