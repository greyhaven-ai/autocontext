"""AC-926: the advise gate reads model JSON with the shared parser.

It was the last site still using a hand-rolled fence stripper after the AC-910
consolidation. That version required the payload to be the ENTIRE message once
fences were removed, so a preamble, a trailing remark, or a reasoning block
before the answer all degraded the gate to its LLM-free path -- ordinary
open-weight output shapes, not edge cases.

These pin the shapes that used to fail, so the migration cannot be quietly
reverted, and the one that must still fail.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from autocontext.providers.base import CompletionResult, LLMProvider

VERDICT = {"should_propose": True, "rationale": "the evidence supports it"}


class _StubProvider(LLMProvider):
    """Returns one fixed response, so the test varies output shape only."""

    def __init__(self, text: str) -> None:
        self._text = text

    def complete(self, *args: Any, **kwargs: Any) -> CompletionResult:
        del args, kwargs
        return CompletionResult(text=self._text, model="stub")

    def default_model(self) -> str:
        return "stub"


def _run(text: str) -> Any:
    from autocontext.ambient.advise_gate import run_advise_gate

    return run_advise_gate(_StubProvider(text), "stub", "evidence", max_output_tokens=256)


@pytest.mark.parametrize(
    "label,text",
    [
        ("bare json", json.dumps(VERDICT)),
        ("json fence", f"```json\n{json.dumps(VERDICT)}\n```"),
        ("untagged fence", f"```\n{json.dumps(VERDICT)}\n```"),
        # The four below all failed before this migration.
        ("preamble then json", f"Here is my verdict:\n\n{json.dumps(VERDICT)}"),
        ("preamble then fence", f"Sure!\n```json\n{json.dumps(VERDICT)}\n```\nHope that helps."),
        ("reasoning fence first", f"```\nthinking out loud\n```\n```json\n{json.dumps(VERDICT)}\n```"),
        (
            "tagged answer after JSON-shaped reasoning",
            "```\n"
            + json.dumps({"should_propose": False, "rationale": "draft"})
            + "\n```\n```json\n"
            + json.dumps(VERDICT)
            + "\n```",
        ),
        ("trailing prose", f"{json.dumps(VERDICT)}\n\nLet me know if you want more."),
    ],
)
def test_gate_decides_across_realistic_output_shapes(label: str, text: str) -> None:
    outcome = _run(text)
    assert outcome.failure == "", f"{label} degraded the gate"
    assert outcome.decision is not None
    assert outcome.decision.should_propose is True


def test_genuine_non_json_still_degrades_rather_than_guessing() -> None:
    """The parser got more tolerant, not credulous.

    A gate that invented a decision from prose would be worse than one that
    declines: the caller must permit when the gate cannot decide, and that only
    stays safe if "cannot decide" is still reachable.
    """
    outcome = _run("I think you should propose it.")
    assert outcome.decision is None
    assert outcome.failure == "parse_error"


def test_schema_violation_is_reported_as_a_parse_failure() -> None:
    """Valid JSON of the wrong shape must not become a decision.

    Separated from the extraction failure above because they are different
    faults with the same required outcome -- the gate declines rather than
    deciding on a payload it does not understand.
    """
    outcome = _run(json.dumps({"unrelated": "object"}))
    assert outcome.decision is None
    assert outcome.failure == "parse_error"


@pytest.mark.parametrize(
    "text",
    [
        (
            "Draft: "
            + json.dumps({"should_propose": False, "rationale": "draft"})
            + "\nFinal: "
            + json.dumps(VERDICT)
        ),
        (
            "```\n"
            + json.dumps({"should_propose": False, "rationale": "scratch"})
            + "\n```\nFinal: "
            + json.dumps(VERDICT)
        ),
    ],
)
def test_competing_verdict_objects_degrade_instead_of_guessing(text: str) -> None:
    outcome = _run(text)
    assert outcome.decision is None
    assert outcome.failure == "parse_error"


def test_unexpected_extraction_exception_degrades_instead_of_escaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import autocontext.ambient.advise_gate as advise_gate

    def fail_extraction(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RecursionError("decoder recursion limit")

    monkeypatch.setattr(advise_gate, "extract_json", fail_extraction)
    outcome = _run(json.dumps(VERDICT))
    assert outcome.decision is None
    assert outcome.failure == "parse_error"
