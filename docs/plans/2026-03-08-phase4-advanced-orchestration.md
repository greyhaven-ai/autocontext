# Phase 4: Advanced Orchestration Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add three opt-in R&D features: probe-based strategy refinement before tournaments (MTS-26), dynamic DAG role reconfiguration by the architect (MTS-27), and ecosystem convergence detection to prevent oscillating playbooks (MTS-28).

**Architecture:** MTS-26 adds a `stage_probe()` between agent generation and tournament — runs 1 match, feeds observation back to competitor for refinement, then proceeds to full tournament. MTS-27 extends the existing `RoleDAG` class with `add_role()`/`remove_role()` methods and parses optional DAG change directives from architect output. MTS-28 adds playbook divergence tracking to `EcosystemRunner` — compares pre/post-phase playbooks via `difflib.SequenceMatcher` and locks to the best version when oscillation is detected.

**Tech Stack:** Python 3.11+, threading, difflib, Pydantic settings, pytest

---

### Task 1: Probe Stage — Settings + Stage Function (MTS-26)

**Files:**
- Modify: `mts/src/mts/config/settings.py:124,251`
- Create: `mts/src/mts/loop/stage_probe.py`
- Test: `mts/tests/test_stage_probe.py`

**Step 1: Write failing tests**

Create `mts/tests/test_stage_probe.py`:

```python
"""Tests for probe-based strategy refinement (MTS-26)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from mts.loop.stage_probe import stage_probe


@dataclass(slots=True)
class _FakeSettings:
    probe_matches: int = 0
    seed_base: int = 1000
    matches_per_generation: int = 3
    code_strategies_enabled: bool = False
    backpressure_min_delta: float = 0.005


@dataclass(slots=True)
class _FakeScenario:
    def initial_state(self, seed: int = 0) -> dict:
        return {"seed": seed}

    def get_observation(self, state: dict, player_id: str = "") -> str:
        return "obs"

    def describe_rules(self) -> str:
        return "rules"

    def describe_evaluation_criteria(self) -> str:
        return "criteria"

    def describe_strategy_interface(self) -> str:
        return '{"move": "str"}'

    def validate_actions(self, state: dict, player_id: str, actions: dict) -> tuple[bool, str]:
        return True, ""

    def replay_to_narrative(self, replay: Any) -> str:
        return "narrative"

    def custom_backpressure(self, result: Any) -> dict:
        return {}


@dataclass(slots=True)
class _FakePrompts:
    competitor: str = "compete"
    analyst: str = "analyze"
    coach: str = "coach"
    architect: str = "architect"


@dataclass(slots=True)
class _FakeCtx:
    run_id: str = "run_1"
    scenario_name: str = "grid_ctf"
    scenario: Any = field(default_factory=_FakeScenario)
    generation: int = 1
    settings: Any = field(default_factory=_FakeSettings)
    previous_best: float = 0.0
    challenger_elo: float = 1000.0
    current_strategy: dict = field(default_factory=lambda: {"move": "up"})
    prompts: Any = field(default_factory=_FakePrompts)
    tool_context: str = ""
    strategy_interface: str = '{"move": "str"}'
    probe_refinement_applied: bool = False


def test_probe_disabled_returns_unchanged() -> None:
    """When probe_matches=0, stage_probe is a no-op."""
    ctx = _FakeCtx()
    result = stage_probe(ctx, agents=MagicMock(), events=MagicMock(), supervisor=MagicMock())
    assert result.current_strategy == {"move": "up"}
    assert result.probe_refinement_applied is False


def test_probe_runs_single_match_and_refines() -> None:
    """When probe_matches=1, runs 1 match and calls competitor for refinement."""
    ctx = _FakeCtx()
    ctx.settings.probe_matches = 1

    # Mock supervisor to return a score
    mock_supervisor = MagicMock()

    # Mock agents
    mock_agents = MagicMock()
    mock_agents.competitor.run.return_value = ('{"move": "down"}', MagicMock())
    mock_agents.translator.translate.return_value = ({"move": "down"}, MagicMock())

    mock_events = MagicMock()

    # Mock the EvaluationRunner
    mock_eval_result = MagicMock()
    mock_eval_result.best_score = 0.3
    mock_eval_result.results = [MagicMock(score=0.3, metadata={"execution_output": MagicMock(result=MagicMock(replay={}, score=0.3))})]

    with patch("mts.loop.stage_probe.EvaluationRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run.return_value = mock_eval_result
        result = stage_probe(ctx, agents=mock_agents, events=mock_events, supervisor=mock_supervisor)

    assert result.probe_refinement_applied is True
    assert result.current_strategy == {"move": "down"}
    mock_events.emit.assert_any_call("probe_started", {"run_id": "run_1", "generation": 1, "probe_matches": 1})


def test_probe_keeps_original_on_failure() -> None:
    """If competitor refinement fails, keep original strategy."""
    ctx = _FakeCtx()
    ctx.settings.probe_matches = 1

    mock_agents = MagicMock()
    mock_agents.competitor.run.side_effect = RuntimeError("LLM error")

    mock_eval_result = MagicMock()
    mock_eval_result.best_score = 0.3
    mock_eval_result.results = [MagicMock(score=0.3, metadata={"execution_output": MagicMock(result=MagicMock(replay={}, score=0.3))})]

    with patch("mts.loop.stage_probe.EvaluationRunner") as mock_runner_cls:
        mock_runner_cls.return_value.run.return_value = mock_eval_result
        result = stage_probe(ctx, agents=mock_agents, events=MagicMock(), supervisor=MagicMock())

    assert result.current_strategy == {"move": "up"}  # Unchanged
    assert result.probe_refinement_applied is False
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_stage_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mts.loop.stage_probe'`

