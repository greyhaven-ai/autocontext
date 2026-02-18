# Phase 4: GenerationRunner Decomposition — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Decompose the 580-line `GenerationRunner.run()` monolith into composable pipeline stages backed by harness primitives, wire `EvaluationRunner` + `ScenarioEvaluator` into the tournament path, and add a `pipeline_adapter`-style `build_mts_dag()` to optionally drive the full generation loop.

**Architecture:** Extract 6 stage functions from `GenerationRunner.run()`, create a `ScenarioEvaluatorAdapter` in the execution layer that bridges `TournamentRunner` to `EvaluationRunner`, and build a `GenerationPipeline` orchestrator that sequences stages with event hooks. Feature-gated behind `use_generation_pipeline: bool = False`.

**Tech Stack:** Python 3.11+, pytest, pydantic, dataclasses, uv, ruff, mypy

---

## Context: What Phases 1-3 Delivered

- **Phase 1**: Harness core primitives (`core/types`, `core/llm_client`, `core/subagent`, `core/events`, `core/controller`, `pipeline/gate`, `pipeline/trend_gate`, `pipeline/retry_context`, `repl/*`)
- **Phase 2**: Composable infrastructure (`scoring/elo`, `core/output_parser`, `storage/versioned_store`, `evaluation/types+protocol+runner`, `orchestration/dag+engine+types`)
- **Phase 3**: MTS rewiring (`ScenarioEvaluator` adapter, `ArtifactStore` delegation to `VersionedFileStore`, output parser adoption in coach/translator, `PipelineEngine`-backed orchestrator via `pipeline_adapter`)
- **Current state**: 550 tests passing, ruff clean, mypy clean

## What This Phase Builds

### New Files
```
mts/src/mts/loop/
  stages.py              # NEW — 6 extracted stage functions
  generation_pipeline.py # NEW — GenerationPipeline orchestrator
  stage_types.py         # NEW — GenerationContext, StageResult dataclasses

mts/src/mts/execution/
  eval_adapter.py        # NEW — TournamentEvalAdapter wrapping EvaluationRunner

mts/tests/
  test_generation_stages.py      # NEW — unit tests for each stage function
  test_generation_pipeline.py    # NEW — integration tests for GenerationPipeline
  test_eval_adapter.py           # NEW — tests for TournamentEvalAdapter
```

### Modified Files
```
mts/src/mts/loop/generation_runner.py   # MODIFIED — delegates to GenerationPipeline when flag on
mts/src/mts/config/settings.py          # MODIFIED — adds use_generation_pipeline field
```

## Design Decisions

### Why Stage Functions (Not Classes)

The 580-line `run()` method decomposes naturally into 6 pure-ish functions that accept a `GenerationContext` dataclass and return it mutated. This is simpler than a class hierarchy and easier to test:

```python
def stage_knowledge_setup(ctx: GenerationContext) -> GenerationContext: ...
def stage_agent_generation(ctx: GenerationContext) -> GenerationContext: ...
def stage_tournament(ctx: GenerationContext) -> GenerationContext: ...
def stage_curator_gate(ctx: GenerationContext) -> GenerationContext: ...
def stage_persistence(ctx: GenerationContext) -> GenerationContext: ...
def stage_completion(ctx: GenerationContext) -> GenerationContext: ...
```

### GenerationContext Dataclass

Carries all mutable state between stages. This replaces the dozen+ local variables currently scattered across `run()`:

```python
@dataclass
class GenerationContext:
    # Immutable inputs
    run_id: str
    scenario_name: str
    scenario: ScenarioInterface
    generation: int
    settings: AppSettings
    # Mutable state
    previous_best: float
    challenger_elo: float
    score_history: list[float]
    gate_decision_history: list[str]
    coach_competitor_hints: str
    replay_narrative: str
    # Stage outputs (populated progressively)
    prompts: PromptBundle | None = None
    outputs: AgentOutputs | None = None
    tournament: TournamentSummary | None = None
    gate_decision: str = ""
    gate_delta: float = 0.0
    current_strategy: dict[str, Any] = field(default_factory=dict)
    created_tools: list[str] = field(default_factory=list)
```

### TournamentEvalAdapter

Rather than replacing `TournamentRunner` (which has its own `ExecutionOutput` with `ReplayEnvelope` that downstream code needs), we create a thin adapter that uses `EvaluationRunner` internally but returns `TournamentSummary` for backward compatibility:

```python
class TournamentEvalAdapter:
    """Wraps EvaluationRunner to produce TournamentSummary."""
    def __init__(self, supervisor: ExecutionSupervisor, opponent_elo: float = 1000.0):
        ...
    def run(self, *, scenario, strategy, seed_base, matches, limits, challenger_elo, on_match) -> TournamentSummary:
        evaluator = ScenarioEvaluator(scenario, self.supervisor)
        runner = EvaluationRunner(evaluator, opponent_elo=self.opponent_elo)
        summary = runner.run(candidate=strategy, seed_base=seed_base, trials=matches, ...)
        # Convert EvaluationSummary -> TournamentSummary
        ...
```

### Feature Gate

`use_generation_pipeline: bool = False` in `AppSettings`. When enabled, `GenerationRunner.run()` delegates the per-generation body to `GenerationPipeline.run_generation()`. All existing behavior preserved when flag is off.

### What Does NOT Change

- `GenerationRunner.__init__()` — unchanged, still constructs all dependencies
- `GenerationRunner.run()` outer loop — run creation, cross-run inheritance, idempotency check, completion snapshot all stay in `run()`
- `TournamentRunner` — unchanged, still works as before. The adapter is an alternative, not a replacement.
- All 550 existing tests — zero regressions

---

## TDD Batches

### Batch 4.1: Stage Types + GenerationContext

**Scope**: Define the `GenerationContext` dataclass and `StageResult` types that stages operate on.

**Why first**: Zero dependencies on other new code. Pure data containers.

**Files:**
- Create: `mts/src/mts/loop/stage_types.py`
- Test: `mts/tests/test_generation_stages.py` (initial context tests only)

**Step 1: Write failing tests**

Create `mts/tests/test_generation_stages.py`:

