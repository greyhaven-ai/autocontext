"""Component sensitivity profiling and credit assignment (AC-199).

Tracks which components changed between generations and attributes
score improvements proportionally to change magnitudes.

Key types:
- ComponentChange: structured change for one component
- GenerationChangeVector: all changes + score delta for a generation
- compute_change_vector(): compare two generation states
- AttributionResult: credit per component
- CreditAssignmentRecord: durable generation-level attribution artifact
- attribute_credit(): lightweight proportional attribution
- format_attribution_for_agent(): prompt context per role
- summarize_credit_patterns(): cross-run pattern summary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ComponentChange:
    """Structured change descriptor for one component."""

    component: str  # playbook, tools, hints, analysis, etc.
    magnitude: float  # 0.0-1.0 normalized change magnitude
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "magnitude": self.magnitude,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentChange:
        return cls(
            component=data["component"],
            magnitude=data.get("magnitude", 0.0),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class GenerationChangeVector:
    """All component changes plus score delta for a generation."""

    generation: int
    score_delta: float
    changes: list[ComponentChange]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_change_magnitude(self) -> float:
        return round(sum(c.magnitude for c in self.changes), 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "score_delta": self.score_delta,
            "changes": [c.to_dict() for c in self.changes],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationChangeVector:
        return cls(
            generation=data.get("generation", 0),
            score_delta=data.get("score_delta", 0.0),
            changes=[ComponentChange.from_dict(c) for c in data.get("changes", [])],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class AttributionResult:
    """Credit attribution per component."""

    generation: int
    total_delta: float
    credits: dict[str, float]  # component → attributed delta
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "total_delta": self.total_delta,
            "credits": self.credits,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttributionResult:
        raw_credits = data.get("credits", {})
        credits = {
            str(component): float(value)
            for component, value in raw_credits.items()
            if isinstance(component, str)
        } if isinstance(raw_credits, dict) else {}
        return cls(
            generation=int(data.get("generation", 0)),
            total_delta=float(data.get("total_delta", 0.0)),
            credits=credits,
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CreditAssignmentRecord:
    """Durable attribution artifact for one generation."""

    run_id: str
    generation: int
    vector: GenerationChangeVector
    attribution: AttributionResult
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "generation": self.generation,
            "vector": self.vector.to_dict(),
            "attribution": self.attribution.to_dict(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreditAssignmentRecord:
        return cls(
            run_id=str(data.get("run_id", "")),
            generation=int(data.get("generation", 0)),
            vector=GenerationChangeVector.from_dict(data.get("vector", {})),
            attribution=AttributionResult.from_dict(data.get("attribution", {})),
            metadata=data.get("metadata", {}),
        )


def _text_change_magnitude(old: str, new: str) -> float:
    """Compute normalized change magnitude between two text strings."""
    if old == new:
        return 0.0
    if not old and not new:
        return 0.0
    if not old or not new:
        return 1.0
    # Character-level edit ratio
    max_len = max(len(old), len(new))
    common = sum(1 for a, b in zip(old, new, strict=False) if a == b)
    return round(1.0 - common / max_len, 4)


def _list_change_magnitude(old: list, new: list) -> float:
    """Compute change magnitude for ordered lists."""
    old_set = set(str(x) for x in old)
    new_set = set(str(x) for x in new)
    if old_set == new_set:
        return 0.0
    total = len(old_set | new_set)
    if total == 0:
        return 0.0
    diff = len(old_set ^ new_set)
    return round(diff / total, 4)


def compute_change_vector(
    generation: int,
    score_delta: float,
    previous_state: dict[str, Any],
    current_state: dict[str, Any],
) -> GenerationChangeVector:
    """Compare two generation states and compute change magnitudes."""
    changes: list[ComponentChange] = []

    # Playbook
    old_pb = str(previous_state.get("playbook", ""))
    new_pb = str(current_state.get("playbook", ""))
    pb_mag = _text_change_magnitude(old_pb, new_pb)
    if pb_mag > 0:
        changes.append(ComponentChange("playbook", pb_mag, f"Playbook changed ({pb_mag:.0%})"))

    # Tools
    old_tools = previous_state.get("tools", [])
    new_tools = current_state.get("tools", [])
    if isinstance(old_tools, list) and isinstance(new_tools, list):
        tools_mag = _list_change_magnitude(old_tools, new_tools)
        if tools_mag > 0:
            added = len(set(str(t) for t in new_tools) - set(str(t) for t in old_tools))
            removed = len(set(str(t) for t in old_tools) - set(str(t) for t in new_tools))
            changes.append(ComponentChange("tools", tools_mag, f"+{added}/-{removed} tools"))

    # Hints
    old_hints = str(previous_state.get("hints", ""))
    new_hints = str(current_state.get("hints", ""))
    hints_mag = _text_change_magnitude(old_hints, new_hints)
    if hints_mag > 0:
        changes.append(ComponentChange("hints", hints_mag, f"Hints changed ({hints_mag:.0%})"))

    # Analysis
    old_analysis = str(previous_state.get("analysis", ""))
    new_analysis = str(current_state.get("analysis", ""))
    analysis_mag = _text_change_magnitude(old_analysis, new_analysis)
    if analysis_mag > 0:
        changes.append(ComponentChange("analysis", analysis_mag, f"Analysis changed ({analysis_mag:.0%})"))

    return GenerationChangeVector(
        generation=generation,
        score_delta=score_delta,
        changes=changes,
    )


def attribute_credit(vector: GenerationChangeVector) -> AttributionResult:
    """Attribute score delta proportionally to change magnitudes."""
    if vector.score_delta <= 0 or not vector.changes:
        return AttributionResult(
            generation=vector.generation,
            total_delta=vector.score_delta,
            credits={c.component: 0.0 for c in vector.changes},
        )

    total_mag = vector.total_change_magnitude
    if total_mag == 0:
        return AttributionResult(
            generation=vector.generation,
            total_delta=vector.score_delta,
            credits={c.component: 0.0 for c in vector.changes},
        )

    credits = {
        c.component: round(vector.score_delta * (c.magnitude / total_mag), 6)
        for c in vector.changes
    }

    return AttributionResult(
        generation=vector.generation,
        total_delta=vector.score_delta,
        credits=credits,
    )


_ROLE_COMPONENT_PRIORITY: dict[str, tuple[str, ...]] = {
    "analyst": ("analysis", "playbook", "hints"),
    "coach": ("playbook", "hints", "analysis"),
    "architect": ("tools",),
    "competitor": ("playbook", "hints"),
}

_ROLE_TITLES: dict[str, str] = {
    "analyst": "Previous Analysis Attribution",
    "coach": "Previous Coaching Attribution",
    "architect": "Previous Tooling Attribution",
    "competitor": "Previous Strategy Attribution",
}

_ROLE_GUIDANCE: dict[str, str] = {
    "analyst": "Use this to focus your next diagnosis on the changes that actually moved score.",
    "coach": "Use this to reinforce the coaching changes that translated into measurable gains.",
    "architect": "Use this to prioritize tool work only where tooling actually moved outcomes.",
    "competitor": "Use this to lean into the strategy surfaces that correlated with progress.",
}


def format_attribution_for_agent(
    result: AttributionResult,
    role: str,
) -> str:
    """Format attribution as prompt context for a specific agent role."""
    if not result.credits or result.total_delta <= 0:
        return ""

    normalized_role = role.strip().lower()
    title = _ROLE_TITLES.get(normalized_role, "Credit Attribution")
    guidance = _ROLE_GUIDANCE.get(normalized_role, "")
    preferred = _ROLE_COMPONENT_PRIORITY.get(normalized_role, ())

    ordered_components: list[str] = []
    for component in preferred:
        if component in result.credits:
            ordered_components.append(component)
    for component, _credit in sorted(result.credits.items(), key=lambda item: (-item[1], item[0])):
        if component not in ordered_components:
            ordered_components.append(component)

    lines = [f"## {title} (Gen {result.generation})"]
    lines.append(f"Total score improvement: +{result.total_delta:.4f}")
    if guidance:
        lines.append(guidance)
    lines.append("")

    for component in ordered_components:
        credit = result.credits.get(component, 0.0)
        pct = credit / result.total_delta * 100 if result.total_delta > 0 else 0
        lines.append(f"- {component}: +{credit:.4f} ({pct:.0f}% of improvement)")

    return "\n".join(lines)


def summarize_credit_patterns(records: list[CreditAssignmentRecord]) -> dict[str, Any]:
    """Summarize component-attribution patterns across runs for analytics."""
    component_rollup: dict[str, dict[str, Any]] = {}
    run_ids = sorted({record.run_id for record in records if record.run_id})

    for record in records:
        total_delta = max(record.attribution.total_delta, 0.0)
        for change in record.vector.changes:
            bucket = component_rollup.setdefault(change.component, {
                "component": change.component,
                "generation_count": 0,
                "positive_generation_count": 0,
                "total_credit": 0.0,
                "total_change_magnitude": 0.0,
                "average_credit": 0.0,
                "average_share": 0.0,
            })
            bucket["generation_count"] += 1
            bucket["total_change_magnitude"] = round(
                float(bucket["total_change_magnitude"]) + change.magnitude,
                6,
            )
            credit = float(record.attribution.credits.get(change.component, 0.0))
            if credit > 0:
                bucket["positive_generation_count"] += 1
            bucket["total_credit"] = round(float(bucket["total_credit"]) + credit, 6)
            if total_delta > 0:
                bucket["average_share"] = round(
                    float(bucket["average_share"]) + (credit / total_delta),
                    6,
                )

    components: list[dict[str, Any]] = []
    for _component, bucket in component_rollup.items():
        generation_count = int(bucket["generation_count"])
        if generation_count > 0:
            bucket["average_credit"] = round(float(bucket["total_credit"]) / generation_count, 6)
            bucket["average_share"] = round(float(bucket["average_share"]) / generation_count, 6)
        components.append(dict(bucket))

    components.sort(key=lambda item: (-float(item["total_credit"]), item["component"]))

    return {
        "total_records": len(records),
        "run_count": len(run_ids),
        "run_ids": run_ids,
        "components": components,
    }
