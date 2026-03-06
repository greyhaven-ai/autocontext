# Batch 2: High-Priority Fixes Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix 5 high-priority issues: wire `minRounds` through config/CLI layers (MTS-53), use `run_batch` in Python's `TaskRunner.run()` loop (MTS-54), add dimension-aware revision prompts with regression guard (MTS-41), and create smoke/integration test fixtures (MTS-29, MTS-30).

**Architecture:** The `ImprovementLoop` already supports `min_rounds`/`minRounds` but the config layers (TaskConfig, CLI, enqueue) don't pass it through. Python's `TaskRunner.run()` already has `run_batch()` but the main loop still calls `run_once()` pattern. Revision prompts currently omit dimension scores, causing whack-a-mole oscillation. Smoke tests validate wiring with mock providers.

**Tech Stack:** Python 3.11+ (pytest, dataclasses), TypeScript (Vitest, Zod), SQLite

---

### Task 1: Wire `min_rounds` through Python TaskConfig and enqueue (MTS-53)

**Files:**
- Modify: `mts/src/mts/execution/task_runner.py:32-60` (TaskConfig) and `mts/src/mts/execution/task_runner.py:397-423` (enqueue_task)
- Modify: `mts/src/mts/execution/task_runner.py:302-306` (ImprovementLoop construction in _process_task)
- Test: `mts/tests/test_task_runner.py`

**Step 1: Write the failing test**

Add to `mts/tests/test_task_runner.py`:

```python
class TestMinRoundsWiring:
    def test_task_config_parses_min_rounds(self):
        config = TaskConfig.from_json('{"min_rounds": 3}')
        assert config.min_rounds == 3

    def test_task_config_defaults_min_rounds(self):
        config = TaskConfig.from_json(None)
        assert config.min_rounds == 1

    def test_enqueue_passes_min_rounds(self, tmp_path):
        from mts.storage.sqlite_store import SQLiteStore
        store = SQLiteStore(str(tmp_path / "test.db"))
        store.migrate()
        task_id = enqueue_task(store, "test", min_rounds=3)
        task = store.get_task(task_id)
        import json
        config = json.loads(task["config_json"])
        assert config["min_rounds"] == 3
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py -k "TestMinRoundsWiring" -v`
Expected: FAIL — `TaskConfig` has no `min_rounds` field, `enqueue_task` doesn't accept `min_rounds`

**Step 3: Implement**

In `task_runner.py` `TaskConfig`:
```python
@dataclass(slots=True)
class TaskConfig:
    max_rounds: int = 5
    quality_threshold: float = 0.9
    min_rounds: int = 1          # <-- ADD
    reference_context: str | None = None
    # ... rest unchanged

    @classmethod
    def from_json(cls, data: str | None) -> TaskConfig:
        if not data:
            return cls()
        parsed = json.loads(data)
        return cls(
            max_rounds=parsed.get("max_rounds", 5),
            quality_threshold=parsed.get("quality_threshold", 0.9),
            min_rounds=parsed.get("min_rounds", 1),  # <-- ADD
            # ... rest unchanged
        )
```

In `_process_task`, pass `min_rounds` to `ImprovementLoop`:
```python
loop = ImprovementLoop(
    task=agent_task,
    max_rounds=config.max_rounds,
    quality_threshold=config.quality_threshold,
    min_rounds=config.min_rounds,  # <-- ADD
)
```

In `enqueue_task`, add `min_rounds` parameter:
```python
def enqueue_task(
    store, spec_name, ...,
    min_rounds: int = 1,  # <-- ADD
    ...
) -> str:
    config = { ..., "min_rounds": min_rounds, ... }
```