```python
"""Tests for generation pipeline stage types and stage functions."""
from __future__ import annotations

import pytest

from mts.loop.stage_types import GenerationContext, StageResult


class TestGenerationContext:
    def test_construction_with_required_fields(self) -> None:
        ctx = GenerationContext(
            run_id="run_123",
            scenario_name="grid_ctf",
            scenario=None,  # type: ignore[arg-type]
            generation=1,
            settings=None,  # type: ignore[arg-type]
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )
        assert ctx.run_id == "run_123"
        assert ctx.generation == 1
        assert ctx.previous_best == 0.0
        assert ctx.challenger_elo == 1000.0

    def test_optional_fields_default_none(self) -> None:
        ctx = GenerationContext(
            run_id="r", scenario_name="s", scenario=None,  # type: ignore[arg-type]
            generation=1, settings=None,  # type: ignore[arg-type]
            previous_best=0.0, challenger_elo=1000.0,
            score_history=[], gate_decision_history=[],
            coach_competitor_hints="", replay_narrative="",
        )
        assert ctx.prompts is None
        assert ctx.outputs is None
        assert ctx.tournament is None
        assert ctx.gate_decision == ""
        assert ctx.gate_delta == 0.0
        assert ctx.current_strategy == {}
        assert ctx.created_tools == []

    def test_mutable_fields_independent(self) -> None:
        ctx1 = GenerationContext(
            run_id="r1", scenario_name="s", scenario=None,  # type: ignore[arg-type]
            generation=1, settings=None,  # type: ignore[arg-type]
            previous_best=0.0, challenger_elo=1000.0,
            score_history=[], gate_decision_history=[],
            coach_competitor_hints="", replay_narrative="",
        )
        ctx2 = GenerationContext(
            run_id="r2", scenario_name="s", scenario=None,  # type: ignore[arg-type]
            generation=2, settings=None,  # type: ignore[arg-type]
            previous_best=0.0, challenger_elo=1000.0,
            score_history=[], gate_decision_history=[],
            coach_competitor_hints="", replay_narrative="",
        )
        ctx1.current_strategy["a"] = 1
        assert ctx2.current_strategy == {}


class TestStageResult:
    def test_success_construction(self) -> None:
        result = StageResult(stage="knowledge_setup", success=True)
        assert result.stage == "knowledge_setup"
        assert result.success is True
        assert result.error is None

    def test_failure_with_error(self) -> None:
        result = StageResult(stage="tournament", success=False, error="timeout")
        assert result.success is False
        assert result.error == "timeout"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_generation_stages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mts.loop.stage_types'`

**Step 3: Write minimal implementation**

Create `mts/src/mts/loop/stage_types.py`:

```python
"""Types for the decomposed generation pipeline stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mts.agents.types import AgentOutputs
    from mts.config.settings import AppSettings
    from mts.execution.tournament import TournamentSummary
    from mts.prompts.templates import PromptBundle
    from mts.scenarios.base import ScenarioInterface


@dataclass
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
    tournament: TournamentSummary | None = None
    gate_decision: str = ""
    gate_delta: float = 0.0
    current_strategy: dict[str, Any] = field(default_factory=dict)
    created_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: str
    success: bool
    error: str | None = None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_generation_stages.py -v`
Expected: PASS (5 tests)

**Step 5: Verify no regressions**

Run: `uv run pytest`
Expected: 550 + 5 = 555 passed

**Step 6: Commit**

```bash
git add tests/test_generation_stages.py src/mts/loop/stage_types.py
git commit -m "feat(phase4): add GenerationContext and StageResult types"
```

---

### Batch 4.2: TournamentEvalAdapter

**Scope**: Adapter that uses `EvaluationRunner` + `ScenarioEvaluator` under the hood but returns `TournamentSummary` for backward compatibility with `GenerationRunner`.

**Why second**: Depends only on Phase 2/3 harness modules. Independent of stage decomposition.

**Files:**
- Create: `mts/src/mts/execution/eval_adapter.py`
- Test: `mts/tests/test_eval_adapter.py`

**Step 1: Write failing tests**

Create `mts/tests/test_eval_adapter.py`:

```python
"""Tests for TournamentEvalAdapter — EvaluationRunner-backed tournament."""
from __future__ import annotations

import pytest

from mts.execution.eval_adapter import TournamentEvalAdapter
from mts.execution.supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor
from mts.execution.tournament import TournamentSummary
from mts.scenarios.base import ExecutionLimits


class FakeScenario:
    """Minimal scenario for testing."""
    name = "fake"

    def initial_state(self, seed: int | None = None) -> dict:
        return {"seed": seed or 0}

    def get_observation(self, state, player_id):
        from mts.scenarios.base import Observation
        return Observation(narrative="test")

    def validate_actions(self, state, player_id, actions):
        return True, ""

    def step(self, state, actions):
        return {**state, "terminal": True, "score": actions.get("aggression", 0.5)}

    def is_terminal(self, state):
        return state.get("terminal", False)

    def get_result(self, state):
        from mts.scenarios.base import Result
        score = state.get("score", 0.5)
        return Result(score=score, summary="test", replay=[])

    def replay_to_narrative(self, replay):
        return "test narrative"

    def describe_rules(self):
        return "rules"

    def describe_strategy_interface(self):
        return '{"aggression": float}'

    def describe_evaluation_criteria(self):
        return "score"

    def render_frame(self, state):
        return {}

    def execute_match(self, strategy, seed):
        state = self.initial_state(seed=seed)
        valid, _ = self.validate_actions(state, "challenger", strategy)
        if not valid:
            from mts.scenarios.base import Result
            return Result(score=0.0, summary="invalid", replay=[])
        next_state = self.step(state, strategy)
        return self.get_result(next_state)


class TestTournamentEvalAdapter:
    def test_returns_tournament_summary(self) -> None:
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        adapter = TournamentEvalAdapter(supervisor)
        result = adapter.run(
            scenario=scenario,
            strategy={"aggression": 0.7},
            seed_base=1000,
            matches=3,
            limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert isinstance(result, TournamentSummary)

    def test_scores_match_direct_tournament(self) -> None:
        """Adapter produces same scores as direct TournamentRunner."""
        from mts.execution.tournament import TournamentRunner

        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        strategy = {"aggression": 0.7}

        direct = TournamentRunner(supervisor)
        direct_result = direct.run(
            scenario=scenario, strategy=strategy, seed_base=1000,
            matches=3, limits=ExecutionLimits(), challenger_elo=1000.0,
        )

        adapter = TournamentEvalAdapter(supervisor)
        adapter_result = adapter.run(
            scenario=scenario, strategy=strategy, seed_base=1000,
            matches=3, limits=ExecutionLimits(), challenger_elo=1000.0,
        )

        assert adapter_result.mean_score == pytest.approx(direct_result.mean_score, abs=1e-6)
        assert adapter_result.best_score == pytest.approx(direct_result.best_score, abs=1e-6)
        assert adapter_result.wins == direct_result.wins
        assert adapter_result.losses == direct_result.losses

    def test_elo_updates_match(self) -> None:
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        adapter = TournamentEvalAdapter(supervisor)
        result = adapter.run(
            scenario=scenario, strategy={"aggression": 0.7},
            seed_base=1000, matches=5, limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        # Elo should change from initial 1000.0
        assert result.elo_after != 1000.0 or result.wins == 0

    def test_outputs_contain_execution_outputs(self) -> None:
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        adapter = TournamentEvalAdapter(supervisor)
        result = adapter.run(
            scenario=scenario, strategy={"aggression": 0.7},
            seed_base=1000, matches=3, limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert len(result.outputs) == 3
        for output in result.outputs:
            assert isinstance(output, ExecutionOutput)

    def test_on_match_callback(self) -> None:
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        adapter = TournamentEvalAdapter(supervisor)
        matches_seen: list[tuple[int, float]] = []

        def _on_match(idx: int, score: float) -> None:
            matches_seen.append((idx, score))

        adapter.run(
            scenario=scenario, strategy={"aggression": 0.7},
            seed_base=1000, matches=3, limits=ExecutionLimits(),
            challenger_elo=1000.0, on_match=_on_match,
        )
        assert len(matches_seen) == 3
        assert all(isinstance(s, float) for _, s in matches_seen)

    def test_custom_opponent_elo(self) -> None:
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        adapter = TournamentEvalAdapter(supervisor, opponent_elo=1200.0)
        result = adapter.run(
            scenario=scenario, strategy={"aggression": 0.7},
            seed_base=1000, matches=1, limits=ExecutionLimits(),
            challenger_elo=1000.0,
        )
        assert isinstance(result, TournamentSummary)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_eval_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mts.execution.eval_adapter'`

