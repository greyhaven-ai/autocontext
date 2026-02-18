# Phase 5: Pipeline Parity + Monolith Deletion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the 6 behavioral gaps between the pipeline and monolithic code paths, then flip the feature flag and delete ~355 lines of duplicate code.

**Architecture:** The `GenerationPipeline` orchestrator gains optional `controller` and `remote` constructor params. Between-stage hooks handle controller checkpoints and PrimeIntellect warm provisioning. Individual stage functions gain missing events. After parity is proven, the monolithic path is deleted and the feature flag removed.

**Tech Stack:** Python 3.11+, pytest, `mts.loop.generation_pipeline`, `mts.loop.stages`, `mts.loop.stage_types`, `mts.harness.core.controller.LoopController`

---

## Context

**Current state**: Phase 4 decomposed `GenerationRunner.run()` into 5 stage functions orchestrated by `GenerationPipeline`. The pipeline is gated behind `MTS_USE_GENERATION_PIPELINE=false` (off by default). Both paths coexist.

**The problem**: The pipeline path silently drops 6 behaviors that the monolithic path implements:

| Gap | Where in monolith | Impact |
|-----|-------------------|--------|
| Controller chat checkpoint | `generation_runner.py:312-318` (after agents, before tournament) | Interactive TUI agent chat broken |
| Controller gate override | `generation_runner.py:416-419` (after tournament, before persistence) | Operators can't force advance/rollback |
| PrimeIntellect warm provision | `generation_runner.py:245-254` (after prompts, before agents) | Remote executor cold-starts every generation |
| `agents_started` event | `generation_runner.py:256-261` | Dashboard doesn't show role list |
| `role_completed` event | `generation_runner.py:277-284` | Dashboard doesn't show role latency |
| `created_tools` in `generation_completed` | `generation_runner.py:572` | Event payload missing tool list |
| Rollback `retry_note` | `generation_runner.py:519-520` | Skill lesson omits retry count |

**Key files**:
- `mts/src/mts/loop/generation_pipeline.py` — orchestrator (106 lines)
- `mts/src/mts/loop/stages.py` — 5 stage functions (456 lines)
- `mts/src/mts/loop/stage_types.py` — GenerationContext dataclass (54 lines)
- `mts/src/mts/loop/generation_runner.py` — monolith (~615 lines, ~355 of which are the duplicated try block)
- `mts/src/mts/harness/core/controller.py` — LoopController (69 lines)
- `mts/tests/test_generation_stages.py` — existing stage tests (27 tests)
- `mts/tests/test_generation_pipeline.py` — existing pipeline tests (6 tests)

---

### Task 1: Add `attempt` field to GenerationContext and expose from stage_tournament

The monolith's rollback skill lesson includes `" after {attempt} retries"` when `attempt > 0`. The pipeline's `stage_tournament` tracks `attempt` locally but never writes it to `GenerationContext`, so `stage_persistence` can't include it.

**Files:**
- Modify: `mts/src/mts/loop/stage_types.py:41` (add field)
- Modify: `mts/src/mts/loop/stages.py:260` (store attempt on ctx before return)
- Test: `mts/tests/test_generation_stages.py`

**Step 1: Write the failing test**

Add to `mts/tests/test_generation_stages.py` in the tournament test section:

```python
def test_stage_tournament_stores_attempt_on_context(self) -> None:
    """stage_tournament populates ctx.attempt after execution."""
    ctx = _make_tournament_ctx()
    result = stage_tournament(
        ctx,
        tournament_runner=ctx._mock_tournament,
        gate=ctx._mock_gate,
        events=ctx._mock_events,
        sqlite=ctx._mock_sqlite,
        artifacts=ctx._mock_artifacts,
    )
    assert hasattr(result, "attempt")
    assert isinstance(result.attempt, int)
    assert result.attempt >= 0
```

Note: This test uses the same `_make_tournament_ctx()` helper that existing tournament tests use. It will fail because `GenerationContext` has no `attempt` field.

**Step 2: Run test to verify it fails**

Run: `cd mts && uv run pytest tests/test_generation_stages.py::TestStageTournament::test_stage_tournament_stores_attempt_on_context -v`
Expected: FAIL — `AttributeError: 'GenerationContext' object has no attribute 'attempt'` or test name not found (depends on where you place it)

