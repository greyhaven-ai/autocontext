# Autoresearch RLM Integration Design

**Date:** 2026-03-09
**Source:** [karpathy/autoresearch](https://github.com/karpathy/autoresearch), [trevin-creator/autoresearch-mlx](https://github.com/trevin-creator/autoresearch-mlx), [miolini/autoresearch-macos](https://github.com/miolini/autoresearch-macos)
**Scope:** 6 independent projects under MTS team, Python codebase
**Relation:** Complements [AutoHarness Integration](2026-03-09-autoharness-integration-design.md) (P1-P6)

## Motivation

Karpathy's autoresearch demonstrates that an LLM coding agent, given a fixed-budget evaluation loop and a mutable codebase, can autonomously discover significant optimizations through iterative experimentation. Over 126 experiments in ~10.5 hours, the agent improved val_bpb from 0.9979 → 0.9697 — discovering weight decay strategies, init scaling, and batch size optimizations. The MLX and MPS forks show the same loop finds fundamentally different optima on different hardware.

Three properties drive this success that MTS partially lacks:

1. **Total experiment visibility** — autoresearch agents see `results.tsv` with every attempt (kept and discarded). MTS agents see score trajectory tables but not what specific strategies were tried at each generation.
2. **Explicit failure memory** — autoresearch's discussion #43 documents dead ends as first-class knowledge (weight tying = +2.24 BPB disaster). MTS discards failed strategies without recording why they failed.
3. **Meta-programmability** — autoresearch's `program.md` is a mutable natural language document controlling the research process itself. MTS hardcodes orchestration order and parameter values.

## Relationship to AutoHarness Projects

These 6 projects are **complementary** to AutoHarness P1-P6, with specific integration points:

| Autoresearch Project | AutoHarness Synergy | Integration Point |
|---|---|---|
| AR-1: Experiment Log | P2 (Competitor RLM) | Log competitor REPL trial results in experiment log |
| AR-2: Dead-End Registry | P1 (Pre-Validation) | Auto-populate dead ends from exhausted pre-validation retries |
| AR-3: Research Protocol | P4 (Tree Search) | Protocol selects exploration mode: linear/rapid/tree |
| AR-4: Rapid Exploration | P4 (Tree Search) | Alternative exploration modes, not replacements |
| AR-5: Session Reports | P6 (Harness Persistence) | Both extend cross-run knowledge artifacts |
| AR-6: Config-Adaptive | P3 (Executable Validators) | Both extend architect prompt; share marker pattern |

### Conflicts Resolved

**P4 (Tree Search) vs AR-4 (Rapid Exploration):** These are alternative exploration strategies. AR-3 (Research Protocol) becomes the meta-controller that selects between `linear` (current gate), `rapid` (binary keep/discard), and `tree` (Thompson sampling). MTS-80 should be updated to support mode selection.

**P3 (Architect Dual Output) + AR-6 (Architect Tuning Proposals):** Both extend the architect prompt with new marker patterns. Whichever ships first establishes the `<!-- MARKER_START/END -->` convention. AR-6 uses `<!-- TUNING_PROPOSAL_START/END -->` alongside P3's `<!-- HARNESS_START/END -->`.

---

## Project AR-1: Experiment Log Injection

### Goal
Surface a complete experiment history to all agents — what was tried, what score it got, what gate decision was made, and a one-line description of the approach.

### Architecture

Extend `ScoreTrajectoryBuilder` with `build_experiment_log(run_id)` that produces a markdown table:

```
| Gen | Strategy Summary | Score | Delta | Gate | Approach |
```

- **Strategy Summary**: truncated strategy JSON (80 chars) from `agent_outputs` table
- **Approach**: extracted from analyst output's first finding or the strategy's dominant parameter changes vs. previous generation
- **Score/Delta/Gate**: from `generations` table

Builds on existing `ProgressSnapshot.blocked_approaches` field (currently unpopulated) and `ContextBudget` for trimming.

### Data Flow

```
SQLite (generations + agent_outputs) → ScoreTrajectoryBuilder.build_experiment_log()
  → stage_knowledge_setup() → build_prompt_bundle(experiment_log=...) → all role prompts
```

### Configuration
- `MTS_EXPERIMENT_LOG_ENABLED` (default `true`) — inject experiment log into prompts
- Uses existing `MTS_CONTEXT_BUDGET_TOKENS` for trimming (experiment log trimmed before trajectory)

### Touch Points
- `knowledge/trajectory.py` — new `build_experiment_log()` method
- `prompts/templates.py` — add `experiment_log` parameter to `build_prompt_bundle()`
- `loop/stages.py` — call `build_experiment_log()` in `stage_knowledge_setup()`
- `prompts/context_budget.py` — add experiment log to trimming cascade (trim before trajectory)
- `config/settings.py` — add `experiment_log_enabled` field

### Tests (~10)
- Empty run returns empty string
- Single generation formats correctly
- Multi-generation with mixed gate decisions
- Long strategy JSON truncated to 80 chars
- Context budget trims oldest entries first
- Approach extraction from analyst output
- Integration: experiment log appears in competitor prompt

---

## Project AR-2: Dead-End Registry

### Goal
Persist failed strategy patterns so agents never re-explore catastrophic directions. Inspired by autoresearch discussion #43's explicit dead-end documentation.

### Architecture

Two layers:
1. **Runtime**: Populate `ProgressSnapshot.blocked_approaches` (exists, currently empty) from rollback gate decisions and probe validation failures
2. **Persistent**: New artifact `knowledge/<scenario>/dead_ends.md` with structured entries that survive across runs

Each dead-end entry:

```markdown
### Dead End #N (Gen {gen}, {date})
**Strategy pattern:** {one-line summary}
**Failure reason:** {from FailureReport or probe validation}
**Score delta:** {delta vs previous best}
```

### Population Triggers
1. **Rollback gate decision** — `stage_tournament()` extracts from `FailureReport`
2. **Consecutive retry exhaustion** — 3+ retries on same approach = dead end
3. **Pre-validation failure** (when P1 enabled) — revision attempts exhausted
4. **Probe validation failure** — strategy fails probe but can't be refined

### Prompt Injection
- Competitor: "## Known Dead Ends\nAvoid these patterns:" + entries
- Coach: "## Failed Approaches\nThese directions were tried and failed:" + entries
- Constraint prompt already says "Do NOT repeat strategies that resulted in rollback" — extend to reference dead-end registry explicitly

### Curator Consolidation
Extend `consolidate_lessons()` to also deduplicate dead ends:
- Merge similar entries (same strategy pattern, different gens)
- Cap at 20 entries (oldest pruned first, unless catastrophic delta)
- Remove entries if strategy space has changed significantly (new scenario version)

### Configuration
- `MTS_DEAD_END_TRACKING_ENABLED` (default `true`)
- `MTS_DEAD_END_MAX_ENTRIES` (default `20`)

### Touch Points
- `storage/artifacts.py` — `read_dead_ends()`, `append_dead_end()`, `replace_dead_ends()`
- `loop/stages.py` — population hooks in `stage_tournament()` on rollback/retry exhaustion
- `loop/stage_probe.py` — population hook on probe validation failure
- `prompts/templates.py` — add `dead_ends` to `build_prompt_bundle()`
- `prompts/context_budget.py` — add dead ends to budget (protected, like hints)
- `agents/curator.py` — extend `consolidate_lessons()` to also consolidate dead ends
- `config/settings.py` — add `dead_end_tracking_enabled`, `dead_end_max_entries`

### Tests (~15)
- Append dead end on rollback
- Append dead end on retry exhaustion (3+)
- Read/write cycle for `dead_ends.md`
- Curator consolidation deduplicates similar entries
- Cap at max entries (oldest pruned)
- Dead ends appear in competitor prompt
- Dead ends survive across generations
- Cross-run persistence (dead_ends.md in knowledge dir)
- Integration with pre-validation failures (when P1 enabled)

---

## Project AR-3: Research Protocol

### Goal
Dynamic meta-document that guides the research direction across generations. The architect can update it to steer focus areas, adjust exploration parameters, and switch between exploration modes.

### Architecture

New knowledge artifact: `knowledge/<scenario>/research_protocol.md`

```markdown
## Exploration Mode
tree | linear | rapid

## Current Focus
Defensive patterns and territory control

## Constraints
- Avoid aggressive opening sequences (see Dead End #3)
- Prioritize strategies with > 0.6 score on defense metric

## Tuning Overrides
```json
{
  "backpressure_min_delta": 0.003,
  "matches_per_generation": 4,
  "rlm_max_turns": 15,
  "probe_matches": 2
}
```
```

### Protocol Lifecycle
1. **Initialization**: Created with default values on first run (or inherited from prior run)
2. **Architect updates**: When stagnation detected or exploration phase completes, architect proposes protocol changes via `<!-- PROTOCOL_START/END -->` markers in its output
3. **Curator gate**: Protocol changes go through curator review (accept/reject/merge)
4. **Application**: `stage_knowledge_setup()` reads protocol and applies tuning overrides to `GenerationContext` for the current generation

### Integration with Exploration Modes
- `linear` — current advance/retry/rollback gate (default)
- `rapid` — binary keep/discard, no retry, no curator, shortened RLM (AR-4)
- `tree` — Thompson-sampling multi-hypothesis search (AutoHarness P4)

The protocol's `Exploration Mode` field is read by the generation pipeline to select which gate and tournament strategy to use.

### Touch Points
- `storage/artifacts.py` — `read_research_protocol()`, `write_research_protocol()`
- `knowledge/protocol.py` (new) — `ResearchProtocol` dataclass, parser, default generator
- `loop/stages.py` — read protocol in `stage_knowledge_setup()`, apply tuning overrides
- `prompts/templates.py` — inject `Current Focus` and `Constraints` into all role prompts
- `agents/architect.py` — parse `<!-- PROTOCOL_START/END -->` markers
- `agents/curator.py` — review protocol changes alongside playbook changes
- `config/settings.py` — add `protocol_enabled` (default `false`)

### Configuration
- `MTS_PROTOCOL_ENABLED` (default `false`)

### Tests (~15)
- Default protocol generation
- Protocol parsing (exploration mode, focus, constraints, tuning overrides)
- Tuning overrides applied to GenerationContext
- Architect protocol proposal parsing
- Curator review of protocol changes
- Protocol persistence across generations
- Cross-run inheritance of protocol
- Exploration mode selection (linear/rapid/tree)
- Invalid tuning values rejected (guardrails)

---

## Project AR-4: Rapid Exploration Mode

### Goal
Strip the generation loop to its essentials for maximum breadth: binary keep/discard, fewer matches, shorter RLM sessions. Inspired by autoresearch's ~12 experiments/hour throughput.

### Architecture

New preset in `presets.py`:

```python
"rapid": {
    "backpressure_min_delta": 0.0,      # any improvement counts
    "backpressure_mode": "simple",
    "curator_enabled": False,
    "max_retries": 0,                    # no retry — advance or rollback only
    "matches_per_generation": 2,         # minimum meaningful comparison
    "rlm_max_turns": 5,                  # quick exploration, not deep analysis
    "probe_matches": 0,                  # skip probe in rapid mode
    "coherence_check_enabled": False,    # skip coherence checks
    "constraint_prompts_enabled": False, # less prompt overhead
}
```

Additionally, a new `exploration_mode` field on `AppSettings`:
- `linear` (default) — current behavior
- `rapid` — applies rapid preset overrides, forces advance-or-rollback-only gate logic
- `tree` — (future, from P4) Thompson-sampling multi-hypothesis

### Gate Logic Change
When `exploration_mode == "rapid"`:
- `stage_tournament()` skips retry loop entirely
- Gate returns `advance` if score improved (any delta > 0), `rollback` otherwise
- No `FailureReport` generation (saves LLM call)
- Curator gate skipped

### Auto-Transition (Optional)
- `MTS_RAPID_GENS` (default `0` = manual): after N rapid generations, switch to linear mode
- Useful for "explore for 10 gens, then refine" workflows

### Touch Points
- `config/presets.py` — add "rapid" preset
- `config/settings.py` — add `exploration_mode` field, `rapid_gens`
- `loop/stages.py` — conditional gate logic in `stage_tournament()`
- `loop/generation_pipeline.py` — mode selection, auto-transition after N gens

### Tests (~10)
- Rapid preset applied correctly
- No retry in rapid mode
- Any positive delta = advance
- Zero or negative delta = rollback
- Curator skipped in rapid mode
- RLM turns capped at 5 in rapid mode
- Auto-transition after N gens (when configured)
- Exploration mode field validation
- Rapid mode event emission

---

## Project AR-5: Cross-Session Reports

### Goal
Auto-generate a research narrative at run completion capturing what was tried, what worked, what failed, and remaining hypotheses. Future runs' analysts read prior reports for cross-run learning.

### Architecture

New function `generate_session_report()` in `knowledge/report.py`:

```python
@dataclass(slots=True)
class SessionReport:
    run_id: str
    scenario: str
    start_score: float
    end_score: float
    start_elo: float
    end_elo: float
    total_generations: int
    duration_seconds: float
    gate_counts: dict[str, int]    # {"advance": N, "retry": N, "rollback": N}
    top_improvements: list[dict]   # [{gen, delta, description}]
    dead_ends_found: int
    stagnation_events: list[str]   # trigger descriptions
    remaining_hypotheses: str      # from last analyst output
    exploration_mode: str
```

### Report Generation
Triggered in `generation_runner.py` after the final generation completes:

```python
report = generate_session_report(run_id, scenario, sqlite, artifacts)
artifacts.write_session_report(scenario, run_id, report.to_markdown())
```

### Storage
- `knowledge/<scenario>/reports/<run_id>.md` — one report per completed run
- Included in `snapshot_knowledge()` for cross-run inheritance
- Buffered write (non-critical I/O)

### Prompt Injection
- `stage_knowledge_setup()` reads the 1-2 most recent session reports
- Injected into analyst prompt as `## Prior Session Reports`
- Context budget: session reports trimmed before playbook but after experiment log

### Report Template

```markdown
# Session Report: {run_id}
**Scenario:** {scenario} | **Date:** {date} | **Duration:** {duration}

## Results
- Score: {start_score:.4f} → {end_score:.4f} (Δ {delta:+.4f})
- Elo: {start_elo:.1f} → {end_elo:.1f}
- Generations: {total} ({advances} advances, {retries} retries, {rollbacks} rollbacks)
- Exploration mode: {mode}

## Top Improvements
| Gen | Delta | Description |
|-----|-------|-------------|
...

## Dead Ends Discovered
{count} dead ends identified (see dead_ends.md)

## Stagnation Events
{list or "None"}

## Remaining Hypotheses
{last analyst recommendations}
```

### Touch Points
- `knowledge/report.py` (new) — `SessionReport` dataclass, `generate_session_report()`, `to_markdown()`
- `storage/artifacts.py` — `write_session_report()`, `read_latest_session_reports()`, extend `snapshot_knowledge()`
- `loop/generation_runner.py` — call `generate_session_report()` on run completion
- `loop/stages.py` — inject reports in `stage_knowledge_setup()`
- `prompts/templates.py` — add `session_reports` parameter to `build_prompt_bundle()`
- `prompts/context_budget.py` — add session reports to trimming cascade

### Configuration
- `MTS_SESSION_REPORTS_ENABLED` (default `true`)

### Tests (~12)
- Report generation with full run data
- Report generation with empty run (1 gen, no advances)
- Gate count calculation
- Top improvements extraction (sorted by delta)
- Remaining hypotheses from last analyst output
- Write/read cycle for report file
- Multiple reports in reports/ directory
- Latest 2 reports loaded for prompt injection
- Context budget trimming of reports
- Snapshot includes reports directory
- Integration: report appears in analyst prompt

---

## Project AR-6: Config-Adaptive Loop

### Goal
The architect analyzes meta-parameter effectiveness and proposes tuning adjustments. Oscillation detection triggers re-evaluation. Curator gates tuning changes.

### Architecture

**Tuning artifact:** `knowledge/<scenario>/tuning.json`

```json
{
  "version": 1,
  "parameters": {
    "matches_per_generation": 4,
    "backpressure_min_delta": 0.003,
    "rlm_max_turns": 15,
    "architect_every_n_gens": 2
  },
  "recommended_by": "run_abc_gen_12",
  "reasoning": "Retry rate was 60% suggesting threshold too tight. Reducing delta and adding matches for more signal.",
  "applied_at": "2026-03-09T12:00:00Z"
}
```

### Architect Analysis Extension
When stagnation is detected OR oscillation is detected in ecosystem mode, the architect receives meta-parameter effectiveness data:

```
## Meta-Parameter Analysis
- Retry rate: 60% (last 5 gens)
- Average gate delta: 0.002 (threshold: 0.005)
- RLM turns used: avg 8 of 25 (32% utilization)
- Matches per gen: 3 (score variance: 0.15)
```

The architect can propose tuning changes via markers:

```
<!-- TUNING_PROPOSAL_START -->
{
  "backpressure_min_delta": 0.003,
  "matches_per_generation": 4,
  "reasoning": "Threshold is too tight — 60% retry rate wastes compute"
}
<!-- TUNING_PROPOSAL_END -->
```

### Curator Gate for Tuning
Curator reviews tuning proposals alongside playbook quality:
- Accept: apply to `tuning.json`
- Reject: discard (current parameters preserved)
- Guardrails: hard min/max bounds per parameter

```python
TUNING_BOUNDS: dict[str, tuple[float, float]] = {
    "matches_per_generation": (1, 10),
    "backpressure_min_delta": (0.0, 0.05),
    "rlm_max_turns": (3, 50),
    "architect_every_n_gens": (1, 10),
    "probe_matches": (0, 5),
}
```

### Application
`generation_runner.py` reads `tuning.json` at run start:
- Env vars > tuning.json > presets > defaults (priority order)
- Tuning values applied to `AppSettings` copy (not persisted to env)

### Trigger: Oscillation Detection
Wire `detect_oscillation()` from ecosystem runner to trigger architect meta-analysis:
- When oscillation detected across N cycles, inject meta-parameter analysis into architect prompt
- Architect proposes tuning adjustment to break oscillation pattern

### Touch Points
- `knowledge/tuning.py` (new) — `TuningConfig` dataclass, parser, bounds validation, `compute_meta_parameter_stats()`
- `storage/artifacts.py` — `read_tuning()`, `write_tuning()`
- `agents/architect.py` — parse `<!-- TUNING_PROPOSAL_START/END -->` markers
- `agents/curator.py` — review tuning proposals
- `loop/generation_runner.py` — load tuning at run start, merge with settings
- `loop/ecosystem_runner.py` — wire oscillation detection to trigger architect tuning analysis
- `prompts/templates.py` — inject meta-parameter stats into architect prompt when triggered
- `config/settings.py` — add `config_adaptive_enabled` (default `false`)

### Configuration
- `MTS_CONFIG_ADAPTIVE_ENABLED` (default `false`)

### Tests (~15)
- Tuning JSON read/write cycle
- Bounds validation (out-of-range rejected)
- Priority order: env vars > tuning.json > presets > defaults
- Meta-parameter stats computation (retry rate, delta avg, RLM utilization)
- Architect tuning proposal parsing
- Curator accepts tuning proposal
- Curator rejects tuning proposal
- Oscillation detection triggers architect analysis
- Tuning applied at run start
- Tuning persists across runs
- Guardrail enforcement (parameters clamped to bounds)
- Integration: architect receives meta-parameter stats

---

## Dependency Graph

```
AR-1 (Experiment Log) ──────────────────────┐
                                             │
AR-2 (Dead-End Registry) ───────────────────>│──> AR-5 (Session Reports)
                                             │
AR-4 (Rapid Exploration) ──> AR-3 (Protocol) ┘
                                  │
AR-6 (Config-Adaptive) <─────────┘
```

- AR-1 and AR-2 are independent, can start immediately
- AR-4 is independent but AR-3 references it as an exploration mode
- AR-3 depends on AR-4 (rapid mode) conceptually, but can ship with linear/rapid only
- AR-5 benefits from AR-1 (experiment log data) and AR-2 (dead-end count)
- AR-6 builds on AR-3 (protocol tuning overrides are the runtime version of tuning.json)

## Implementation Order

1. **Wave 1 (parallel):** AR-1 (Experiment Log) + AR-2 (Dead-End Registry) + AR-4 (Rapid Exploration) — all independent, low-medium effort
2. **Wave 2:** AR-3 (Research Protocol) — builds on AR-4's exploration mode concept
3. **Wave 3 (parallel):** AR-5 (Session Reports) + AR-6 (Config-Adaptive) — both extend knowledge artifacts