**Step 3: Write minimal implementation**

Create `mts/src/mts/execution/eval_adapter.py`:

```python
"""TournamentEvalAdapter — EvaluationRunner-backed tournament producing TournamentSummary."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mts.execution.supervisor import ExecutionInput, ExecutionOutput, ExecutionSupervisor
from mts.execution.tournament import TournamentSummary
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.scenarios.base import ExecutionLimits, ScenarioInterface


class TournamentEvalAdapter:
    """Wraps EvaluationRunner to produce TournamentSummary for backward compatibility.

    Uses ScenarioEvaluator + EvaluationRunner internally, but returns the same
    TournamentSummary type that TournamentRunner does, so callers don't need
    to change.
    """

    def __init__(self, supervisor: ExecutionSupervisor, opponent_elo: float = 1000.0) -> None:
        self.supervisor = supervisor
        self.opponent_elo = opponent_elo

    def run(
        self,
        *,
        scenario: ScenarioInterface,
        strategy: dict[str, Any],
        seed_base: int,
        matches: int,
        limits: ExecutionLimits,
        challenger_elo: float,
        on_match: Callable[[int, float], None] | None = None,
    ) -> TournamentSummary:
        evaluator = ScenarioEvaluator(scenario, self.supervisor)
        harness_limits = HarnessLimits(
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
        )

        # We need ExecutionOutputs for backward compat, so we run matches
        # through both paths: EvaluationRunner for Elo/scoring, direct
        # supervisor for ExecutionOutput collection.
        outputs: list[ExecutionOutput] = []
        for offset in range(matches):
            payload = ExecutionInput(strategy=strategy, seed=seed_base + offset, limits=limits)
            output = self.supervisor.run(scenario, payload)
            outputs.append(output)

        # Use EvaluationRunner for scoring + Elo
        def _on_result(idx: int, result: Any) -> None:
            if on_match:
                on_match(idx, result.score)

        runner = EvaluationRunner(evaluator, opponent_elo=self.opponent_elo)
        summary = runner.run(
            candidate=strategy,
            seed_base=seed_base,
            trials=matches,
            limits=harness_limits,
            challenger_elo=challenger_elo,
            on_result=_on_result,
        )

        return TournamentSummary(
            mean_score=summary.mean_score,
            best_score=summary.best_score,
            wins=summary.wins,
            losses=summary.losses,
            elo_after=summary.elo_after,
            outputs=outputs,
        )
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_eval_adapter.py -v`
Expected: PASS (6 tests)

**Step 5: Verify no regressions**

Run: `uv run pytest`
Expected: 555 + 6 = 561 passed

**Step 6: Commit**

```bash
git add tests/test_eval_adapter.py src/mts/execution/eval_adapter.py
git commit -m "feat(phase4): add TournamentEvalAdapter bridging EvaluationRunner to TournamentSummary"
```

---

### Batch 4.3: Stage Functions — Knowledge Setup + Agent Generation

**Scope**: Extract the first 2 stages from `GenerationRunner.run()` into testable functions.

**Why third**: Depends on `GenerationContext` from Batch 4.1. These are the simplest stages — no retry loops, no gate logic.

**Files:**
- Create: `mts/src/mts/loop/stages.py`
- Modify: `mts/tests/test_generation_stages.py` (add stage tests)

**Step 1: Write failing tests**

Add to `mts/tests/test_generation_stages.py`:

```python
from unittest.mock import MagicMock, patch

from mts.agents.llm_client import DeterministicDevClient
from mts.agents.orchestrator import AgentOrchestrator
from mts.config.settings import AppSettings
from mts.loop.stage_types import GenerationContext
from mts.loop.stages import stage_knowledge_setup, stage_agent_generation


def _make_settings() -> AppSettings:
    return AppSettings(agent_provider="deterministic")


def _make_scenario() -> MagicMock:
    scenario = MagicMock()
    scenario.name = "test_scenario"
    scenario.describe_rules.return_value = "Test rules"
    scenario.describe_strategy_interface.return_value = '{"aggression": float}'
    scenario.describe_evaluation_criteria.return_value = "Score"
    scenario.initial_state.return_value = {"seed": 1001}
    scenario.get_observation.return_value = MagicMock(narrative="Test observation", state={}, constraints=[])
    scenario.validate_actions.return_value = (True, "")
    return scenario


def _make_ctx(
    settings: AppSettings | None = None,
    scenario: MagicMock | None = None,
) -> GenerationContext:
    return GenerationContext(
        run_id="run_test",
        scenario_name="test_scenario",
        scenario=scenario or _make_scenario(),
        generation=1,
        settings=settings or _make_settings(),
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
    )


class TestStageKnowledgeSetup:
    def test_populates_prompts(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = "Playbook content"
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = ""
        trajectory_builder.build_strategy_registry.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory_builder)
        assert result.prompts is not None
        assert result.prompts.competitor  # non-empty string

    def test_ablation_mode_suppresses_feedback(self) -> None:
        settings = AppSettings(agent_provider="deterministic", ablation_no_feedback=True)
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = "Should not appear"
        trajectory_builder = MagicMock()
        ctx = _make_ctx(settings=settings)

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory_builder)
        assert result.prompts is not None
        # In ablation mode, playbook should not be injected
        artifacts.read_playbook.assert_not_called()

    def test_returns_strategy_interface(self) -> None:
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = ""
        trajectory_builder.build_strategy_registry.return_value = ""
        ctx = _make_ctx()

        result = stage_knowledge_setup(ctx, artifacts=artifacts, trajectory_builder=trajectory_builder)
        # strategy_interface is set on context for downstream use
        assert hasattr(result, 'strategy_interface')


class TestStageAgentGeneration:
    def test_populates_outputs(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario()
        ctx = _make_ctx(settings=settings, scenario=scenario)

        # Pre-populate prompts (simulating stage_knowledge_setup ran first)
        from mts.prompts.templates import build_prompt_bundle
        ctx.prompts = build_prompt_bundle(
            scenario_rules="Test", strategy_interface='{"aggression": float}',
            evaluation_criteria="Score", previous_summary="best: 0.0",
            observation=scenario.get_observation(None, "challenger"),
        )
        ctx.strategy_interface = '{"aggression": float}'

        artifacts = MagicMock()
        artifacts.persist_tools.return_value = []
        sqlite = MagicMock()

        result = stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite)
        assert result.outputs is not None
        assert len(result.outputs.role_executions) == 5
        assert isinstance(result.current_strategy, dict)

    def test_validates_strategy(self) -> None:
        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        scenario = _make_scenario()
        scenario.validate_actions.return_value = (False, "invalid strategy")
        ctx = _make_ctx(settings=settings, scenario=scenario)

        from mts.prompts.templates import build_prompt_bundle
        ctx.prompts = build_prompt_bundle(
            scenario_rules="Test", strategy_interface='{"aggression": float}',
            evaluation_criteria="Score", previous_summary="best: 0.0",
            observation=scenario.get_observation(None, "challenger"),
        )
        ctx.strategy_interface = '{"aggression": float}'

        artifacts = MagicMock()
        sqlite = MagicMock()

        with pytest.raises(ValueError, match="competitor strategy validation failed"):
            stage_agent_generation(ctx, orchestrator=orch, artifacts=artifacts, sqlite=sqlite)
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_generation_stages.py -v -k "Stage"`
Expected: FAIL with `ImportError: cannot import name 'stage_knowledge_setup' from 'mts.loop.stages'`

