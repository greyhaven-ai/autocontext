# Phase 3: Rewire MTS to Compose Harness Primitives

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewire MTS's orchestrator, tournament runner, and artifact store to delegate to Phase 2 harness abstractions — making MTS the first consumer of its own configurable infrastructure, without changing any externally observable behavior.

**Architecture:** Thin adapter pattern. Each MTS module acquires a harness counterpart it delegates to, while preserving its existing public API. The `AgentOrchestrator` gains an optional `PipelineEngine` codepath. `TournamentRunner` gains an `Evaluator` adapter. `ArtifactStore` playbook methods delegate to `VersionedFileStore`. All changes are additive — original codepaths remain as fallbacks.

**Tech Stack:** Python 3.11+, pytest, harness primitives from Phase 2 (`PipelineEngine`, `RoleDAG`, `EvaluationRunner`, `Evaluator`, `VersionedFileStore`, `extract_json`, `extract_delimited_section`)

---

## Phase 2 Recap (What We Have)

```
mts/src/mts/harness/
  core/output_parser.py          # strip_json_fences, extract_json, extract_tagged_content, extract_delimited_section
  scoring/elo.py                 # expected_score, update_elo (execution/elo.py re-exports)
  storage/versioned_store.py     # VersionedFileStore — write/archive/prune/rollback
  evaluation/types.py            # EvaluationLimits, EvaluationResult, EvaluationSummary
  evaluation/protocol.py         # Evaluator protocol
  evaluation/runner.py           # EvaluationRunner — N-trial evaluation with Elo scoring
  orchestration/types.py         # RoleSpec, PipelineConfig
  orchestration/dag.py           # RoleDAG — topo sort, cycle detection, parallel batches
  orchestration/engine.py        # PipelineEngine — DAG-ordered execution
```

**Test count:** 517 passing (447 original + 70 harness).

## Import Graph (files Phase 3 touches)

```
generation_runner.py
  ├── AgentOrchestrator      (agents/orchestrator.py, re-exported via agents/__init__.py)
  ├── TournamentRunner       (execution/tournament.py, re-exported via execution/__init__.py)
  ├── ArtifactStore          (storage/artifacts.py, re-exported via storage/__init__.py)
  └── BackpressureGate       (backpressure/gate.py)

AgentOrchestrator.run_generation() → AgentOutputs
  (used by: generation_runner.py, test_orchestrator_feedback.py, test_agent_sdk_integration.py)

TournamentRunner.run() → TournamentSummary
  (used by: generation_runner.py only — accessed via self.tournament)

ArtifactStore.write_playbook / rollback_playbook / read_playbook
  (used by: generation_runner.py, mcp/tools.py, mcp/sandbox.py, various tests)
```

## Risk Model

| Change | Risk | Mitigation |
|--------|------|------------|
| ScenarioEvaluator adapter | Low — new class, no existing code modified | Adapter wraps existing supervisor.run() |
| ArtifactStore delegates to VersionedFileStore | Medium — touches 6 playbook methods | Keep exact same file layout, test byte-for-byte equivalence |
| PipelineEngine codepath in orchestrator | Medium — parallel execution timing | Feature-gated behind `use_pipeline_engine` flag, off by default |
| Output parser adoption in coach/architect | Low — pure function replacement | Side-by-side tests showing identical results |

## TDD Batches

### Batch 3.1: ScenarioEvaluator Adapter

**Scope**: Bridge MTS's `ScenarioInterface` + `ExecutionSupervisor` to the harness `Evaluator` protocol. New file, zero modifications to existing code.

**Why first**: Zero dependencies on other Phase 3 work. Pure adapter.

**Files:**
- Create: `mts/src/mts/harness/evaluation/scenario_evaluator.py`
- Test: `mts/tests/test_harness/test_harness_scenario_evaluator.py`

**Step 1: Write failing tests**

