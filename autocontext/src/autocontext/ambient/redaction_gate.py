"""redaction applies at ingest, before anything is persisted as training-eligible."""

from __future__ import annotations

from typing import Any

from autocontext.sharing.redactor import redact_content_with_report


def _redact_value(value: Any, counter: list[int]) -> Any:
    if isinstance(value, str):
        cleaned, report = redact_content_with_report(value)
        counter[0] += report.total_count
        return cleaned
    if isinstance(value, dict):
        return {key: _redact_value(item, counter) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, counter) for item in value]
    return value


def redact_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    # dict keys are structural and not redacted; payloads are JSON-origin so
    # keys are strings and never carry content.
    counter = [0]
    redacted = {key: _redact_value(value, counter) for key, value in payload.items()}
    return redacted, counter[0]