**Step 3: Implement the fix**

In `mts/src/mts/loop/stage_types.py`, add after line 42 (`created_tools`):

```python
    attempt: int = 0
```

In `mts/src/mts/loop/stages.py`, in `stage_tournament()`, add before the final `return ctx` at line 261:

```python
    ctx.attempt = attempt
```

**Step 4: Run test to verify it passes**

Run: `cd mts && uv run pytest tests/test_generation_stages.py -v`
Expected: All tests PASS including the new one.

**Step 5: Commit**

```bash
git add mts/src/mts/loop/stage_types.py mts/src/mts/loop/stages.py mts/tests/test_generation_stages.py
git commit -m "feat(phase5): expose tournament attempt count on GenerationContext"
```

---

### Task 2: Add `agents_started` and `role_completed` events to stage_agent_generation

The monolith emits `agents_started` (with role list) before agent orchestration and `role_completed` (with per-role latency/tokens) after. The pipeline path emits neither.

**Files:**
- Modify: `mts/src/mts/loop/stages.py:73-120` (stage_agent_generation function)
- Test: `mts/tests/test_generation_stages.py`

**Step 1: Write the failing tests**

Add to `mts/tests/test_generation_stages.py`:

```python
def test_stage_agent_generation_emits_agents_started(self) -> None:
    """stage_agent_generation emits agents_started event."""
    ctx = _make_ctx()
    events = MagicMock()
    stage_agent_generation(
        ctx,
        orchestrator=ctx._mock_orchestrator,
        artifacts=ctx._mock_artifacts,
        sqlite=ctx._mock_sqlite,
        events=events,
    )
    event_names = [call.args[0] for call in events.emit.call_args_list]
    assert "agents_started" in event_names

def test_stage_agent_generation_emits_role_completed(self) -> None:
    """stage_agent_generation emits role_completed for each role execution."""
    ctx = _make_ctx()
    events = MagicMock()
    stage_agent_generation(
        ctx,
        orchestrator=ctx._mock_orchestrator,
        artifacts=ctx._mock_artifacts,
        sqlite=ctx._mock_sqlite,
        events=events,
    )
    event_names = [call.args[0] for call in events.emit.call_args_list]
    assert "role_completed" in event_names
```

Note: `stage_agent_generation` currently has no `events` parameter. These tests will fail because the function signature doesn't accept `events`.

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_generation_stages.py -k "agents_started or role_completed" -v`
Expected: FAIL — `TypeError: stage_agent_generation() got an unexpected keyword argument 'events'`

**Step 3: Implement the fix**

In `mts/src/mts/loop/stages.py`, modify `stage_agent_generation`:

1. Add `events` parameter to the function signature (after `on_role_event`):

```python
def stage_agent_generation(
    ctx: GenerationContext,
    *,
    orchestrator: AgentOrchestrator,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    on_role_event: Any | None = None,
    events: EventStreamEmitter | None = None,
) -> GenerationContext:
```

2. Add before the `orchestrator.run_generation()` call (before line 84):

```python
    if events is not None:
        roles = ["competitor", "analyst", "coach", "architect"]
        if orchestrator.curator is not None:
            roles.append("curator")
        events.emit("agents_started", {
            "run_id": ctx.run_id, "generation": ctx.generation, "roles": roles,
        })
```

3. Add after the `for role_execution in outputs.role_executions:` sqlite loop (after line 114):

```python
    if events is not None:
        for role_execution in outputs.role_executions:
            events.emit("role_completed", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "role": role_execution.role,
                "latency_ms": role_execution.usage.latency_ms,
                "tokens": role_execution.usage.input_tokens + role_execution.usage.output_tokens,
            })