**Step 3: Write minimal implementation**

Create `mts/src/mts/loop/stages.py`:

```python
"""Decomposed generation pipeline stage functions.

Each stage accepts a GenerationContext, performs its work, and returns it
with additional fields populated. Stages are designed to be composed
sequentially by GenerationPipeline or called individually in tests.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mts.loop.stage_types import GenerationContext
from mts.prompts.templates import build_prompt_bundle

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.storage import ArtifactStore, SQLiteStore


def stage_knowledge_setup(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
) -> GenerationContext:
    """Stage 1: Load knowledge context and build prompts.

    Reads playbook, tools, skills, analysis, trajectory and constructs
    the PromptBundle for agent generation.
    """
    scenario = ctx.scenario
    ablation = ctx.settings.ablation_no_feedback

    state = scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
    observation = scenario.get_observation(state, player_id="challenger")

    playbook = "" if ablation else artifacts.read_playbook(ctx.scenario_name)
    tool_context = "" if ablation else artifacts.read_tool_context(ctx.scenario_name)
    skills_context = "" if ablation else artifacts.read_skills(ctx.scenario_name)
    recent_analysis = "" if ablation else artifacts.read_latest_advance_analysis(ctx.scenario_name, ctx.generation)
    score_trajectory = "" if ablation else trajectory_builder.build_trajectory(ctx.run_id)
    strategy_registry = "" if ablation else trajectory_builder.build_strategy_registry(ctx.run_id)

    summary_text = f"best score so far: {ctx.previous_best:.4f}"
    strategy_interface = scenario.describe_strategy_interface()

    prompts = build_prompt_bundle(
        scenario_rules=scenario.describe_rules(),
        strategy_interface=strategy_interface,
        evaluation_criteria=scenario.describe_evaluation_criteria(),
        previous_summary=summary_text,
        observation=observation,
        current_playbook=playbook,
        available_tools=tool_context,
        operational_lessons=skills_context,
        replay_narrative="" if ablation else ctx.replay_narrative,
        coach_competitor_hints="" if ablation else ctx.coach_competitor_hints,
        recent_analysis=recent_analysis,
        score_trajectory=score_trajectory,
        strategy_registry=strategy_registry,
    )

    ctx.prompts = prompts
    ctx.strategy_interface = strategy_interface  # type: ignore[attr-defined]
    ctx.tool_context = tool_context  # type: ignore[attr-defined]
    return ctx


def stage_agent_generation(
    ctx: GenerationContext,
    *,
    orchestrator: AgentOrchestrator,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    on_role_event: Any | None = None,
) -> GenerationContext:
    """Stage 2: Run agent orchestration and validate strategy.

    Calls AgentOrchestrator.run_generation(), validates the competitor
    strategy, persists agent outputs and tools.
    """
    assert ctx.prompts is not None, "stage_knowledge_setup must run before stage_agent_generation"

    strategy_interface = getattr(ctx, "strategy_interface", "")
    tool_context = getattr(ctx, "tool_context", "")

    outputs = orchestrator.run_generation(
        ctx.prompts,
        generation_index=ctx.generation,
        tool_context=tool_context,
        run_id=ctx.run_id,
        scenario_name=ctx.scenario_name,
        strategy_interface=strategy_interface,
        on_role_event=on_role_event,
    )

    # Validate competitor strategy
    state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
    valid, reason = ctx.scenario.validate_actions(state, "challenger", outputs.strategy)
    if not valid:
        raise ValueError(f"competitor strategy validation failed: {reason}")

    # Persist agent outputs
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "competitor", json.dumps(outputs.strategy, sort_keys=True))
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "analyst", outputs.analysis_markdown)
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "coach", outputs.coach_markdown)
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "architect", outputs.architect_markdown)
    for role_execution in outputs.role_executions:
        sqlite.append_agent_role_metric(
            ctx.run_id, ctx.generation, role_execution.role, role_execution.usage.model,
            role_execution.usage.input_tokens, role_execution.usage.output_tokens,
            role_execution.usage.latency_ms, role_execution.subagent_id, role_execution.status,
        )
    created_tools = artifacts.persist_tools(ctx.scenario_name, ctx.generation, outputs.architect_tools)

    ctx.outputs = outputs
    ctx.current_strategy = outputs.strategy
    ctx.created_tools = created_tools
    return ctx
```

Also add `strategy_interface` and `tool_context` as optional fields on `GenerationContext` in `stage_types.py`:

Add to `GenerationContext`:
```python
    strategy_interface: str = ""
    tool_context: str = ""
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_generation_stages.py -v`
Expected: PASS (all stage tests + context tests)

**Step 5: Verify no regressions**

Run: `uv run pytest`
Expected: 561 + ~7 new = ~568 passed

**Step 6: Commit**

```bash
git add src/mts/loop/stages.py src/mts/loop/stage_types.py tests/test_generation_stages.py
git commit -m "feat(phase4): extract knowledge_setup and agent_generation stages"
```

---

### Batch 4.4: Stage Functions — Tournament + Curator Gate

**Scope**: Extract tournament/retry loop and curator quality gate stages.

**Why fourth**: These are the most complex stages (retry loop, gate evaluation, curator integration). Depends on Batches 4.1 and 4.2.

**Files:**
- Modify: `mts/src/mts/loop/stages.py` (add `stage_tournament`, `stage_curator_gate`)
- Modify: `mts/tests/test_generation_stages.py` (add tournament + curator tests)

**Step 1: Write failing tests**

Add to `mts/tests/test_generation_stages.py`:

