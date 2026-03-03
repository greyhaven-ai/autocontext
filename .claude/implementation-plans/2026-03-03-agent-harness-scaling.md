# Agent Harness Scaling: Self-Improving Agents & Long-Running Tasks

**Date**: 2026-03-03
**Status**: Proposed
**Sources**:
- [Cursor: Scaling Long-Running Autonomous Coding](https://cursor.com/blog/scaling-agents)
- [Cursor: Towards Self-Driving Codebases](https://cursor.com/blog/self-driving-codebases)
- [Anthropic: Effective Harnesses for Long-Running Agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Phil Schmid: The Importance of Agent Harness in 2026](https://www.philschmid.de/agent-harness-2026)

## Overview

Research synthesis mapping industry patterns for scaling autonomous agents to concrete MTS improvements. Organized into 4 phases with 12 proposals.

---

## Current Strengths

MTS already aligns with several patterns identified as critical:

- **Hierarchical role separation** — Competitor→Analyst→Coach→Architect→Curator maps to Cursor's planner→worker→judge hierarchy
- **Structured progress artifacts** — Playbooks, hints, analysis, score trajectories, cross-run snapshots
- **Model selection per role** — `MTS_MODEL_*` settings + `ConfigAdvisor` for automated recommendations
- **Self-correction through retry** — Backpressure gate (`advance`/`retry`/`rollback`), trend-aware gate relaxation
- **Knowledge feedback loops** — Cross-run inheritance, skill export, lesson consolidation
- **Harness trajectories as data** — Comprehensive audit trail (ndjson events, role metrics, cost tracking)
- **Modular architecture** — Harness module with separable subsystems (evaluation, orchestration, pipeline, scoring, cost, audit)

---

## Gap Analysis

| Gap | Research Source | Impact |
|-----|---------------|--------|
| No structured JSON progress tracking | Anthropic: "JSON > Markdown for stability" | Agents parse prose to understand progress |
| No fresh start / context reset | Cursor: "Periodic fresh starts combat drift" | Agents stuck in local optima |
| Instruction-oriented prompts | Cursor: "Constraints > instructions" | Lower compliance on long runs |
| No recursive subplanning | Cursor: "Root planner → subplanners → workers" | Complex problems can't decompose mid-run |
| Raw string handoffs between roles | Cursor: "Structured handoffs" | Silent degradation on parse failure |
| No context budget management | Schmid: "Context Window = RAM" | Context overflow on 50+ gen runs |
| No mid-generation observation | Cursor: "Observation-driven iteration" | Competitor proposes blind |
| Growing settings complexity | Cursor/Schmid: "Simplicity prevails" | 60+ settings, 13 for disabled features |

---

## Phase 1: Foundation Hardening

**Theme**: Prompt quality and reliability — highest ROI per research consensus.

### 1.1 Structured JSON Progress File

**What**: Write `knowledge/<scenario>/progress.json` alongside `playbook.md` with quantified strategy dimensions, stagnation count, blocked approaches, trend data.

**Why**: Anthropic research shows JSON is more stable than markdown for machine-readable progress. Agents parse structure instead of prose.

**Schema**:
```json
{
  "generation": 12,
  "best_score": 0.87,
  "best_elo": 1523,
  "last_advance_generation": 10,
  "stagnation_count": 2,
  "gate_history": ["advance", "advance", "rollback", "rollback"],
  "top_lessons": ["lesson1", "lesson2"],
  "blocked_approaches": ["high aggression + low defense"],
  "strategy_summary": { "best_params": {} }
}
```

**Files changed**:
- `mts/src/mts/storage/artifacts.py` — add `write_progress()`, `read_progress()`
- `mts/src/mts/loop/stages.py` — call `write_progress()` in `stage_persistence`
- `mts/src/mts/prompts/templates.py` — inject progress JSON into prompts

### 1.2 Constraint-Oriented Prompt Reframing

**What**: Rewrite agent prompts to lead with constraints rather than instructions.

**Why**: Cursor found "No TODOs" outperforms "remember to finish." Negative constraints are more reliably followed.

**Example transformation**:
```
BEFORE: "Describe your strategy reasoning and recommend specific parameter values."

AFTER:
"Constraints:
- Do NOT repeat any strategy from the registry that resulted in rollback
- Do NOT set parameters outside the valid ranges in the strategy interface
- Do NOT omit reasoning for each parameter choice
- Do NOT propose strategies that contradict the top operational lessons

Produce: strategy reasoning followed by specific parameter values."
```

**Files changed**:
- `mts/src/mts/prompts/templates.py` — rewrite role prompt suffixes
- `mts/src/mts/agents/curator.py` — add constraints to curator prompts
- `mts/src/mts/rlm/prompts.py` — add constraints to RLM system prompts

### 1.3 Stagnation Detection and Fresh Start

**What**: Detect N consecutive rollbacks or score plateau, then archive playbook, reset to distilled summary (top 5 lessons + best strategy), clear stale hints.

**Why**: Cursor found periodic fresh starts combat drift and tunnel vision.

**Detection criteria**:
- N consecutive rollbacks (configurable, default 5)
- Score variance below epsilon for M generations (configurable)
- Same strategy parameters repeated across rollbacks

**Reset actions**:
1. Archive current playbook to `playbook_versions/`
2. Generate distilled playbook: top 5 lessons + best strategy summary
3. Clear hints.md
4. Inject "fresh start" directive into next competitor prompt

**Files changed**:
- New: `mts/src/mts/harness/pipeline/stagnation.py` — `StagnationDetector` class
- `mts/src/mts/loop/stages.py` — check stagnation after `stage_tournament`
- `mts/src/mts/config/settings.py` — `MTS_STAGNATION_WINDOW`, `MTS_STAGNATION_RESET_ENABLED`

### 1.4 Typed Role Handoff Contracts

**What**: Replace raw string concatenation between roles with typed dataclasses enforcing contracts.

**Why**: Cursor: "No direct inter-agent communication; structured handoffs instead." Prevents silent degradation when delimiter parsing fails.

**Contracts**:
```python
@dataclass(slots=True)
class CompetitorOutput:
    raw_text: str
    strategy: dict[str, Any]
    reasoning: str

@dataclass(slots=True)
class AnalystOutput:
    findings: list[str]
    root_causes: list[str]
    recommendations: list[str]
    raw_markdown: str

@dataclass(slots=True)
class CoachOutput:
    playbook: str
    lessons: list[str]
    hints: str
    raw_markdown: str
    parse_success: bool

@dataclass(slots=True)
class ArchitectOutput:
    tool_specs: list[dict[str, Any]]
    changelog_entry: str
    raw_markdown: str
    parse_success: bool
```

**Files changed**:
- New: `mts/src/mts/agents/types.py` — typed output dataclasses
- `mts/src/mts/agents/pipeline_adapter.py` — produce typed outputs
- `mts/src/mts/agents/coach.py` — `parse_coach_sections` returns `CoachOutput`
- `mts/src/mts/agents/architect.py` — returns `ArchitectOutput`
- `mts/src/mts/agents/orchestrator.py` — consume typed outputs

---

## Phase 2: Context Management

**Theme**: Prevent context exhaustion and ensure clean state on long runs.

### 2.1 Context Budget Management

**What**: Estimate token count per prompt component, progressively summarize when exceeding budget.

**Why**: Schmid: "Context Window = RAM." Overflowing context degrades performance.

**Progressive summarization cascade**:
1. Truncate score trajectory to last N generations
2. Summarize playbook to key points
3. Prune tool context to recently-used tools only
4. Rank and trim lessons to top-K by relevance

**Files changed**:
- New: `mts/src/mts/prompts/context_budget.py` — `ContextBudget` class
- `mts/src/mts/prompts/templates.py` — apply budget before building prompt
- `mts/src/mts/knowledge/trajectory.py` — add `build_windowed_trajectory()`
- `mts/src/mts/config/settings.py` — `MTS_CONTEXT_BUDGET_TOKENS`

### 2.2 Session Startup Verification

**What**: Deterministic verification sequence before each generation to ensure clean state.

**Why**: Anthropic: startup protocol (pwd → read progress → select feature → init → test → work).

**Verification steps**:
1. Playbook exists and is parseable
2. SQLite accessible, generation count matches expectations
3. Knowledge directory structure intact
4. `progress.json` valid (from 1.1)
5. Recovery markers checked
6. Startup state logged to events

**Files changed**:
- `mts/src/mts/loop/stages.py` — new `stage_startup_verification()`
- `mts/src/mts/loop/generation_pipeline.py` — insert before `stage_knowledge_setup`

### 2.3 Knowledge Coherence Verification

**What**: Post-generation check that accumulated knowledge artifacts are internally consistent.

**Why**: Anthropic: "End-to-end verification — test as human user, not just unit metrics."

**Checks**:
- Playbook references strategies that exist in the registry
- Lessons don't contradict each other
- Tools referenced in playbook exist in tools directory
- Hints are consistent with current playbook direction

**Files changed**:
- `mts/src/mts/loop/stages.py` — new `stage_knowledge_verification()`
- `mts/src/mts/loop/generation_pipeline.py` — optional stage after persistence

---

## Phase 3: Throughput & Simplification

**Theme**: Performance optimization and configuration simplification.

### 3.1 Asynchronous Artifact Writes

**What**: Buffer non-critical writes to a thread-safe queue flushed by background thread.

**Why**: Cursor: "Disk I/O was the actual throughput bottleneck, not compute."

**Critical (synchronous)**: playbook, SQLite, recovery markers
**Buffered (async)**: analysis markdown, coach history, skill notes, event stream

**Files changed**:
- New: `mts/src/mts/storage/async_writer.py` — `AsyncArtifactWriter`
- `mts/src/mts/storage/artifacts.py` — `buffered_write_markdown()` methods
- `mts/src/mts/loop/generation_runner.py` — flush buffer on run completion

### 3.2 Settings Simplification

**What**: Add `MTS_PRESET` mechanism and reduce individual settings count.

**Why**: Cursor/Schmid: "Simplicity prevails." Vercel removed 80% of tools for efficiency.

**Presets**:
- `conservative` — high quality threshold, slow advance, curator enabled
- `aggressive` — lower threshold, fast iteration, curator disabled
- `experimental` — probes enabled, stagnation resets, dynamic DAG

**Actions**:
- Remove individual env vars for disabled subsystems (trust, identity, heartbeat, adapt)
- Merge related settings into presets
- Keep individual overrides possible but not default

**Files changed**:
- `mts/src/mts/config/settings.py` — add preset system, consolidate settings

---

## Phase 4: Advanced Orchestration

**Theme**: Dynamic, adaptive agent coordination.

### 4.1 Mid-Generation Observation Loop

**What**: Split tournament into probe (1 match) → competitor refinement → full evaluation.

**Why**: Cursor: "Observation-driven iteration > assumption-based design."

**Distinct from retry** (which fires after full tournament fails gate). The probe lets the competitor observe actual match behavior before committing to a full tournament.

**Files changed**:
- `mts/src/mts/loop/stages.py` — new `stage_probe()` before `stage_tournament`
- `mts/src/mts/agents/competitor.py` — `refine_from_observation()`
- `mts/src/mts/config/settings.py` — `MTS_PROBE_MATCHES` (default 0 = disabled)

### 4.2 Dynamic DAG Reconfiguration

**What**: Allow architect to modify the role DAG for subsequent generations.

**Why**: Cursor: "Recursive subplanners" — system should add/remove roles based on discovery.

**Examples**:
- After stagnation: add "diversifier" role for orthogonal strategies
- When no new tools needed: remove architect role
- When score plateaus: add "critic" role that specifically challenges the playbook

**Files changed**:
- `mts/src/mts/harness/orchestration/dag.py` — add `add_role()`, `remove_role()` with cycle detection
- `mts/src/mts/agents/architect.py` — parse `{"dag_changes": [...]}` block
- `mts/src/mts/agents/orchestrator.py` — apply DAG changes between generations

### 4.3 Ecosystem Convergence Detection

**What**: Track playbook divergence between ecosystem phases. Lock to best version if oscillating.

**Why**: Without detection, alternating phases can oscillate between contradictory strategies.

**Mechanism**:
- After each phase, compute divergence metric between pre/post playbook
- If divergence exceeds threshold for N cycles, lock to highest-scoring version
- Emit warning event on detected oscillation

**Files changed**:
- `mts/src/mts/loop/ecosystem_runner.py` — `_check_convergence()`
- `mts/src/mts/storage/artifacts.py` — `playbook_diff()`

---

## Sequencing Rationale

1. **Prompt engineering first** (Cursor: highest leverage, lowest risk)
2. **Context management second** (Schmid: binding constraint on long runs)
3. **Throughput third** (Cursor: actual bottleneck is I/O, not compute)
4. **Advanced orchestration last** (only useful once simpler improvements are in place)

**Cross-cutting principle**: Simplicity. Each proposal in its simplest viable form first. AppSettings should shrink, not grow. If a feature doesn't demonstrably improve deterministic test runs, remove it.
