"""Bounded LLM review gate for ambient advise proposals (AC-900).

One provider call per advise cycle decides whether the collected evidence is
worth emitting proposals. The gate is an EVALUATIVE role: frozen under the
charter's asymmetric-trainability posture, never a training target. Any
provider or parse failure returns None and the caller must degrade to the
LLM-free path (permit); the gate may filter, never silently suppress.

Prompt shape adapted from prime-agent's auto-refine review gate.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, ValidationError

from autocontext.providers.base import LLMProvider, ProviderError

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


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        body = [line for line in lines if not line.strip().startswith("```")]
        return "\n".join(body).strip()
    return stripped


def run_advise_gate(
    provider: LLMProvider,
    model: str,
    evidence_summary: str,
    *,
    max_output_tokens: int,
) -> AdviseGateDecision | None:
    """One bounded gate call; None on any failure (caller degrades to permit)."""
    try:
        result = provider.complete(
            system_prompt=ADVISE_GATE_SYSTEM_PROMPT,
            user_prompt=evidence_summary,
            model=model,
            temperature=0.0,
            max_tokens=max_output_tokens,
        )
    except ProviderError:
        logger.warning("advise gate provider call failed; degrading to LLM-free path", exc_info=True)
        return None
    try:
        return AdviseGateDecision.model_validate(json.loads(_strip_fences(result.text)))
    except (ValueError, ValidationError):
        logger.warning("advise gate verdict unparseable; degrading to LLM-free path")
        return None
