from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from autocontext.prompts.context_budget import estimate_tokens

SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ContextSelectionCandidate:
    """One context artifact considered for a prompt or runtime namespace."""

    artifact_id: str
    artifact_type: str
    source: str
    candidate_token_estimate: int
    selected_token_estimate: int
    selected: bool
    selection_reason: str
    candidate_content_hash: str
    selected_content_hash: str = ""
    useful: bool | None = None
    freshness_generation_delta: int | None = None

    @classmethod
    def from_contents(
        cls,
        *,
        artifact_id: str,
        artifact_type: str,
        source: str,
        candidate_content: str,
        selected_content: str,
        selection_reason: str,
        useful: bool | None = None,
        freshness_generation_delta: int | None = None,
    ) -> ContextSelectionCandidate:
        selected = bool(selected_content.strip())
        return cls(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            source=source,
            candidate_token_estimate=estimate_tokens(candidate_content),
            selected_token_estimate=estimate_tokens(selected_content) if selected else 0,
            selected=selected,
            selection_reason=selection_reason,
            candidate_content_hash=_content_hash(candidate_content),
            selected_content_hash=_content_hash(selected_content) if selected else "",
            useful=useful,
            freshness_generation_delta=freshness_generation_delta,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "artifact_type": self.artifact_type,
            "source": self.source,
            "candidate_token_estimate": self.candidate_token_estimate,
            "selected_token_estimate": self.selected_token_estimate,
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "candidate_content_hash": self.candidate_content_hash,
            "selected_content_hash": self.selected_content_hash,
            "useful": self.useful,
            "freshness_generation_delta": self.freshness_generation_delta,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ContextSelectionCandidate:
        return cls(
            artifact_id=str(data.get("artifact_id", "")),
            artifact_type=str(data.get("artifact_type", "")),
            source=str(data.get("source", "")),
            candidate_token_estimate=_coerce_int(data.get("candidate_token_estimate")),
            selected_token_estimate=_coerce_int(data.get("selected_token_estimate")),
            selected=bool(data.get("selected", False)),
            selection_reason=str(data.get("selection_reason", "")),
            candidate_content_hash=str(data.get("candidate_content_hash", "")),
            selected_content_hash=str(data.get("selected_content_hash", "")),
            useful=_coerce_optional_bool(data.get("useful")),
            freshness_generation_delta=_coerce_optional_int(data.get("freshness_generation_delta")),
        )


@dataclass(frozen=True)
class ContextSelectionDecision:
    """Context-selection trace for one run stage."""

    run_id: str
    scenario_name: str
    generation: int
    stage: str
    candidates: tuple[ContextSelectionCandidate, ...]
    created_at: str = field(default_factory=_utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def metrics(self) -> dict[str, Any]:
        candidates = list(self.candidates)
        selected = [candidate for candidate in candidates if candidate.selected]
        useful_candidates = [candidate for candidate in candidates if candidate.useful is True]
        useful_selected = [candidate for candidate in selected if candidate.useful is True]
        freshness_values = [
            candidate.freshness_generation_delta
            for candidate in selected
            if candidate.freshness_generation_delta is not None
        ]
        duplicate_count = _duplicate_selected_hash_count(selected)
        useful_recall = (
            len(useful_selected) / len(useful_candidates)
            if useful_candidates
            else None
        )
        mean_freshness = (
            sum(freshness_values) / len(freshness_values)
            if freshness_values
            else None
        )
        return {
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "candidate_token_estimate": sum(candidate.candidate_token_estimate for candidate in candidates),
            "selected_token_estimate": sum(candidate.selected_token_estimate for candidate in selected),
            "selection_rate": len(selected) / len(candidates) if candidates else 0.0,
            "duplicate_content_rate": duplicate_count / len(selected) if selected else 0.0,
            "useful_candidate_count": len(useful_candidates),
            "useful_selected_count": len(useful_selected),
            "useful_artifact_recall": useful_recall,
            "mean_selected_freshness_generation_delta": mean_freshness,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "scenario_name": self.scenario_name,
            "generation": self.generation,
            "stage": self.stage,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
            "metrics": self.metrics(),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ContextSelectionDecision:
        raw_candidates = data.get("candidates", ())
        candidate_items = raw_candidates if isinstance(raw_candidates, list | tuple) else ()
        candidates = (
            ContextSelectionCandidate.from_dict(candidate)
            for candidate in candidate_items
            if isinstance(candidate, Mapping)
        )
        raw_metadata = data.get("metadata", {})
        metadata = (
            {str(key): value for key, value in raw_metadata.items()}
            if isinstance(raw_metadata, Mapping)
            else {}
        )
        return cls(
            run_id=str(data.get("run_id", "")),
            scenario_name=str(data.get("scenario_name", "")),
            generation=_coerce_int(data.get("generation")),
            stage=str(data.get("stage", "")),
            created_at=str(data.get("created_at", "")),
            candidates=tuple(candidates),
            metadata=metadata,
        )


def build_prompt_context_selection_decision(
    *,
    run_id: str,
    scenario_name: str,
    generation: int,
    stage: str,
    candidate_components: Mapping[str, str],
    selected_components: Mapping[str, str],
    metadata: Mapping[str, Any] | None = None,
) -> ContextSelectionDecision:
    """Create a decision from raw prompt components and retained components."""
    candidate_names = list(candidate_components)
    extra_selected_names = [name for name in selected_components if name not in candidate_components]
    candidates: list[ContextSelectionCandidate] = []
    for name in [*candidate_names, *extra_selected_names]:
        candidate_content = str(candidate_components.get(name, ""))
        selected_content = str(selected_components.get(name, ""))
        candidates.append(
            ContextSelectionCandidate.from_contents(
                artifact_id=name,
                artifact_type="prompt_component",
                source="prompt_assembly",
                candidate_content=candidate_content,
                selected_content=selected_content,
                selection_reason=_selection_reason(
                    candidate_content=candidate_content,
                    selected_content=selected_content,
                ),
            )
        )
    return ContextSelectionDecision(
        run_id=run_id,
        scenario_name=scenario_name,
        generation=generation,
        stage=stage,
        candidates=tuple(candidates),
        metadata=dict(metadata or {}),
    )


def _selection_reason(*, candidate_content: str, selected_content: str) -> str:
    if selected_content.strip():
        return "retained_after_prompt_assembly"
    if candidate_content.strip():
        return "removed_by_prompt_assembly"
    return "empty_component"


def _duplicate_selected_hash_count(candidates: list[ContextSelectionCandidate]) -> int:
    hashes = [candidate.selected_content_hash for candidate in candidates if candidate.selected_content_hash]
    return sum(count - 1 for count in Counter(hashes).values() if count > 1)


def _content_hash(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _coerce_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return None