**Step 3: Add settings field**

In `mts/src/mts/config/settings.py`, add after `coherence_check_enabled` (line 123):

```python
    # Probe matches (Phase 4)
    probe_matches: int = Field(default=0, ge=0, description="Probe matches before full tournament (0=disabled)")
```

In `load_settings()`, add before the closing paren:

```python
        probe_matches=int(_get("probe_matches", "MTS_PROBE_MATCHES", "0")),
```

**Step 4: Add `probe_refinement_applied` to GenerationContext**

In `mts/src/mts/loop/stage_types.py`, add after `fresh_start_triggered` (line 46):

```python
    probe_refinement_applied: bool = False
```

**Step 5: Implement stage_probe**

Create `mts/src/mts/loop/stage_probe.py`:

```python
"""Probe stage — run a small number of matches before the full tournament.

The competitor observes probe results and refines its strategy before
the full evaluation. Disabled by default (probe_matches=0).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.execution.supervisor import ExecutionSupervisor
    from mts.loop.events import EventStreamEmitter

LOGGER = logging.getLogger(__name__)


def stage_probe(
    ctx: GenerationContext,
    *,
    agents: AgentOrchestrator,
    events: EventStreamEmitter,
    supervisor: ExecutionSupervisor,
) -> GenerationContext:
    """Stage 2.5: Run probe matches and refine strategy before full tournament."""
    if ctx.settings.probe_matches < 1:
        return ctx
    assert ctx.prompts is not None, "stage_knowledge_setup must run first"

    events.emit("probe_started", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "probe_matches": ctx.settings.probe_matches,
    })

    # Run probe matches
    evaluator = ScenarioEvaluator(ctx.scenario, supervisor)
    runner = EvaluationRunner(evaluator)
    probe_result = runner.run(
        candidate=ctx.current_strategy,
        seed_base=ctx.settings.seed_base + (ctx.generation * 100) + 90,
        trials=ctx.settings.probe_matches,
        limits=HarnessLimits(),
        challenger_elo=ctx.challenger_elo,
    )

    # Build refinement prompt with probe observations
    best_eval = max(probe_result.results, key=lambda r: r.score)
    best_exec = best_eval.metadata["execution_output"]
    probe_narrative = ctx.scenario.replay_to_narrative(best_exec.result.replay)

    is_code_strategy = "__code__" in ctx.current_strategy

    refinement_prompt = (
        ctx.prompts.competitor
        + f"\n\n--- PROBE OBSERVATION ---\n"
        f"You ran {ctx.settings.probe_matches} probe match(es). "
        f"Best probe score: {probe_result.best_score:.4f}.\n"
        f"Replay narrative:\n{probe_narrative}\n\n"
        f"Based on this observation, refine your strategy. "
        f"You may keep your approach if the probe looks promising, "
        f"or adjust based on what you observed.\n"
    )
    if is_code_strategy:
        refinement_prompt += "Emit refined Python code.\n"
    else:
        refinement_prompt += (
            f"Previous strategy: {json.dumps(ctx.current_strategy, sort_keys=True)}\n"
        )

    # Attempt refinement
    try:
        raw_text, _ = agents.competitor.run(refinement_prompt, tool_context=ctx.tool_context)
        if is_code_strategy:
            revised, _ = agents.translator.translate_code(raw_text)
        else:
            revised, _ = agents.translator.translate(raw_text, ctx.strategy_interface)

        # Validate non-code strategies
        if "__code__" not in revised:
            state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
            valid, reason = ctx.scenario.validate_actions(state, "challenger", revised)
            if not valid:
                LOGGER.warning("probe refinement produced invalid strategy: %s", reason)
                raise ValueError(reason)

        ctx.current_strategy = revised
        ctx.probe_refinement_applied = True
        LOGGER.info("probe refinement applied (probe_score=%.4f)", probe_result.best_score)
    except Exception:
        LOGGER.warning("probe refinement failed, keeping original strategy", exc_info=True)

    events.emit("probe_completed", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "probe_score": probe_result.best_score,
        "refined": ctx.probe_refinement_applied,
    })

    return ctx
```

