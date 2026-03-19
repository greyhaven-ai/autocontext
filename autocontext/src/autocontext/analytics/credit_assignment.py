"""Component sensitivity profiling and credit assignment (AC-199).

Tracks which components changed between generations and attributes
score improvements proportionally to change magnitudes.

Key types:
- ComponentChange: structured change for one component
- GenerationChangeVector: all changes + score delta for a generation
- compute_change_vector(): compare two generation states
- AttributionResult: credit per component
- attribute_credit(): lightweight proportional attribution
- format_attribution_for_agent(): prompt context per role
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


def _list_change_magnitude(old: list, new: list) -> float:  # type: ignore[type-arg]
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

    return GenerationChangeVector(
        generation=generation,
        score_delta=score_delta,
        changes=changes,
    )


@dataclass(slots=True)
class AttributionResult:
    """Credit attribution per component."""

    generation: int
    total_delta: float
    credits: dict[str, float]  # component → attributed delta
    metadata: dict[str, Any] = field(default_factory=dict)


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


def format_attribution_for_agent(
    result: AttributionResult,
    role: str,
) -> str:
    """Format attribution as prompt context for a specific agent role."""
    if not result.credits or result.total_delta <= 0:
        return ""

    lines = [f"## Credit Attribution (Gen {result.generation})"]
    lines.append(f"Total score improvement: +{result.total_delta:.4f}\n")

    for component, credit in sorted(result.credits.items(), key=lambda x: -x[1]):
        pct = credit / result.total_delta * 100 if result.total_delta > 0 else 0
        lines.append(f"- {component}: +{credit:.4f} ({pct:.0f}% of improvement)")

    return "\n".join(lines)
