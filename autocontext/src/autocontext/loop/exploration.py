"""Exploration mechanisms: novelty bonus, divergent competitor, multi-basin (AC-339 + AC-341).

Two-tier exploration system:
1. Novelty bonus (AC-339): continuous gentle pressure toward novel strategies
2. Multi-basin exploration (AC-341): triggered aggressive branching when stuck

Key types:
- NoveltyConfig, compute_novelty_score, apply_novelty_bonus
- DivergentCompetitorConfig, should_spawn_divergent
- MultiBasinConfig, BasinCandidate, generate_basin_candidates, BranchRecord
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# AC-339: Novelty-weighted exploration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class NoveltyConfig:
    """Configuration for novelty bonus."""

    weight: float = 0.1  # fraction of score bonus for max novelty
    enabled: bool = True


def compute_novelty_score(
    current: dict[str, Any],
    recent: list[dict[str, Any]],
) -> float:
    """Compute novelty as normalized distance from recent strategies.

    Returns 0.0 (identical to recent) to 1.0 (maximally different).
    Only compares numeric values.
    """
    if not recent:
        return 1.0

    # Extract numeric keys
    all_keys = set(current)
    for r in recent:
        all_keys.update(r)
    numeric_keys = [
        k for k in sorted(all_keys)
        if isinstance(current.get(k), (int, float))
        or any(isinstance(r.get(k), (int, float)) for r in recent)
    ]

    if not numeric_keys:
        return 0.0

    # Compute mean of recent for each key
    mean_recent: dict[str, float] = {}
    for k in numeric_keys:
        vals = [float(r[k]) for r in recent if isinstance(r.get(k), (int, float))]
        mean_recent[k] = sum(vals) / len(vals) if vals else 0.0

    # Euclidean distance
    dist_sq = sum(
        (float(current.get(k, 0.0)) - mean_recent.get(k, 0.0)) ** 2
        for k in numeric_keys
        if isinstance(current.get(k), (int, float))
    )
    dist = math.sqrt(dist_sq)

    # Normalize: max possible distance for N dims with values in [0,1] is sqrt(N)
    max_dist = math.sqrt(len(numeric_keys))
    if max_dist == 0:
        return 0.0

    return min(1.0, round(dist / max_dist, 6))


def apply_novelty_bonus(
    raw_score: float,
    novelty: float,
    config: NoveltyConfig,
) -> float:
    """Apply novelty bonus to raw score. Capped at 1.0."""
    if not config.enabled:
        return raw_score
    return min(1.0, raw_score + config.weight * novelty)


@dataclass(slots=True)
class DivergentCompetitorConfig:
    """Configuration for divergent competitor spawning."""

    enabled: bool = True
    rollback_threshold: int = 5
    temperature: float = 0.7


def should_spawn_divergent(
    gate_history: list[str],
    config: DivergentCompetitorConfig,
) -> bool:
    """Check if consecutive rollbacks exceed threshold."""
    if not config.enabled:
        return False

    consecutive = 0
    for decision in reversed(gate_history):
        if decision == "rollback":
            consecutive += 1
        else:
            break

    return consecutive >= config.rollback_threshold


def should_trigger_multi_basin(
    gate_history: list[str],
    generation: int,
    config: MultiBasinConfig,
) -> bool:
    """Trigger multi-basin exploration on repeated stall or periodic cadence."""
    if not config.enabled:
        return False

    if config.periodic_every_n > 0 and generation > 0 and generation % config.periodic_every_n == 0:
        return True

    consecutive = 0
    for decision in reversed(gate_history):
        if decision in {"retry", "rollback"}:
            consecutive += 1
        else:
            break
    return consecutive >= config.trigger_rollbacks


# ---------------------------------------------------------------------------
# AC-341: Multi-basin playbook exploration
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MultiBasinConfig:
    """Configuration for multi-basin exploration."""

    enabled: bool = False
    trigger_rollbacks: int = 3
    candidates: int = 3
    periodic_every_n: int = 0  # 0 = disabled


@dataclass(slots=True)
class BasinCandidate:
    """A candidate strategy branch for multi-basin exploration."""

    branch_type: str  # conservative, experimental, divergent
    playbook: str
    lessons: str
    temperature: float
    metadata: dict[str, Any] = Field(default_factory=dict)


def _strip_specific_tactics(playbook: str) -> str:
    """Keep high-level structure while dropping tactic-heavy bullets/checklists."""
    stripped_lines: list[str] = []
    for line in playbook.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith(("- ", "* ", "+ ")):
            continue
        if len(text) > 2 and text[0].isdigit() and text[1] == ".":
            continue
        stripped_lines.append(line.rstrip())

    candidate = "\n".join(stripped_lines).strip()
    if candidate:
        return candidate
    return "Retain only the high-level strategic principles from the existing playbook."


def generate_basin_candidates(
    playbook: str,
    lessons: str,
    config: MultiBasinConfig,
) -> list[BasinCandidate]:
    """Generate parallel strategy candidates with different perspectives."""
    if not config.enabled:
        return []

    candidates: list[BasinCandidate] = []

    # Conservative: current playbook, standard temperature
    candidates.append(BasinCandidate(
        branch_type="conservative",
        playbook=playbook,
        lessons=lessons,
        temperature=0.2,
    ))

    # Experimental: lessons only (strip tactics), higher temperature
    if config.candidates >= 2:
        candidates.append(BasinCandidate(
            branch_type="experimental",
            playbook=_strip_specific_tactics(playbook),
            lessons=lessons,
            temperature=0.5,
            metadata={"note": "Specific playbook tactics stripped, lessons retained"},
        ))

    # Divergent: no playbook, lessons only, high temperature
    if config.candidates >= 3:
        candidates.append(BasinCandidate(
            branch_type="divergent",
            playbook="",
            lessons=lessons,
            temperature=0.7,
            metadata={"note": "Fresh start with lessons only"},
        ))

    return candidates[:config.candidates]


class BranchRecord(BaseModel):
    """Records which branch produced a strategy."""

    generation: int
    branch_type: str
    score: float
    advanced: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchRecord:
        return cls.model_validate(data)