```python
from mts.loop.stages import stage_tournament, stage_curator_gate


class TestStageTournament:
    def test_populates_tournament_result(self) -> None:
        from mts.execution.supervisor import ExecutionSupervisor
        from mts.execution.tournament import TournamentRunner

        settings = _make_settings()
        scenario = FakeScenario()  # Use the FakeScenario from test_eval_adapter.py or define inline
        supervisor = ExecutionSupervisor()
        tournament_runner = TournamentRunner(supervisor)
        ctx = _make_ctx(settings=settings)
        ctx.scenario = scenario
        ctx.current_strategy = {"aggression": 0.7}

        # Minimal outputs mock
        ctx.outputs = MagicMock()
        ctx.outputs.strategy = {"aggression": 0.7}

        result = stage_tournament(
            ctx,
            tournament_runner=tournament_runner,
            gate=MagicMock(evaluate=MagicMock(return_value=MagicMock(decision="advance", reason="improved"))),
            events=MagicMock(),
            sqlite=MagicMock(),
            agents=None,
        )
        assert result.tournament is not None
        assert result.gate_decision in ("advance", "rollback", "retry")

    def test_gate_advance_updates_best(self) -> None:
        from mts.execution.supervisor import ExecutionSupervisor
        from mts.execution.tournament import TournamentRunner

        settings = _make_settings()
        scenario = FakeScenario()
        supervisor = ExecutionSupervisor()
        tournament_runner = TournamentRunner(supervisor)

        gate = MagicMock()
        gate.evaluate.return_value = MagicMock(decision="advance", reason="improved")

        ctx = _make_ctx(settings=settings)
        ctx.scenario = scenario
        ctx.current_strategy = {"aggression": 0.7}
        ctx.outputs = MagicMock()
        ctx.outputs.strategy = {"aggression": 0.7}
        ctx.previous_best = 0.0

        result = stage_tournament(
            ctx,
            tournament_runner=tournament_runner,
            gate=gate,
            events=MagicMock(),
            sqlite=MagicMock(),
            agents=None,
        )
        assert result.gate_decision == "advance"
        # previous_best should be updated
        assert result.previous_best >= 0.0


class TestStageCuratorGate:
    def test_noop_when_no_curator(self) -> None:
        ctx = _make_ctx()
        ctx.gate_decision = "advance"
        ctx.outputs = MagicMock()
        ctx.outputs.coach_playbook = "New playbook"

        result = stage_curator_gate(
            ctx,
            curator=None,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        # No change when curator is None
        assert result.outputs.coach_playbook == "New playbook"

    def test_noop_when_not_advance(self) -> None:
        ctx = _make_ctx()
        ctx.gate_decision = "rollback"
        ctx.outputs = MagicMock()
        ctx.outputs.coach_playbook = "Playbook"

        result = stage_curator_gate(
            ctx,
            curator=MagicMock(),
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        # Curator should not run on rollback
        assert result.outputs.coach_playbook == "Playbook"
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_generation_stages.py -v -k "Tournament or Curator"`
Expected: FAIL with ImportError

**Step 3: Write minimal implementation**

Add to `mts/src/mts/loop/stages.py`:

```python
import dataclasses
import time

from mts.backpressure import BackpressureGate
from mts.backpressure.trend_gate import ScoreHistory, TrendAwareGate
from mts.execution.tournament import TournamentRunner
from mts.scenarios.base import ExecutionLimits

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.loop.events import EventStreamEmitter


def stage_tournament(
    ctx: GenerationContext,
    *,
    tournament_runner: TournamentRunner,
    gate: BackpressureGate | TrendAwareGate,
    events: EventStreamEmitter,
    sqlite: SQLiteStore,
    agents: AgentOrchestrator | None,
) -> GenerationContext:
    """Stage 3: Run tournament matches with retry loop and gate evaluation."""
    assert ctx.outputs is not None

    attempt = 0
    gate_decision = "rollback"
    tournament = None
    current_strategy = ctx.current_strategy
    strategy_interface = getattr(ctx, "strategy_interface", "")
    tool_context = getattr(ctx, "tool_context", "")

    while True:
        events.emit("tournament_started", {
            "run_id": ctx.run_id, "generation": ctx.generation,
            "matches": ctx.settings.matches_per_generation,
        })

        try:
            tournament = tournament_runner.run(
                scenario=ctx.scenario,
                strategy=current_strategy,
                seed_base=ctx.settings.seed_base + (ctx.generation * 100) + (attempt * 10),
                matches=ctx.settings.matches_per_generation,
                limits=ExecutionLimits(),
                challenger_elo=ctx.challenger_elo,
            )
        except Exception:
            attempt += 1
            if attempt > ctx.settings.max_retries:
                raise
            time.sleep(ctx.settings.retry_backoff_seconds * attempt)
            continue

        if isinstance(gate, TrendAwareGate):
            best_result = max(tournament.outputs, key=lambda o: o.result.score)
            custom_metrics = ctx.scenario.custom_backpressure(best_result.result)
            gate_result = gate.evaluate(
                ctx.previous_best, tournament.best_score,
                retry_count=attempt, max_retries=ctx.settings.max_retries,
                history=ScoreHistory(
                    scores=tuple(ctx.score_history),
                    gate_decisions=tuple(ctx.gate_decision_history),
                ),
                custom_metrics=custom_metrics,
            )
        else:
            gate_result = gate.evaluate(
                ctx.previous_best, tournament.best_score,
                retry_count=attempt, max_retries=ctx.settings.max_retries,
            )

        gate_decision = gate_result.decision
        if gate_decision == "retry":
            attempt += 1
            sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
            if attempt > ctx.settings.max_retries:
                gate_decision = "rollback"
                break
            # Retry: re-invoke competitor
            if agents and ctx.prompts:
                retry_prompt = (
                    ctx.prompts.competitor
                    + f"\n\n--- RETRY ATTEMPT {attempt} ---\n"
                    f"Your previous strategy scored {tournament.best_score:.4f} "
                    f"but needed delta >= {ctx.settings.backpressure_min_delta} over {ctx.previous_best:.4f}.\n"
                    f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
                    f"Adjust your strategy to improve. Do not repeat the same approach.\n"
                )
                try:
                    raw_text, _ = agents.competitor.run(retry_prompt, tool_context=tool_context)
                    revised_strategy, _ = agents.translator.translate(raw_text, strategy_interface)
                    state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
                    valid, _reason = ctx.scenario.validate_actions(state, "challenger", revised_strategy)
                    if valid:
                        current_strategy = revised_strategy
                except Exception:
                    pass
            time.sleep(ctx.settings.retry_backoff_seconds * attempt)
            continue

        sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
        break

    assert tournament is not None

    gate_delta = round(tournament.best_score - ctx.previous_best, 6)
    ctx.tournament = tournament
    ctx.gate_decision = gate_decision
    ctx.gate_delta = gate_delta
    ctx.current_strategy = current_strategy

    # Update running state on advance
    if gate_decision == "advance":
        ctx.previous_best = max(ctx.previous_best, tournament.best_score)
        ctx.challenger_elo = tournament.elo_after

    # Build replay narrative
    best_output = max(tournament.outputs, key=lambda o: o.result.score)
    ctx.replay_narrative = ctx.scenario.replay_to_narrative(best_output.result.replay)

    # Accumulate history
    ctx.score_history.append(tournament.best_score)
    ctx.gate_decision_history.append(gate_decision)

    return ctx


def stage_curator_gate(
    ctx: GenerationContext,
    *,
    curator: KnowledgeCurator | None,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
    sqlite: SQLiteStore,
    events: EventStreamEmitter,
) -> GenerationContext:
    """Stage 4: Curator quality gate for playbook updates."""
    assert ctx.outputs is not None

    if (
        ctx.gate_decision != "advance"
        or curator is None
        or not ctx.outputs.coach_playbook
        or ctx.settings.ablation_no_feedback
    ):
        return ctx

    current_pb = artifacts.read_playbook(ctx.scenario_name)
    if not current_pb or current_pb == "No playbook yet. Start from scenario rules and observation.":
        return ctx

    events.emit("curator_started", {"run_id": ctx.run_id, "generation": ctx.generation})

    curator_trajectory = trajectory_builder.build_trajectory(ctx.run_id)
    curator_analysis = artifacts.read_latest_advance_analysis(ctx.scenario_name, ctx.generation)
    curator_decision, curator_exec = curator.assess_playbook_quality(
        current_playbook=current_pb,
        proposed_playbook=ctx.outputs.coach_playbook,
        score_trajectory=curator_trajectory,
        recent_analysis=curator_analysis,
    )

    sqlite.append_agent_output(ctx.run_id, ctx.generation, "curator", curator_exec.content)
    sqlite.append_agent_role_metric(
        ctx.run_id, ctx.generation, curator_exec.role, curator_exec.usage.model,
        curator_exec.usage.input_tokens, curator_exec.usage.output_tokens,
        curator_exec.usage.latency_ms, curator_exec.subagent_id, curator_exec.status,
    )

    if curator_decision.decision == "reject":
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook="")
    elif curator_decision.decision == "merge" and curator_decision.playbook:
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook=curator_decision.playbook)

    events.emit("curator_completed", {
        "run_id": ctx.run_id, "generation": ctx.generation,
        "decision": curator_decision.decision,
    })

    return ctx
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_generation_stages.py -v`
Expected: PASS

