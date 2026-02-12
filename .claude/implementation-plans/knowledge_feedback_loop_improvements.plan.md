# Knowledge Feedback Loop Improvements

## Problem

Live API ablation proved the feedback loop is essential (othello WITHOUT produced the IDENTICAL strategy 5/5 generations) but has critical gaps:

1. **Playbook overwrite is destructive** — deterministic runs nuked rich 48-line playbooks with 12-line stubs
2. **Richest knowledge never reaches agents** — coach_history.md, analysis/*.md, architect changelog never injected
3. **SKILL.md lessons grow unbounded** — 76+ bullets, extensive duplication, no pruning
4. **Ephemeral state lost on restart** — coach hints and replay narrative are local variables
5. **No score trajectory** — agents see only "best score so far: X.XXXX", not the full evolution
6. **Tools never updated** — gen 1 tools persist unchanged despite architect recommendations

## Design

Introduce a **KnowledgeCurator** agent (Opus-level) plus 5 foundational improvements in 6 batches:

```
Batch 1: Score Trajectory + Strategy Registry (data layer)
    ↓
Batch 2: Hint Persistence + Analysis Injection (fill gaps)
    ↓
Batch 3: Playbook Versioning (prerequisite for quality gate)
    ↓
Batch 4: KnowledgeCurator Agent (centerpiece — quality gate + lesson consolidation)
    ↓
Batch 5: Architect Tool Updates (tool lifecycle)
    ↓
Batch 6: Cross-Run Knowledge Inheritance (long-term memory)
```

---

## Batch 1: Score Trajectory + Strategy-Score Registry

**Goal**: Replace "best score so far: X.XXXX" with structured generation history.

### New files

**`src/mts/knowledge/__init__.py`** — empty package init

**`src/mts/knowledge/trajectory.py`** — `ScoreTrajectoryBuilder`
```python
class ScoreTrajectoryBuilder:
    def __init__(self, sqlite: SQLiteStore):
        self.sqlite = sqlite

    def build_trajectory(self, run_id: str) -> str:
        """Markdown table: Gen | Mean | Best | Elo | Gate | Delta"""
        rows = self.sqlite.get_generation_trajectory(run_id)
        # Return formatted table or "" if empty

    def build_strategy_registry(self, run_id: str) -> str:
        """Markdown table: Gen | Strategy (truncated) | Best Score | Gate"""
        rows = self.sqlite.get_strategy_score_history(run_id)
        # Return formatted table or "" if empty
```

### Modified files

| File | Change |
|------|--------|
| `src/mts/storage/sqlite_store.py` | Add `get_generation_trajectory(run_id)` and `get_strategy_score_history(run_id)` queries joining `generations` + `agent_outputs` |
| `src/mts/prompts/templates.py` | Add `score_trajectory: str = ""` and `strategy_registry: str = ""` params to `build_prompt_bundle()`. Insert conditional blocks after `previous_summary` in `base_context` |
| `src/mts/loop/generation_runner.py` | Instantiate `ScoreTrajectoryBuilder(self.sqlite)` in `__init__`. Build trajectory/registry before `build_prompt_bundle()` (suppressed by ablation). Pass to prompt builder |

### Tests — `tests/test_score_trajectory.py`

| Test | Asserts |
|------|---------|
| `test_trajectory_empty_run` | No data → empty string |
| `test_trajectory_single_gen` | 1 gen → table with 1 row |
| `test_trajectory_multi_gen` | 3 gens → 3 rows, correct deltas |
| `test_trajectory_includes_gate` | Gate column shows advance/rollback |
| `test_registry_empty` | No data → empty string |
| `test_registry_maps_strategy_to_score` | Strategy JSON appears with score |
| `test_trajectory_in_competitor_prompt` | Prompt contains "Score trajectory" |
| `test_trajectory_in_analyst_prompt` | Analyst also sees trajectory |
| `test_trajectory_absent_when_empty` | Empty → no header injected |
| `test_registry_truncates_long_strategies` | Strategies > 200 chars truncated |

### Verification
```bash
uv run pytest tests/test_score_trajectory.py -v
uv run ruff check src tests && uv run mypy src
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id batch1_smoke
```

---

## Batch 2: Hint Persistence + Analysis Injection

**Goal**: Coach hints survive run restart. Most recent advance analysis reaches agents.

### Modified files

| File | Change |
|------|--------|
| `src/mts/storage/artifacts.py` | Add `write_hints(scenario, content)`, `read_hints(scenario) -> str`, `read_latest_advance_analysis(scenario, current_gen) -> str` |
| `src/mts/prompts/templates.py` | Add `recent_analysis: str = ""` param. Insert "Most recent generation analysis" block after lessons, before replay |
| `src/mts/loop/generation_runner.py` | **Load hints on run start**: change `coach_competitor_hints = ""` → `self.artifacts.read_hints(scenario_name)`. **Persist hints on advance**: call `self.artifacts.write_hints()`. **Load analysis**: call `read_latest_advance_analysis()` before prompt build |

### Key design — `artifacts.py` methods

```python
def write_hints(self, scenario_name: str, content: str) -> None:
    self.write_markdown(self.knowledge_root / scenario_name / "hints.md", content)

def read_hints(self, scenario_name: str) -> str:
    path = self.knowledge_root / scenario_name / "hints.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""

def read_latest_advance_analysis(self, scenario_name: str, current_gen: int) -> str:
    analysis_dir = self.knowledge_root / scenario_name / "analysis"
    if not analysis_dir.exists():
        return ""
    # Find highest gen_N.md where N < current_gen
    candidates = sorted(analysis_dir.glob("gen_*.md"), reverse=True)
    for path in candidates:
        num = int(path.stem.split("_")[1])
        if num < current_gen:
            return path.read_text(encoding="utf-8")
    return ""
```

### Tests

**`tests/test_hint_persistence.py`** (5 tests)

| Test | Asserts |
|------|---------|
| `test_hints_written_on_advance` | After advance gate, hints.md exists with content |
| `test_hints_not_written_on_rollback` | After rollback, hints.md unchanged |
| `test_hints_loaded_on_run_start` | Pre-seed hints.md; gen 1 prompt contains hints |
| `test_hints_survive_restart` | New runner instance reads persisted hints |
| `test_empty_hints_graceful` | No hints.md → empty string, no crash |

**`tests/test_analysis_injection.py`** (5 tests)

| Test | Asserts |
|------|---------|
| `test_latest_analysis_injected_gen2` | Gen 2 prompt contains gen 1 analysis |
| `test_no_analysis_for_gen1` | Gen 1 has no prior analysis |
| `test_analysis_picks_highest_gen` | With gen_1,2,3: returns gen_3 |
| `test_analysis_in_prompt_bundle` | "Most recent generation analysis" in all role prompts |
| `test_analysis_suppressed_by_ablation` | Ablation → no analysis injected |

### Verification
```bash
uv run pytest tests/test_hint_persistence.py tests/test_analysis_injection.py -v
uv run pytest && uv run ruff check src tests && uv run mypy src
```

---

## Batch 3: Playbook Versioning

**Goal**: Keep last N playbook versions. Prerequisite for curator quality gate.

### Config

| Setting | Default | Env |
|---------|---------|-----|
| `playbook_max_versions` | 5 | `MTS_PLAYBOOK_MAX_VERSIONS` |

### Modified files

| File | Change |
|------|--------|
| `src/mts/config/settings.py` | Add `playbook_max_versions: int = Field(default=5, ge=1)` + env var |
| `src/mts/storage/artifacts.py` | Modify `write_playbook()` to archive current before overwrite. Add `_prune_playbook_versions()`, `rollback_playbook()`, `playbook_version_count()` |
| `src/mts/loop/generation_runner.py` | Pass `max_versions` through to artifact store (store on `ArtifactStore.__init__`) |

### Key design — versioning in `artifacts.py`

```python
def write_playbook(self, scenario_name: str, content: str) -> None:
    playbook_path = self.knowledge_root / scenario_name / "playbook.md"
    versions_dir = self.knowledge_root / scenario_name / "playbook_versions"
    if playbook_path.exists():
        versions_dir.mkdir(parents=True, exist_ok=True)
        existing = playbook_path.read_text(encoding="utf-8")
        existing_versions = sorted(versions_dir.glob("playbook_v*.md"))
        next_num = len(existing_versions) + 1
        (versions_dir / f"playbook_v{next_num:04d}.md").write_text(existing, encoding="utf-8")
        self._prune_playbook_versions(versions_dir, self._max_playbook_versions)
    self.write_markdown(playbook_path, content)

def rollback_playbook(self, scenario_name: str) -> bool:
    versions_dir = self.knowledge_root / scenario_name / "playbook_versions"
    versions = sorted(versions_dir.glob("playbook_v*.md"))
    if not versions:
        return False
    latest = versions[-1]
    self.write_markdown(self.knowledge_root / scenario_name / "playbook.md", latest.read_text(encoding="utf-8"))
    latest.unlink()
    return True
```

### Tests — `tests/test_playbook_versioning.py` (9 tests)

| Test | Asserts |
|------|---------|
| `test_first_write_no_version` | First write → no version files |
| `test_second_write_creates_version` | Second write → playbook_v0001.md with old content |
| `test_version_content_matches_previous` | Version content == playbook before overwrite |
| `test_pruning_at_max` | N+2 writes with max=N → only N versions remain |
| `test_rollback_restores_previous` | After 3 writes, rollback → v0002 content is current |
| `test_rollback_empty_returns_false` | No versions → False |
| `test_version_count_accurate` | Correct count returned |
| `test_read_specific_version` | Read version by number |
| `test_integration_runner_versions` | Full 3-gen run creates version files |

### Verification
```bash
uv run pytest tests/test_playbook_versioning.py -v
uv run pytest && uv run ruff check src tests && uv run mypy src
```

---

## Batch 4: KnowledgeCurator Agent (Centerpiece)

**Goal**: Opus-level agent for playbook quality gating and SKILL.md lesson consolidation.

### Config

| Setting | Default | Env |
|---------|---------|-----|
| `model_curator` | `claude-opus-4-6` | `MTS_MODEL_CURATOR` |
| `curator_enabled` | `True` | `MTS_CURATOR_ENABLED` |
| `curator_consolidate_every_n_gens` | 3 | `MTS_CURATOR_CONSOLIDATE_EVERY_N_GENS` |
| `skill_max_lessons` | 30 | `MTS_SKILL_MAX_LESSONS` |

### New file: `src/mts/agents/curator.py`

```python
@dataclass(slots=True)
class CuratorPlaybookDecision:
    decision: str   # "accept" | "reject" | "merge"
    playbook: str   # Resulting playbook content
    score: int      # Quality score 1-10
    reasoning: str

@dataclass(slots=True)
class CuratorLessonResult:
    consolidated_lessons: list[str]
    removed_count: int
    reasoning: str

class KnowledgeCurator:
    def __init__(self, runtime: SubagentRuntime, model: str):
        self.runtime = runtime
        self.model = model

    def assess_playbook_quality(
        self, current_playbook: str, proposed_playbook: str,
        score_trajectory: str, recent_analysis: str,
    ) -> tuple[CuratorPlaybookDecision, RoleExecution]:
        """Compare current vs proposed playbook. Return accept/reject/merge decision."""
        # Prompt instructs curator to score both on coverage/specificity/actionability
        # Output parsed from markers:
        #   <!-- CURATOR_DECISION: accept|reject|merge -->
        #   <!-- CURATOR_PLAYBOOK_START --> ... <!-- CURATOR_PLAYBOOK_END -->
        #   <!-- CURATOR_SCORE: N -->
        ...

    def consolidate_lessons(
        self, existing_lessons: list[str], max_lessons: int, score_trajectory: str,
    ) -> tuple[CuratorLessonResult, RoleExecution]:
        """Deduplicate semantically, rank by evidence, cap at max_lessons."""
        # Output parsed from:
        #   <!-- CONSOLIDATED_LESSONS_START --> ... <!-- CONSOLIDATED_LESSONS_END -->
        #   <!-- LESSONS_REMOVED: N -->
        ...
```

Parsing functions: `parse_curator_playbook_decision(content)`, `parse_curator_lesson_result(content)` — follow same regex pattern as `parse_coach_sections()`.

### Integration into generation loop

The curator runs at two points in the loop:

```
... tournament + gate decision ...

STEP 5 — CURATOR QUALITY GATE (if advance + curator enabled):
    current_pb = self.artifacts.read_playbook(scenario_name)
    proposed_pb = outputs.coach_playbook
    if both non-empty:
        decision, exec = self.agents.curator.assess_playbook_quality(...)
        if decision.decision == "reject":
            outputs = dataclasses.replace(outputs, coach_playbook="")
        elif decision.decision == "merge":
            outputs = dataclasses.replace(outputs, coach_playbook=decision.playbook)
        # "accept" → no change
        # Track execution metrics

STEP 6 — persist_generation() (uses possibly-modified outputs)

STEP 7 — persist_skill_note()

STEP 8 — CURATOR LESSON CONSOLIDATION (if gen % N == 0):
    existing = self.artifacts.read_skill_lessons_raw(scenario_name)
    if len(existing) > self.settings.skill_max_lessons:
        result, exec = self.agents.curator.consolidate_lessons(
            existing, max_lessons, score_trajectory)
        self.artifacts.replace_skill_lessons(scenario_name, result.consolidated_lessons)
```

### Modified files

| File | Change |
|------|--------|
| `src/mts/agents/curator.py` | **NEW** — KnowledgeCurator, dataclasses, parsing functions |
| `src/mts/config/settings.py` | Add 4 curator settings + env vars in `load_settings()` |
| `src/mts/agents/orchestrator.py` | Add `self.curator = KnowledgeCurator(runtime, settings.model_curator) if settings.curator_enabled else None` |
| `src/mts/agents/__init__.py` | Export `KnowledgeCurator` |
| `src/mts/storage/artifacts.py` | Add `read_skill_lessons_raw(scenario) -> list[str]` and `replace_skill_lessons(scenario, lessons)` |
| `src/mts/loop/generation_runner.py` | Add curator quality gate (step 5) and lesson consolidation (step 8) |
| `src/mts/agents/llm_client.py` | Add curator detection branches to `DeterministicDevClient.generate()` |

### DeterministicDevClient branches

```python
elif "curator" in prompt_lower and "playbook quality" in prompt_lower:
    text = self._curator_playbook_response()
elif "curator" in prompt_lower and "consolidate" in prompt_lower:
    text = self._curator_consolidate_response()
```

`_curator_playbook_response()` → structured output with `<!-- CURATOR_DECISION: accept -->` markers
`_curator_consolidate_response()` → structured output with `<!-- CONSOLIDATED_LESSONS_START -->` markers

### Tests

**`tests/test_curator.py`** (10 tests)

| Test | Asserts |
|------|---------|
| `test_parse_playbook_accept` | Parse accept decision, playbook extracted |
| `test_parse_playbook_reject` | Parse reject decision |
| `test_parse_playbook_merge` | Parse merge with merged content |
| `test_parse_score` | Integer score extracted |
| `test_parse_lesson_consolidation` | Consolidated lessons parsed, removed_count correct |
| `test_curator_rejects_low_quality` | Mock reject → playbook unchanged after advance |
| `test_curator_accepts_good_playbook` | Mock accept → playbook updated normally |
| `test_curator_merges` | Merged playbook written |
| `test_curator_disabled_skips` | `curator_enabled=False` → no call |
| `test_deterministic_curator_branches` | DeterministicDevClient returns valid curator output |

**`tests/test_skill_consolidation.py`** (6 tests)

| Test | Asserts |
|------|---------|
| `test_read_skill_lessons_raw` | Returns list of bullet strings |
| `test_replace_skill_lessons` | Overwrites lessons section, preserves SKILL.md structure |
| `test_consolidation_triggered_at_interval` | Every N gens, consolidation runs |
| `test_consolidation_skipped_under_threshold` | Below max_lessons → no call |
| `test_consolidation_reduces_count` | After consolidation, count <= max_lessons |
| `test_curator_lesson_roundtrip` | Parse deterministic consolidation output |

**`tests/test_curator_integration.py`** (3 tests)

| Test | Asserts |
|------|---------|
| `test_curator_runs_after_tournament` | Curator output in agent_outputs table |
| `test_playbook_quality_gate_e2e` | 3-gen run with playbook versions reflecting curator |
| `test_curator_and_coach_coexist` | Coach runs normally, curator post-processes |

### Verification
```bash
uv run pytest tests/test_curator.py tests/test_skill_consolidation.py tests/test_curator_integration.py -v
uv run pytest && uv run ruff check src tests && uv run mypy src
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 6 --run-id batch4_smoke
```

---

## Batch 5: Architect Tool Updates

**Goal**: Allow architect to update existing tools with archival of old versions.

### Modified files

| File | Change |
|------|--------|
| `src/mts/storage/artifacts.py` | Modify `persist_tools()`: if tool exists, archive to `tools/_archive/{name}_gen{N}.py` before overwrite. Tag updated tools in return list |
| `src/mts/prompts/templates.py` | Add "You may CREATE new tools or UPDATE existing tools by using the same name." to architect prompt suffix |

### Tests — `tests/test_architect_tool_updates.py` (6 tests)

| Test | Asserts |
|------|---------|
| `test_new_tool_creates_file` | Standard behavior unchanged |
| `test_update_overwrites_file` | Same name → file updated |
| `test_update_archives_old` | `_archive/` has old version |
| `test_archive_filename_includes_gen` | `name_gen2.py` format |
| `test_update_tagged_in_list` | Return list includes "(updated)" |
| `test_prompt_mentions_update` | "UPDATE existing tools" in architect prompt |

### Verification
```bash
uv run pytest tests/test_architect_tool_updates.py -v && uv run pytest
```

---

## Batch 6: Cross-Run Knowledge Inheritance

**Goal**: Best playbook and lessons survive across runs for the same scenario.

### Config

| Setting | Default | Env |
|---------|---------|-----|
| `cross_run_inheritance` | `True` | `MTS_CROSS_RUN_INHERITANCE` |

### New migration: `migrations/004_knowledge_inheritance.sql`

```sql
CREATE TABLE IF NOT EXISTS knowledge_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario TEXT NOT NULL,
    run_id TEXT NOT NULL,
    best_score REAL NOT NULL,
    best_elo REAL NOT NULL,
    playbook_hash TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_knowledge_snapshots_scenario
    ON knowledge_snapshots(scenario, best_score DESC);
```

### Modified files

| File | Change |
|------|--------|
| `migrations/004_knowledge_inheritance.sql` | **NEW** — snapshot table |
| `src/mts/config/settings.py` | Add `cross_run_inheritance` bool |
| `src/mts/storage/sqlite_store.py` | Add `save_knowledge_snapshot()`, `get_best_knowledge_snapshot()` |
| `src/mts/storage/artifacts.py` | Add `snapshot_knowledge(scenario, run_id) -> str` (copies playbook + skills + hints to `snapshots/<run_id>/`), `restore_knowledge_snapshot(scenario, source_run_id) -> bool` |
| `src/mts/loop/generation_runner.py` | On run completion: snapshot. On run start: if no playbook exists, restore from best snapshot |

### Tests — `tests/test_cross_run_inheritance.py` (9 tests)

| Test | Asserts |
|------|---------|
| `test_snapshot_on_completion` | Snapshot dir exists after run |
| `test_best_snapshot_query` | Two snapshots → highest score returned |
| `test_no_snapshot_returns_none` | Fresh DB → None |
| `test_restore_on_fresh_run` | Run 1 → clean → run 2 inherits |
| `test_no_restore_when_playbook_exists` | Existing playbook → skip |
| `test_disabled_by_config` | `cross_run_inheritance=False` → skip |
| `test_disabled_by_ablation` | Ablation → skip |
| `test_snapshot_includes_hints` | hints.md in snapshot |
| `test_snapshot_includes_skills` | SKILL.md in snapshot |

### Verification
```bash
uv run pytest tests/test_cross_run_inheritance.py -v && uv run pytest
uv run ruff check src tests && uv run mypy src
# Integration: two runs, second inherits
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 3 --run-id inherit_r1
rm knowledge/grid_ctf/playbook.md
MTS_AGENT_PROVIDER=deterministic uv run mts run --scenario grid_ctf --gens 1 --run-id inherit_r2
```

---

## Critical Files Summary

| File | Batches | Nature |
|------|---------|--------|
| `src/mts/loop/generation_runner.py` | 1,2,3,4,6 | Central integration point |
| `src/mts/storage/artifacts.py` | 2,3,4,5,6 | Persistence layer |
| `src/mts/prompts/templates.py` | 1,2,5 | Prompt assembly |
| `src/mts/agents/curator.py` | 4 | **NEW** — Opus-level quality gate |
| `src/mts/knowledge/trajectory.py` | 1 | **NEW** — score trajectory builder |
| `src/mts/config/settings.py` | 3,4,6 | Configuration |
| `src/mts/agents/llm_client.py` | 4 | DeterministicDevClient curator branches |
| `src/mts/agents/orchestrator.py` | 4 | Curator wiring |
| `src/mts/storage/sqlite_store.py` | 1,6 | New queries + snapshot table |

## Test Count

| Batch | New Tests |
|-------|-----------|
| 1: Score Trajectory | 10 |
| 2: Hints + Analysis | 10 |
| 3: Playbook Versioning | 9 |
| 4: KnowledgeCurator | 19 |
| 5: Architect Updates | 6 |
| 6: Cross-Run Inheritance | 9 |
| **Total new tests** | **63** |
| **Existing tests (101)** | All green |

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| New `build_prompt_bundle()` params break existing callers | All new params default to `""` — backward compatible |
| Curator adds API cost (~$0.05/call) | `curator_enabled` flag; runs only on advance (quality gate) + every N gens (consolidation) |
| Curator produces bad merge | Playbook versioning (Batch 3) provides rollback; curator output logged for audit |
| Lesson consolidation loses critical info | Curator is Opus-level with full score trajectory context; original lessons preserved in coach_history.md |
| DeterministicDevClient detection conflicts | Each role has unique keyword: "playbook quality" (curator gate), "consolidate" (curator lessons) |
| Ablation mode breaks | All new injections check `ablation_no_feedback` flag — suppressed when True |
| Migration 004 breaks existing DBs | Only adds new table, no ALTER on existing tables |