```python
# tests/test_harness/test_harness_scenario_evaluator.py
"""Tests for ScenarioEvaluator — adapter bridging ScenarioInterface to Evaluator protocol."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest

from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult


# --- Minimal fakes matching ScenarioInterface + ExecutionSupervisor contracts ---

class FakeResult:
    def __init__(self, score: float, errors: list[str] | None = None) -> None:
        self.score = score
        self.summary = "test"
        self.replay = []
        self.metrics: dict[str, float] = {"score": score}
        self.validation_errors = errors or []
        self.passed_validation = len(self.validation_errors) == 0


class FakeReplay:
    def __init__(self) -> None:
        self.scenario = "test"
        self.seed = 0
        self.narrative = "replay"
        self.timeline: list[dict[str, Any]] = []

    def model_dump(self) -> dict[str, Any]:
        return {"scenario": self.scenario, "seed": self.seed}


@dataclass
class FakeExecutionInput:
    strategy: Mapping[str, object]
    seed: int
    limits: Any


@dataclass
class FakeExecutionOutput:
    result: FakeResult
    replay: FakeReplay


class FakeScenario:
    name = "test_scenario"

    def execute_match(self, strategy: Mapping[str, Any], seed: int) -> FakeResult:
        score = strategy.get("score", 0.5)
        return FakeResult(score=float(score))


class FakeSupervisor:
    def __init__(self, score: float = 0.75) -> None:
        self._score = score
        self.calls: list[tuple[Any, Any]] = []

    def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
        self.calls.append((scenario, payload))
        return FakeExecutionOutput(
            result=FakeResult(score=self._score),
            replay=FakeReplay(),
        )


class TestScenarioEvaluator:
    def test_implements_evaluator_protocol(self) -> None:
        """ScenarioEvaluator is structurally compatible with Evaluator protocol."""
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor())
        assert hasattr(evaluator, "evaluate")

    def test_evaluate_returns_evaluation_result(self) -> None:
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.8))
        result = evaluator.evaluate({"score": 0.8}, seed=42, limits=EvaluationLimits())
        assert isinstance(result, EvaluationResult)
        assert result.score == 0.8

    def test_evaluate_passes_strategy_and_seed(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(FakeScenario(), supervisor)
        evaluator.evaluate({"score": 0.5}, seed=99, limits=EvaluationLimits())
        assert len(supervisor.calls) == 1
        _, payload = supervisor.calls[0]
        assert payload.seed == 99

    def test_evaluate_maps_limits(self) -> None:
        supervisor = FakeSupervisor()
        evaluator = ScenarioEvaluator(FakeScenario(), supervisor)
        limits = EvaluationLimits(timeout_seconds=30.0, max_memory_mb=1024)
        evaluator.evaluate({}, seed=1, limits=limits)
        _, payload = supervisor.calls[0]
        assert payload.limits.timeout_seconds == 30.0
        assert payload.limits.max_memory_mb == 1024

    def test_evaluate_captures_errors(self) -> None:
        """Validation errors from the result propagate to EvaluationResult.errors."""
        class ErrorSupervisor:
            def run(self, scenario: Any, payload: Any) -> FakeExecutionOutput:
                return FakeExecutionOutput(
                    result=FakeResult(score=0.0, errors=["invalid param"]),
                    replay=FakeReplay(),
                )

        evaluator = ScenarioEvaluator(FakeScenario(), ErrorSupervisor())
        result = evaluator.evaluate({}, seed=1, limits=EvaluationLimits())
        assert result.errors == ["invalid param"]
        assert result.passed is False

    def test_evaluate_captures_replay_data(self) -> None:
        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor())
        result = evaluator.evaluate({}, seed=1, limits=EvaluationLimits())
        assert "scenario" in result.replay_data

    def test_works_with_evaluation_runner(self) -> None:
        """ScenarioEvaluator integrates with EvaluationRunner end-to-end."""
        from mts.harness.evaluation.runner import EvaluationRunner

        evaluator = ScenarioEvaluator(FakeScenario(), FakeSupervisor(score=0.7))
        runner = EvaluationRunner(evaluator=evaluator)
        summary = runner.run(
            candidate={"score": 0.7},
            seed_base=0,
            trials=3,
            limits=EvaluationLimits(),
            challenger_elo=1000.0,
        )
        assert summary.mean_score == pytest.approx(0.7)
        assert len(summary.results) == 3
```

**Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_harness/test_harness_scenario_evaluator.py -v`
Expected: `ModuleNotFoundError: No module named 'mts.harness.evaluation.scenario_evaluator'`

**Step 3: Implement ScenarioEvaluator**

```python
# src/mts/harness/evaluation/scenario_evaluator.py
"""ScenarioEvaluator — adapter bridging MTS ScenarioInterface to harness Evaluator protocol."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mts.harness.evaluation.types import EvaluationLimits, EvaluationResult


class ScenarioEvaluator:
    """Adapts a ScenarioInterface + ExecutionSupervisor to the Evaluator protocol.

    Uses duck typing — accepts any object with the right method signatures.
    This avoids importing MTS-domain types into the harness layer.
    """

    def __init__(self, scenario: Any, supervisor: Any) -> None:
        self._scenario = scenario
        self._supervisor = supervisor

    def evaluate(
        self,
        candidate: Mapping[str, Any],
        seed: int,
        limits: EvaluationLimits,
    ) -> EvaluationResult:
        from mts.scenarios.base import ExecutionLimits as MtsLimits
        from mts.execution.supervisor import ExecutionInput

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
            metadata=dict(output.result.metrics) if hasattr(output.result, "metrics") else {},
            replay_data=output.replay.model_dump() if hasattr(output.replay, "model_dump") else {},
        )
```

Note: This adapter does import from `mts.*` outside harness because it IS the bridge between harness and MTS. The imports are deferred inside the method to keep module-level clean. However, since this adapter lives in `mts.harness.evaluation` and serves as the explicit bridge, the import is justified. If we want strict purity, we can move it to `mts/adapters/` instead — **ask the user at step time**.

**Alternative: Duck-typed adapter (no MTS imports)**

```python
class ScenarioEvaluator:
    """Duck-typed adapter — works with any supervisor that has .run(scenario, payload) -> output."""

    def __init__(self, scenario: Any, supervisor: Any, limits_factory: Any = None, input_factory: Any = None) -> None:
        self._scenario = scenario
        self._supervisor = supervisor
        self._limits_factory = limits_factory
        self._input_factory = input_factory

    def evaluate(self, candidate: Mapping[str, Any], seed: int, limits: EvaluationLimits) -> EvaluationResult:
        # Build payload using injected factories
        mts_limits = self._limits_factory(limits) if self._limits_factory else limits
        payload = self._input_factory(candidate, seed, mts_limits) if self._input_factory else ...
```

**Decision**: Use the deferred-import approach. It's simpler, keeps the test fakes clean, and the adapter is explicitly a bridge module.

**Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests/test_harness/test_harness_scenario_evaluator.py -v`
Expected: All 7 tests PASS

**Step 5: Full suite verification**

Run: `uv run pytest -q`
Expected: 524+ passed (517 + 7 new)

**Step 6: Lint and type check**

Run: `uv run ruff check src tests && uv run mypy src`

**Step 7: Commit**

```bash
git add src/mts/harness/evaluation/scenario_evaluator.py tests/test_harness/test_harness_scenario_evaluator.py
git commit -m "feat(harness): add ScenarioEvaluator adapter bridging ScenarioInterface to Evaluator protocol"
```

---

### Batch 3.2: ArtifactStore Delegates Playbook Methods to VersionedFileStore

**Scope**: Rewire `ArtifactStore`'s 6 playbook methods (`write_playbook`, `rollback_playbook`, `_prune_playbook_versions`, `read_playbook`, `playbook_version_count`, `read_playbook_version`) to delegate to a `VersionedFileStore` instance internally.

**Why second**: Independent of orchestrator changes. The `VersionedFileStore` uses a slightly different naming convention (`.versions/name/v0001.txt`) vs ArtifactStore's current convention (`playbook_versions/playbook_v0001.md`). We must preserve the EXACT current file layout for backward compatibility with existing runs. Two approaches:

**Approach A (Selected)**: Subclass/configure `VersionedFileStore` to use the existing `playbook_versions/playbook_v*.md` naming convention.

**Approach B**: Keep current ArtifactStore methods as-is, only adopt VersionedFileStore for NEW storage needs. This avoids any risk but doesn't demonstrate the delegation pattern.

We go with **Approach A** — but with a safety net: the adapter configures `VersionedFileStore` to match the existing naming, and we write byte-for-byte equivalence tests.

**Files:**
- Modify: `mts/src/mts/harness/storage/versioned_store.py` (add naming customization)
- Modify: `mts/src/mts/storage/artifacts.py:47-102` (delegate playbook methods)
- Test: `mts/tests/test_harness/test_harness_versioned_store.py` (add naming tests)
- Test: `mts/tests/test_playbook_versioning.py` (existing — must still pass)

**Step 1: Write failing tests for customizable naming**

Add to `test_harness_versioned_store.py`:

```python
class TestVersionedFileStoreCustomNaming:
    def test_custom_prefix_and_suffix(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=3,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        store.write("playbook.md", "v1")
        store.write("playbook.md", "v2")
        versions_dir = tmp_path / "playbook_versions"
        assert versions_dir.exists()
        assert (versions_dir / "playbook_v0001.md").exists()
        assert (versions_dir / "playbook_v0001.md").read_text() == "v1"

    def test_custom_naming_rollback(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=3,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        store.write("playbook.md", "v1")
        store.write("playbook.md", "v2")
        assert store.rollback("playbook.md") is True
        assert store.read("playbook.md") == "v1"

    def test_custom_naming_prune(self, tmp_path: Path) -> None:
        store = VersionedFileStore(
            root=tmp_path,
            max_versions=2,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
        for i in range(1, 5):
            store.write("playbook.md", f"v{i}")
        assert store.version_count("playbook.md") == 2
```

**Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_harness/test_harness_versioned_store.py::TestVersionedFileStoreCustomNaming -v`
Expected: FAIL — `VersionedFileStore.__init__() got unexpected keyword arguments`

**Step 3: Update VersionedFileStore with customizable naming**

Modify `mts/src/mts/harness/storage/versioned_store.py`:

```python
class VersionedFileStore:
    """Manages versioned text files with automatic archiving."""

    def __init__(
        self,
        root: Path,
        max_versions: int = 5,
        versions_dir_name: str = ".versions",
        version_prefix: str = "v",
        version_suffix: str = ".txt",
    ) -> None:
        self._root = root
        self._max_versions = max_versions
        self._versions_dir_name = versions_dir_name
        self._version_prefix = version_prefix
        self._version_suffix = version_suffix

    def _versions_dir(self, name: str) -> Path:
        if self._versions_dir_name == ".versions":
            return self._root / f".versions/{name}"
        return self._root / self._versions_dir_name

    def _version_glob(self) -> str:
        return f"{self._version_prefix}*{self._version_suffix}"

    def _version_path(self, versions_dir: Path, num: int) -> Path:
        return versions_dir / f"{self._version_prefix}{num:04d}{self._version_suffix}"

    # ... write/read/rollback/version_count/read_version/_prune all updated
    # to use self._versions_dir(), self._version_glob(), self._version_path()
```

**Step 4: Run tests to verify GREEN**

Run: `uv run pytest tests/test_harness/test_harness_versioned_store.py -v`
Expected: All tests PASS (original 11 + 3 new)

**Step 5: Write failing test for ArtifactStore delegation**

Add a new test file `tests/test_playbook_delegation.py`:

```python
"""Tests verifying ArtifactStore playbook methods delegate to VersionedFileStore."""
from __future__ import annotations

from pathlib import Path

import pytest

from mts.storage.artifacts import ArtifactStore


@pytest.fixture()
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        max_playbook_versions=3,
    )


class TestPlaybookDelegation:
    def test_write_playbook_creates_file(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "content v1")
        assert store.read_playbook("grid_ctf") != "No playbook yet. Start from scenario rules and observation."
        content = store.read_playbook("grid_ctf")
        assert "content v1" in content

    def test_write_archives_previous(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        assert store.playbook_version_count("grid_ctf") == 1

    def test_rollback_restores(self, store: ArtifactStore) -> None:
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        assert store.rollback_playbook("grid_ctf") is True
        assert "v1" in store.read_playbook("grid_ctf")

    def test_version_file_layout_matches_legacy(self, store: ArtifactStore, tmp_path: Path) -> None:
        """Verify the versioned files use the legacy playbook_versions/playbook_v*.md naming."""
        store.write_playbook("grid_ctf", "v1")
        store.write_playbook("grid_ctf", "v2")
        versions_dir = tmp_path / "knowledge" / "grid_ctf" / "playbook_versions"
        assert versions_dir.exists(), "Should use playbook_versions/ directory"
        files = list(versions_dir.glob("playbook_v*.md"))
        assert len(files) == 1
        assert files[0].name == "playbook_v0001.md"

    def test_uses_versioned_file_store_internally(self, store: ArtifactStore) -> None:
        """ArtifactStore should have a _playbook_store attribute after Phase 3 delegation."""
        from mts.harness.storage.versioned_store import VersionedFileStore
        assert hasattr(store, "_playbook_store")
        assert isinstance(store._playbook_store, VersionedFileStore)
```

**Step 6: Run to verify RED**

Run: `uv run pytest tests/test_playbook_delegation.py -v`
Expected: FAIL on `test_uses_versioned_file_store_internally` (no `_playbook_store` attribute yet)

**Step 7: Modify ArtifactStore to delegate**

Modify `mts/src/mts/storage/artifacts.py`:

In `__init__`, add:
```python
from mts.harness.storage.versioned_store import VersionedFileStore
# ... existing init code ...
# Per-scenario VersionedFileStore instances created lazily
self._playbook_stores: dict[str, VersionedFileStore] = {}
```

Add helper:
```python
def _playbook_store(self, scenario_name: str) -> VersionedFileStore:
    if scenario_name not in self._playbook_stores:
        from mts.harness.storage.versioned_store import VersionedFileStore
        self._playbook_stores[scenario_name] = VersionedFileStore(
            root=self.knowledge_root / scenario_name,
            max_versions=self._max_playbook_versions,
            versions_dir_name="playbook_versions",
            version_prefix="playbook_v",
            version_suffix=".md",
        )
    return self._playbook_stores[scenario_name]
```

Rewrite playbook methods to delegate:
```python
def read_playbook(self, scenario_name: str) -> str:
    content = self._playbook_store(scenario_name).read("playbook.md")
    return content if content else "No playbook yet. Start from scenario rules and observation."

def write_playbook(self, scenario_name: str, content: str) -> None:
    self._playbook_store(scenario_name).write("playbook.md", content.strip() + "\n")

def _prune_playbook_versions(self, versions_dir: Path) -> None:
    # Now handled by VersionedFileStore internally — kept for backward compat if called directly
    pass

def rollback_playbook(self, scenario_name: str) -> bool:
    return self._playbook_store(scenario_name).rollback("playbook.md")

def playbook_version_count(self, scenario_name: str) -> int:
    return self._playbook_store(scenario_name).version_count("playbook.md")

def read_playbook_version(self, scenario_name: str, version_num: int) -> str:
    return self._playbook_store(scenario_name).read_version("playbook.md", version_num)
```

**Step 8: Run tests to verify GREEN**

Run: `uv run pytest tests/test_playbook_delegation.py tests/test_playbook_versioning.py -v`
Expected: All pass

**Step 9: Full suite**

Run: `uv run pytest -q`
Expected: All pass

**Step 10: Lint + types**

Run: `uv run ruff check src tests && uv run mypy src`

**Step 11: Commit**

```bash
git add src/mts/harness/storage/versioned_store.py src/mts/storage/artifacts.py \
        tests/test_harness/test_harness_versioned_store.py tests/test_playbook_delegation.py
git commit -m "refactor: delegate ArtifactStore playbook methods to VersionedFileStore"
```

---

### Batch 3.3: Output Parser Adoption in Coach + Translator

**Scope**: Replace inline regex/parsing in `agents/coach.py::parse_coach_sections()` and `agents/translator.py::_strip_fences()` with calls to `harness.core.output_parser` functions.

**Why third**: Independent of Batches 3.1/3.2. Pure substitution — same behavior, cleaner code.

**Files:**
- Modify: `mts/src/mts/agents/coach.py` (`parse_coach_sections` function)
- Modify: `mts/src/mts/agents/translator.py` (`_strip_fences` static method)
- Test: `mts/tests/test_harness/test_output_parser_adoption.py` (equivalence tests)
- Existing: `mts/tests/test_strategy_translator.py` (must still pass)
- Existing: `mts/tests/test_playbook_versioning.py` (must still pass — tests coach parsing)

**Step 1: Read current implementations**

Read: `src/mts/agents/coach.py` — find `parse_coach_sections()` function
Read: `src/mts/agents/translator.py` — find `_strip_fences()` static method

**Step 2: Write equivalence tests**

```python
# tests/test_harness/test_output_parser_adoption.py
"""Equivalence tests: output_parser functions match existing inline parsing."""
from __future__ import annotations

from mts.harness.core.output_parser import extract_delimited_section, extract_json, strip_json_fences


class TestCoachParsingEquivalence:
    def test_playbook_extraction_matches(self) -> None:
        text = (
            "Some preamble\n"
            "<!-- PLAYBOOK_START -->\n"
            "Strategy: balanced offense\n"
            "<!-- PLAYBOOK_END -->\n"
            "<!-- LESSONS_START -->\n"
            "- Lesson 1\n"
            "<!-- LESSONS_END -->\n"
        )
        playbook = extract_delimited_section(text, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->")
        lessons = extract_delimited_section(text, "<!-- LESSONS_START -->", "<!-- LESSONS_END -->")
        assert playbook == "Strategy: balanced offense"
        assert lessons == "- Lesson 1"

    def test_hints_extraction(self) -> None:
        text = (
            "Coach output\n"
            "<!-- COMPETITOR_HINTS_START -->\n"
            "Try higher aggression\n"
            "<!-- COMPETITOR_HINTS_END -->\n"
        )
        hints = extract_delimited_section(text, "<!-- COMPETITOR_HINTS_START -->", "<!-- COMPETITOR_HINTS_END -->")
        assert hints == "Try higher aggression"

    def test_missing_section_returns_none(self) -> None:
        text = "No markers here"
        assert extract_delimited_section(text, "<!-- PLAYBOOK_START -->", "<!-- PLAYBOOK_END -->") is None


class TestTranslatorParsingEquivalence:
    def test_strip_fences_json_tag(self) -> None:
        text = '```json\n{"aggression": 0.8}\n```'
        assert strip_json_fences(text) == '{"aggression": 0.8}'

    def test_strip_fences_no_tag(self) -> None:
        text = '```\n{"aggression": 0.8}\n```'
        assert strip_json_fences(text) == '{"aggression": 0.8}'

    def test_strip_fences_passthrough(self) -> None:
        text = '{"aggression": 0.8}'
        assert strip_json_fences(text) == '{"aggression": 0.8}'

    def test_extract_json_full_pipeline(self) -> None:
        text = 'Here is the strategy:\n```json\n{"aggression": 0.8, "defense": 0.3}\n```'
        result = extract_json(text)
        assert result == {"aggression": 0.8, "defense": 0.3}
```

**Step 3: Run equivalence tests to verify they pass** (these test harness functions we already built)

Run: `uv run pytest tests/test_harness/test_output_parser_adoption.py -v`
Expected: All PASS (these test the harness side which already works)

**Step 4: Modify coach.py — replace `parse_coach_sections()` inline parsing**

Read the current `parse_coach_sections()` and replace the manual `text.find()` / string slicing with `extract_delimited_section()` calls. The function signature and return type stay identical.

**Step 5: Modify translator.py — replace `_strip_fences()` with output_parser import**

Replace the `_strip_fences` static method body with a delegation to `strip_json_fences()`.

**Step 6: Run existing tests to verify no regressions**

Run: `uv run pytest tests/test_strategy_translator.py tests/test_playbook_versioning.py tests/test_orchestrator_feedback.py -v`
Expected: All PASS

**Step 7: Full suite**

Run: `uv run pytest -q`

**Step 8: Lint + types**

Run: `uv run ruff check src tests && uv run mypy src`

**Step 9: Commit**

```bash
git add src/mts/agents/coach.py src/mts/agents/translator.py \
        tests/test_harness/test_output_parser_adoption.py
git commit -m "refactor: adopt harness output_parser in coach and translator"
```

---

### Batch 3.4: PipelineEngine-Backed Orchestrator (Feature-Gated)

**Scope**: Add an optional `PipelineEngine` codepath to `AgentOrchestrator` that expresses the existing 5-role DAG declaratively. Controlled by a flag — when off (default), the existing hardcoded logic runs unchanged. When on, the same behavior is achieved via `PipelineEngine`.

**Why last**: Depends on all prior batches. Highest complexity. Feature-gated for safety.

**Files:**
- Create: `mts/src/mts/agents/pipeline_adapter.py` (builds RoleDAG + RoleHandler from existing runners)
- Modify: `mts/src/mts/agents/orchestrator.py` (add optional pipeline codepath)
- Modify: `mts/src/mts/config/settings.py` (add `use_pipeline_engine: bool = False`)
- Test: `mts/tests/test_pipeline_adapter.py`

**Step 1: Write failing tests**

```python
# tests/test_pipeline_adapter.py
"""Tests for PipelineEngine-backed orchestrator codepath."""
from __future__ import annotations

from mts.agents.llm_client import DeterministicDevClient
from mts.agents.orchestrator import AgentOrchestrator
from mts.agents.pipeline_adapter import build_mts_dag, build_role_handler
from mts.config.settings import AppSettings
from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.engine import PipelineEngine
from mts.harness.orchestration.types import RoleSpec
from mts.prompts.templates import PromptBundle


def _make_settings(use_pipeline: bool = False) -> AppSettings:
    return AppSettings(agent_provider="deterministic", use_pipeline_engine=use_pipeline)


def _make_prompt_bundle() -> PromptBundle:
    base = (
        "Scenario rules:\nTest\n\nStrategy interface:\n{}\n\n"
        "Evaluation criteria:\nScore\n\nObservation narrative:\nTest\n\n"
        "Observation state:\n{}\n\nConstraints:\nNone\n\n"
        "Current playbook:\nNone\n\nAvailable tools:\nNone\n\n"
        "Previous generation summary:\nNone\n"
    )
    return PromptBundle(
        competitor=base + "Describe your strategy.",
        analyst=base + "Analyze strengths/failures. Findings, Root Causes, Actionable Recommendations.",
        coach=base + "You are the playbook coach. <!-- PLAYBOOK_START -->\nplaybook\n<!-- PLAYBOOK_END -->",
        architect=base + "Propose tooling.",
    )


class TestBuildMtsDag:
    def test_dag_has_five_roles(self) -> None:
        dag = build_mts_dag()
        assert len(dag.roles) == 5

    def test_dag_batch_order(self) -> None:
        dag = build_mts_dag()
        batches = dag.execution_batches()
        assert batches[0] == ["competitor"]
        assert batches[1] == ["translator"]
        assert "analyst" in batches[2]
        assert "architect" in batches[2]
        # Coach depends on analyst, comes after
        assert "coach" in batches[3]

    def test_dag_validates(self) -> None:
        dag = build_mts_dag()
        dag.validate()  # Should not raise


class TestBuildRoleHandler:
    def test_handler_returns_role_execution(self) -> None:
        client = DeterministicDevClient()
        settings = _make_settings()
        orch = AgentOrchestrator(client=client, settings=settings)
        handler = build_role_handler(orch)
        from mts.harness.core.types import RoleExecution
        result = handler("competitor", "Describe your strategy.", {})
        assert isinstance(result, RoleExecution)
        assert result.role == "competitor"


class TestPipelineOrchestratorIntegration:
    def test_pipeline_produces_same_roles_as_direct(self) -> None:
        """Pipeline codepath produces AgentOutputs with all 5 role executions."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        assert len(outputs.role_executions) == 5
        roles = {e.role for e in outputs.role_executions}
        assert roles == {"competitor", "translator", "analyst", "coach", "architect"}

    def test_pipeline_backward_compatible(self) -> None:
        """Pipeline path produces valid AgentOutputs with all required fields."""
        client = DeterministicDevClient()
        settings = _make_settings(use_pipeline=True)
        orch = AgentOrchestrator(client=client, settings=settings)
        prompts = _make_prompt_bundle()
        outputs = orch.run_generation(prompts, generation_index=1)
        assert isinstance(outputs.strategy, dict)
        assert outputs.analysis_markdown
        assert outputs.coach_markdown
        assert outputs.architect_markdown

    def test_direct_and_pipeline_produce_equivalent_output(self) -> None:
        """With deterministic client, both codepaths produce equivalent results."""
        prompts = _make_prompt_bundle()

        client_a = DeterministicDevClient()
        orch_a = AgentOrchestrator(client=client_a, settings=_make_settings(use_pipeline=False))
        outputs_a = orch_a.run_generation(prompts, generation_index=1)

        client_b = DeterministicDevClient()
        orch_b = AgentOrchestrator(client=client_b, settings=_make_settings(use_pipeline=True))
        outputs_b = orch_b.run_generation(prompts, generation_index=1)

        assert outputs_a.strategy == outputs_b.strategy
        assert len(outputs_a.role_executions) == len(outputs_b.role_executions)
