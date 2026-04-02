"""Research runtime plumbing — connects adapter to session (AC-498).

ResearchEnabledSession extends the session model with:
- Optional research adapter attachment
- Per-session query budget enforcement
- Research event emission
- Accumulated research history for prompt injection
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from autocontext.research.types import ResearchAdapter, ResearchConfig, ResearchQuery, ResearchResult

logger = logging.getLogger(__name__)


class _ResearchEvent:
    """Lightweight event for research activity tracking."""

    __slots__ = ("eventId", "eventType", "timestamp", "payload")

    def __init__(self, event_type: str, payload: dict[str, Any]) -> None:
        self.eventId = uuid.uuid4().hex[:12]
        self.eventType = event_type
        self.timestamp = datetime.now(UTC).isoformat()
        self.payload = payload


class ResearchEnabledSession:
    """Session with optional research capabilities.

    Wraps research adapter with budget enforcement and history tracking.
    Create via ResearchEnabledSession.create().
    """

    def __init__(
        self,
        goal: str,
        research_adapter: ResearchAdapter | None = None,
        research_config: ResearchConfig | None = None,
    ) -> None:
        self.session_id = uuid.uuid4().hex[:16]
        self.goal = goal
        self._adapter = research_adapter
        self._config = research_config or ResearchConfig(enabled=research_adapter is not None)
        self._query_count = 0
        self._history: list[ResearchResult] = []
        self.events: list[_ResearchEvent] = []

        self.events.append(_ResearchEvent("session_created", {"goal": goal}))

    @classmethod
    def create(
        cls,
        goal: str,
        research_adapter: ResearchAdapter | None = None,
        research_config: ResearchConfig | None = None,
    ) -> ResearchEnabledSession:
        return cls(goal=goal, research_adapter=research_adapter, research_config=research_config)

    @property
    def has_research(self) -> bool:
        return self._adapter is not None and self._config.enabled

    @property
    def research_queries_used(self) -> int:
        return self._query_count

    @property
    def research_history(self) -> list[ResearchResult]:
        return list(self._history)

    def research(self, query: ResearchQuery) -> ResearchResult | None:
        """Execute a research query if adapter is available and budget allows.

        Returns None if:
        - No adapter attached
        - Research is disabled by config
        - Query budget exhausted
        """
        if self._adapter is None or not self._config.enabled:
            return None

        if self._query_count >= self._config.max_queries_per_session:
            logger.debug("research budget exhausted (%d/%d)", self._query_count, self._config.max_queries_per_session)
            return None

        result = self._adapter.search(query)
        self._query_count += 1
        self._history.append(result)

        self.events.append(_ResearchEvent("research_requested", {
            "topic": query.topic,
            "confidence": result.confidence,
            "citations": len(result.citations),
        }))

        return result