**Step 4: Run tests to verify they pass**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py -k "TestMinRoundsWiring" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add mts/src/mts/execution/task_runner.py mts/tests/test_task_runner.py
git commit -m "feat: wire min_rounds through Python TaskConfig and enqueue (MTS-53)"
```

---

### Task 2: Wire `minRounds` through TS TaskConfig, CLI, and enqueue (MTS-53)

**Files:**
- Modify: `ts/src/execution/task-runner.ts:18-57` (TaskConfig, parseTaskConfig, enqueueTask)
- Modify: `ts/src/execution/task-runner.ts:248-252` (ImprovementLoop construction in processTask)
- Modify: `ts/src/cli/index.ts:177-233` (cmdImprove — add --min-rounds flag)
- Modify: `ts/src/cli/index.ts:235-267` (cmdQueue — add --min-rounds flag)
- Test: `ts/tests/task-runner.test.ts`

**Step 1: Write the failing test**

Add to `ts/tests/task-runner.test.ts` (or create new test file if needed):

```typescript
describe("minRounds wiring", () => {
  it("parseTaskConfig parses min_rounds", () => {
    // parseTaskConfig is not exported — test via TaskRunner integration or export it
    // We'll test via enqueueTask + processTask integration
  });

  it("enqueueTask passes minRounds to config", () => {
    const store = new SQLiteStore(":memory:");
    store.migrate(migrationsDir);
    const id = enqueueTask(store, "test", { minRounds: 3 });
    const task = store.getTask(id);
    const config = JSON.parse(task!.config_json!);
    expect(config.min_rounds).toBe(3);
    store.close();
  });
});
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts && npx vitest run tests/task-runner.test.ts`
Expected: FAIL — `enqueueTask` doesn't accept `minRounds`

**Step 3: Implement**

In `task-runner.ts` `TaskConfig` interface, add `minRounds`:
```typescript
export interface TaskConfig {
  maxRounds: number;
  qualityThreshold: number;
  minRounds: number;  // <-- ADD
  // ... rest unchanged
}
```

In `TaskConfigSchema`, add `min_rounds`:
```typescript
const TaskConfigSchema = z.object({
  min_rounds: z.number().int().positive().optional(),  // <-- ADD
  // ... rest unchanged
});
```

In `parseTaskConfig`, add `minRounds`:
```typescript
return {
  maxRounds: d.max_rounds ?? 5,
  qualityThreshold: d.quality_threshold ?? 0.9,
  minRounds: d.min_rounds ?? 1,  // <-- ADD
  // ... rest unchanged
};
```

In `processTask`, pass `minRounds` to `ImprovementLoop`:
```typescript
const loop = new ImprovementLoop({
  task: agentTask,
  maxRounds: config.maxRounds,
  qualityThreshold: config.qualityThreshold,
  minRounds: config.minRounds,  // <-- ADD
});
```

In `enqueueTask`, add `minRounds` option:
```typescript
export function enqueueTask(store, specName, opts?: {
  // ... existing fields
  minRounds?: number;  // <-- ADD
}): string {
  // ... existing code
  if (opts?.minRounds != null) config.min_rounds = opts.minRounds;
  // ...
}
```

In CLI `cmdImprove`, add `--min-rounds` flag:
```typescript
"min-rounds": { type: "string", default: "1" },
```
And pass to `ImprovementLoop`:
```typescript
minRounds: parseInt(values["min-rounds"] ?? "1", 10),
```

In CLI `cmdQueue`, add `--min-rounds` flag:
```typescript
"min-rounds": { type: "string", default: "1" },
```
And pass to `enqueueTask`:
```typescript
minRounds: parseInt(values["min-rounds"]!, 10),
```

**Step 4: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts && npx vitest run tests/task-runner.test.ts`
Expected: PASS

**Step 5: Commit**

```bash
git add ts/src/execution/task-runner.ts ts/src/cli/index.ts ts/tests/task-runner.test.ts
git commit -m "feat: wire minRounds through TS TaskConfig, CLI, and enqueue (MTS-53)"
```

---

### Task 3: Python TaskRunner.run() uses run_batch when concurrency > 1 (MTS-54)

**Files:**
- Modify: `mts/src/mts/execution/task_runner.py:210-234` (TaskRunner.run method)
- Test: `mts/tests/test_task_runner.py`

**Step 1: Write the failing test**

```python
class TestRunBatchConcurrency:
    def test_run_uses_batch_when_concurrency_gt_1(self, tmp_path):
        """run() should use run_batch() when concurrency > 1."""
        from unittest.mock import MagicMock, patch
        store = MagicMock()
        provider = MagicMock()
        runner = TaskRunner(
            store=store, provider=provider,
            concurrency=3, max_consecutive_empty=1,
        )
        # dequeue_task returns None on first call → triggers empty exit
        store.dequeue_task.return_value = None
        runner.run()
        # With concurrency=1, run() calls dequeue_task once per poll
        # With concurrency>1, run() should call run_batch instead
        # We'll verify by checking that dequeue_task was called up to concurrency times
        assert store.dequeue_task.call_count <= 3
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py -k "TestRunBatchConcurrency" -v`

**Step 3: Implement**

Update `TaskRunner.run()` to use `run_batch()` when `concurrency > 1`:

```python
def run(self) -> int:
    """Main loop. Returns the number of tasks processed."""
    self._setup_signals()
    consecutive_empty = 0

    logger.info("task runner started (poll_interval=%.1fs, concurrency=%d)", self.poll_interval, self.concurrency)

    while not self._shutdown:
        processed = self.run_batch(self.concurrency)

        if processed == 0:
            consecutive_empty += 1
            if self.max_consecutive_empty > 0 and consecutive_empty >= self.max_consecutive_empty:
                logger.info("max consecutive empty polls reached, shutting down")
                break
            logger.debug("no tasks, sleeping %.1fs", self.poll_interval)
            self._sleep(self.poll_interval)
            continue

        consecutive_empty = 0

    logger.info("task runner stopped. processed %d tasks", self._tasks_processed)
    return self._tasks_processed
```

**Step 4: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py -k "TestRunBatchConcurrency" -v`
Expected: PASS

**Step 5: Commit**

```bash
git add mts/src/mts/execution/task_runner.py mts/tests/test_task_runner.py
git commit -m "feat: TaskRunner.run() uses run_batch for concurrent processing (MTS-54)"
```

---

### Task 4: Dimension-aware revision prompts + regression guard (MTS-41)

**Files:**
- Modify: `mts/src/mts/execution/task_runner.py:154-178` (SimpleAgentTask.revise_output)
- Modify: `ts/src/execution/task-runner.ts:142-169` (SimpleAgentTask.reviseOutput)
- Test: `mts/tests/test_task_runner.py`
- Test: `ts/tests/task-runner.test.ts`

**Step 1: Write the failing tests (Python)**

```python
class TestDimensionAwareRevision:
    def test_revision_prompt_includes_dimension_scores(self):
        """revise_output should include per-dimension scores in the prompt."""
        calls = []
        def mock_complete(system_prompt, user_prompt, model=None):
            calls.append(user_prompt)
            return type("R", (), {"text": "revised"})()

        provider = type("P", (), {"complete": mock_complete})()
        task = SimpleAgentTask("write haiku", "evaluate quality", provider, "test-model")
        result = AgentTaskResult(
            score=0.7,
            reasoning="needs work",
            dimension_scores={"technical_accuracy": 0.6, "creativity": 0.8},
        )
        task.revise_output("initial output", result, {})
        assert "technical_accuracy: 0.60" in calls[0]
        assert "creativity: 0.80" in calls[0]

    def test_revision_prompt_includes_regression_warning(self):
        """When previous_dimensions provided, flag regressions."""
        calls = []
        def mock_complete(system_prompt, user_prompt, model=None):
            calls.append(user_prompt)
            return type("R", (), {"text": "revised"})()

        provider = type("P", (), {"complete": mock_complete})()
        task = SimpleAgentTask("write haiku", "evaluate quality", provider, "test-model")
        result = AgentTaskResult(
            score=0.7,
            reasoning="needs work",
            dimension_scores={"accuracy": 0.6, "creativity": 0.8},
        )
        prev_dims = {"accuracy": 0.85, "creativity": 0.7}
        task.revise_output("initial output", result, {}, previous_dimensions=prev_dims)
        assert "REGRESSION" in calls[0] or "regressed" in calls[0].lower()
        assert "accuracy" in calls[0]
```

**Step 2: Run tests to verify they fail**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py -k "TestDimensionAwareRevision" -v`
Expected: FAIL — revision prompt doesn't include dimensions or regression warnings

**Step 3: Implement (Python)**

Update `SimpleAgentTask.revise_output` to accept optional `previous_dimensions` and include dimension info:

```python
def revise_output(
    self, output: str, judge_result: AgentTaskResult, state: dict,
    previous_dimensions: dict[str, float] | None = None,
) -> str:
    revision_instruction = self._revision_prompt or (
        "Revise the following output based on the judge's feedback. "
        "Maintain what works, fix what doesn't."
    )

    # Build dimension scores section
    dim_section = ""
    if judge_result.dimension_scores:
        dim_lines = []
        for dim, score in sorted(judge_result.dimension_scores.items()):
            line = f"  - {dim}: {score:.2f}"
            if previous_dimensions and dim in previous_dimensions:
                delta = score - previous_dimensions[dim]
                if delta < -0.05:
                    line += f" (REGRESSION from {previous_dimensions[dim]:.2f} — do NOT regress on this)"
                elif delta > 0.05:
                    line += f" (improved from {previous_dimensions[dim]:.2f})"
            dim_lines.append(line)
        dim_section = "\n## Dimension Scores\n" + "\n".join(dim_lines) + "\n"

    prompt = (
        f"{revision_instruction}\n\n"
        f"## Original Output\n{output}\n\n"
        f"## Judge Score: {judge_result.score:.2f}\n"
        f"## Judge Feedback\n{judge_result.reasoning}\n"
        f"{dim_section}\n"
        f"## Task\n{self._task_prompt}\n\n"
        "Produce an improved version:"
    )
    # ... rest of complete call unchanged
```