```

**Step 2: Run tests to verify RED**

Run: `uv run pytest tests/test_pipeline_adapter.py -v`
Expected: FAIL — `No module named 'mts.agents.pipeline_adapter'`

**Step 3: Add setting**

In `mts/src/mts/config/settings.py`, add to `AppSettings`:
```python
use_pipeline_engine: bool = False
```

**Step 4: Create pipeline_adapter.py**

```python
# src/mts/agents/pipeline_adapter.py
"""Adapter building a harness PipelineEngine from MTS orchestrator components."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mts.agents.architect import parse_architect_tool_specs
from mts.agents.coach import parse_coach_sections
from mts.harness.core.types import RoleExecution
from mts.harness.orchestration.dag import RoleDAG
from mts.harness.orchestration.types import RoleSpec

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator


def build_mts_dag() -> RoleDAG:
    """Build the standard MTS 5-role DAG."""
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])


def build_role_handler(orch: AgentOrchestrator) -> Any:
    """Build a RoleHandler callable that delegates to the orchestrator's role runners.

    Returns a callable matching: (name, prompt, completed) -> RoleExecution
    """

    def handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
        if name == "competitor":
            _raw_text, exec_result = orch.competitor.run(prompt, tool_context="")
            return exec_result
        elif name == "translator":
            competitor_exec = completed.get("competitor")
            raw_text = competitor_exec.content if competitor_exec else ""
            _strategy, exec_result = orch.translator.translate(raw_text, "")
            return exec_result
        elif name == "analyst":
            return orch.analyst.run(prompt)
        elif name == "architect":
            return orch.architect.run(prompt)
        elif name == "coach":
            analyst_exec = completed.get("analyst")
            enriched = prompt
            if analyst_exec:
                enriched = orch._enrich_coach_prompt(prompt, analyst_exec.content)
            return orch.coach.run(enriched)
        else:
            raise ValueError(f"Unknown role: {name}")

    return handler
```

**Step 5: Modify AgentOrchestrator.run_generation()**

Add pipeline codepath at the top of `run_generation()`:

```python
def run_generation(self, prompts, generation_index, ...):
    if self.settings.use_pipeline_engine and not (self.settings.rlm_enabled and self._rlm_loader is not None):
        return self._run_via_pipeline(prompts, generation_index, tool_context, on_role_event)
    # ... existing code unchanged ...
```

Add `_run_via_pipeline()` method:

```python
def _run_via_pipeline(self, prompts, generation_index, tool_context, on_role_event):
    from mts.agents.pipeline_adapter import build_mts_dag, build_role_handler
    from mts.harness.orchestration.engine import PipelineEngine

    dag = build_mts_dag()

    architect_prompt = prompts.architect
    if generation_index % self.settings.architect_every_n_gens != 0:
        architect_prompt += "\n\nArchitect cadence note: no major intervention; return minimal status + empty tools array."

    prompt_map = {
        "competitor": prompts.competitor,
        "translator": "",  # translator uses competitor output, not a prompt
        "analyst": prompts.analyst,
        "architect": architect_prompt,
        "coach": prompts.coach,
    }

    handler = build_role_handler(self)
    # Inject tool_context into handler for competitor
    base_handler = handler
    def handler_with_context(name, prompt, completed):
        if name == "competitor":
            _raw_text, exec_result = self.competitor.run(prompt, tool_context=tool_context)
            return exec_result
        return base_handler(name, prompt, completed)

    engine = PipelineEngine(dag, handler_with_context, max_workers=2)
    results = engine.execute(prompt_map, on_role_event=on_role_event)

    # Extract strategy from translator
    translator_exec = results["translator"]
    import json
    try:
        strategy = json.loads(translator_exec.content)
    except (json.JSONDecodeError, TypeError):
        from mts.agents.translator import StrategyTranslator
        strategy = StrategyTranslator._parse_strategy(translator_exec.content)

    tools = parse_architect_tool_specs(results["architect"].content)
    coach_playbook, coach_lessons, coach_hints = parse_coach_sections(results["coach"].content)

    return AgentOutputs(
        strategy=strategy,
        analysis_markdown=results["analyst"].content,
        coach_markdown=results["coach"].content,
        coach_playbook=coach_playbook,
        coach_lessons=coach_lessons,
        coach_competitor_hints=coach_hints,
        architect_markdown=results["architect"].content,
        architect_tools=tools,
        role_executions=[results[r] for r in ["competitor", "translator", "analyst", "coach", "architect"]],
    )
```

**Step 6: Run tests to verify GREEN**

Run: `uv run pytest tests/test_pipeline_adapter.py -v`
Expected: All PASS

**Step 7: Run existing orchestrator tests**

Run: `uv run pytest tests/test_orchestrator_feedback.py tests/test_agent_sdk_integration.py -v`
Expected: All PASS (default flag is off, so existing codepath executes)

**Step 8: Full suite**

Run: `uv run pytest -q`
Expected: All pass

**Step 9: Lint + types**

Run: `uv run ruff check src tests && uv run mypy src`

**Step 10: Commit**

```bash
git add src/mts/agents/pipeline_adapter.py src/mts/agents/orchestrator.py \
        src/mts/config/settings.py tests/test_pipeline_adapter.py
git commit -m "feat: add PipelineEngine-backed orchestrator codepath (feature-gated)"
```

---

## Verification (After All Batches)

```bash
# All existing tests pass (zero regressions)
uv run pytest -q

# New tests pass
uv run pytest tests/test_harness/test_harness_scenario_evaluator.py \
              tests/test_playbook_delegation.py \
              tests/test_harness/test_output_parser_adoption.py \
              tests/test_pipeline_adapter.py -v

# Lint and type check
uv run ruff check src tests
uv run mypy src

# Import verification
python -c "from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator; print('OK')"
python -c "from mts.agents.pipeline_adapter import build_mts_dag; print('OK')"

# Feature gate verification: pipeline off → existing codepath
python -c "from mts.config.settings import AppSettings; s = AppSettings(); assert s.use_pipeline_engine is False"

# Backward compatibility: existing code produces identical results
uv run pytest tests/test_orchestrator_feedback.py tests/test_runner_integration.py -v
```

## What Does NOT Change

- `GenerationRunner.run()` — the 580-line loop stays intact (Phase 4 will decompose it)
- `TournamentRunner` — still used directly by `GenerationRunner`; the `ScenarioEvaluator` adapter is available but not wired into the loop yet
- Default behavior — `use_pipeline_engine=False` means all production paths are unchanged
- All existing imports — no re-export shims needed beyond the elo.py one from Phase 2

## Phase 4 Preview (Not in Scope)

Phase 4 will:
- Set `use_pipeline_engine=True` as default after field testing
- Replace `TournamentRunner` calls in `GenerationRunner` with `EvaluationRunner` + `ScenarioEvaluator`
- Decompose `GenerationRunner.run()` into pipeline stages
- Add identity (SOUL.md equivalent) and periodic autonomy (HEARTBEAT.md)
