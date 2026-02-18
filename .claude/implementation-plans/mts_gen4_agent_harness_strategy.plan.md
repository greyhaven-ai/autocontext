# MTS Gen 4 Agent Harness Strategy

| | |
|---|---|
| **Date** | February 17, 2026 |
| **From** | Jay + Claude (working session) |
| **Status** | Exploratory — audit complete, ready for phased execution |

---

## Part 1: Original Thought Experiment

### Context & Objective

This document captures the output of a brainstorming session focused on identifying the next generation of LLM infrastructure harnesses — tools and platforms that wrap foundation models to enable autonomous, persistent, and self-improving agent workflows. The goal is to inform how we evolve the MTS repository into a more capable harness, drawing on patterns from Claude Code, OpenClaw, and the Ralph Wiggum loop methodology.

The core question: **What infrastructure do we need to build so that agents can work autonomously, maintain state across sessions, coordinate with other agents, and improve over time?**

### The Trajectory We're Tracking

The market has evolved through three distinct phases, each removing a constraint on agent autonomy:

| Generation | Example | Key Innovation | Constraint Removed |
|---|---|---|---|
| Gen 1 | Claude Code, Cursor, Aider | Agent in terminal/editor | IDE dependency |
| Gen 2 | OpenClaw (145k+ GH stars) | Messaging-native, persistent memory, skills ecosystem | Developer requirement |
| Gen 3 | Ralph Wiggum loops | Stateless iteration, AFK coding, ship-while-you-sleep | Human presence |
| Gen 4 | ??? (what we're building toward) | Self-improving, coordinated, org-aware agents | Isolation, amnesia, single-task limits |

### The Local-First Thesis

A critical strategic insight: **the agents that achieved mass adoption all run locally.** OpenClaw, Claude Code CLI, and Ralph Wiggum loops succeed because they have direct access to the user's local filesystem, installed tools, and existing integrations — no OAuth flows, no GitHub app permissions, no cloud sandbox friction.

Cloud-first agents (Codex background agents, Cursor cloud, Devin) struggled with adoption despite heavy funding. The core problem is connection friction — getting a cloud agent authenticated against your GitHub, your databases, your APIs is a multi-step onboarding gauntlet that kills conversion. Local agents skip all of that. They inherit the user's environment. `git`, `npm`, `docker`, `psql` — everything just works.

The industry is converging on this insight: Codex shipped a desktop app running local automations via cron jobs. Claude desktop added SSH access. **MTS should be built local-first.** Cloud orchestration can come later as a coordination layer, not as the primary execution environment.

### OpenClaw's Autonomy Primitives

Two primitives worth studying:

**HEARTBEAT.md** — Periodic autonomy mechanism. Background daemon with configurable heartbeat (default 30 min). On each heartbeat, the agent reads a checklist, decides whether any item requires action, and either messages the user or silently returns HEARTBEAT_OK. Unlike a cron job that executes blindly, the heartbeat lets the agent exercise periodic *judgment*.

**SOUL.md** — Agent identity and behavior definition. Every time the agent wakes, it reads SOUL.md — it reads itself into being. Critically, this file is writable. The agent can modify its own soul. This is the self-improvement hook.

The combination: persistent identity (SOUL.md), periodic autonomy (HEARTBEAT.md), accumulated memory (disk files), social context (agent-to-agent discovery). Costs: Claude Opus heartbeats run $5-30/day. Gaps: no audit trail between heartbeats, no approval workflow, no visibility into agent coordination.

### The Ralph Wiggum Pattern

Deceptively simple — a bash while loop that repeatedly feeds an agent a prompt until work is complete:

```bash
while :; do cat PROMPT.md | claude-code ; done
```

Key architectural decisions:
- **Fresh context per iteration**: No context window overflow, no lossy compaction
- **Memory via filesystem**: State persists through git history, progress.txt, structured JSON
- **AFK-native**: Designed for unsupervised execution with configurable caps and circuit breakers

What Ralph gets right: **separation of cognition from state**. The agent is ephemeral; the project state is durable.

### Six Infrastructure Gaps Identified

#### 1. Structured State Layer (Beyond progress.txt)
An append-only project state layer — richer than flat files but lighter than a knowledge graph. Should capture decisions made, context learned, architectural constraints discovered, and rationale.

Requirements: Schema-versioned state, decision log with rationale, dependency graph of tasks, conflict detection for multi-agent overlap, compact enough for context windows.

#### 2. Multi-Agent Coordination Protocol
Currently every agent loop is isolated. Real work involves interdependencies — a frontend agent needs the API contract from a backend agent.

Requirements: Typed message-passing between agent processes, shared artifact registry, coordination primitives (locks, semaphores, barriers), event bus for cross-agent notifications.

#### 3. Supervision & Control Plane
One loop on one repo is manageable. Forty loops across an org's monorepo is chaos without visibility.

Requirements: Aggregate dashboard, cost budgeting and rate limiting, anomaly detection, policy enforcement, full audit trail.

#### 4. Plugin Trust & Capability Security
As agents become always-on and more autonomous, the plugin/skill marketplace becomes a critical attack surface.

Requirements: Capability-based security model, sandboxed execution per plugin, behavioral monitoring, signed plugins, runtime permission escalation requires human approval.

#### 5. Self-Evaluation & Intelligent Termination
Agents need to assess their own confidence, recognize when they're stuck, and make economically rational decisions about whether to keep iterating.

Requirements: Confidence scoring per iteration, cost-aware termination, stuck detection with escalation paths, quality gates, self-improving prompts.

#### 6. Ephemeral Environment Provisioning
Complex tasks need richer environments on demand. Current approaches (VMs, Docker) are too slow.

Requirements: API-driven environment spin-up with sub-second cold starts, declarative environment specs, snapshot/restore, cost-optimized lifecycle management.

---

## Part 2: Architecture Audit Results

A comprehensive three-agent deep audit was performed on the MTS repository, covering all major subsystems. The finding: **MTS already implements working versions of all six infrastructure gaps**, but they're coupled to the strategy-tournament domain.

### Gap-to-Implementation Mapping

| Infrastructure Gap | MTS Implementation | Location | Maturity |
|---|---|---|---|
| 1. Structured State | SQLite (6 tables) + ArtifactStore (versioned playbooks, analysis, tools, hints, snapshots) + ndjson event log | `storage/`, `knowledge/trajectory.py`, `knowledge/export.py` | 8/10 |
| 2. Multi-Agent Coordination | 5-role orchestrator with DAG ordering, parallel execution via ThreadPoolExecutor, shared knowledge directory | `agents/orchestrator.py`, `loop/generation_runner.py` | 7/10 |
| 3. Supervision & Control Plane | EventStreamEmitter, WebSocket dashboard, LoopController (pause/resume/hint/override), per-role cost telemetry | `loop/events.py`, `loop/controller.py`, `server/app.py`, `server/protocol.py` | 7/10 |
| 4. Plugin Security | Per-role tool scoping (Agent SDK), sandboxed REPL (RLM), subprocess isolation (LocalExecutor), MCP sandboxes | `agents/agent_sdk_client.py`, `rlm/repl_worker.py`, `mcp/sandbox.py` | 6/10 |
| 5. Self-Evaluation | Backpressure gate (advance/retry/rollback), trend-aware plateau detection, curator quality gate, Elo scoring | `backpressure/gate.py`, `backpressure/trend_gate.py`, `agents/curator.py` | 8/10 |
| 6. Ephemeral Environments | MCP SandboxManager (isolated SQLite + knowledge + runs), PrimeIntellect remote execution, LocalExecutor subprocess | `mcp/sandbox.py`, `execution/executors/` | 6/10 |

### Extractable Primitives (ranked by reuse value)

These components can be extracted from MTS with minimal modification:

**1. `ReplWorker`** (`rlm/repl_worker.py`)
- Complete sandboxed Python REPL. Zero MTS imports.
- Restricted builtins, safe module whitelist, dual-path timeout (SIGALRM + thread), stdout capture with truncation.
- Standalone library candidate — extract as-is.

**2. `RlmSession`** (`rlm/session.py`)
- Generic multi-turn "LLM writes code, REPL executes it, results feed back" loop.
- One trivial MTS type dependency (`RoleExecution`).
- The agent-with-code-execution pattern generalized.

**3. `EventStreamEmitter`** (`loop/events.py`)
- Generic ndjson event file + in-memory pub/sub. 41 lines.
- Needs: thread safety, channel filtering, async option, proper error handling.

**4. `LanguageModelClient` + `SubagentRuntime`** (`agents/llm_client.py`, `agents/subagent_runtime.py`)
- Clean "call an LLM" abstraction with three providers (Anthropic, Agent SDK, Deterministic).
- `generate()` / `generate_multiturn()` interface is fully generic.

**5. `ExecutionEngine` protocol + `LocalExecutor`** (`execution/executors/`)
- Structural typing protocol for isolated execution.
- Subprocess + RLIMIT_AS + ProcessPoolExecutor with ThreadPoolExecutor fallback.

**6. `BackpressureGate` + `TrendAwareGate`** (`backpressure/`)
- Configurable advance/retry/rollback decision engine with plateau detection.
- The "evaluate iteration quality and decide whether to continue" primitive.

**7. SQLite migration system** (`storage/sqlite_store.py`)
- Schema-versioned SQLite with file-ordered migrations and tracking table.

**8. WebSocket protocol** (`server/protocol.py`)
- Pydantic-validated, discriminated-union typed, JSON-Schema-exportable.
- 413 lines. Enterprise-grade pattern.

### Three Domain-Coupling Bottlenecks

**Bottleneck 1: `AgentOutputs` is a closed type** (`agents/types.py:24-34`)
- Fields: `strategy`, `coach_playbook`, `coach_lessons`, `architect_tools` — pure tournament concepts.
- Harness needs: `dict[str, RoleExecution]` or typed pipeline output registry.

**Bottleneck 2: Orchestrator is a hardcoded DAG** (`agents/orchestrator.py:75-141`)
- Exact sequence baked in: competitor → translator → analyst → coach+architect parallel → curator.
- Harness needs: configurable pipeline — define roles, dependencies, prompts, parsers.

**Bottleneck 3: `ScenarioInterface` is game-oriented** (`scenarios/base.py:63-137`)
- Methods: `initial_state`, `step`, `is_terminal`, `execute_match` assume turn-based simulation.
- Harness needs: `EvaluationInterface` — `describe()`, `validate(input)`, `evaluate(input, seed)`, `explain(result)`.

### What's Actually Missing (not just locked away)

These capabilities do NOT exist in MTS today:

1. **Periodic autonomy (HEARTBEAT.md)** — No daemon mode. LoopController requires active WebSocket connection.
2. **Writable identity (SOUL.md)** — Agents can't modify their own prompts/behavior. Curator updates playbooks but not agent instructions.
3. **Peer-to-peer agent discovery** — Top-down orchestration only. No message passing between agents across pipelines.
4. **Cost-aware termination** — Token usage tracked but not used in gate decisions.
5. **Decision rationale capture** — System captures WHAT happened but not structured WHY.
6. **Local environment inheritance** — Runs in own directory. Doesn't inherit user's git/npm/docker.

---

## Part 3: Strategic Recommendation

### The Inversion

Instead of "add harness capabilities to MTS," we propose **inverting the dependency**: extract the generic harness layer OUT of MTS, then rebuild MTS as the first application on that harness.

```
mts-harness/                    # NEW: Generic agent infrastructure
  core/
    llm_client.py              # <- extracted from agents/llm_client.py
    repl_worker.py             # <- extracted from rlm/repl_worker.py
    repl_session.py            # <- extracted from rlm/session.py
    events.py                  # <- extracted from loop/events.py
    store.py                   # <- extracted from storage/sqlite_store.py
    artifacts.py               # <- extracted from storage/artifacts.py
    executor.py                # <- extracted from execution/executors/base.py
    local_executor.py          # <- extracted from execution/executors/local.py
  pipeline/
    pipeline.py                # NEW: Configurable role DAG engine
    gate.py                    # <- extracted from backpressure/gate.py
    trend_gate.py              # <- extracted from backpressure/trend_gate.py
  identity/
    soul.py                    # NEW: Writable agent identity
    heartbeat.py               # NEW: Periodic autonomy daemon
  control/
    controller.py              # <- extracted from loop/controller.py
    protocol.py                # <- generalized from server/protocol.py
    server.py                  # <- generalized from server/app.py
  knowledge/
    trajectory.py              # <- generalized from knowledge/trajectory.py
    search.py                  # <- generalized from knowledge/search.py
    export.py                  # <- generalized from knowledge/export.py
  mcp/
    server.py                  # <- generalized from mcp/server.py
    sandbox.py                 # <- extracted from mcp/sandbox.py

mts/                           # EXISTING: Strategy tournament app
  src/mts/
    scenarios/                 # Unchanged - ScenarioInterface implementations
    agents/                    # Simplified - role configs + prompt templates only
    loop/                      # Simplified - pipeline config + tournament logic
    ...                        # Everything else imports from mts-harness
```

### Challenges to Original Assumptions

**"Wrap MTS in a Ralph Wiggum loop."** — Not needed. MTS's GenerationRunner IS a loop — more sophisticated than Ralph. Generations are iterations. Backpressure is termination logic. Knowledge directory is filesystem memory. The actual gap is making this loop configurable for non-tournament tasks.

**"Replace flat-file state with transaction log."** — MTS already has this. SQLite + ArtifactStore + ndjson events. The ndjson event file IS a transaction log. Need: make stores domain-agnostic and add decision rationale capture.

**"Build local-first."** — MTS is already local-first. SQLite, filesystem artifacts, local subprocess execution. Gap is exposing the user's local environment TO the agents.

---

## Part 4: Phased Implementation Plan

### Phase 1 — Extract Core Primitives
**Effort**: 2-3 weeks | **Risk**: Low (pure refactor)

Extract domain-agnostic components into a `harness/` package within the repo (monorepo approach before splitting):

**Batch 1.1: Core extraction**
- Extract `ReplWorker` into `harness/core/repl_worker.py` — zero MTS imports, lift as-is
- Extract `LanguageModelClient` + `ModelResponse` + `SubagentRuntime` + `SubagentTask` into `harness/core/llm.py`
- Extract `RoleExecution` + `RoleUsage` into `harness/core/types.py`
- Extract `EventStreamEmitter` into `harness/core/events.py`, add thread safety (Lock around `_subscribers` and `_sequence`)
- MTS imports from `harness.core` instead of inline definitions

**Batch 1.2: Storage extraction**
- Extract the migration system from `SQLiteStore` into `harness/core/migration.py` — the `migrate()` method and schema tracking
- Extract the versioned-artifact pattern from `ArtifactStore` into `harness/core/artifacts.py` — versioned writes with archive, rollback, pruning
- MTS's `SQLiteStore` and `ArtifactStore` inherit from / compose with the harness versions

**Batch 1.3: Execution extraction**
- Extract `ExecutionEngine` protocol into `harness/core/executor.py`
- Extract `LocalExecutor` subprocess isolation into `harness/core/local_executor.py`
- Extract `BackpressureGate` + `TrendAwareGate` + `GateDecision` into `harness/pipeline/gate.py`

**Validation**: All 330+ existing tests must pass. No behavior changes. Run full CI.

### Phase 2 — Configurable Pipeline Engine
**Effort**: 2-3 weeks | **Risk**: Medium (new abstraction layer)

Build the missing intermediate abstraction — a pipeline engine that sits between "call an LLM" and "run the MTS generation loop."

**Batch 2.1: Pipeline definition**
```python
# harness/pipeline/pipeline.py

@dataclass
class RoleConfig:
    name: str
    model: str
    prompt_template: Callable[..., str]  # accepts context, returns prompt
    parser: Callable[[str], dict[str, Any]]  # parses raw LLM output
    max_tokens: int = 2000
    temperature: float = 0.3
    tools: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)

@dataclass
class PipelineConfig:
    name: str
    roles: list[RoleConfig]
    gate: BackpressureGate
    evaluator: EvaluationInterface  # NEW — see Batch 2.2
    max_iterations: int = 10
    max_retries: int = 2

class PipelineEngine:
    """Executes a configured pipeline of LLM roles with evaluation gating."""
    def __init__(self, config: PipelineConfig, llm: LanguageModelClient,
                 store: SQLiteStore, artifacts: ArtifactStore,
                 events: EventStreamEmitter): ...

    def run(self, task_id: str, iterations: int, context: dict[str, Any]) -> RunSummary:
        """Run the pipeline for N iterations with backpressure gating."""
        ...
```

**Batch 2.2: Evaluation interface**
```python
# harness/pipeline/evaluation.py

class EvaluationInterface(Protocol):
    def describe(self) -> str:
        """Human-readable description of what's being evaluated."""

    def validate(self, artifact: dict[str, Any]) -> tuple[bool, str]:
        """Validate an artifact before evaluation."""

    def evaluate(self, artifact: dict[str, Any], seed: int) -> EvalResult:
        """Evaluate an artifact, return scored result."""

    def explain(self, result: EvalResult) -> str:
        """Human-readable explanation of the result."""
```

**Batch 2.3: Rebuild MTS on pipeline**
- Implement `TournamentEvaluator(EvaluationInterface)` wrapping existing `TournamentRunner`
- Implement `ScenarioAdapter` converting `ScenarioInterface` methods to `EvaluationInterface`
- Rebuild `AgentOrchestrator` as a `PipelineConfig` with 5 `RoleConfig` entries
- Rebuild `GenerationRunner.run()` to delegate to `PipelineEngine.run()`
- All existing tests must still pass

### Phase 3 — Identity & Autonomy
**Effort**: 1-2 weeks | **Risk**: Low (new features, no breaking changes)

**Batch 3.1: Writable identity (SOUL.md equivalent)**
```yaml
# harness.yaml — read on startup, writable by the agent
identity:
  name: "mts-strategy-evolver"
  version: 1
  purpose: "Evolve optimal strategies for game scenarios through iterative LLM-driven exploration"

behavior:
  risk_tolerance: 0.3        # how aggressively to explore vs exploit
  escalation_threshold: 3    # consecutive failures before escalating to human
  cost_budget_usd: 50.0      # per-run cost limit

context_summary: |
  Last run: grid_ctf, 5 generations, best score 0.847
  Key lesson: aggression > 0.8 with defense < 0.4 consistently fails
  Current approach: balanced offense/defense with zone control

accumulated_knowledge:
  scenarios_solved: ["grid_ctf", "othello"]
  total_runs: 12
  total_generations: 47
```

- Agent reads `harness.yaml` on startup
- Agent can update `context_summary` and `behavior` fields after each run
- History tracked via git or append-only changelog

**Batch 3.2: Decision rationale log**
- New `decisions.ndjson` — append-only log of structured decisions:
  ```json
  {"ts": "...", "iteration": 3, "decision": "advance", "reason": "score delta 0.012 > threshold 0.005",
   "alternatives_considered": ["retry with higher aggression", "rollback to gen 1 playbook"],
   "confidence": 0.7, "cost_so_far_usd": 2.34}
  ```
- Injected into agent prompts as "decision history" context block

**Batch 3.3: Periodic autonomy (HEARTBEAT.md equivalent)**
- `heartbeat.yaml` — checklist of periodic checks:
  ```yaml
  interval_minutes: 30
  checks:
    - name: "check_stalled_runs"
      description: "Look for runs that haven't progressed in > 1 hour"
      action: "notify_user"
    - name: "check_new_scenarios"
      description: "Look for new custom scenarios that haven't been optimized"
      action: "start_run"
    - name: "consolidate_knowledge"
      description: "Check if any scenario has > 30 unconsolidated lessons"
      action: "run_curator"
  ```
- LaunchAgent (macOS) / systemd (Linux) daemon template
- Each heartbeat invocation: read harness.yaml, read heartbeat.yaml, evaluate checks, act or return OK
- Cost tracking per heartbeat cycle

### Phase 4 — External Agent Interface
**Effort**: 2-3 weeks | **Risk**: Medium (API design decisions)

Generalize the MCP server so external agents can use MTS as infrastructure.

**Batch 4.1: Generic MCP tools**
- `harness_create_pipeline` — define a pipeline from role configs
- `harness_run_pipeline` — execute a pipeline with context
- `harness_query_knowledge` — search accumulated knowledge across all pipelines
- `harness_get_trajectory` — get iteration history for any pipeline run
- `harness_create_sandbox` — isolated environment for experimentation
- Keep existing `mts_*` tools as a "strategy tournament" tool group

**Batch 4.2: Generalized Knowledge API**
- `GET /api/harness/pipelines` — list all pipeline types
- `GET /api/harness/runs` — list runs across all pipelines
- `POST /api/harness/search` — search knowledge across all pipeline types
- `POST /api/harness/solve` — submit a problem, get back a skill package
- Existing `/api/knowledge/*` routes remain as MTS-specific shortcuts

**Batch 4.3: Portable skill packages**
- Generalize `SkillPackage` to work with any pipeline output
- Export format: markdown + JSON + metadata, importable into any agent framework
- Include decision log and trajectory in exports

### Phase 5 — Fleet & Coordination
**Effort**: 3-4 weeks | **Risk**: High (distributed systems concerns)

**Batch 5.1: Cost-aware termination**
- Track cumulative spend per pipeline run (sum of `RoleUsage` across iterations)
- Add `cost_budget_usd` to `PipelineConfig`
- Gate decision factors in cost: `if cumulative_cost > budget * 0.9: force_advance`
- Expected-value calculation: `EV(next_iteration) = P(improvement) * value(improvement) - cost(iteration)`

**Batch 5.2: Agent-to-agent discovery**
- Shared artifact registry extending `ArtifactStore`
- Agents can publish named artifacts (API specs, schemas, interfaces)
- Agents can subscribe to artifact updates
- Event bus for cross-pipeline notifications

**Batch 5.3: Fleet dashboard**
- Aggregate view of all active pipelines
- Per-pipeline: current iteration, score trajectory, cost, time since last progress
- Stuck detection: flag pipelines with > N consecutive retries or > T time without advance
- Policy enforcement: block pipelines from exceeding cost/time budgets

---

## Part 5: Priority & Sequencing

### What to Build First

The single highest-leverage move: **Phase 2 (Pipeline Engine)**. Everything else is refinement. The pipeline engine is the missing layer that turns MTS from "a strategy tournament system" into "a configurable agent loop that happens to ship with a strategy tournament as its first application."

However, Phase 1 (core extraction) is a prerequisite — you need clean primitives before building the pipeline abstraction on top.

### Recommended Sequence

```
Phase 1 (Extract Core)     ████████░░  2-3 weeks
                                  ↓
Phase 2 (Pipeline Engine)   ░░████████  2-3 weeks
                                  ↓
Phase 3 (Identity)          ░░░░████░░  1-2 weeks  (can partially overlap Phase 2)
                                  ↓
Phase 4 (External API)      ░░░░░░████  2-3 weeks
                                  ↓
Phase 5 (Fleet)             ░░░░░░░░██  3-4 weeks  (can be deferred)
```

**Total estimated effort**: 10-15 weeks for Phases 1-4. Phase 5 can be deferred until there's actual multi-pipeline demand.

### Decision Points

| After Phase | Decision |
|---|---|
| Phase 1 | Do we split `mts-harness` into a separate package, or keep as monorepo? |
| Phase 2 | Do we ship the pipeline engine as a standalone tool, or keep it MTS-internal? |
| Phase 3 | What's the daemon hosting model? LaunchAgent? Docker? Claude Code hook? |
| Phase 4 | What's the MCP tool naming convention for the harness vs MTS-specific tools? |

### Success Metrics

- **Phase 1**: All 330+ tests pass. Zero behavior changes. Clean import boundaries.
- **Phase 2**: Can define a non-tournament pipeline (e.g., code review) using only `PipelineConfig` — no MTS-specific code required.
- **Phase 3**: Agent can update its own `harness.yaml` after a run and use it to make better decisions next run.
- **Phase 4**: An external Claude Code agent can invoke `harness_create_pipeline` + `harness_run_pipeline` via MCP and get back a skill package.
- **Phase 5**: Dashboard shows 3+ concurrent pipelines with cost tracking and stuck detection.