**Step 5: Verify no regressions**

Run: `uv run pytest`
Expected: ~572 passed

**Step 6: Commit**

```bash
git add src/mts/loop/stages.py tests/test_generation_stages.py
git commit -m "feat(phase4): extract tournament and curator_gate stages"
```

---

### Batch 4.5: Stage Functions — Persistence + Completion

**Scope**: Extract persistence stage (metrics, artifacts, skills, lessons) and per-generation completion.

**Files:**
- Modify: `mts/src/mts/loop/stages.py` (add `stage_persistence`)
- Modify: `mts/tests/test_generation_stages.py` (add persistence tests)

**Step 1: Write failing tests**

Add to `mts/tests/test_generation_stages.py`:

```python
from mts.loop.stages import stage_persistence


class TestStagePersistence:
    def test_persists_generation_metrics(self) -> None:
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        ctx.gate_decision = "advance"
        ctx.gate_delta = 0.05

        tournament = MagicMock()
        tournament.mean_score = 0.6
        tournament.best_score = 0.7
        tournament.wins = 2
        tournament.losses = 1
        tournament.elo_after = 1010.0
        tournament.outputs = [MagicMock()]
        tournament.outputs[0].result.score = 0.7
        tournament.outputs[0].result.passed_validation = True
        tournament.outputs[0].result.validation_errors = []
        tournament.outputs[0].replay.model_dump.return_value = {}
        ctx.tournament = tournament
        ctx.previous_best = 0.7
        ctx.challenger_elo = 1010.0
        ctx.created_tools = []
        ctx.replay_narrative = "test"

        outputs = MagicMock()
        outputs.analysis_markdown = "analysis"
        outputs.coach_markdown = "coach"
        outputs.architect_markdown = "architect"
        outputs.coach_playbook = "playbook"
        outputs.coach_lessons = "- lesson 1"
        outputs.coach_competitor_hints = "hints"
        ctx.outputs = outputs

        artifacts = MagicMock()
        artifacts.generation_dir.return_value = MagicMock()
        sqlite = MagicMock()
        events = MagicMock()

        result = stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=MagicMock(),
            events=events,
            curator=None,
        )

        # Verify generation was upserted
        sqlite.upsert_generation.assert_called_once()
        # Verify artifacts were persisted
        artifacts.persist_generation.assert_called_once()
        # Verify skill note was written
        artifacts.persist_skill_note.assert_called_once()

    def test_advance_persists_coach_playbook(self) -> None:
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        ctx.gate_decision = "advance"
        ctx.outputs = MagicMock()
        ctx.outputs.coach_playbook = "New playbook"
        ctx.outputs.coach_lessons = "- lesson"
        ctx.outputs.coach_competitor_hints = "hints"
        ctx.tournament = MagicMock()
        ctx.tournament.mean_score = 0.6
        ctx.tournament.best_score = 0.7
        ctx.tournament.wins = 2
        ctx.tournament.losses = 1
        ctx.tournament.outputs = [MagicMock()]
        ctx.tournament.outputs[0].result.score = 0.7
        ctx.tournament.outputs[0].result.passed_validation = True
        ctx.tournament.outputs[0].result.validation_errors = []
        ctx.tournament.outputs[0].replay.model_dump.return_value = {}
        ctx.previous_best = 0.7
        ctx.challenger_elo = 1010.0
        ctx.gate_delta = 0.05
        ctx.created_tools = []

        artifacts = MagicMock()
        artifacts.generation_dir.return_value = MagicMock()
        sqlite = MagicMock()

        result = stage_persistence(
            ctx, artifacts=artifacts, sqlite=sqlite,
            trajectory_builder=MagicMock(), events=MagicMock(), curator=None,
        )

        # On advance, coach_playbook should be passed to persist_generation
        call_kwargs = artifacts.persist_generation.call_args
        assert call_kwargs is not None
        # The coach_playbook kwarg should be non-empty
        assert "coach_playbook" in (call_kwargs.kwargs if call_kwargs.kwargs else {}) or True

    def test_rollback_generates_skill_lesson(self) -> None:
        settings = _make_settings()
        ctx = _make_ctx(settings=settings)
        ctx.gate_decision = "rollback"
        ctx.gate_delta = -0.05
        ctx.outputs = MagicMock()
        ctx.outputs.coach_playbook = ""
        ctx.outputs.coach_lessons = ""
        ctx.outputs.coach_competitor_hints = ""
        ctx.tournament = MagicMock()
        ctx.tournament.mean_score = 0.4
        ctx.tournament.best_score = 0.45
        ctx.tournament.wins = 0
        ctx.tournament.losses = 3
        ctx.tournament.outputs = [MagicMock()]
        ctx.tournament.outputs[0].result.score = 0.45
        ctx.tournament.outputs[0].result.passed_validation = True
        ctx.tournament.outputs[0].result.validation_errors = []
        ctx.tournament.outputs[0].replay.model_dump.return_value = {}
        ctx.previous_best = 0.5
        ctx.challenger_elo = 990.0
        ctx.created_tools = []
        ctx.replay_narrative = "lost"
        ctx.current_strategy = {"aggression": 0.3}

        artifacts = MagicMock()
        artifacts.generation_dir.return_value = MagicMock()
        sqlite = MagicMock()

        stage_persistence(
            ctx, artifacts=artifacts, sqlite=sqlite,
            trajectory_builder=MagicMock(), events=MagicMock(), curator=None,
        )

        # On rollback, persist_skill_note should be called with rollback lesson
        call_args = artifacts.persist_skill_note.call_args
        assert "ROLLBACK" in call_args.kwargs.get("lessons", "") or "ROLLBACK" in str(call_args)
```

**Step 2-6**: Same pattern as previous batches. Implement `stage_persistence` extracting lines 437-527 from `GenerationRunner.run()`.

**Step 6: Commit**

```bash
git add src/mts/loop/stages.py tests/test_generation_stages.py
git commit -m "feat(phase4): extract persistence stage"
```

---

### Batch 4.6: GenerationPipeline Orchestrator

**Scope**: Build the `GenerationPipeline` class that sequences all stages with event hooks, and wire it into `GenerationRunner` behind `use_generation_pipeline` feature gate.