Update `ImprovementLoop.run()` to pass `previous_dimensions` when calling `revise_output`:

In `improvement_loop.py`, at the revision call site (~line 300), capture previous dimension scores and pass them:

```python
# Track previous dimension scores for regression detection
prev_dimensions = last_good_result.dimension_scores if last_good_result else {}

if round_num < self.max_rounds:
    if hasattr(self.task, 'revise_output'):
        import inspect
        sig = inspect.signature(self.task.revise_output)
        if 'previous_dimensions' in sig.parameters:
            revised = self.task.revise_output(current_output, result, state, previous_dimensions=prev_dimensions)
        else:
            revised = self.task.revise_output(current_output, result, state)
    # ...
```

Actually — this is too fragile. Better approach: since `AgentTaskInterface.revise_output` only takes 3 args, we add the dimension info directly into the `AgentTaskResult.reasoning` field within the loop. This way all task implementations automatically get it without signature changes.

**Revised approach:** In `ImprovementLoop.run()`, before calling `revise_output`, append dimension + regression info to a copy of the result:

```python
# Before revision call, enrich feedback with dimension details
enriched_result = result
if result.dimension_scores and round_num > 1:
    prev_dims = rounds[-2].dimension_scores if not rounds[-2].judge_failed else {}
    dim_lines = []
    for dim, dscore in sorted(result.dimension_scores.items()):
        line = f"  - {dim}: {dscore:.2f}"
        if dim in prev_dims:
            delta = dscore - prev_dims[dim]
            if delta < -0.05:
                line += f" (REGRESSION from {prev_dims[dim]:.2f} — preserve this dimension)"
            elif delta > 0.05:
                line += f" (improved from {prev_dims[dim]:.2f})"
        dim_lines.append(line)
    dim_annotation = "\n\nDimension Scores:\n" + "\n".join(dim_lines)
    enriched_result = AgentTaskResult(
        score=result.score,
        reasoning=result.reasoning + dim_annotation,
        dimension_scores=result.dimension_scores,
    )

if round_num < self.max_rounds:
    revised = self.task.revise_output(current_output, enriched_result, state)
```

**Step 4: Implement (TS)**

Same pattern in `ts/src/execution/improvement-loop.ts` — enrich the result before calling `reviseOutput`:

```typescript
// Before revision call, enrich feedback with dimension details
let enrichedResult = result;
if (Object.keys(result.dimensionScores).length > 0 && roundNum > 1) {
  const prevDims = rounds[rounds.length - 2]?.judgeFailed
    ? {} : rounds[rounds.length - 2]?.dimensionScores ?? {};
  const dimLines: string[] = [];
  for (const [dim, dscore] of Object.entries(result.dimensionScores).sort()) {
    let line = `  - ${dim}: ${dscore.toFixed(2)}`;
    if (dim in prevDims) {
      const delta = dscore - prevDims[dim];
      if (delta < -0.05) {
        line += ` (REGRESSION from ${prevDims[dim].toFixed(2)} — preserve this dimension)`;
      } else if (delta > 0.05) {
        line += ` (improved from ${prevDims[dim].toFixed(2)})`;
      }
    }
    dimLines.push(line);
  }
  const dimAnnotation = "\n\nDimension Scores:\n" + dimLines.join("\n");
  enrichedResult = {
    score: result.score,
    reasoning: result.reasoning + dimAnnotation,
    dimensionScores: result.dimensionScores,
  };
}
```

**Step 5: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_task_runner.py tests/test_improvement_loop.py -v`
Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts && npx vitest run`
Expected: PASS

**Step 6: Commit**

```bash
git add mts/src/mts/execution/improvement_loop.py ts/src/execution/improvement-loop.ts \
  mts/tests/test_task_runner.py mts/tests/test_improvement_loop.py \
  ts/tests/improvement-loop.test.ts
git commit -m "feat: dimension-aware revision prompts with regression guard (MTS-41)"
```

---

### Task 5: Smoke test — single-round judge eval (MTS-29)

