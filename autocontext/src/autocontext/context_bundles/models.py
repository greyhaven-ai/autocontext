"""Immutable context-bundle contracts (AC-973).

A bundle is the complete, content-addressed context and harness configuration
used by a generation. Lifecycle state intentionally lives outside the bundle:
changing ``proposed`` to ``active`` must never change the bundle digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1


class ComponentKind(StrEnum):
    PLAYBOOK = "playbook"
    HINTS = "hints"
    PROMPT_FRAGMENT = "prompt_fragment"
    CONTEXT_POLICY = "context_policy"
    COMPLETION_CHECK = "completion_check"
    TOOL_GUIDANCE = "tool_guidance"
    TOOL_SPEC = "tool_spec"
    HARNESS_VALIDATOR = "harness_validator"
    ROUTING_CONFIG = "routing_config"


class BundleLifecycle(StrEnum):
    PROPOSED = "proposed"
    SCREENED = "screened"
    CONFIRMED = "confirmed"
    ACTIVE = "active"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class TrialLane(StrEnum):
    SCREEN = "screen"
    CONFIRMATION = "confirmation"
    HELDOUT = "heldout"


class ComparisonDecision(StrEnum):
    NEEDS_SCREEN = "needs_screen"
    NEEDS_CONFIRMATION = "needs_confirmation"
    NEEDS_HELDOUT = "needs_heldout"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


def canonical_json(value: Any) -> str:
    """Return the canonical JSON representation used by every digest."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class BundleComponent:
    """One immutable context surface.

    ``content`` is always a string. Structured values use canonical JSON and
    declare ``media_type=application/json`` so Python and TypeScript hash the
    exact same bytes.
    """

    kind: ComponentKind
    key: str
    content: str
    media_type: str = "text/plain"

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("bundle component key is required")
        if not self.media_type.strip():
            raise ValueError("bundle component media_type is required")

    @classmethod
    def json(cls, kind: ComponentKind, key: str, value: Any) -> BundleComponent:
        return cls(kind=kind, key=key, content=canonical_json(value), media_type="application/json")

    @property
    def digest(self) -> str:
        return stable_digest(self._digest_payload())

    def _digest_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind.value,
            "key": self.key,
            "content": self.content,
            "media_type": self.media_type,
        }

    def to_dict(self) -> dict[str, str]:
        return {**self._digest_payload(), "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BundleComponent:
        component = cls(
            kind=ComponentKind(str(data["kind"])),
            key=str(data["key"]),
            content=str(data.get("content", "")),
            media_type=str(data.get("media_type", "text/plain")),
        )
        expected = data.get("digest")
        if expected is not None and expected != component.digest:
            raise ValueError(f"component digest mismatch for {component.key!r}")
        return component


@dataclass(frozen=True, slots=True)
class ContextBundle:
    """Complete immutable context manifest for one scenario and evaluator epoch."""

    scenario: str
    evaluator_epoch: str
    components: tuple[BundleComponent, ...]
    parent_digest: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported context bundle schema version: {self.schema_version}")
        if not self.scenario.strip():
            raise ValueError("context bundle scenario is required")
        if not self.evaluator_epoch.strip():
            raise ValueError("context bundle evaluator_epoch is required")
        identities = [(component.kind.value, component.key) for component in self.components]
        if len(identities) != len(set(identities)):
            raise ValueError("context bundle component kind/key pairs must be unique")
        expected_order = sorted(identities)
        if identities != expected_order:
            raise ValueError("context bundle components must be sorted by kind and key")

    @classmethod
    def create(
        cls,
        *,
        scenario: str,
        evaluator_epoch: str,
        components: list[BundleComponent] | tuple[BundleComponent, ...],
        parent_digest: str | None = None,
    ) -> ContextBundle:
        ordered = tuple(sorted(components, key=lambda item: (item.kind.value, item.key)))
        return cls(
            scenario=scenario,
            evaluator_epoch=evaluator_epoch,
            components=ordered,
            parent_digest=parent_digest,
        )

    def _digest_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scenario": self.scenario,
            "evaluator_epoch": self.evaluator_epoch,
            "parent_digest": self.parent_digest,
            "components": [component.to_dict() for component in self.components],
        }

    @property
    def digest(self) -> str:
        return stable_digest(self._digest_payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._digest_payload(), "digest": self.digest}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextBundle:
        bundle = cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            scenario=str(data["scenario"]),
            evaluator_epoch=str(data["evaluator_epoch"]),
            parent_digest=(str(data["parent_digest"]) if data.get("parent_digest") is not None else None),
            components=tuple(BundleComponent.from_dict(item) for item in data.get("components", [])),
        )
        expected = data.get("digest")
        if expected is not None and expected != bundle.digest:
            raise ValueError("context bundle digest mismatch")
        return bundle

    def components_of_kind(self, kind: ComponentKind) -> tuple[BundleComponent, ...]:
        return tuple(component for component in self.components if component.kind == kind)