**Step 6: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_stage_probe.py -v`
Expected: PASS (3 tests)

**Step 7: Run lint + mypy + full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 8: Commit**

```bash
git add mts/src/mts/config/settings.py mts/src/mts/loop/stage_probe.py \
  mts/src/mts/loop/stage_types.py mts/tests/test_stage_probe.py
git commit -m "feat: probe stage for pre-tournament strategy refinement (MTS-26)"
```

---

### Task 2: Wire Probe Stage into Pipeline (MTS-26)

**Files:**
- Modify: `mts/src/mts/loop/generation_pipeline.py:10-17,129-138`
- Test: `mts/tests/test_probe_pipeline.py`

**Step 1: Write failing test**

Create `mts/tests/test_probe_pipeline.py`:

```python
"""Tests for probe integration in GenerationPipeline (MTS-26)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mts.loop.generation_pipeline import GenerationPipeline


def test_pipeline_calls_probe_when_enabled() -> None:
    """Pipeline calls stage_probe between agent generation and tournament."""
    pipeline = GenerationPipeline(
        orchestrator=MagicMock(),
        supervisor=MagicMock(),
        gate=MagicMock(),
        artifacts=MagicMock(),
        sqlite=MagicMock(),
        trajectory_builder=MagicMock(),
        events=MagicMock(),
        curator=None,
    )

    mock_ctx = MagicMock()
    mock_ctx.generation = 2  # Skip startup verification
    mock_ctx.settings.probe_matches = 1
    mock_ctx.settings.coherence_check_enabled = False

    with (
        patch("mts.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx) as mock_ks,
        patch("mts.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx) as mock_ag,
        patch("mts.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("mts.loop.generation_pipeline.stage_tournament", return_value=mock_ctx) as mock_tour,
        patch("mts.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    mock_probe.assert_called_once()
    # Verify ordering: knowledge_setup -> agent_generation -> probe -> tournament
    ks_order = mock_ks.call_args_list[0]
    ag_order = mock_ag.call_args_list[0]
    probe_order = mock_probe.call_args_list[0]
    tour_order = mock_tour.call_args_list[0]
    assert ks_order is not None
    assert ag_order is not None
    assert probe_order is not None
    assert tour_order is not None


def test_pipeline_skips_probe_when_disabled() -> None:
    """Pipeline does not call stage_probe when probe_matches=0."""
    pipeline = GenerationPipeline(
        orchestrator=MagicMock(),
        supervisor=MagicMock(),
        gate=MagicMock(),
        artifacts=MagicMock(),
        sqlite=MagicMock(),
        trajectory_builder=MagicMock(),
        events=MagicMock(),
        curator=None,
    )

    mock_ctx = MagicMock()
    mock_ctx.generation = 2
    mock_ctx.settings.probe_matches = 0
    mock_ctx.settings.coherence_check_enabled = False

    with (
        patch("mts.loop.generation_pipeline.stage_knowledge_setup", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_agent_generation", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_probe", return_value=mock_ctx) as mock_probe,
        patch("mts.loop.generation_pipeline.stage_tournament", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_stagnation_check", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_curator_gate", return_value=mock_ctx),
        patch("mts.loop.generation_pipeline.stage_persistence", return_value=mock_ctx),
    ):
        pipeline.run_generation(mock_ctx)

    # stage_probe is called but returns immediately (no-op when probe_matches=0)
    mock_probe.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_probe_pipeline.py -v`
Expected: FAIL with `ImportError` (stage_probe not imported in generation_pipeline)

**Step 3: Wire stage_probe into GenerationPipeline**

In `mts/src/mts/loop/generation_pipeline.py`:

Add to imports (after `stage_knowledge_setup` import, line 13):

```python
from mts.loop.stage_probe import stage_probe
```

Add the probe stage call between Stage 2 (agent generation) and Stage 3 (tournament). Insert after the controller chat checkpoint block (after line 127) and before `# Stage 3: Tournament + gate` (line 129):

```python
        # Stage 2.5: Probe (optional — refine strategy from observation)
        ctx = stage_probe(
            ctx,
            agents=self._orchestrator,
            events=self._events,
            supervisor=self._supervisor,
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_probe_pipeline.py tests/test_stage_probe.py -v`
Expected: PASS (5 tests)

**Step 5: Run full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 6: Commit**

```bash
git add mts/src/mts/loop/generation_pipeline.py mts/tests/test_probe_pipeline.py
git commit -m "feat: wire probe stage into generation pipeline (MTS-26)"
```

---

### Task 3: RoleDAG Mutation Methods (MTS-27)

**Files:**
- Modify: `mts/src/mts/harness/orchestration/dag.py`
- Test: `mts/tests/test_dag_mutation.py`

**Step 1: Write failing tests**

Create `mts/tests/test_dag_mutation.py`:

```python
"""Tests for RoleDAG mutation methods (MTS-27)."""
from __future__ import annotations

import pytest

from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec


def _base_dag() -> RoleDAG:
    """Standard 5-role MTS DAG."""
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])


def test_add_role_appends() -> None:
    """Adding a role increases the DAG."""
    dag = _base_dag()
    dag.add_role(RoleSpec(name="critic", depends_on=("analyst",)))
    assert "critic" in dag.roles
    dag.validate()


def test_add_role_duplicate_raises() -> None:
    """Adding a duplicate role name raises ValueError."""
    dag = _base_dag()
    with pytest.raises(ValueError, match="already exists"):
        dag.add_role(RoleSpec(name="analyst", depends_on=("translator",)))


def test_add_role_cycle_raises() -> None:
    """Adding a role that creates a cycle raises ValueError."""
    dag = _base_dag()
    # coach depends on analyst; adding analyst depending on coach = cycle
    with pytest.raises(ValueError, match="[Cc]ycle"):
        dag.add_role(RoleSpec(name="x", depends_on=("coach",)))
        dag.add_role(RoleSpec(name="y", depends_on=("x",)))
        # Now try to make coach depend on y (which depends on x, which depends on coach)
        dag.remove_role("coach")
        dag.add_role(RoleSpec(name="coach", depends_on=("analyst", "y")))


def test_add_role_missing_dep_raises() -> None:
    """Adding a role with a missing dependency raises ValueError."""
    dag = _base_dag()
    with pytest.raises(ValueError, match="unknown role"):
        dag.add_role(RoleSpec(name="critic", depends_on=("nonexistent",)))


def test_remove_role() -> None:
    """Removing a role removes it from the DAG."""
    dag = _base_dag()
    dag.remove_role("architect")
    assert "architect" not in dag.roles
    dag.validate()


def test_remove_role_unknown_raises() -> None:
    """Removing a nonexistent role raises ValueError."""
    dag = _base_dag()
    with pytest.raises(ValueError, match="not found"):
        dag.remove_role("nonexistent")


def test_remove_role_with_dependents_raises() -> None:
    """Removing a role that other roles depend on raises ValueError."""
    dag = _base_dag()
    with pytest.raises(ValueError, match="depended on by"):
        dag.remove_role("analyst")  # coach depends on analyst


def test_execution_batches_after_mutation() -> None:
    """Execution batches reflect the mutated DAG."""
    dag = _base_dag()
    dag.add_role(RoleSpec(name="critic", depends_on=("coach",)))
    batches = dag.execution_batches()
    # critic should be in the last batch (after coach)
    flat = [name for batch in batches for name in batch]
    assert flat.index("critic") > flat.index("coach")
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_dag_mutation.py -v`
Expected: FAIL with `AttributeError: 'RoleDAG' object has no attribute 'add_role'`

**Step 3: Implement add_role and remove_role**

In `mts/src/mts/harness/orchestration/dag.py`, add these methods to the `RoleDAG` class:

```python
    def add_role(self, role: RoleSpec) -> None:
        """Add a role to the DAG. Validates no duplicates, no missing deps, no cycles."""
        if role.name in self._roles:
            raise ValueError(f"Role '{role.name}' already exists in DAG")
        for dep in role.depends_on:
            if dep not in self._roles:
                raise ValueError(f"Role '{role.name}' depends on unknown role '{dep}'")
        self._roles[role.name] = role
        self._names.append(role.name)
        # Validate no cycles were introduced
        try:
            self.execution_batches()
        except ValueError:
            # Rollback
            del self._roles[role.name]
            self._names.remove(role.name)
            raise

    def remove_role(self, name: str) -> None:
        """Remove a role from the DAG. Fails if other roles depend on it."""
        if name not in self._roles:
            raise ValueError(f"Role '{name}' not found in DAG")
        # Check no other role depends on this one
        dependents = [r.name for r in self._roles.values() if name in r.depends_on]
        if dependents:
            raise ValueError(f"Role '{name}' is depended on by: {', '.join(dependents)}")
        del self._roles[name]
        self._names.remove(name)
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_dag_mutation.py -v`
Expected: PASS (8 tests)

**Step 5: Run lint + mypy + full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 6: Commit**

```bash
git add mts/src/mts/harness/orchestration/dag.py mts/tests/test_dag_mutation.py
git commit -m "feat: add_role/remove_role with cycle detection for RoleDAG (MTS-27)"
```

---

### Task 4: Parse DAG Changes from Architect Output (MTS-27)

**Files:**
- Modify: `mts/src/mts/agents/architect.py`
- Test: `mts/tests/test_architect_dag_changes.py`

**Step 1: Write failing tests**

Create `mts/tests/test_architect_dag_changes.py`:

```python
"""Tests for parsing DAG change directives from architect output (MTS-27)."""
from __future__ import annotations

from mts.agents.architect import parse_dag_changes


def test_parse_no_markers_returns_empty() -> None:
    """No DAG_CHANGES markers → empty list."""
    content = "Some architect output with tools."
    assert parse_dag_changes(content) == []


def test_parse_add_role() -> None:
    """Parse an add_role directive."""
    content = (
        "Some text\n"
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
        "More text"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 1
    assert changes[0]["action"] == "add_role"
    assert changes[0]["name"] == "critic"
    assert changes[0]["depends_on"] == ["analyst"]


def test_parse_remove_role() -> None:
    """Parse a remove_role directive."""
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "remove_role", "name": "architect"}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 1
    assert changes[0]["action"] == "remove_role"
    assert changes[0]["name"] == "architect"


def test_parse_multiple_changes() -> None:
    """Parse multiple DAG changes."""
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": ['
        '{"action": "remove_role", "name": "architect"},'
        '{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}'
        ']}\n'
        "<!-- DAG_CHANGES_END -->\n"
    )
    changes = parse_dag_changes(content)
    assert len(changes) == 2


def test_parse_malformed_json_returns_empty() -> None:
    """Malformed JSON between markers → empty list."""
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        "not valid json\n"
        "<!-- DAG_CHANGES_END -->\n"
    )
    assert parse_dag_changes(content) == []


def test_parse_invalid_action_skipped() -> None:
    """Unknown actions are skipped."""
    content = (
        "<!-- DAG_CHANGES_START -->\n"
        '{"changes": [{"action": "explode", "name": "boom"}]}\n'
        "<!-- DAG_CHANGES_END -->\n"
    )
    assert parse_dag_changes(content) == []
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_architect_dag_changes.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_dag_changes' from 'mts.agents.architect'`

**Step 3: Implement parse_dag_changes**

In `mts/src/mts/agents/architect.py`, add after `parse_architect_tool_specs`:

```python
_DAG_START = "<!-- DAG_CHANGES_START -->"
_DAG_END = "<!-- DAG_CHANGES_END -->"
_VALID_ACTIONS = {"add_role", "remove_role"}


def parse_dag_changes(content: str) -> list[dict[str, Any]]:
    """Extract DAG change directives from architect output.

    Looks for <!-- DAG_CHANGES_START --> ... <!-- DAG_CHANGES_END --> markers
    containing JSON: {"changes": [{"action": "add_role"|"remove_role", "name": ..., "depends_on": [...]}]}
    """
    start = content.find(_DAG_START)
    end = content.find(_DAG_END)
    if start == -1 or end == -1 or end <= start:
        return []
    body = content[start + len(_DAG_START) : end].strip()
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, Mapping):
        return []
    changes = decoded.get("changes")
    if not isinstance(changes, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in changes:
        if not isinstance(item, Mapping):
            continue
        action = item.get("action")
        name = item.get("name")
        if action not in _VALID_ACTIONS or not isinstance(name, str):
            continue
        entry: dict[str, Any] = {"action": action, "name": name}
        if action == "add_role":
            deps = item.get("depends_on", [])
            entry["depends_on"] = list(deps) if isinstance(deps, list) else []
        valid.append(entry)
    return valid
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_architect_dag_changes.py -v`
Expected: PASS (6 tests)

**Step 5: Run lint + mypy + full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 6: Commit**

```bash
git add mts/src/mts/agents/architect.py mts/tests/test_architect_dag_changes.py
git commit -m "feat: parse DAG change directives from architect output (MTS-27)"
```

---

### Task 5: Apply DAG Changes Between Generations (MTS-27)

**Files:**
- Modify: `mts/src/mts/agents/orchestrator.py` (add `apply_dag_changes` method)
- Modify: `mts/src/mts/loop/stages.py:149` (persist DAG changes in stage_agent_generation)
- Modify: `mts/src/mts/loop/stage_types.py:46` (add `dag_changes` field)
- Test: `mts/tests/test_dag_apply.py`

**Step 1: Write failing tests**

Create `mts/tests/test_dag_apply.py`:

```python
"""Tests for applying DAG changes in the orchestrator (MTS-27)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec


def _base_dag() -> RoleDAG:
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])


def test_apply_add_role() -> None:
    """apply_dag_changes adds a new role to the DAG."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = _base_dag()
    changes = [{"action": "add_role", "name": "critic", "depends_on": ["analyst"]}]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 1
    assert skipped == 0
    assert "critic" in dag.roles


def test_apply_remove_role() -> None:
    """apply_dag_changes removes a role from the DAG."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = _base_dag()
    changes = [{"action": "remove_role", "name": "architect"}]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 1
    assert "architect" not in dag.roles


def test_apply_invalid_change_skipped() -> None:
    """Invalid changes (e.g., removing a depended-upon role) are skipped."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = _base_dag()
    changes = [{"action": "remove_role", "name": "analyst"}]  # coach depends on analyst
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 0
    assert skipped == 1
    assert "analyst" in dag.roles  # Unchanged


def test_apply_multiple_changes() -> None:
    """Multiple changes are applied in order."""
    from mts.agents.orchestrator import apply_dag_changes

    dag = _base_dag()
    changes = [
        {"action": "remove_role", "name": "architect"},
        {"action": "add_role", "name": "critic", "depends_on": ["analyst"]},
    ]
    applied, skipped = apply_dag_changes(dag, changes)
    assert applied == 2
    assert "architect" not in dag.roles
    assert "critic" in dag.roles
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_dag_apply.py -v`
Expected: FAIL with `ImportError: cannot import name 'apply_dag_changes' from 'mts.agents.orchestrator'`

**Step 3: Implement apply_dag_changes**

Read `mts/src/mts/agents/orchestrator.py` first (the subagent will need to find the right insertion point). Add a module-level function:

```python
def apply_dag_changes(dag: RoleDAG, changes: list[dict[str, Any]]) -> tuple[int, int]:
    """Apply a list of DAG change directives. Returns (applied, skipped) counts."""
    applied = 0
    skipped = 0
    for change in changes:
        action = change.get("action")
        name = change.get("name", "")
        try:
            if action == "add_role":
                deps = tuple(change.get("depends_on", []))
                dag.add_role(RoleSpec(name=name, depends_on=deps))
                applied += 1
            elif action == "remove_role":
                dag.remove_role(name)
                applied += 1
            else:
                skipped += 1
        except ValueError:
            skipped += 1
    return applied, skipped
```

Add the required imports at the top of `orchestrator.py`:

```python
from typing import Any
from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec
```

**Step 4: Add `dag_changes` field to GenerationContext**

In `mts/src/mts/loop/stage_types.py`, add after `probe_refinement_applied`:

```python
    dag_changes: list[dict[str, Any]] = field(default_factory=list)
```

**Step 5: Capture DAG changes in stage_agent_generation**

In `mts/src/mts/loop/stages.py`, after `ctx.created_tools = created_tools` (line 153), add:

```python
    # Parse DAG change directives from architect output
    from mts.agents.architect import parse_dag_changes
    ctx.dag_changes = parse_dag_changes(outputs.architect_markdown)
```

**Step 6: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_dag_apply.py -v`
Expected: PASS (4 tests)

**Step 7: Run full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 8: Commit**

```bash
git add mts/src/mts/agents/orchestrator.py mts/src/mts/loop/stages.py \
  mts/src/mts/loop/stage_types.py mts/tests/test_dag_apply.py
git commit -m "feat: apply DAG changes between generations (MTS-27)"
```

---

### Task 6: Ecosystem Convergence Detection (MTS-28)

**Files:**
- Modify: `mts/src/mts/config/settings.py:125,252-254`
- Modify: `mts/src/mts/loop/ecosystem_runner.py:1-7,48-127`
- Test: `mts/tests/test_ecosystem_convergence.py`

**Step 1: Write failing tests**

Create `mts/tests/test_ecosystem_convergence.py`:

```python
"""Tests for ecosystem convergence detection (MTS-28)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from mts.loop.ecosystem_runner import compute_playbook_divergence, detect_oscillation


