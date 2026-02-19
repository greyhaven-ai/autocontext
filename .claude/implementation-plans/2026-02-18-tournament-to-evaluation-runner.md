# TournamentRunner → EvaluationRunner Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix two bugs in the EvaluationRunner adapter layer, then replace `TournamentRunner` with `EvaluationRunner` as the production execution path in `stage_tournament`.

**Architecture:** Fix the double-execution bug in `TournamentEvalAdapter` first (Task 1), fix the replay type mismatch in `ScenarioEvaluator` (Task 2), refactor `stage_tournament` to use EvaluationRunner directly (Task 3), update `GenerationRunner` wiring (Task 4), delete `TournamentRunner` and `TournamentEvalAdapter` (Task 5), clean up imports and types (Task 6).

**Tech Stack:** Python 3.11+, pytest, uv

---

## Context

### Current State

`TournamentRunner` (63 lines in `execution/tournament.py`) runs N matches via `ExecutionSupervisor`, accumulating `ExecutionOutput` objects and computing Elo scores. It's called from `stage_tournament` in `stages.py`.

`EvaluationRunner` (58 lines in `harness/evaluation/runner.py`) is a domain-agnostic equivalent that accepts an `Evaluator` protocol and returns `EvaluationSummary`. It already exists and is tested (148 lines of tests).

`ScenarioEvaluator` (43 lines in `harness/evaluation/scenario_evaluator.py`) adapts `ScenarioInterface + ExecutionSupervisor` to the `Evaluator` protocol. It works but loses the typed `ReplayEnvelope` by converting it to a flat dict.

`TournamentEvalAdapter` (70 lines in `execution/eval_adapter.py`) is a backward-compatibility bridge that wraps `EvaluationRunner` to produce `TournamentSummary`. It has a **double-execution bug** — runs 2N matches instead of N.

### The Two Prerequisite Bugs

**Bug 1: Double Execution** (`eval_adapter.py:34-39`)
The adapter runs `supervisor.run()` N times to collect `ExecutionOutput` objects (lines 34-39), then `EvaluationRunner.run()` internally calls `evaluator.evaluate()` N more times (which calls `supervisor.run()` again). Total: 2N executions for N matches.

**Bug 2: Replay Type Loss** (`scenario_evaluator.py:42`)
`ScenarioEvaluator.evaluate()` converts `ReplayEnvelope` to `dict` via `model_dump()`. But `stage_tournament` (lines 192, 261-262) needs `ExecutionOutput.result.replay` (a `list[dict]`) and `stage_persistence` (line 406) needs `ExecutionOutput.replay.model_dump()`. The typed structure is lost.

### Migration Strategy

Rather than fixing `TournamentEvalAdapter` (which is a transitional shim), we'll:
1. Fix `ScenarioEvaluator` to preserve `ExecutionOutput` in metadata
2. Refactor `stage_tournament` to use `EvaluationRunner` directly
3. Delete both `TournamentRunner` and `TournamentEvalAdapter`

### Key Files

| File | Role | Action |
|------|------|--------|
| `src/mts/harness/evaluation/scenario_evaluator.py` | Evaluator adapter | Modify — store `ExecutionOutput` in metadata |
| `src/mts/loop/stages.py` | Pipeline stages | Modify — `stage_tournament` uses EvaluationRunner |
| `src/mts/loop/stage_types.py` | Context type | Modify — change `tournament` field type |
| `src/mts/loop/generation_runner.py` | Runner setup | Modify — wire EvaluationRunner instead of TournamentRunner |
| `src/mts/loop/generation_pipeline.py` | Pipeline glue | Modify — accept EvaluationRunner |
| `src/mts/execution/tournament.py` | Old runner | Delete (after migration) |
| `src/mts/execution/eval_adapter.py` | Old adapter | Delete (after migration) |
| `src/mts/execution/__init__.py` | Package exports | Modify — remove TournamentRunner export |
| `tests/test_eval_adapter.py` | Adapter tests | Delete |
| `tests/test_harness/test_harness_scenario_evaluator.py` | Evaluator tests | Modify — add metadata test |
| `tests/test_generation_stages.py` | Stage tests | Modify — adapt to new types |
| `tests/test_generation_pipeline.py` | Pipeline tests | Adapt if needed |

### Current Test Count: 580 passed

---

## Task 1: Fix ScenarioEvaluator to Preserve ExecutionOutput

The core fix: store the full `ExecutionOutput` in `EvaluationResult.metadata["execution_output"]` so downstream code can access `Result`, `ReplayEnvelope`, and all typed fields.

**Files:**
- Modify: `mts/src/mts/harness/evaluation/scenario_evaluator.py:36-43`
- Modify: `mts/tests/test_harness/test_harness_scenario_evaluator.py` — add test

**Step 1: Write the failing test**