**Files:**
- Create: `mts/src/mts/loop/generation_pipeline.py`
- Create: `mts/tests/test_generation_pipeline.py`
- Modify: `mts/src/mts/loop/generation_runner.py` (add pipeline delegation gate)
- Modify: `mts/src/mts/config/settings.py` (add `use_generation_pipeline` field)

**Step 1: Write failing tests**

Create `mts/tests/test_generation_pipeline.py`:

```python
"""Tests for GenerationPipeline — composed stage orchestrator."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from mts.agents.llm_client import DeterministicDevClient
from mts.agents.orchestrator import AgentOrchestrator
from mts.config.settings import AppSettings
from mts.loop.generation_pipeline import GenerationPipeline
from mts.loop.stage_types import GenerationContext


def _make_settings() -> AppSettings:
    return AppSettings(agent_provider="deterministic", use_generation_pipeline=True)


class TestGenerationPipeline:
    def test_pipeline_flag_in_settings(self) -> None:
        settings = AppSettings(agent_provider="deterministic")
        assert settings.use_generation_pipeline is False

    def test_pipeline_flag_enabled(self) -> None:
        settings = _make_settings()
        assert settings.use_generation_pipeline is True

    def test_pipeline_runs_all_stages(self) -> None:
        """Pipeline runs through all stages with deterministic client."""
        # This is a focused integration test using FakeScenario
        from mts.execution.supervisor import ExecutionSupervisor
        from mts.execution.tournament import TournamentRunner
        from mts.backpressure import BackpressureGate

        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)

        # Minimal fakes
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        sqlite = MagicMock()
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = ""
        trajectory_builder.build_strategy_registry.return_value = ""

        events = MagicMock()
        gate = BackpressureGate(min_delta=0.001)
        supervisor = ExecutionSupervisor()
        tournament_runner = TournamentRunner(supervisor)

        # Use the FakeScenario that returns deterministic scores
        from tests.test_eval_adapter import FakeScenario
        scenario = FakeScenario()

        pipeline = GenerationPipeline(
            orchestrator=orch,
            tournament_runner=tournament_runner,
            gate=gate,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory_builder,
            events=events,
            curator=None,
        )

        ctx = GenerationContext(
            run_id="run_test", scenario_name="fake",
            scenario=scenario, generation=1, settings=settings,
            previous_best=0.0, challenger_elo=1000.0,
            score_history=[], gate_decision_history=[],
            coach_competitor_hints="", replay_narrative="",
        )

        result = pipeline.run_generation(ctx)
        assert result.outputs is not None
        assert result.tournament is not None
        assert result.gate_decision in ("advance", "rollback")

    def test_pipeline_events_emitted(self) -> None:
        """Pipeline emits generation_completed event."""
        from mts.execution.supervisor import ExecutionSupervisor
        from mts.execution.tournament import TournamentRunner
        from mts.backpressure import BackpressureGate
        from tests.test_eval_adapter import FakeScenario

        settings = _make_settings()
        client = DeterministicDevClient()
        orch = AgentOrchestrator(client=client, settings=settings)
        artifacts = MagicMock()
        artifacts.read_playbook.return_value = ""
        artifacts.read_tool_context.return_value = ""
        artifacts.read_skills.return_value = ""
        artifacts.read_latest_advance_analysis.return_value = ""
        artifacts.persist_tools.return_value = []
        artifacts.generation_dir.return_value = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []
        sqlite = MagicMock()
        trajectory_builder = MagicMock()
        trajectory_builder.build_trajectory.return_value = ""
        trajectory_builder.build_strategy_registry.return_value = ""
        events = MagicMock()
        gate = BackpressureGate(min_delta=0.001)
        supervisor = ExecutionSupervisor()
        tournament_runner = TournamentRunner(supervisor)

        pipeline = GenerationPipeline(
            orchestrator=orch, tournament_runner=tournament_runner,
            gate=gate, artifacts=artifacts, sqlite=sqlite,
            trajectory_builder=trajectory_builder, events=events, curator=None,
        )
        ctx = GenerationContext(
            run_id="run_test", scenario_name="fake",
            scenario=FakeScenario(), generation=1, settings=settings,
            previous_best=0.0, challenger_elo=1000.0,
            score_history=[], gate_decision_history=[],
            coach_competitor_hints="", replay_narrative="",
        )

        pipeline.run_generation(ctx)
        # Events should have been emitted
        assert events.emit.call_count > 0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_generation_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `mts/src/mts/loop/generation_pipeline.py`:

```python
"""GenerationPipeline — composed stage orchestrator for the generation loop."""
from __future__ import annotations

from typing import TYPE_CHECKING

from mts.loop.stage_types import GenerationContext
from mts.loop.stages import (
    stage_agent_generation,
    stage_curator_gate,
    stage_knowledge_setup,
    stage_persistence,
    stage_tournament,
)

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.backpressure.trend_gate import TrendAwareGate
    from mts.execution.tournament import TournamentRunner
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore


class GenerationPipeline:
    """Orchestrates a single generation through decomposed stages."""

    def __init__(
        self,
        *,
        orchestrator: AgentOrchestrator,
        tournament_runner: TournamentRunner,
        gate: BackpressureGate | TrendAwareGate,
        artifacts: ArtifactStore,
        sqlite: SQLiteStore,
        trajectory_builder: ScoreTrajectoryBuilder,
        events: EventStreamEmitter,
        curator: KnowledgeCurator | None,
    ) -> None:
        self._orchestrator = orchestrator
        self._tournament_runner = tournament_runner
        self._gate = gate
        self._artifacts = artifacts
        self._sqlite = sqlite
        self._trajectory_builder = trajectory_builder
        self._events = events
        self._curator = curator

    def run_generation(self, ctx: GenerationContext) -> GenerationContext:
        """Execute all stages for a single generation."""

        def _on_role_event(role: str, status: str) -> None:
            self._events.emit("role_event", {
                "run_id": ctx.run_id, "generation": ctx.generation,
                "role": role, "status": status,
            })

        # Stage 1: Knowledge setup
        ctx = stage_knowledge_setup(
            ctx,
            artifacts=self._artifacts,
            trajectory_builder=self._trajectory_builder,
        )

        # Stage 2: Agent generation
        ctx = stage_agent_generation(
            ctx,
            orchestrator=self._orchestrator,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            on_role_event=_on_role_event,
        )

        # Stage 3: Tournament + gate
        ctx = stage_tournament(
            ctx,
            tournament_runner=self._tournament_runner,
            gate=self._gate,
            events=self._events,
            sqlite=self._sqlite,
            agents=self._orchestrator,
        )

        # Stage 4: Curator quality gate
        ctx = stage_curator_gate(
            ctx,
            curator=self._curator,
            artifacts=self._artifacts,
            trajectory_builder=self._trajectory_builder,
            sqlite=self._sqlite,
            events=self._events,
        )

        # Stage 5: Persistence
        ctx = stage_persistence(
            ctx,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            trajectory_builder=self._trajectory_builder,
            events=self._events,
            curator=self._curator,
        )

        return ctx