```

4. Update `generation_pipeline.py` to pass `events` to `stage_agent_generation`:

In `GenerationPipeline.run_generation()`, change the stage 2 call:

```python
        ctx = stage_agent_generation(
            ctx,
            orchestrator=self._orchestrator,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            on_role_event=_on_role_event,
            events=self._events,
        )
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_generation_stages.py -v`
Expected: All tests PASS.

**Step 5: Run full test suite**

Run: `cd mts && uv run pytest`
Expected: All 589+ tests pass (existing tests that don't pass `events` will still work since it defaults to `None`).

**Step 6: Commit**

```bash
git add mts/src/mts/loop/stages.py mts/src/mts/loop/generation_pipeline.py mts/tests/test_generation_stages.py
git commit -m "feat(phase5): add agents_started and role_completed events to pipeline"
```

---

### Task 3: Add `created_tools` to `generation_completed` event + fix rollback retry_note

Two small fixes in `stage_persistence`:
1. Add `created_tools` to the `generation_completed` event payload (monolith has it at line 572)
2. Use `ctx.attempt` to add retry_note to rollback skill lessons (monolith has it at lines 518-520)

**Files:**
- Modify: `mts/src/mts/loop/stages.py:395-456` (stage_persistence function)
- Test: `mts/tests/test_generation_stages.py`

**Step 1: Write the failing tests**

Add to `mts/tests/test_generation_stages.py`:

```python
def test_stage_persistence_emits_created_tools(self) -> None:
    """generation_completed event includes created_tools."""
    ctx = _make_persistence_ctx()
    ctx.created_tools = ["tool_a.py", "tool_b.py"]
    events = MagicMock()
    stage_persistence(
        ctx,
        artifacts=ctx._mock_artifacts,
        sqlite=ctx._mock_sqlite,
        trajectory_builder=ctx._mock_trajectory,
        events=events,
        curator=None,
    )
    gen_completed_calls = [
        call for call in events.emit.call_args_list
        if call.args[0] == "generation_completed"
    ]
    assert len(gen_completed_calls) == 1
    payload = gen_completed_calls[0].args[1]
    assert "created_tools" in payload
    assert payload["created_tools"] == ["tool_a.py", "tool_b.py"]

def test_stage_persistence_rollback_includes_retry_note(self) -> None:
    """Rollback skill lesson includes retry count when attempt > 0."""
    ctx = _make_persistence_ctx()
    ctx.gate_decision = "rollback"
    ctx.attempt = 2
    artifacts = MagicMock()
    artifacts.read_skill_lessons_raw.return_value = []
    stage_persistence(
        ctx,
        artifacts=artifacts,
        sqlite=ctx._mock_sqlite,
        trajectory_builder=ctx._mock_trajectory,
        events=ctx._mock_events,
        curator=None,
    )
    persist_call = artifacts.persist_skill_note.call_args
    lessons_text = persist_call.kwargs.get("lessons", persist_call[1].get("lessons", ""))
    assert "after 2 retries" in lessons_text
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_generation_stages.py -k "created_tools or retry_note" -v`
Expected: FAIL — payload doesn't contain `created_tools`; lesson doesn't contain retry note.

**Step 3: Implement the fixes**

In `mts/src/mts/loop/stages.py`, `stage_persistence()`:

1. Add `created_tools` to the `generation_completed` event payload (around line 446):

Change:
```python
    events.emit("generation_completed", {
        "run_id": run_id,
        "generation": generation,
        "mean_score": tournament.mean_score,
        "best_score": ctx.previous_best,
        "elo": ctx.challenger_elo,
        "gate_decision": gate_decision,
        "gate_delta": gate_delta,
    })
```

To:
```python
    events.emit("generation_completed", {
        "run_id": run_id,
        "generation": generation,
        "mean_score": tournament.mean_score,
        "best_score": ctx.previous_best,
        "elo": ctx.challenger_elo,
        "gate_decision": gate_decision,
        "gate_delta": gate_delta,
        "created_tools": ctx.created_tools,
    })
