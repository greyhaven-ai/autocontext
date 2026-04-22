"""Types for the decomposed generation pipeline stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.agents.skeptic import SkepticReview
    from autocontext.agents.types import AgentOutputs
    from autocontext.config.settings import AppSettings
    from autocontext.execution.policy_refinement import PolicyRefinementResult
    from autocontext.harness.evaluation.types import EvaluationSummary
    from autocontext.harness.pipeline.holdout import HoldoutResult
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
    base_tool_names: list[str] = field(default_factory=list)
    base_analysis: str = ""
    fresh_start_triggered: bool = False
    probe_refinement_applied: bool = False
    dag_changes: list[dict[str, Any]] = field(default_factory=list)
    base_playbook: str = ""
    base_lessons: str = ""
    exploration_metadata: dict[str, Any] = field(default_factory=dict)
    cost_control_metadata: dict[str, Any] = field(default_factory=dict)
    semantic_compaction_benchmark: dict[str, Any] | None = None

    # Pipeline wiring: tuning proposal from architect (AR-6)
    tuning_proposal: TuningConfig | None = None

    # Staged validation results (AC-200)
    staged_validation_results: list[Any] | None = None
    staged_validation_metrics: dict[str, Any] | None = None

    # Policy refinement result (AC-156)
    policy_refinement_result: PolicyRefinementResult | None = None

    # AC-174: generation timing
    generation_start_time: float = 0.0
    generation_elapsed_seconds: float = 0.0
    phased_execution: dict[str, Any] | None = None

    # Environment snapshot prompt section (AC-503)
    environment_snapshot: str = ""
    evidence_manifest: str = ""

    # Consultation result (AC-212)
    consultation_result: Any | None = None
    holdout_result: HoldoutResult | None = None
    skeptic_review: SkepticReview | None = None
    applied_competitor_hints: str = ""
    challenger_uncertainty: float | None = None


@dataclass(slots=True)
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: str
    success: bool
    error: str | None = None
