from __future__ import annotations

from autocontext.ambient.redaction_gate import redact_payload

_FAKE_KEY = "sk-ant-api03-" + "a" * 32


def test_redacts_nested_strings_and_counts() -> None:
    payload = {
        "prompt": f"use key {_FAKE_KEY} please",
        "meta": {"notes": [f"backup {_FAKE_KEY}", "clean"]},
        "score": 1.5,
    }
    redacted, findings = redact_payload(payload)
    assert findings >= 2
    assert _FAKE_KEY not in str(redacted)
    assert redacted["score"] == 1.5
    assert redacted["meta"]["notes"][1] == "clean"


def test_clean_payload_zero_findings_and_not_mutated() -> None:
    payload = {"prompt": "hello", "n": 1}
    redacted, findings = redact_payload(payload)
    assert findings == 0
    assert redacted == payload
    payload["prompt"] = "changed"
    assert redacted["prompt"] == "hello"
