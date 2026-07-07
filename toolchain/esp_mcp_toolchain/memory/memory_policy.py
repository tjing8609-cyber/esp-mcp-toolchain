from __future__ import annotations


def validate_memory(namespace: str, key: str, value: str, source: str, confidence: float) -> list[str]:
    errors = []
    if not namespace:
        errors.append("namespace is required")
    if not key:
        errors.append("key is required")
    if not value:
        errors.append("value is required")
    if not source:
        errors.append("source is required")
    if confidence < 0 or confidence > 1:
        errors.append("confidence must be between 0 and 1")
    return errors

