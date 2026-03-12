"""Thin SDK client for programmatic AutoContext usage (AC-187).

Provides a high-level ``AutoContext`` class that delegates to the same pure-function
tool implementations used by the CLI and MCP server, returning typed result
models instead of raw dicts.

Example usage::

    from autocontext import AutoContext

    client = AutoContext(db_path="runs/autocontext.sqlite3")
    scenarios = client.list_scenarios()
    result = client.evaluate("grid_ctf", {"aggression": 0.5}, matches=5)
    print(result.mean_score)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.config import AppSettings, load_settings
from autocontext.config.settings import validate_harness_mode
from autocontext.mcp import tools
from autocontext.mcp.tools import MtsToolContext
from autocontext.sdk_models import EvaluateResult, MatchResult, SearchResult, ValidateResult


class AutoContext:
    """High-level SDK for programmatic AutoContext usage.

    Wraps the shared tool layer that the CLI and MCP server also use,
    exposing a small, stable API with typed return values.
    """

    def __init__(
        self,
        *,
        settings: AppSettings | None = None,
        db_path: str | Path | None = None,
        knowledge_root: str | Path | None = None,
        skills_root: str | Path | None = None,
        claude_skills_path: str | Path | None = None,
        **overrides: Any,
    ) -> None:
        """Initialize the SDK client.

        Parameters
        ----------
        settings:
            Pre-built ``AppSettings`` instance.  When provided, all other
            keyword arguments are ignored.
        db_path, knowledge_root, skills_root, claude_skills_path:
            Convenience path overrides that map to the corresponding
            ``AppSettings`` fields.
        **overrides:
            Arbitrary additional ``AppSettings`` field overrides
            (e.g. ``matches_per_generation=5``).
        """
        if settings is not None:
            self._settings = validate_harness_mode(settings)
        else:
            base_settings = load_settings()
            kwargs: dict[str, Any] = {}
            if db_path is not None:
                kwargs["db_path"] = Path(db_path)
            if knowledge_root is not None:
                kwargs["knowledge_root"] = Path(knowledge_root)
            if skills_root is not None:
                kwargs["skills_root"] = Path(skills_root)
            if claude_skills_path is not None:
                kwargs["claude_skills_path"] = Path(claude_skills_path)
            kwargs.update(overrides)
            self._settings = validate_harness_mode(base_settings.model_copy(update=kwargs))

        self._ctx = MtsToolContext(self._settings)

    # -- Scenario discovery -------------------------------------------------

    def list_scenarios(self) -> list[dict[str, str]]:
        """Return available scenarios with name and rules preview."""
        return tools.list_scenarios()

    def describe_scenario(self, name: str) -> dict[str, str]:
        """Return full scenario description: rules, strategy interface, evaluation criteria."""
        return tools.describe_scenario(name)

    # -- Strategy evaluation ------------------------------------------------

    def validate(self, scenario: str, strategy: dict[str, object]) -> ValidateResult:
        """Validate a strategy dict against scenario constraints.

        Returns a :class:`ValidateResult` with ``valid`` and ``reason`` fields.
        """
        raw: dict[str, Any] = tools.validate_strategy_against_harness(scenario, strategy, ctx=self._ctx)
        if "error" in raw:
            return ValidateResult(valid=False, reason=str(raw["error"]))
        return ValidateResult(
            valid=bool(raw.get("valid", False)),
            reason=str(raw.get("reason", "")),
        )

    def evaluate(
        self,
        scenario: str,
        strategy: dict[str, object],
        matches: int = 3,
        seed_base: int = 42,
    ) -> EvaluateResult:
        """Run *matches* tournament games and return aggregate scores.

        Returns an :class:`EvaluateResult`.  If the scenario is an agent task
        (which uses judge evaluation), the ``error`` field is populated instead.
        """
        validation: dict[str, Any] = tools.validate_strategy_against_harness(scenario, strategy, ctx=self._ctx)
        if "error" in validation:
            return EvaluateResult(error=str(validation["error"]))
        if not bool(validation.get("valid", False)):
            return EvaluateResult(error=str(validation.get("reason", "validation failed")))

        raw: dict[str, Any] = tools.evaluate_strategy(scenario, strategy, num_matches=matches, seed_base=seed_base)
        if "error" in raw:
            return EvaluateResult(error=str(raw["error"]))
        return EvaluateResult(
            scores=list(raw.get("scores", [])),
            mean_score=float(raw.get("mean_score", 0.0)),
            best_score=float(raw.get("best_score", 0.0)),
            matches=int(raw.get("matches", 0)),
        )

    def match(
        self,
        scenario: str,
        strategy: dict[str, object],
        seed: int = 42,
    ) -> MatchResult:
        """Execute a single match and return the result.

        Returns a :class:`MatchResult`.
        """
        validation: dict[str, Any] = tools.validate_strategy_against_harness(scenario, strategy, ctx=self._ctx)
        if "error" in validation:
            return MatchResult(error=str(validation["error"]))
        if not bool(validation.get("valid", False)):
            return MatchResult(error=str(validation.get("reason", "validation failed")))

        raw: dict[str, Any] = tools.run_match(scenario, strategy, seed=seed)
        if "error" in raw:
            return MatchResult(error=str(raw["error"]))
        return MatchResult(
            score=float(raw.get("score", 0.0)),
            winner=str(raw.get("winner", "")),
            summary=str(raw.get("summary", "")),
            metrics=dict(raw.get("metrics", {})),
            replay=raw.get("replay"),
        )

    # -- Knowledge ----------------------------------------------------------

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Search solved scenarios by natural-language query.

        Returns a list of :class:`SearchResult` ranked by relevance.
        """
        raw_list: list[dict[str, Any]] = tools.search_strategies(self._ctx, query, top_k)
        return [
            SearchResult(
                scenario_name=str(r.get("scenario", "")),
                display_name=str(r.get("display_name", "")),
                description=str(r.get("description", "")),
                relevance=float(r.get("relevance", 0.0)),
                best_score=float(r.get("best_score", 0.0)),
                best_elo=float(r.get("best_elo", 1500.0)),
                match_reason=str(r.get("match_reason", "")),
            )
            for r in raw_list
        ]

    def export_skill(self, scenario: str) -> dict[str, object]:
        """Export a portable skill package for a solved scenario."""
        return tools.export_skill(self._ctx, scenario)

    def export_package(self, scenario: str) -> dict[str, object]:
        """Export a versioned, portable strategy package."""
        return tools.export_package(self._ctx, scenario)

    # -- Artifacts ----------------------------------------------------------

    def list_artifacts(
        self,
        scenario: str | None = None,
        artifact_type: str | None = None,
    ) -> list[dict[str, object]]:
        """List published artifacts, optionally filtered by scenario or type."""
        return tools.list_artifacts(self._ctx, scenario=scenario, artifact_type=artifact_type)