Add to `test_harness_scenario_evaluator.py`:

```python
def test_evaluate_preserves_execution_output(self) -> None:
    """EvaluationResult.metadata contains the full ExecutionOutput."""
    from mts.execution.supervisor import ExecutionOutput

    evaluator = ScenarioEvaluator(self.scenario, self.supervisor)
    result = evaluator.evaluate({"aggression": 0.7}, seed=42, limits=self.limits)
    assert "execution_output" in result.metadata
    assert isinstance(result.metadata["execution_output"], ExecutionOutput)
    assert result.metadata["execution_output"].result.score == result.score
```

**Step 2: Run test — expect FAIL**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest tests/test_harness/test_harness_scenario_evaluator.py -v -k "test_evaluate_preserves_execution_output"`
Expected: FAIL — `"execution_output"` not in metadata

**Step 3: Fix ScenarioEvaluator**

In `scenario_evaluator.py`, change the `evaluate` method to store the `ExecutionOutput`:

```python
    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        from mts.execution.supervisor import ExecutionInput
        from mts.scenarios.base import ExecutionLimits as MtsLimits

        mts_limits = MtsLimits(
            timeout_seconds=limits.timeout_seconds,
            max_memory_mb=limits.max_memory_mb,
            network_access=limits.network_access,
        )
        payload = ExecutionInput(strategy=candidate, seed=seed, limits=mts_limits)
        output = self._supervisor.run(self._scenario, payload)
        return EvaluationResult(
            score=output.result.score,
            passed=output.result.passed_validation,
            errors=list(output.result.validation_errors),
            metadata={
                "metrics": dict(output.result.metrics) if hasattr(output.result, "metrics") else {},
                "execution_output": output,
            },
            replay_data=output.replay.model_dump() if hasattr(output.replay, "model_dump") else {},
        )
```

Key change: `metadata` now contains `{"metrics": {...}, "execution_output": output}` instead of just the metrics dict.

**Step 4: Run test — expect PASS**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest tests/test_harness/test_harness_scenario_evaluator.py -v`
Expected: all pass

**Step 5: Run full suite**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 581 passed

**Step 6: Commit**

```bash
git add mts/src/mts/harness/evaluation/scenario_evaluator.py mts/tests/test_harness/test_harness_scenario_evaluator.py
git commit -m "fix: ScenarioEvaluator preserves ExecutionOutput in metadata

Stores the full ExecutionOutput in EvaluationResult.metadata so
downstream consumers can access typed Result and ReplayEnvelope
without losing structure through model_dump() serialization."
```

---

## Task 2: Refactor stage_tournament to Use EvaluationRunner

Replace the `TournamentRunner` dependency in `stage_tournament` with `ScenarioEvaluator` + `EvaluationRunner`. The `EvaluationSummary.results` list contains `EvaluationResult` objects, each with `metadata["execution_output"]` for backward-compatible access to `ExecutionOutput`.

**Files:**
- Modify: `mts/src/mts/loop/stages.py` — `stage_tournament` signature and body
- Modify: `mts/src/mts/loop/stage_types.py` — change `tournament` field type
- Modify: `mts/tests/test_generation_stages.py` — adapt tournament stage tests

**Step 1: Update `stage_types.py`**

Change the `tournament` field on `GenerationContext` from `TournamentSummary | None` to `EvaluationSummary | None`:

```python
# In TYPE_CHECKING block, replace:
#     from mts.execution.tournament import TournamentSummary
# With:
    from mts.harness.evaluation.types import EvaluationSummary

# In the dataclass, replace:
#     tournament: TournamentSummary | None = None
# With:
    tournament: EvaluationSummary | None = None
```

**Step 2: Refactor `stage_tournament` in `stages.py`**

Replace the function signature and body. Key changes:
- Accept `supervisor: ExecutionSupervisor` instead of `tournament_runner: TournamentRunner`
- Create `ScenarioEvaluator` + `EvaluationRunner` internally
- Use `EvaluationSummary` instead of `TournamentSummary`
- Extract `ExecutionOutput` from `result.metadata["execution_output"]` where needed

The refactored function (showing the key structural changes):

```python
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits

def stage_tournament(
    ctx: GenerationContext,
    *,
    supervisor: ExecutionSupervisor,  # Changed from tournament_runner
    gate: BackpressureGate | TrendAwareGate,
    events: EventStreamEmitter,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
    agents: AgentOrchestrator | None = None,
) -> GenerationContext:
```

Inside the function, replace:
```python
        tournament = tournament_runner.run(
            scenario=scenario,
            strategy=current_strategy,
            seed_base=...,
            matches=...,
            limits=ExecutionLimits(),
            challenger_elo=ctx.challenger_elo,
            on_match=_on_match,
        )
```