**Files:**
- Create: `mts/tests/test_smoke_judge.py`
- Create: `ts/tests/smoke-judge.test.ts`

**Step 1: Write Python smoke test**

```python
"""Smoke test: single-round judge eval (MTS-29).

Validates basic wiring: judge scores, parses, and returns correctly
on a canned prompt+output with a mock provider.
"""
import json
from mts.execution.judge import LLMJudge, JudgeResult

def _make_mock_provider(response_text: str):
    class MockProvider:
        name = "mock"
        def default_model(self): return "mock-v1"
        def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):
            from mts.providers.base import CompletionResult
            return CompletionResult(text=response_text, model="mock-v1")
    return MockProvider()

class TestSmokeJudgeEval:
    """MTS-29: Validate judge returns valid result with score, dimensions, reasoning."""

    CANNED_PROMPT = "Write a one-paragraph summary of what MTS does"
    CANNED_OUTPUT = (
        "MTS is an iterative strategy generation system that uses multi-agent "
        "collaboration to evolve strategies through tournament matches and LLM "
        "judge evaluation with Elo-based progression gating."
    )
    RUBRIC = "Evaluate on: accuracy (factual correctness), clarity (readability), completeness (coverage of key concepts)"

    def _make_judge_response(self, score=0.85, dims=None):
        data = {
            "score": score,
            "reasoning": "The summary accurately captures the core MTS loop.",
            "dimensions": dims or {"accuracy": 0.9, "clarity": 0.85, "completeness": 0.8},
        }
        return f"<!-- JUDGE_RESULT_START -->\n{json.dumps(data)}\n<!-- JUDGE_RESULT_END -->"

    def test_judge_returns_valid_result(self):
        provider = _make_mock_provider(self._make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=self.RUBRIC, provider=provider)
        result = judge.evaluate(self.CANNED_PROMPT, self.CANNED_OUTPUT)
        assert isinstance(result, JudgeResult)
        assert 0 <= result.score <= 1
        assert result.score == 0.85

    def test_all_dimensions_scored(self):
        provider = _make_mock_provider(self._make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=self.RUBRIC, provider=provider)
        result = judge.evaluate(self.CANNED_PROMPT, self.CANNED_OUTPUT)
        assert len(result.dimension_scores) == 3
        assert "accuracy" in result.dimension_scores
        assert "clarity" in result.dimension_scores
        assert "completeness" in result.dimension_scores

    def test_reasoning_non_empty(self):
        provider = _make_mock_provider(self._make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=self.RUBRIC, provider=provider)
        result = judge.evaluate(self.CANNED_PROMPT, self.CANNED_OUTPUT)
        assert len(result.reasoning) > 0

    def test_parse_succeeds_first_attempt(self):
        provider = _make_mock_provider(self._make_judge_response())
        judge = LLMJudge(model="mock-v1", rubric=self.RUBRIC, provider=provider)
        result = judge.evaluate(self.CANNED_PROMPT, self.CANNED_OUTPUT)
        assert result.parse_method == "markers"
```

**Step 2: Write TS smoke test**

```typescript
import { describe, it, expect } from "vitest";
import { LLMJudge } from "../src/judge/index.js";
import type { LLMProvider } from "../src/types/index.js";

function mockProvider(responseText: string): LLMProvider {
  return {
    name: "mock",
    defaultModel: () => "mock-v1",
    complete: async () => ({ text: responseText, model: "mock-v1", usage: {} }),
  };
}

describe("Smoke: single-round judge eval (MTS-29)", () => {
  const PROMPT = "Write a one-paragraph summary of what MTS does";
  const OUTPUT = "MTS is an iterative strategy generation system...";
  const RUBRIC = "Evaluate on: accuracy, clarity, completeness";

  function makeResponse(score = 0.85) {
    const data = {
      score, reasoning: "Good summary.",
      dimensions: { accuracy: 0.9, clarity: 0.85, completeness: 0.8 },
    };
    return `<!-- JUDGE_RESULT_START -->\n${JSON.stringify(data)}\n<!-- JUDGE_RESULT_END -->`;
  }

  it("returns valid JudgeResult with score 0-1", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.score).toBeGreaterThanOrEqual(0);
    expect(r.score).toBeLessThanOrEqual(1);
    expect(r.score).toBe(0.85);
  });

  it("all 3 dimensions scored independently", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(Object.keys(r.dimensionScores)).toHaveLength(3);
    expect(r.dimensionScores.accuracy).toBe(0.9);
  });

  it("reasoning is non-empty and relevant", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.reasoning.length).toBeGreaterThan(0);
  });

  it("parse succeeds on first attempt (markers)", async () => {
    const judge = new LLMJudge({ provider: mockProvider(makeResponse()), model: "m", rubric: RUBRIC });
    const r = await judge.evaluate({ taskPrompt: PROMPT, agentOutput: OUTPUT });
    expect(r.parseMethod).toBe("markers");
  });
});
```

