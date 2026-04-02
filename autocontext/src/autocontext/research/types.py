"""External research adapter contract and domain types (AC-497).

Domain concepts:
- ResearchQuery: what we're asking (topic, context, urgency, constraints)
- Citation: provenance tracking for a single source
- ResearchResult: what comes back (summary, citations, confidence)
- ResearchAdapter: Protocol for pluggable research backends
- ResearchConfig: opt-in settings surface
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class Urgency(StrEnum):
    """How urgently research is needed."""

    LOW = "low"        # Background enrichment, no rush
    NORMAL = "normal"  # Standard research request
    HIGH = "high"      # Blocking on this for next decision


class ResearchQuery(BaseModel):
    """What we're asking the research backend.

    Carries enough context for the adapter to scope the search.
    """

    topic: str
    context: str = ""
    urgency: Urgency = Urgency.NORMAL
    max_results: int = Field(default=5, ge=1)
    constraints: list[str] = Field(default_factory=list)
    scenario_family: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"frozen": True}


class Citation(BaseModel):
    """One source with provenance tracking."""

    source: str
    url: str = ""
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    snippet: str = ""
    retrieved_at: str = ""

    model_config = {"frozen": True}


class ResearchResult(BaseModel):
    """What the research backend returns."""

    query_topic: str
    summary: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_citations(self) -> bool:
        return len(self.citations) > 0

    model_config = {"frozen": True}


@runtime_checkable
class ResearchAdapter(Protocol):
    """Protocol for pluggable external research backends.

    Implementors: Perplexity, Exa, Google Scholar, internal doc search, etc.
    """

    def search(self, query: ResearchQuery) -> ResearchResult:
        """Execute a research query and return structured results."""
        ...


class ResearchConfig(BaseModel):
    """Opt-in settings for external research integration.

    Disabled by default — must be explicitly enabled per workspace.
    """

    enabled: bool = False
    adapter_name: str = ""  # e.g. "perplexity", "exa", "internal"
    max_queries_per_session: int = Field(default=20, ge=0)
    max_queries_per_turn: int = Field(default=3, ge=0)
    require_citations: bool = True
    min_confidence: float = Field(default=0.3, ge=0.0, le=1.0)

    model_config = {"frozen": True}