def test_divergence_identical() -> None:
    """Identical playbooks have 0.0 divergence."""
    assert compute_playbook_divergence("# Strategy\nBe aggressive.", "# Strategy\nBe aggressive.") == 0.0


def test_divergence_completely_different() -> None:
    """Completely different playbooks have high divergence."""
    d = compute_playbook_divergence("alpha beta gamma", "xray yankee zulu")
    assert d > 0.5


def test_divergence_empty_strings() -> None:
    """Two empty strings have 0.0 divergence."""
    assert compute_playbook_divergence("", "") == 0.0


def test_divergence_one_empty() -> None:
    """One empty playbook has 1.0 divergence."""
    assert compute_playbook_divergence("some content", "") == 1.0


def test_oscillation_detected() -> None:
    """Oscillation detected when divergence exceeds threshold for N cycles."""
    history = [0.6, 0.7, 0.65, 0.8]  # All above 0.5 threshold
    assert detect_oscillation(history, threshold=0.5, window=3) is True


def test_oscillation_not_detected_below_threshold() -> None:
    """No oscillation when divergence is below threshold."""
    history = [0.1, 0.2, 0.15, 0.05]
    assert detect_oscillation(history, threshold=0.5, window=3) is False


def test_oscillation_not_detected_insufficient_history() -> None:
    """No oscillation with insufficient history."""
    history = [0.8, 0.9]  # Only 2 entries, window=3
    assert detect_oscillation(history, threshold=0.5, window=3) is False


