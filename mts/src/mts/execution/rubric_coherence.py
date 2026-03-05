from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class RubricCoherenceResult:
    """Result of rubric coherence pre-check."""

    warnings: list[str] = field(default_factory=list)
    is_coherent: bool = True


def check_rubric_coherence(rubric: str) -> RubricCoherenceResult:
    """Check a rubric for potential coherence issues.

    Detects contradictory adjective pairs, overly vague criteria,
    and underspecified rubrics. Returns warnings (non-blocking).
    """
    warnings: list[str] = []

    # Check for contradictory adjective pairs
    contradictions = [
        ("simple", "complex"),
        ("brief", "comprehensive"),
        ("concise", "detailed"),
        ("short", "thorough"),
        ("minimal", "extensive"),
    ]
    lower = rubric.lower()
    for a, b in contradictions:
        if re.search(rf"\b{a}\b", lower) and re.search(rf"\b{b}\b", lower):
            warnings.append(f'Potentially contradictory criteria: "{a}" and "{b}" both appear')

    # Check for overly vague criteria
    vague_matches = re.findall(r"\b(good|nice|appropriate|adequate|proper)\b", lower)
    if len(vague_matches) > 2:
        sample = ", ".join(vague_matches[:3])
        warnings.append(f"Rubric may be too vague: {len(vague_matches)} generic terms found ({sample})")

    # Check for very short rubric (likely underspecified)
    if len(rubric.strip().split()) < 10:
        warnings.append("Rubric may be underspecified: fewer than 10 words")

    return RubricCoherenceResult(warnings=warnings, is_coherent=len(warnings) == 0)
