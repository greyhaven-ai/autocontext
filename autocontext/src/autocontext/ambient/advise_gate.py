"""Bounded LLM review gate for ambient advise proposals (AC-900).

One provider call per advise cycle decides whether the collected evidence is
worth emitting proposals. The gate is an EVALUATIVE role: frozen under the
charter's asymmetric-trainability posture, never a training target. Any
provider or parse failure returns None and the caller must degrade to the
LLM-free path (permit); the gate may filter, never silently suppress.

Prompt shape adapted from prime-agent's auto-refine review gate.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from pydantic import BaseModel, ValidationError

from autocontext.harness.core.output_parser import extract_json
from autocontext.providers.base import LLMProvider

logger = logging.getLogger(__name__)

ADVISE_GATE_SYSTEM_PROMPT = (
    "You are the ambient advise review gate. Decide whether the observed "
    "evidence justifies emitting training-target proposals this cycle. "
    "Approve durable, repeated signals; reject one-off noise, unsupported "
    "hypotheses, and transient artifacts. Return JSON only: "
    '{"should_propose": true|false, "rationale": "short reason"}'
)


class AdviseGateDecision(BaseModel):
    """Verdict of one gate consultation."""

    should_propose: bool
    rationale: str = ""


class GateOutcome(NamedTuple):
    """Decision plus a failure label when the gate could not decide."""

    decision: AdviseGateDecision | None
    failure: str  # "" | "provider_error" | "parse_error"


def run_advise_gate(
    provider: LLMProvider,
    model: str,
    evidence_summary: str,
    *,
    max_output_tokens: int,
) -> GateOutcome:
    """One bounded gate call; a failed gate never decides (caller must permit).

    The catch is deliberately broad: provider stacks raise more than
    ProviderError (a missing API key surfaces as TypeError from the anthropic
    client), and ANY escape here would trip the stage breaker and suppress
    proposals permanently, the exact failure the gate must never cause.
    """
    try:
        result = provider.complete(
            system_prompt=ADVISE_GATE_SYSTEM_PROMPT,
            user_prompt=evidence_summary,
            model=model,
            temperature=0.0,
            max_tokens=max_output_tokens,
        )
    except Exception:
        logger.warning("advise gate provider call failed; degrading to LLM-free path", exc_info=True)
        return GateOutcome(None, "provider_error")
    # AC-926: the shared model-JSON parser, not a local fence stripper. The
    # hand-rolled version required the payload to be the ENTIRE message once
    # fences were removed, so any preamble ("Here is my verdict:"), any trailing
    # remark, or a reasoning block before the answer degraded the gate to the
    # LLM-free path. Those are ordinary open-weight output shapes, not edge
    # cases; extract_json handles all four and still refuses genuine non-JSON.
    # This gate must also fail open on ambiguous or unexpectedly pathological
    # output: a parser exception must never escape into the ambient stage
    # breaker, and competing verdict objects are not a decision we can trust.
    try:
        decoded = extract_json(result.text, on_failure="none", require_unique=True)
    except Exception:
        logger.warning("advise gate verdict extraction failed; degrading to LLM-free path", exc_info=True)
        return GateOutcome(None, "parse_error")
    if decoded is None:
        logger.warning("advise gate verdict unparseable or ambiguous; degrading to LLM-free path")
        return GateOutcome(None, "parse_error")
    try:
        decision = AdviseGateDecision.model_validate(decoded)
    except ValidationError:
        logger.warning("advise gate verdict failed schema validation; degrading to LLM-free path")
        return GateOutcome(None, "parse_error")
    return GateOutcome(decision, "")