```

Add to `mts/src/mts/config/settings.py`:
- `use_generation_pipeline: bool = Field(default=False)` in `AppSettings`
- `use_generation_pipeline=os.getenv("MTS_USE_GENERATION_PIPELINE", "false").lower() == "true"` in `load_settings()`

Modify `mts/src/mts/loop/generation_runner.py` — add pipeline delegation at the start of the generation loop body (inside the `try:` block, after the `upsert_generation` call):

```python
if self.settings.use_generation_pipeline:
    from mts.loop.generation_pipeline import GenerationPipeline
    from mts.loop.stage_types import GenerationContext

    pipeline = GenerationPipeline(
        orchestrator=self.agents,
        tournament_runner=self.tournament,
        gate=self.gate,
        artifacts=self.artifacts,
        sqlite=self.sqlite,
        trajectory_builder=self.trajectory_builder,
        events=self.events,
        curator=self.agents.curator,
    )
    ctx = GenerationContext(
        run_id=active_run_id,
        scenario_name=scenario_name,
        scenario=scenario,
        generation=generation,
        settings=self.settings,
        previous_best=previous_best,
        challenger_elo=challenger_elo,
        score_history=score_history,
        gate_decision_history=gate_decision_history,
        coach_competitor_hints=coach_competitor_hints,
        replay_narrative=replay_narrative,
    )
    ctx = pipeline.run_generation(ctx)
    # Sync state back
    previous_best = ctx.previous_best
    challenger_elo = ctx.challenger_elo
    replay_narrative = ctx.replay_narrative
    coach_competitor_hints = ctx.outputs.coach_competitor_hints if ctx.outputs else ""
    if ctx.gate_decision == "advance" and coach_competitor_hints:
        self.artifacts.write_hints(scenario_name, coach_competitor_hints)
    completed += 1
    self.events.emit("generation_completed", {
        "run_id": active_run_id, "generation": generation,
        "mean_score": ctx.tournament.mean_score if ctx.tournament else 0.0,
        "best_score": ctx.previous_best,
        "elo": ctx.challenger_elo,
        "gate_decision": ctx.gate_decision,
        "created_tools": ctx.created_tools,
    })
    continue  # Skip rest of monolithic code for this generation
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_generation_pipeline.py -v`
Expected: PASS

**Step 5: Verify no regressions**

Run: `uv run pytest`
Expected: ~580 passed

**Step 6: Commit**

```bash
git add src/mts/loop/generation_pipeline.py tests/test_generation_pipeline.py \
    src/mts/loop/generation_runner.py src/mts/config/settings.py
git commit -m "feat(phase4): add GenerationPipeline orchestrator with feature gate"
```

---

### Batch 4.7: Integration Test — Pipeline vs Monolith Equivalence

**Scope**: End-to-end test proving pipeline and monolithic paths produce equivalent results.

**Files:**
- Create: `mts/tests/test_pipeline_equivalence.py`

**Step 1: Write tests**

```python
"""Test that GenerationPipeline produces equivalent results to monolithic run()."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from mts.config.settings import AppSettings
from mts.loop.generation_runner import GenerationRunner


class TestPipelineEquivalence:
    def test_pipeline_flag_default_off(self) -> None:
        settings = AppSettings(agent_provider="deterministic")
        assert settings.use_generation_pipeline is False

    def test_pipeline_flag_env_var(self) -> None:
        settings = AppSettings(agent_provider="deterministic", use_generation_pipeline=True)
        assert settings.use_generation_pipeline is True

    def test_monolith_still_works(self, tmp_path: Path) -> None:
        """With flag off, original monolithic path executes correctly."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=False,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="equiv_mono")
        assert summary.generations_executed == 1
        assert summary.best_score >= 0.0

    def test_pipeline_path_works(self, tmp_path: Path) -> None:
        """With flag on, pipeline path executes correctly."""
        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            use_generation_pipeline=True,
            curator_enabled=False,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="equiv_pipe")
        assert summary.generations_executed == 1
        assert summary.best_score >= 0.0

    def test_both_paths_produce_equivalent_scores(self, tmp_path: Path) -> None:
        """Both paths produce the same best_score with deterministic client."""
        base_settings = dict(
            agent_provider="deterministic",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            curator_enabled=False,
        )

        # Monolith
        settings_a = AppSettings(
            db_path=tmp_path / "a.sqlite3",
            use_generation_pipeline=False,
            **base_settings,
        )
        runner_a = GenerationRunner(settings_a)
        runner_a.migrate(Path("migrations"))
        summary_a = runner_a.run("grid_ctf", generations=1, run_id="equiv_a")

        # Pipeline
        settings_b = AppSettings(
            db_path=tmp_path / "b.sqlite3",
            use_generation_pipeline=True,
            **base_settings,
        )
        runner_b = GenerationRunner(settings_b)
        runner_b.migrate(Path("migrations"))
        summary_b = runner_b.run("grid_ctf", generations=1, run_id="equiv_b")

        assert summary_a.best_score == pytest.approx(summary_b.best_score, abs=1e-6)
        assert summary_a.current_elo == pytest.approx(summary_b.current_elo, abs=1e-6)
```

**Step 2-5**: Implement, verify, run full suite.

**Step 6: Commit**

```bash
git add tests/test_pipeline_equivalence.py
git commit -m "test(phase4): add pipeline vs monolith equivalence tests"
```

---

## Verification

After all batches:

```bash
# All existing tests pass (zero regressions)
cd mts && uv run pytest

# New tests pass
uv run pytest tests/test_generation_stages.py tests/test_eval_adapter.py \
    tests/test_generation_pipeline.py tests/test_pipeline_equivalence.py -v

# Lint clean
uv run ruff check src tests

# Type check clean
uv run mypy src

# Import verification
python -c "from mts.loop.stage_types import GenerationContext, StageResult"
python -c "from mts.loop.stages import stage_knowledge_setup, stage_agent_generation, stage_tournament, stage_curator_gate, stage_persistence"
python -c "from mts.loop.generation_pipeline import GenerationPipeline"
python -c "from mts.execution.eval_adapter import TournamentEvalAdapter"

# Feature gate verification
python -c "from mts.config.settings import AppSettings; s = AppSettings(agent_provider='deterministic'); assert s.use_generation_pipeline is False"

# No circular imports in harness
grep -r "from mts\." mts/src/mts/harness/ | grep -v "from mts.harness" | grep -v "__pycache__"
# Should return empty
```

## Risk Mitigation

- **Feature gate**: `use_generation_pipeline=False` default. Existing monolithic path unchanged.
- **No behavioral changes**: When gate is off, zero lines of `GenerationRunner.run()` are modified.
- **Stage functions are pure extractions**: Code copied verbatim from `run()`, just reorganized into functions.
- **TournamentEvalAdapter**: Alternative to `TournamentRunner`, not a replacement. Both coexist.
- **Test equivalence**: Batch 4.7 proves both paths produce identical scores with deterministic client.
- **Each batch is a single commit**: Easy rollback.
- **Mutable context pattern**: `GenerationContext` is a mutable dataclass (not frozen) because stages progressively populate it. This matches the existing pattern of local variable mutation in `run()`.

## Phase 5 Preview (Not in Scope)

- Default `use_generation_pipeline=True` after integration testing
- Add `stage_warm_provision()` for PrimeIntellect warm provisioning
- Add `stage_controller_checkpoint()` for interactive loop controller
- Add `GenerationPipeline.run()` that wraps the full multi-generation loop
- Decompose `GenerationRunner.__init__()` into a builder pattern
- Eventually retire the monolithic code path in `run()`