With:
```python
        evaluator = ScenarioEvaluator(scenario, supervisor)
        harness_limits = HarnessLimits()
        runner = EvaluationRunner(evaluator)

        def _on_result(idx: int, result: EvaluationResult) -> None:
            _on_match(idx, result.score)

        tournament = runner.run(
            candidate=current_strategy,
            seed_base=settings.seed_base + (ctx.generation * 100) + (attempt * 10),
            trials=settings.matches_per_generation,
            limits=harness_limits,
            challenger_elo=ctx.challenger_elo,
            on_result=_on_result,
        )
```

For `tournament.outputs` usage, extract `ExecutionOutput` from results:
```python
# Where code does: best_output = max(tournament.outputs, key=lambda o: o.result.score)
# Replace with:
best_result = max(tournament.results, key=lambda r: r.score)
best_output = best_result.metadata["execution_output"]
```

For custom backpressure (line 192-193):
```python
best_result = max(tournament.results, key=lambda r: r.score)
best_exec_output = best_result.metadata["execution_output"]
custom_metrics = scenario.custom_backpressure(best_exec_output.result)
```

For replay narrative (line 261-262):
```python
best_result = max(tournament.results, key=lambda r: r.score)
best_exec_output = best_result.metadata["execution_output"]
replay_narrative = scenario.replay_to_narrative(best_exec_output.result.replay)
```

**Step 3: Update `stage_persistence` in `stages.py`**

Change references from `tournament.outputs` to extract from `tournament.results`:

For match insertion (line 380):
```python
    for idx, eval_result in enumerate(tournament.results):
        match_output = eval_result.metadata["execution_output"]
        sqlite.insert_match(
            run_id, generation,
            settings.seed_base + (generation * 100) + idx,
            match_output.result.score,
            match_output.result.passed_validation,
            json.dumps(match_output.result.validation_errors),
        )
```

For replay payload (line 406):
```python
        replay_payload=tournament.results[0].metadata["execution_output"].replay.model_dump(),
```

**Step 4: Update imports in `stages.py`**

Remove:
```python
from mts.execution.tournament import TournamentRunner  # from TYPE_CHECKING
```

Add:
```python
from mts.execution.supervisor import ExecutionSupervisor  # in TYPE_CHECKING
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits, EvaluationResult
```

**Step 5: Update tests in `test_generation_stages.py`**

Tests that create/mock `TournamentRunner` need to pass `ExecutionSupervisor` instead. Tests that assert on `tournament.outputs` need to assert on `tournament.results[i].metadata["execution_output"]`.

The test's `FakeTournamentRunner` should be replaced with a real `ExecutionSupervisor` wrapping an inline executor (same pattern as `test_eval_adapter.py:81-98`), or mock the supervisor directly.

**Step 6: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 580 passed (same count — tests adapted, not added/removed)

**Step 7: Commit**

```bash
git add mts/src/mts/loop/stages.py mts/src/mts/loop/stage_types.py mts/tests/test_generation_stages.py
git commit -m "refactor: stage_tournament uses EvaluationRunner instead of TournamentRunner

Replaces TournamentRunner dependency with ScenarioEvaluator +
EvaluationRunner. ExecutionOutput objects are accessible via
EvaluationResult.metadata['execution_output'] for backward compat.
Eliminates the double-execution bug in the old adapter path."
```

---

## Task 3: Update GenerationRunner + GenerationPipeline Wiring

Wire the new `ExecutionSupervisor` through `GenerationPipeline` to `stage_tournament`.

**Files:**
- Modify: `mts/src/mts/loop/generation_runner.py` — pass `supervisor` instead of `tournament`
- Modify: `mts/src/mts/loop/generation_pipeline.py` — accept `supervisor` instead of `tournament_runner`
- Modify: `mts/tests/test_generation_pipeline.py` — update mocks if needed

**Step 1: Update GenerationPipeline**

In `generation_pipeline.py`, change the constructor:

```python
# Replace:
#     tournament_runner: TournamentRunner,
# With:
    supervisor: ExecutionSupervisor,
```

And in `run_generation()`, pass `supervisor` to `stage_tournament`:
```python
        ctx = stage_tournament(
            ctx,
            supervisor=self._supervisor,  # was tournament_runner=self._tournament_runner
            gate=self._gate,
            ...
        )
```

Update TYPE_CHECKING imports:
```python
# Remove: from mts.execution.tournament import TournamentRunner
# Add: from mts.execution.supervisor import ExecutionSupervisor
```

**Step 2: Update GenerationRunner**

In `generation_runner.py`:
- Remove `self.tournament = TournamentRunner(self.executor)` (line 78)
- Pass `supervisor=self.executor` to `GenerationPipeline` instead of `tournament_runner=self.tournament`
- Remove `TournamentRunner` from import line 11

**Step 3: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: 580 passed

**Step 4: Commit**