@dataclass(frozen=True, slots=True)
class MatchedTrial:
    """A candidate/incumbent score pair from one identical evaluation unit."""

    candidate_digest: str
    incumbent_digest: str | None
    evaluator_epoch: str
    cohort: str
    fixture: str
    fixture_digest: str
    seed: int
    lane: TrialLane
    candidate_score: float
    incumbent_score: float
    candidate_valid: bool = True
    incumbent_valid: bool = True

    @property
    def pair_key(self) -> str:
        return stable_digest(
            {
                "evaluator_epoch": self.evaluator_epoch,
                "cohort": self.cohort,
                "fixture": self.fixture,
                "fixture_digest": self.fixture_digest,
                "seed": self.seed,
                "lane": self.lane.value,
            }
        )

    @property
    def delta(self) -> float:
        return self.candidate_score - self.incumbent_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_digest": self.candidate_digest,
            "incumbent_digest": self.incumbent_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "cohort": self.cohort,
            "fixture": self.fixture,
            "fixture_digest": self.fixture_digest,
            "seed": self.seed,
            "lane": self.lane.value,
            "candidate_score": self.candidate_score,
            "incumbent_score": self.incumbent_score,
            "candidate_valid": self.candidate_valid,
            "incumbent_valid": self.incumbent_valid,
            "pair_key": self.pair_key,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchedTrial:
        trial = cls(
            candidate_digest=str(data["candidate_digest"]),
            incumbent_digest=(str(data["incumbent_digest"]) if data.get("incumbent_digest") is not None else None),
            evaluator_epoch=str(data["evaluator_epoch"]),
            cohort=str(data["cohort"]),
            fixture=str(data["fixture"]),
            fixture_digest=str(data["fixture_digest"]),
            seed=int(data["seed"]),
            lane=TrialLane(str(data["lane"])),
            candidate_score=float(data["candidate_score"]),
            incumbent_score=float(data["incumbent_score"]),
            candidate_valid=bool(data.get("candidate_valid", True)),
            incumbent_valid=bool(data.get("incumbent_valid", True)),
        )
        expected = data.get("pair_key")
        if expected is not None and expected != trial.pair_key:
            raise ValueError("matched trial pair_key mismatch")
        return trial


@dataclass(frozen=True, slots=True)
class ConfirmationPolicy:
    min_screen_pairs: int = 2
    min_confirmation_pairs: int = 6
    max_confirmation_pairs: int = 20
    min_heldout_pairs: int = 2
    min_effect: float = 0.0
    confidence_z: float = 1.96

    def __post_init__(self) -> None:
        if self.min_screen_pairs < 1:
            raise ValueError("min_screen_pairs must be positive")
        if self.min_confirmation_pairs < 2:
            raise ValueError("min_confirmation_pairs must be at least 2")
        if self.max_confirmation_pairs < self.min_confirmation_pairs:
            raise ValueError("max_confirmation_pairs must be >= min_confirmation_pairs")
        if self.min_heldout_pairs < 1:
            raise ValueError("min_heldout_pairs must be positive")
        if self.confidence_z <= 0:
            raise ValueError("confidence_z must be positive")

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_screen_pairs": self.min_screen_pairs,
            "min_confirmation_pairs": self.min_confirmation_pairs,
            "max_confirmation_pairs": self.max_confirmation_pairs,
            "min_heldout_pairs": self.min_heldout_pairs,
            "min_effect": self.min_effect,
            "confidence_z": self.confidence_z,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConfirmationPolicy:
        return cls(**data)


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    decision: ComparisonDecision
    reason: str
    screen_pairs: int
    confirmation_pairs: int
    heldout_pairs: int
    mean_effect: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "screen_pairs": self.screen_pairs,
            "confirmation_pairs": self.confirmation_pairs,
            "heldout_pairs": self.heldout_pairs,
            "mean_effect": self.mean_effect,
            "confidence_low": self.confidence_low,
            "confidence_high": self.confidence_high,
        }


@dataclass(frozen=True, slots=True)
class PromotionArtifact:
    promotion_id: str
    candidate_digest: str
    incumbent_digest: str | None
    rollback_target_digest: str | None
    evaluator_epoch: str
    cohort: str
    rationale: str
    comparison: ComparisonResult
    promoted_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "promotion_id": self.promotion_id,
            "candidate_digest": self.candidate_digest,
            "incumbent_digest": self.incumbent_digest,
            "rollback_target_digest": self.rollback_target_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "cohort": self.cohort,
            "rationale": self.rationale,
            "comparison": self.comparison.to_dict(),
            "promoted_at": self.promoted_at,
        }
