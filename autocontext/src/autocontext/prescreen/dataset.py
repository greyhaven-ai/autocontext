"""Examples for the pre-screen models (design section 2): eligible rounds of one family and epoch, time-ordered."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean

from autocontext.prescreen.rounds import EligibleRound, LedgerRound

FEATURE_SPEC_VERSION = "prescreen-features-v1"
STRUCTURAL_FEATURES = (
    "round_number",
    "previous_score",
    "previous_dimension_mean",
    "previous_dimension_min",
    "log_output_chars",
    "output_length_ratio",
    "token_jaccard_with_previous",
    "required_concept_coverage",
)
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Example:
    row_id: int
    loop_id: str
    group: str
    created_at: str
    round_number: int
    failed: bool
    structural: tuple[float, ...]
    text: str


def feature_spec_hash() -> str:
    """Content id binding a model to the exact features it was trained on."""
    return hashlib.sha256(json.dumps([FEATURE_SPEC_VERSION, list(STRUCTURAL_FEATURES)]).encode("utf-8")).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def structural_features(current: LedgerRound, previous: LedgerRound) -> tuple[float, ...]:
    """Values in STRUCTURAL_FEATURES order; 'previous' is the judged round just before this one in the same loop."""
    dims = list(previous.dimension_scores.values())
    current_chars, previous_chars = len(current.output), len(previous.output)
    now, before = _tokens(current.output), _tokens(previous.output)
    union = now | before
    concepts = current.required_concepts
    lowered = current.output.lower()
    return (
        float(current.round_number),
        previous.score,
        mean(dims) if dims else previous.score,
        min(dims) if dims else previous.score,
        math.log1p(current_chars),
        current_chars / previous_chars if previous_chars else 1.0,
        len(now & before) / len(union) if union else 1.0,
        sum(concept.lower() in lowered for concept in concepts) / len(concepts) if concepts else 1.0,
    )


def build_examples(eligible: Sequence[EligibleRound], *, family: str, epoch: str) -> list[Example]:
    """Eligible rounds of one family whose own verdict came from `epoch`. The label is 'the judge failed it'."""
    chosen = [e for e in eligible if e.round.scenario_family == family and e.round.evaluator_epoch == epoch]
    chosen.sort(key=lambda e: (e.round.created_at, e.round.row_id))
    return [
        Example(
            row_id=e.round.row_id,
            loop_id=e.round.loop_id,
            group=e.round.scenario_name or e.round.loop_id,
            created_at=e.round.created_at,
            round_number=e.round.round_number,
            failed=not e.round.passed,
            structural=structural_features(e.round, e.previous),
            text=f"{e.round.task_prompt}\n\n{e.round.output}",
        )
        for e in chosen
    ]
