"""Typed result models for the AutoContext SDK (AC-187).

These Pydantic models provide structured return types for SDK operations,
insulating callers from internal dict shapes.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ValidateResult(BaseModel):
    """Result of strategy validation against scenario constraints."""

    valid: bool
    reason: str = ""


class EvaluateResult(BaseModel):
    """Aggregate result from evaluating a strategy over multiple matches."""

    scores: list[float] = Field(default_factory=list)
    mean_score: float = 0.0
    best_score: float = 0.0
    matches: int = 0
    error: str | None = None


class MatchResult(BaseModel):
    """Result from a single match execution."""

    score: float = 0.0
    winner: str = ""
    summary: str = ""
    metrics: dict[str, object] = Field(default_factory=dict)
    replay: list[object] | None = None
    error: str | None = None


class SearchResult(BaseModel):
    """A single search hit from the knowledge index."""

    scenario_name: str
    display_name: str = ""
    description: str = ""
    relevance: float = 0.0
    best_score: float = 0.0
    best_elo: float = 1500.0
    match_reason: str = ""