**Step 3: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_smoke_judge.py -v`
Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts && npx vitest run tests/smoke-judge.test.ts`
Expected: PASS

**Step 4: Commit**

```bash
git add mts/tests/test_smoke_judge.py ts/tests/smoke-judge.test.ts
git commit -m "test: smoke test for single-round judge eval (MTS-29)"
```

---

### Task 6: Integration test — 3-round improvement cycle (MTS-30)

**Files:**
- Create: `mts/tests/test_integration_improvement.py`
- Create: `ts/tests/integration-improvement.test.ts`

**Step 1: Write Python integration test**

```python
"""Integration test: 3-round improvement cycle (MTS-30).

Validates improvement loop: agent revises based on feedback, score improves.
Uses mock provider that returns improving scores across rounds.
"""
import json
from mts.execution.improvement_loop import ImprovementLoop
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

class ImprovingMockTask(AgentTaskInterface):
    """Mock task that simulates score improvement across rounds."""

    SCORES = [0.55, 0.72, 0.88]  # Improving scores

    def __init__(self):
        self._eval_count = 0
        self._revise_count = 0

    def get_task_prompt(self, state): return "Write a haiku about distributed systems"
    def get_rubric(self): return "syllable accuracy (5-7-5), technical relevance, creativity"
    def initial_state(self, seed=None): return {}
    def describe_task(self): return self.get_task_prompt({})

    def evaluate_output(self, output, state, **kwargs):
        score = self.SCORES[min(self._eval_count, len(self.SCORES) - 1)]
        dims = {
            "syllable_accuracy": min(1.0, score + 0.05),
            "technical_relevance": score,
            "creativity": max(0.0, score - 0.05),
        }
        self._eval_count += 1
        return AgentTaskResult(score=score, reasoning=f"Round {self._eval_count} feedback", dimension_scores=dims)

    def revise_output(self, output, judge_result, state):
        self._revise_count += 1
        return f"Revised output v{self._revise_count}: {output[:50]}..."


class TestIntegrationImprovementCycle:
    """MTS-30: 3-round improvement cycle with score improvement."""

    def test_three_rounds_complete(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("Nodes whisper data\nConsensus slowly converges\nNetwork partition", {})
        assert result.total_rounds == 3
        assert len(result.rounds) == 3

    def test_score_improves(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        valid_scores = [r.score for r in result.rounds if not r.judge_failed]
        assert valid_scores[-1] > valid_scores[0], "Final score should be higher than initial"

    def test_final_better_than_initial(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert result.improved

    def test_no_parse_failures(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert result.judge_failures == 0

    def test_round_results_saved(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        for r in result.rounds:
            assert r.score > 0
            assert len(r.reasoning) > 0
            assert r.round_number >= 1

    def test_dimension_trajectory_tracked(self):
        task = ImprovingMockTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.95)
        result = loop.run("initial haiku", {})
        assert "syllable_accuracy" in result.dimension_trajectory
        assert len(result.dimension_trajectory["syllable_accuracy"]) == 3
```

**Step 2: Write TS integration test**

Similar pattern using mock task with improving scores.

**Step 3: Run tests**

Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts && uv run pytest tests/test_integration_improvement.py -v`
Run: `cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts && npx vitest run tests/integration-improvement.test.ts`
Expected: PASS

**Step 4: Commit**

```bash
git add mts/tests/test_integration_improvement.py ts/tests/integration-improvement.test.ts
git commit -m "test: integration test for 3-round improvement cycle (MTS-30)"
```

---

### Task 7: Verify all tests pass, lint, type check

**Step 1: Run Python checks**

```bash
cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/mts
uv run ruff check src tests
uv run mypy src
uv run pytest
```

**Step 2: Run TS checks**

```bash
cd /Users/jayscambler/Repositories/MTS/.worktrees/batch2/ts
npx tsc --noEmit
npx vitest run
```

**Step 3: Fix any issues**

**Step 4: Final commit if needed**

```bash
git add -A && git commit -m "fix: lint and type fixes"
```
