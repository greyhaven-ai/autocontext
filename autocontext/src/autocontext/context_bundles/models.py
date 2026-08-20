"""Immutable context-bundle contracts (AC-973).

A bundle is the complete, content-addressed context and harness configuration
used by a generation. Lifecycle state intentionally lives outside the bundle:
changing ``proposed`` to ``active`` must never change the bundle digest.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1
MAX_SAFE_INTEGER = (1 << 53) - 1


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


def utf16_sort_key(value: str) -> bytes:
    """Return the ECMAScript/JCS property-order key for ``value``."""
    return value.encode("utf-16-be", errors="surrogatepass")


def _validate_unicode_scalars(value: str) -> None:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("canonical JSON does not permit lone UTF-16 surrogates")


def _canonical_number(value: int | float) -> str:
    """Serialize a number with ECMAScript's binary64 JSON spelling."""

    if isinstance(value, int) and abs(value) > MAX_SAFE_INTEGER:
        raise ValueError("canonical JSON integers must be within the JavaScript safe integer range")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError("canonical JSON does not permit numbers outside binary64") from exc
    if not math.isfinite(number):
        raise ValueError("canonical JSON does not permit non-finite numbers")
    if number.is_integer() and abs(number) > MAX_SAFE_INTEGER:
        raise ValueError("canonical JSON integers must be within the JavaScript safe integer range")
    if number == 0:
        return "0"

    negative = number < 0
    decimal = Decimal(repr(abs(number)))
    digits_tuple = decimal.as_tuple().digits
    exponent = decimal.as_tuple().exponent
    if not isinstance(exponent, int):  # Defensive: finite binary64 values always have an integer exponent.
        raise ValueError("canonical JSON does not permit non-finite numbers")
    while len(digits_tuple) > 1 and digits_tuple[-1] == 0:
        digits_tuple = digits_tuple[:-1]
        exponent += 1
    digits = "".join(str(digit) for digit in digits_tuple)
    decimal_point = len(digits) + exponent

    if 0 < decimal_point <= 21:
        if decimal_point >= len(digits):
            result = digits + ("0" * (decimal_point - len(digits)))
        else:
            result = f"{digits[:decimal_point]}.{digits[decimal_point:]}"
    elif -6 < decimal_point <= 0:
        result = f"0.{('0' * -decimal_point)}{digits}"
    else:
        mantissa = digits if len(digits) == 1 else f"{digits[0]}.{digits[1:]}"
        scientific_exponent = decimal_point - 1
        result = f"{mantissa}e{'+' if scientific_exponent >= 0 else ''}{scientific_exponent}"
    return f"-{result}" if negative else result