```bash
git add mts/src/mts/loop/generation_runner.py mts/src/mts/loop/generation_pipeline.py mts/tests/test_generation_pipeline.py
git commit -m "refactor: wire ExecutionSupervisor directly to pipeline, bypassing TournamentRunner"
```

---

## Task 4: Delete TournamentRunner, TournamentEvalAdapter, and Tests

Now that nothing references them, delete the dead code.

**Files:**
- Delete: `mts/src/mts/execution/tournament.py`
- Delete: `mts/src/mts/execution/eval_adapter.py`
- Delete: `mts/tests/test_eval_adapter.py`
- Modify: `mts/src/mts/execution/__init__.py` — remove exports

**Step 1: Verify no remaining references**

Run:
```bash
grep -rn "TournamentRunner\|TournamentSummary\|TournamentEvalAdapter" mts/src mts/tests --include="*.py" | grep -v "__pycache__"
```

Expected: Only hits in `tournament.py`, `eval_adapter.py`, `test_eval_adapter.py`, and `execution/__init__.py`.

**Step 2: Update `execution/__init__.py`**

Remove `TournamentRunner` and `TournamentSummary` from exports. Keep `ExecutionSupervisor`.

**Step 3: Delete the files**

```bash
rm mts/src/mts/execution/tournament.py
rm mts/src/mts/execution/eval_adapter.py
rm mts/tests/test_eval_adapter.py
```

**Step 4: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q`
Expected: ~574 passed (580 - 6 adapter tests)

**Step 5: Run lint and type check**

```bash
uv run ruff check src tests
uv run mypy src
```

**Step 6: Commit**

```bash
git rm mts/src/mts/execution/tournament.py mts/src/mts/execution/eval_adapter.py mts/tests/test_eval_adapter.py
git add mts/src/mts/execution/__init__.py
git commit -m "refactor: delete TournamentRunner and TournamentEvalAdapter

TournamentRunner replaced by EvaluationRunner in stage_tournament.
TournamentEvalAdapter (with its double-execution bug) no longer needed.
Underlying evaluation is now fully harness-backed."
```

---

## Task 5: Update Harness Roadmap

**Files:**
- Modify: `.claude/implementation-plans/harness-roadmap.md`

Mark Direction 3 as completed. Update the Completed section to include Directions 1, 2, and 3.

**Step 1: Update roadmap**

Move Direction 3 to Completed, noting the bugs fixed and files deleted.

**Step 2: Commit**

```bash
git add .claude/implementation-plans/harness-roadmap.md
git commit -m "docs: mark Direction 3 (TournamentRunner migration) complete in roadmap"
```

---

## Verification

After all tasks:

```bash
# All tests pass
cd /Users/jayscambler/Repositories/MTS/mts && uv run pytest --tb=short -q

# Lint clean
uv run ruff check src tests

# Type check clean
uv run mypy src

# No stale TournamentRunner references
grep -rn "TournamentRunner\|TournamentEvalAdapter" mts/src mts/tests --include="*.py" | grep -v __pycache__
# Expected: empty

# EvaluationRunner is the sole tournament path
grep -rn "EvaluationRunner" mts/src --include="*.py"
# Expected: harness/evaluation/runner.py (def), stages.py (usage), scenario_evaluator.py (import)

# Harness import boundary respected
grep -r "from mts\." mts/src/mts/harness/ --include="*.py" | grep -v "from mts.harness" | grep -v __pycache__
# Expected: only scenario_evaluator.py (runtime imports inside evaluate())
```

## Summary of Changes

| Task | Files Changed | Lines Added | Lines Removed | Tests Added | Tests Removed |
|------|---------------|-------------|---------------|-------------|---------------|
| 1. Fix ScenarioEvaluator | 2 | ~10 | ~3 | 1 | 0 |
| 2. Refactor stage_tournament | 3 | ~30 | ~20 | 0 | 0 |
| 3. Update wiring | 3 | ~5 | ~10 | 0 | 0 |
| 4. Delete dead code | 4 (3 deleted) | 0 | ~380 | 0 | 6 |
| 5. Update roadmap | 1 | ~10 | ~15 | 0 | 0 |
| **Total** | **~10** | **~55** | **~430** | **1** | **6** |

Net: ~375 lines deleted, 1 test added, 6 tests removed. Double-execution bug eliminated. All tournament scoring now flows through the domain-agnostic EvaluationRunner.

## Risk Mitigation

- **Task 1 is isolated** — fixing ScenarioEvaluator is backward-compatible (adds data to metadata, doesn't change existing fields)
- **Task 2 is the big refactor** — touches `stages.py` which is the most critical file. Full test suite validates correctness.
- **Task 3 is wiring only** — no behavioral change, just passing `supervisor` instead of `tournament`
- **Task 4 is pure deletion** — only after Tasks 1-3 prove the new path works
- **Each task is independently committable and verifiable**