def test_oscillation_empty_history() -> None:
    """Empty history → no oscillation."""
    assert detect_oscillation([], threshold=0.5, window=3) is False
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_ecosystem_convergence.py -v`
Expected: FAIL with `ImportError: cannot import name 'compute_playbook_divergence' from 'mts.loop.ecosystem_runner'`

**Step 3: Add settings fields**

In `mts/src/mts/config/settings.py`, add after `probe_matches`:

```python
    # Ecosystem convergence (Phase 4)
    ecosystem_convergence_enabled: bool = Field(
        default=False, description="Track playbook divergence between ecosystem phases",
    )
    ecosystem_divergence_threshold: float = Field(
        default=0.3, ge=0.0, le=1.0, description="Divergence ratio above which phases are oscillating",
    )
    ecosystem_oscillation_window: int = Field(
        default=3, ge=2, description="Consecutive high-divergence cycles to trigger lock",
    )
```

In `load_settings()`, add before the closing paren:

```python
        ecosystem_convergence_enabled=_get_bool(
            "ecosystem_convergence_enabled", "MTS_ECOSYSTEM_CONVERGENCE_ENABLED", "false",
        ),
        ecosystem_divergence_threshold=float(
            _get("ecosystem_divergence_threshold", "MTS_ECOSYSTEM_DIVERGENCE_THRESHOLD", "0.3"),
        ),
        ecosystem_oscillation_window=int(
            _get("ecosystem_oscillation_window", "MTS_ECOSYSTEM_OSCILLATION_WINDOW", "3"),
        ),