def canonical_json(value: Any) -> str:
    """Return JCS-compatible JSON used by every cross-runtime digest.

    Object keys use UTF-16 code-unit order and numbers use ECMAScript's
    binary64 representation so Python and TypeScript hash identical bytes.
    Integral values outside JavaScript's safe range are rejected rather than
    rounded into a colliding digest.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        _validate_unicode_scalars(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float)):
        return _canonical_number(value)
    if isinstance(value, (list, tuple)):
        return f"[{','.join(canonical_json(item) for item in value)}]"
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical JSON object keys must be strings")
        items = (f"{canonical_json(key)}:{canonical_json(value[key])}" for key in sorted(value, key=utf16_sort_key))
        return f"{{{','.join(items)}}}"
    raise TypeError(f"value is not canonical-JSON serializable: {type(value).__name__}")


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
        expected_order = sorted(identities, key=lambda identity: (utf16_sort_key(identity[0]), utf16_sort_key(identity[1])))
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
        ordered = tuple(
            sorted(
                components,
                key=lambda item: (utf16_sort_key(item.kind.value), utf16_sort_key(item.key)),
            )
        )
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
        # Lane and display name are metadata, not independent evidence. The
        # same evaluation unit must collide if either is relabeled.
        return stable_digest(
            {
                "evaluator_epoch": self.evaluator_epoch,
                "cohort": self.cohort,
                "fixture_digest": self.fixture_digest,
                "seed": self.seed,
            }
        )

    @property
    def legacy_pair_key(self) -> str:
        """Return the schema-v1 identity used by persisted pre-migration rows."""

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
        required_strings = (
            "candidate_digest",
            "evaluator_epoch",
            "cohort",
            "fixture",
            "fixture_digest",
            "lane",
        )
        for field_name in required_strings:
            if not isinstance(data.get(field_name), str):
                raise TypeError(f"matched trial {field_name} must be a string")
        incumbent_digest = data.get("incumbent_digest")
        if incumbent_digest is not None and not isinstance(incumbent_digest, str):
            raise TypeError("matched trial incumbent_digest must be a string or null")
        seed = data.get("seed")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("matched trial seed must be an integer")
        if abs(seed) > MAX_SAFE_INTEGER:
            raise ValueError("matched trial seed must be within the JavaScript safe integer range")

        scores: dict[str, float] = {}
        for field_name in ("candidate_score", "incumbent_score"):
            raw_score = data.get(field_name)
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise TypeError(f"matched trial {field_name} must be numeric")
            if isinstance(raw_score, int) and abs(raw_score) > MAX_SAFE_INTEGER:
                raise ValueError(f"matched trial {field_name} integer must be within the JavaScript safe integer range")
            score = float(raw_score)
            if not math.isfinite(score):
                raise ValueError(f"matched trial {field_name} must be finite")
            scores[field_name] = score

        validity: dict[str, bool] = {}
        for field_name in ("candidate_valid", "incumbent_valid"):
            raw_validity = data.get(field_name, True)
            if not isinstance(raw_validity, bool):
                raise TypeError(f"matched trial {field_name} must be a boolean")
            validity[field_name] = raw_validity
        expected = data.get("pair_key")
        if expected is not None and not isinstance(expected, str):
            raise TypeError("matched trial pair_key must be a string or null")

        trial = cls(
            candidate_digest=data["candidate_digest"],
            incumbent_digest=incumbent_digest,
            evaluator_epoch=data["evaluator_epoch"],
            cohort=data["cohort"],
            fixture=data["fixture"],
            fixture_digest=data["fixture_digest"],
            seed=seed,
            lane=TrialLane(data["lane"]),
            candidate_score=scores["candidate_score"],
            incumbent_score=scores["incumbent_score"],
            candidate_valid=validity["candidate_valid"],
            incumbent_valid=validity["incumbent_valid"],
        )
        if expected is not None and expected not in {trial.pair_key, trial.legacy_pair_key}:
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
        counts = {
            "min_screen_pairs": self.min_screen_pairs,
            "min_confirmation_pairs": self.min_confirmation_pairs,
            "max_confirmation_pairs": self.max_confirmation_pairs,
            "min_heldout_pairs": self.min_heldout_pairs,
        }
        for name, value in counts.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.min_screen_pairs < 1:
            raise ValueError("min_screen_pairs must be positive")
        if self.min_confirmation_pairs < 2:
            raise ValueError("min_confirmation_pairs must be at least 2")
        if self.max_confirmation_pairs < self.min_confirmation_pairs:
            raise ValueError("max_confirmation_pairs must be >= min_confirmation_pairs")
        if self.min_heldout_pairs < 1:
            raise ValueError("min_heldout_pairs must be positive")
        if not math.isfinite(self.min_effect):
            raise ValueError("min_effect must be finite")
        if not math.isfinite(self.confidence_z) or self.confidence_z <= 0:
            raise ValueError("confidence_z must be finite and positive")

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
    confirmation_policy: ConfirmationPolicy
    confirmation_policy_digest: str
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
            "confirmation_policy": self.confirmation_policy.to_dict(),
            "confirmation_policy_digest": self.confirmation_policy_digest,
            "promoted_at": self.promoted_at,
        }