```

2. Add retry_note to the rollback skill lesson (around line 399):

Change:
```python
    else:
        skill_lessons = (
            f"- Generation {generation} ROLLBACK "
```

To:
```python
    else:
        retry_note = f" after {ctx.attempt} retries" if ctx.attempt > 0 else ""
        skill_lessons = (
            f"- Generation {generation} ROLLBACK{retry_note} "
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_generation_stages.py -v`
Expected: All tests PASS.

**Step 5: Commit**

```bash
git add mts/src/mts/loop/stages.py mts/tests/test_generation_stages.py
git commit -m "feat(phase5): add created_tools to event payload and retry_note to rollback lessons"
```

---

### Task 4: Add controller checkpoints and PrimeIntellect warm provision to GenerationPipeline

This is the core integration task. The pipeline orchestrator gains:
- Optional `controller: LoopController | None` — for chat checkpoint and gate override
- Optional `remote` client + `settings` references — for PrimeIntellect warm provision
- A `chat_with_agent` callable — for processing agent chat requests

**Files:**
- Modify: `mts/src/mts/loop/generation_pipeline.py`
- Modify: `mts/src/mts/loop/generation_runner.py:185-219` (update pipeline construction)
- Test: `mts/tests/test_generation_pipeline.py`

**Step 1: Write the failing tests**

Add to `mts/tests/test_generation_pipeline.py`:

```python
from unittest.mock import MagicMock, patch


class TestPipelineControllerCheckpoints:
    def test_pipeline_accepts_controller(self) -> None:
        """GenerationPipeline constructor accepts an optional controller parameter."""
        from mts.harness.core.controller import LoopController
        from mts.loop.generation_pipeline import GenerationPipeline

        controller = LoopController()
        # Should not raise — just verifying the constructor accepts it
        pipeline = GenerationPipeline(
            orchestrator=MagicMock(),
            tournament_runner=MagicMock(),
            gate=MagicMock(),
            artifacts=MagicMock(),
            sqlite=MagicMock(),
            trajectory_builder=MagicMock(),
            events=MagicMock(),
            curator=None,
            controller=controller,
        )
        assert pipeline._controller is controller

    def test_pipeline_gate_override_applied(self, tmp_path: Path) -> None:
        """When controller has a gate override, it's applied after stage_tournament."""
        from mts.harness.core.controller import LoopController

        controller = LoopController()
        controller.set_gate_override("advance")

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
        runner.controller = controller
        summary = runner.run("grid_ctf", generations=1, run_id="override_test")
        assert summary.generations_executed == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd mts && uv run pytest tests/test_generation_pipeline.py -k "controller" -v`
Expected: FAIL — `TypeError: GenerationPipeline.__init__() got an unexpected keyword argument 'controller'`

**Step 3: Implement the changes**

In `mts/src/mts/loop/generation_pipeline.py`:

1. Add imports:

```python
from collections.abc import Callable

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.backpressure.trend_gate import TrendAwareGate
    from mts.execution.tournament import TournamentRunner
    from mts.harness.core.controller import LoopController
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore
```

2. Update `__init__` to accept optional controller, warm_provision_fn, and chat_fn:

```python
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
        controller: LoopController | None = None,
        warm_provision_fn: Callable[..., dict] | None = None,
        chat_with_agent_fn: Callable[[str, str, object, str], str] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._tournament_runner = tournament_runner
        self._gate = gate
        self._artifacts = artifacts
        self._sqlite = sqlite
        self._trajectory_builder = trajectory_builder
        self._events = events
        self._curator = curator
        self._controller = controller
        self._warm_provision_fn = warm_provision_fn
        self._chat_with_agent_fn = chat_with_agent_fn
```

3. Update `run_generation` to inject hooks between stages:

```python
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

        # Hook: PrimeIntellect warm provision (between knowledge setup and agent gen)
        if self._warm_provision_fn is not None:
            warm_state = self._warm_provision_fn(ctx)
            self._events.emit("primeintellect_warm_state", {
                "run_id": ctx.run_id, "generation": ctx.generation, **warm_state,
            })

        # Stage 2: Agent generation
        ctx = stage_agent_generation(
            ctx,
            orchestrator=self._orchestrator,
            artifacts=self._artifacts,
            sqlite=self._sqlite,
            on_role_event=_on_role_event,
            events=self._events,
        )

        # Hook: Controller chat checkpoint (between agent gen and tournament)
        if self._controller is not None and self._chat_with_agent_fn is not None:
            chat_request = self._controller.poll_chat()
            if chat_request:
                role, message = chat_request
                response = self._chat_with_agent_fn(role, message, ctx.prompts, ctx.tool_context)
                self._controller.respond_chat(role, response)

        # Stage 3: Tournament + gate
        ctx = stage_tournament(
            ctx,
            tournament_runner=self._tournament_runner,
            gate=self._gate,
            events=self._events,
            sqlite=self._sqlite,
            artifacts=self._artifacts,
            agents=self._orchestrator,
        )

        # Hook: Controller gate override (between tournament and curator)
        if self._controller is not None:
            override = self._controller.take_gate_override()
            if override:
                ctx.gate_decision = override

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

4. In `mts/src/mts/loop/generation_runner.py`, update the pipeline construction (lines 189-198) to pass controller, warm_provision_fn, and chat_fn:

```python
                    warm_fn = None
                    if self.settings.executor_mode == "primeintellect":
                        def _warm(ctx_arg: object) -> dict:
                            return self.remote.warm_provision(
                                environment_name=f"{scenario_name}-gen-{generation}",
                                max_retries=self.settings.primeintellect_max_retries,
                                backoff_seconds=self.settings.primeintellect_backoff_seconds,
                            )
                        warm_fn = _warm

                    pipeline = GenerationPipeline(
                        orchestrator=self.agents,
                        tournament_runner=self.tournament,
                        gate=self.gate,
                        artifacts=self.artifacts,
                        sqlite=self.sqlite,
                        trajectory_builder=self.trajectory_builder,
                        events=self.events,
                        curator=self.agents.curator,
                        controller=self.controller,
                        warm_provision_fn=warm_fn,
                        chat_with_agent_fn=self._chat_with_agent,
                    )
```

**Step 4: Run tests to verify they pass**

Run: `cd mts && uv run pytest tests/test_generation_pipeline.py -v`
Expected: All tests PASS (existing + new).

**Step 5: Run full test suite**

Run: `cd mts && uv run pytest`
Expected: All 589+ tests pass.

**Step 6: Run lint**

Run: `cd mts && uv run ruff check src tests`
Expected: All checks passed.

**Step 7: Commit**

```bash
git add mts/src/mts/loop/generation_pipeline.py mts/src/mts/loop/generation_runner.py mts/tests/test_generation_pipeline.py
git commit -m "feat(phase5): add controller checkpoints and warm provision to pipeline"
```

---

### Task 5: Flip the feature flag to default=True

Now that pipeline parity is achieved, flip the default so all existing integration tests exercise the pipeline path.

**Files:**
- Modify: `mts/src/mts/config/settings.py` (change default)
- Test: full suite

**Step 1: Change the default**

In `mts/src/mts/config/settings.py`, change:

```python
    use_generation_pipeline: bool = Field(default=False)
```

To:

```python
    use_generation_pipeline: bool = Field(default=True)
```

**Step 2: Run the full test suite**

Run: `cd mts && uv run pytest -v 2>&1 | tail -20`
Expected: All tests pass. This is the critical validation — every existing integration test now runs through the pipeline path instead of the monolith.

**Step 3: Run lint**

Run: `cd mts && uv run ruff check src tests`
Expected: All checks passed.

**Step 4: Update the flag test**

In `mts/tests/test_generation_pipeline.py`, update `test_flag_default_off`:

```python
    def test_flag_default_on(self) -> None:
        settings = AppSettings(agent_provider="deterministic")
        assert settings.use_generation_pipeline is True
```

And the `test_monolith_still_works` test should explicitly set `use_generation_pipeline=False` (it already does, so no change needed).

**Step 5: Run tests again**

Run: `cd mts && uv run pytest tests/test_generation_pipeline.py -v`
Expected: All PASS.

**Step 6: Commit**

```bash
git add mts/src/mts/config/settings.py mts/tests/test_generation_pipeline.py
git commit -m "feat(phase5): flip use_generation_pipeline default to True"
```

---

### Task 6: Delete the monolithic code path

With the flag defaulting to True and all tests passing, remove the ~355 lines of duplicate code and the feature flag.

**Files:**
- Modify: `mts/src/mts/loop/generation_runner.py` (delete monolithic try block, remove flag check)
- Modify: `mts/src/mts/config/settings.py` (remove `use_generation_pipeline` field and env var)
- Modify: `mts/tests/test_generation_pipeline.py` (remove flag tests and monolith test)
- Test: full suite

**Step 1: Simplify generation_runner.py**

Replace the entire `try:` block (lines 184-591) with just the pipeline path. The new code inside the `for generation` loop's try block should be:

```python
            try:
                from mts.loop.generation_pipeline import GenerationPipeline
                from mts.loop.stage_types import GenerationContext

                warm_fn = None
                if self.settings.executor_mode == "primeintellect":
                    def _warm(ctx_arg: object) -> dict:
                        return self.remote.warm_provision(
                            environment_name=f"{scenario_name}-gen-{generation}",
                            max_retries=self.settings.primeintellect_max_retries,
                            backoff_seconds=self.settings.primeintellect_backoff_seconds,
                        )
                    warm_fn = _warm

                pipeline = GenerationPipeline(
                    orchestrator=self.agents,
                    tournament_runner=self.tournament,
                    gate=self.gate,
                    artifacts=self.artifacts,
                    sqlite=self.sqlite,
                    trajectory_builder=self.trajectory_builder,
                    events=self.events,
                    curator=self.agents.curator,
                    controller=self.controller,
                    warm_provision_fn=warm_fn,
                    chat_with_agent_fn=self._chat_with_agent,
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
                previous_best = ctx.previous_best
                challenger_elo = ctx.challenger_elo
                replay_narrative = ctx.replay_narrative
                coach_competitor_hints = ctx.coach_competitor_hints
                completed += 1
```

Keep the existing `except Exception as exc:` block unchanged (lines 575-591).

This deletes ~355 lines.

**Step 2: Remove the feature flag**

In `mts/src/mts/config/settings.py`:
- Delete `use_generation_pipeline: bool = Field(default=True)` from `AppSettings`
- Delete `use_generation_pipeline=os.getenv(...)` from `load_settings()`

**Step 3: Clean up tests**

In `mts/tests/test_generation_pipeline.py`:
- Delete `TestGenerationPipelineFlag` class (flag no longer exists)
- Delete `test_monolith_still_works` (monolith no longer exists)
- Keep integration and equivalence tests (they now test the only path)
- Remove `use_generation_pipeline=True` from remaining test `AppSettings` constructors (field no longer exists)

**Step 4: Clean up any other references**

Search for `use_generation_pipeline` across the codebase and remove any remaining references.

Run: `grep -r "use_generation_pipeline" mts/src mts/tests`

Fix any remaining occurrences.

**Step 5: Remove unused imports from generation_runner.py**

After deleting the monolithic block, some imports at the top of `generation_runner.py` may become unused. Run `ruff check` and fix any F401 (unused import) errors.

**Step 6: Run full test suite**

Run: `cd mts && uv run pytest`
Expected: All tests pass (count will drop by 2-3 since we removed flag tests and monolith test).

**Step 7: Run lint + type check**

Run: `cd mts && uv run ruff check src tests && uv run mypy src/mts/loop/generation_runner.py src/mts/loop/generation_pipeline.py src/mts/loop/stages.py`
Expected: All clean.

**Step 8: Verify line count reduction**

Run: `wc -l mts/src/mts/loop/generation_runner.py`
Expected: ~260 lines (down from ~615).

**Step 9: Commit**

```bash
git add mts/src/mts/loop/generation_runner.py mts/src/mts/config/settings.py mts/tests/test_generation_pipeline.py
git commit -m "refactor(phase5): delete monolithic generation loop (~355 lines removed)"
```

---

## Verification Checklist

After all 6 tasks:

```bash
# Full test suite passes
cd mts && uv run pytest

# Lint clean
uv run ruff check src tests

# Type check clean for modified files
uv run mypy src/mts/loop/generation_runner.py src/mts/loop/generation_pipeline.py src/mts/loop/stages.py src/mts/loop/stage_types.py

# generation_runner.py is significantly smaller
wc -l src/mts/loop/generation_runner.py
# Expected: ~260 lines (was ~615)

# No references to deleted flag
grep -r "use_generation_pipeline" src tests
# Expected: empty

# Pipeline imports work
uv run python -c "from mts.loop.generation_pipeline import GenerationPipeline; print('OK')"

# Deterministic smoke test
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 2 --run-id verify_phase5
```

## Risk Mitigation

- **Task 5 is the critical validation gate.** Flipping the flag runs all 589+ tests through the pipeline path. If anything breaks, we catch it before deleting code.
- **Tasks 1-4 are additive.** They add features to the pipeline without changing the monolith. Zero regression risk.
- **Task 6 is the big delete.** It's done last, after the flag flip proves parity. If it somehow breaks, `git revert` restores everything.
- **Each task is a separate commit.** Easy to bisect or revert.