```

**Step 4: Implement convergence functions and integrate into EcosystemRunner**

In `mts/src/mts/loop/ecosystem_runner.py`, add imports at the top:

```python
import difflib
```

Add module-level helper functions before the `EcosystemRunner` class:

```python
def compute_playbook_divergence(before: str, after: str) -> float:
    """Compute divergence between two playbook versions.

    Returns 0.0 for identical, 1.0 for completely different.
    Uses SequenceMatcher ratio (similarity), inverted to divergence.
    """
    if not before and not after:
        return 0.0
    if not before or not after:
        return 1.0
    similarity = difflib.SequenceMatcher(None, before, after).ratio()
    return round(1.0 - similarity, 4)


def detect_oscillation(
    divergence_history: list[float],
    threshold: float,
    window: int,
) -> bool:
    """Detect playbook oscillation from divergence history.

    Returns True if the last `window` entries all exceed `threshold`.
    """
    if len(divergence_history) < window:
        return False
    recent = divergence_history[-window:]
    return all(d > threshold for d in recent)
```

In `EcosystemRunner.__init__`, add a divergence tracker:

```python
        self._divergence_history: list[float] = []
        self._locked = False
```

In `EcosystemRunner.run()`, add convergence checking inside the phase loop. After `summary = runner.run(...)` and `summaries.append(summary)`, add:

```python
                # Convergence detection
                if (
                    self.base_settings.ecosystem_convergence_enabled
                    and not self._locked
                ):
                    from mts.storage import ArtifactStore
                    artifacts = ArtifactStore(
                        self.base_settings.runs_root,
                        self.base_settings.knowledge_root,
                        self.base_settings.skills_root,
                        self.base_settings.claude_skills_path,
                    )
                    post_playbook = artifacts.read_playbook(self.config.scenario)
                    if hasattr(self, "_pre_playbook"):
                        divergence = compute_playbook_divergence(self._pre_playbook, post_playbook)
                        self._divergence_history.append(divergence)

                        if detect_oscillation(
                            self._divergence_history,
                            threshold=self.base_settings.ecosystem_divergence_threshold,
                            window=self.base_settings.ecosystem_oscillation_window,
                        ):
                            self._locked = True
                            self.events.emit(
                                "ecosystem_convergence_locked",
                                {
                                    "scenario": self.config.scenario,
                                    "cycle": cycle,
                                    "divergence_history": self._divergence_history,
                                },
                                channel="ecosystem",
                            )
                            LOGGER.warning(
                                "ecosystem convergence lock: playbook oscillating for %d cycles",
                                self.base_settings.ecosystem_oscillation_window,
                            )
                    self._pre_playbook = post_playbook
