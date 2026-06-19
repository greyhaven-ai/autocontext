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

import hashlib
import re
from typing import Any

from pydantic import BaseModel, Field


class ComponentChange(BaseModel):
    """Structured change descriptor for one component."""

    component: str  # playbook, tools, hints, analysis, etc.
    magnitude: float  # 0.0-1.0 normalized change magnitude
    description: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ComponentChange:
        return cls.model_validate(data)


class GenerationChangeVector(BaseModel):
    """All component changes plus score delta for a generation."""

    generation: int
    score_delta: float
    changes: list[ComponentChange]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def total_change_magnitude(self) -> float:
        return round(sum(c.magnitude for c in self.changes), 6)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationChangeVector:
        return cls.model_validate(data)


class AttributionResult(BaseModel):
    """Credit attribution per component."""

    generation: int
    total_delta: float
    credits: dict[str, float]  # component → attributed delta
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AttributionResult:
        return cls.model_validate(data)


class CreditAssignmentRecord(BaseModel):
    """Durable attribution artifact for one generation."""

    run_id: str
    generation: int
    vector: GenerationChangeVector
    attribution: AttributionResult
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CreditAssignmentRecord:
        return cls.model_validate(data)



class KnowledgeSpan(BaseModel):
    """Stable line/span identity for prompt context attribution."""

    span_id: str
    source: str
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def _normalize_span_text(text: str) -> str:
    stripped = re.sub(r"^(?:[-*]\s+|\d+\.\s+)", "", text.strip())
    return " ".join(stripped.split())


def _span_id(source: str, text: str) -> str:
    digest = hashlib.sha1(f"{source}:{_normalize_span_text(text).lower()}".encode()).hexdigest()[:12]
    return f"{source}:{digest}"


def extract_knowledge_spans(source: str, content: str) -> list[KnowledgeSpan]:
    """Extract stable non-empty line spans from a knowledge component."""
    spans: list[KnowledgeSpan] = []
    ordinal = 0
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        normalized = _normalize_span_text(raw_line)
        if not normalized:
            continue
        ordinal += 1
        spans.append(KnowledgeSpan(
            span_id=_span_id(source, normalized),
            source=source,
            text=normalized,
            metadata={"source": source, "line_number": line_number, "ordinal": ordinal},
        ))
    return spans


def build_span_attribution(
    vector: GenerationChangeVector,
    attribution: AttributionResult,
    *,
    current_state: dict[str, Any],
) -> dict[str, Any]:
    """Distribute component credit onto individual current-state spans."""
    total_mag = vector.total_change_magnitude
    rows: list[dict[str, Any]] = []
    for change in vector.changes:
        spans = extract_knowledge_spans(change.component, str(current_state.get(change.component, "")))
        if not spans:
            continue
        component_credit = float(attribution.credits.get(change.component, 0.0))
        if component_credit == 0.0 and vector.score_delta < 0 and total_mag > 0:
            component_credit = round(vector.score_delta * (change.magnitude / total_mag), 6)
        per_span = round(component_credit / len(spans), 6)
        for span in spans:
            rows.append({
                "span_id": span.span_id,
                "source": span.source,
                "text": span.text,
                "credit": per_span,
                "evidence_level": "component_correlated",
                "metadata": span.metadata,
            })
    return {"schema_version": 1, "mode": "span", "spans": rows}


def rank_spans_by_credit(spans: list[KnowledgeSpan], credits: dict[str, float]) -> list[dict[str, Any]]:
    """Rank spans by credit while marking demotion candidates instead of deleting them."""
    rows = []
    for span in spans:
        credit = float(credits.get(span.span_id, 0.0))
        rows.append({
            "span_id": span.span_id,
            "source": span.source,
            "text": span.text,
            "credit": credit,
            "demoted": credit <= 0,
            "metadata": span.metadata,
        })
    return sorted(rows, key=lambda row: (-float(row["credit"]), str(row["span_id"])))

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
        changes.append(ComponentChange(component="playbook", magnitude=pb_mag, description=f"Playbook changed ({pb_mag:.0%})"))

    # Tools
    old_tools = previous_state.get("tools", [])
    new_tools = current_state.get("tools", [])
    if isinstance(old_tools, list) and isinstance(new_tools, list):
        tools_mag = _list_change_magnitude(old_tools, new_tools)
        if tools_mag > 0:
            added = len(set(str(t) for t in new_tools) - set(str(t) for t in old_tools))
            removed = len(set(str(t) for t in old_tools) - set(str(t) for t in new_tools))
            changes.append(ComponentChange(component="tools", magnitude=tools_mag, description=f"+{added}/-{removed} tools"))

    # Hints
    old_hints = str(previous_state.get("hints", ""))
    new_hints = str(current_state.get("hints", ""))
    hints_mag = _text_change_magnitude(old_hints, new_hints)
    if hints_mag > 0:
        changes.append(ComponentChange(component="hints", magnitude=hints_mag, description=f"Hints changed ({hints_mag:.0%})"))

    # Analysis
    old_analysis = str(previous_state.get("analysis", ""))
    new_analysis = str(current_state.get("analysis", ""))
    analysis_mag = _text_change_magnitude(old_analysis, new_analysis)
    if analysis_mag > 0:
        changes.append(ComponentChange(
            component="analysis", magnitude=analysis_mag, description=f"Analysis changed ({analysis_mag:.0%})",
        ))

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
    span_report = result.metadata.get("span_attribution")
    spans = span_report.get("spans") if isinstance(span_report, dict) else None
    has_spans = isinstance(spans, list) and bool(spans)
    if not result.credits and not has_spans:
        return ""
    if result.total_delta <= 0 and not has_spans:
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

    positive_delta = result.total_delta > 0
    lines = [f"## {title} (Gen {result.generation})"]
    if positive_delta:
        lines.append(f"Total score improvement: +{result.total_delta:.4f}")
        if guidance:
            lines.append(guidance)
    else:
        lines.append(f"Total score delta: {result.total_delta:+.4f}")
        lines.append("Use low/negative span credits as demotion candidates before preserving guidance.")
    lines.append("")

    for component in ordered_components:
        credit = result.credits.get(component, 0.0)
        if positive_delta:
            pct = credit / result.total_delta * 100
            lines.append(f"- {component}: +{credit:.4f} ({pct:.0f}% of improvement)")
        else:
            lines.append(f"- {component}: {credit:+.4f} (non-positive run)")

    if isinstance(spans, list) and spans:
        lines.append("")
        lines.append("Span attribution (component-correlated, noisy):")
        preferred_sources = set(preferred) if preferred else set(result.credits)
        shown = 0
        sorted_spans = sorted(
            spans,
            key=lambda item: float(item.get("credit", 0.0)) if isinstance(item, dict) else 0.0,
            reverse=positive_delta,
        )
        for span in sorted_spans:
            if not isinstance(span, dict) or str(span.get("source", "")) not in preferred_sources:
                continue
            lines.append(f"- {span.get('text', '')}: {float(span.get('credit', 0.0)):+.4f}")
            shown += 1
            if shown >= 5:
                break

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
