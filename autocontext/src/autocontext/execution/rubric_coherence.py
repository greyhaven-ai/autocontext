from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class RubricCoherenceResult:
    """Result of rubric coherence pre-check."""

    warnings: list[str] = field(default_factory=list)
    is_coherent: bool = True


def _has_pattern(lower: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, lower) for pattern in patterns)


def _allows_separate_depth_and_accessibility(lower: str) -> bool:
    separate_surface_patterns = (
        r"\b(?:two|2)\s+(?:separate\s+)?(?:sections|parts|versions|explanations)\b",
        r"\bseparate\s+(?:sections|parts|versions|explanations|audiences)\b",
    )
    if _has_pattern(lower, separate_surface_patterns):
        return True

    advanced_unit = r"(?:advanced|expert|graduate|technical)\s+(?:section|version|treatment|explanation)"
    beginner_unit = r"(?:beginner|child|kid|layperson)\s+(?:section|version|explanation)"
    return bool(
        re.search(rf"\b{advanced_unit}\b.*\b{beginner_unit}\b", lower)
        or re.search(rf"\b{beginner_unit}\b.*\b{advanced_unit}\b", lower)
    )


def check_rubric_coherence(rubric: str) -> RubricCoherenceResult:
    """Check a rubric for potential coherence issues.

    Detects contradictory adjective pairs, same-span audience/depth conflicts,
    overly vague criteria, and underspecified rubrics. Returns warnings
    (non-blocking).
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

    depth_patterns = (
        r"\bgraduate\b",
        r"\bgraduate-level\b",
        r"\bseminar depth\b",
        r"\badvanced\b",
        r"\bexpert\b",
        r"\btechnical depth\b",
        r"\brigorous\b",
    )
    child_accessibility_patterns = (
        r"\b5-year-old\b",
        r"\bfive-year-old\b",
        r"\bchild\b",
        r"\bkid\b",
        r"\bbeginner\b",
        r"\blayperson\b",
        r"\baccessible to a child\b",
    )
    if (
        _has_pattern(lower, depth_patterns)
        and _has_pattern(lower, child_accessibility_patterns)
        and not _allows_separate_depth_and_accessibility(lower)
    ):
        warnings.append("Potentially contradictory criteria: graduate-level depth and child-level accessibility both appear")

    # Check for overly vague criteria
    vague_matches = re.findall(r"\b(good|nice|appropriate|adequate|proper)\b", lower)
    if len(vague_matches) > 2:
        sample = ", ".join(vague_matches[:3])
        warnings.append(f"Rubric may be too vague: {len(vague_matches)} generic terms found ({sample})")

    # Check for very short rubric (likely underspecified)
    if len(rubric.strip().split()) < 10:
        warnings.append("Rubric may be underspecified: fewer than 10 words")

    return RubricCoherenceResult(warnings=warnings, is_coherent=len(warnings) == 0)