```

Also, initialize `_pre_playbook` before the cycle loop (after `summaries` init):

```python
        # Read initial playbook state for convergence tracking
        if self.base_settings.ecosystem_convergence_enabled:
            from mts.storage import ArtifactStore
            _init_artifacts = ArtifactStore(
                self.base_settings.runs_root,
                self.base_settings.knowledge_root,
                self.base_settings.skills_root,
                self.base_settings.claude_skills_path,
            )
            self._pre_playbook = _init_artifacts.read_playbook(self.config.scenario)
```

**Step 5: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_ecosystem_convergence.py -v`
Expected: PASS (8 tests)

**Step 6: Run full suite**

Run: `cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest -x`
Expected: All pass

**Step 7: Commit**

```bash
git add mts/src/mts/config/settings.py mts/src/mts/loop/ecosystem_runner.py \
  mts/tests/test_ecosystem_convergence.py
git commit -m "feat: ecosystem convergence detection with oscillation lock (MTS-28)"
```

---

### Task 7: Final Verification + PR

**Step 1: Run full suite**

```bash
cd mts && uv run ruff check src tests && uv run mypy src && uv run pytest
```

**Step 2: Push and create PR**

```bash
git push -u origin feat/phase4-advanced-orchestration
gh pr create --title "feat: Phase 4 advanced orchestration (MTS-26, MTS-27, MTS-28)" \
  --body "## Summary
- **MTS-26**: Probe stage for pre-tournament strategy refinement (probe_matches setting, stage_probe, pipeline integration)
- **MTS-27**: Dynamic DAG reconfiguration (add_role/remove_role on RoleDAG, parse DAG changes from architect output, apply between generations)
- **MTS-28**: Ecosystem convergence detection (playbook divergence tracking, oscillation lock when divergence exceeds threshold)

All three features disabled by default — pure opt-in R&D.

## Test plan
- [ ] Probe stage: disabled no-op, runs+refines, keeps original on failure (3 tests)
- [ ] Probe pipeline: called when enabled, skipped when disabled (2 tests)
- [ ] DAG mutation: add/remove/duplicate/cycle/missing-dep/dependents/batches (8 tests)
- [ ] DAG changes parsing: no markers, add, remove, multiple, malformed, invalid action (6 tests)
- [ ] DAG apply: add, remove, invalid skipped, multiple (4 tests)
- [ ] Convergence: divergence calc, oscillation detection, edge cases (8 tests)
- [ ] Full suite passes, ruff clean, mypy clean"
```
