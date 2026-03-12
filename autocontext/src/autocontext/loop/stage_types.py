"""Types for the decomposed generation pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.agents.types import AgentOutputs
    from autocontext.config.settings import AppSettings
    from autocontext.harness.evaluation.types import EvaluationSummary
    from autocontext.knowledge.tuning import TuningConfig
    from autocontext.prompts.templates import PromptBundle
    from autocontext.scenarios.base import ScenarioInterface


@dataclass(slots=True)
class GenerationContext:
    """Carries all mutable state between generation pipeline stages."""

    # Immutable inputs
    run_id: str
    scenario_name: str
    scenario: ScenarioInterface
    generation: int
    settings: AppSettings

    # Mutable state carried across generations
    previous_best: float
    challenger_elo: float
    score_history: list[float]
    gate_decision_history: list[str]
    coach_competitor_hints: str
    replay_narrative: str

    # Stage outputs (populated progressively by stages)
    prompts: PromptBundle | None = None
    outputs: AgentOutputs | None = None
    tournament: EvaluationSummary | None = None
    gate_decision: str = ""
    gate_delta: float = 0.0
    current_strategy: dict[str, Any] = field(default_factory=dict)
    created_tools: list[str] = field(default_factory=list)
    attempt: int = 0
    strategy_interface: str = ""
    tool_context: str = ""
    fresh_start_triggered: bool = False
    probe_refinement_applied: bool = False
    dag_changes: list[dict[str, Any]] = field(default_factory=list)

    # Pipeline wiring: tuning proposal from architect (AR-6)
    tuning_proposal: TuningConfig | None = None

    # Staged validation results (AC-200)
    staged_validation_results: list[Any] | None = None
    staged_validation_metrics: dict[str, Any] | None = None


@dataclass(slots=True)
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: str
    success: bool
    error: str | None = None
