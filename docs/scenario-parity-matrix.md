# Scenario Parity Matrix — Python & TypeScript

> Produced for [AC-431](https://linear.app/greyhaven/issue/AC-431). Captures the current state of scenario surfaces, creation flows, and runtime support across both packages.

## Product Goal

> A user can describe a scenario, task, mission, or related objective in plain language, and the agent can build, develop, use, think through, and adapt the runtime structures it needs in real time to improve its ultimate output.

Built-in scenarios are **not** the product. They are deterministic test fixtures for CI and development. The actual success criterion is the plain-language creation → runtime adaptation → iterative improvement loop. This matrix measures how close each package is to delivering that end-to-end.

## 1. Built-in Deterministic Fixtures

> **These exist for testing only.** They are hardcoded harness surfaces for CI smoke tests and deterministic regression coverage. They are not the product abstraction and should not be confused with the plain-language creation flow that represents the real user-facing value.

These are hardcoded scenarios registered in `SCENARIO_REGISTRY` at import time. They exist primarily as **deterministic test fixtures** and CI smoke-test surfaces, not as the primary product abstraction.

| Fixture | Python (`autocontext/`) | TypeScript (`ts/`) | Type | Notes |
|---------|:-----------------------:|:------------------:|------|-------|
| `grid_ctf` | ✅ Registered | ✅ Registered | Game | Full `ScenarioInterface`; used in CI smoke tests |
| `othello` | ✅ Registered | ✅ Registered | Game | Full `ScenarioInterface` |
| `resource_trader` | ❌ | ✅ Registered | Game | TS-only built-in game scenario |
| `word_count` | ❌ | ✅ Registered (in `AGENT_TASK_REGISTRY`) | Agent task | TS-only deterministic agent task (algorithmic eval, no LLM judge needed) |

**Key point:** Built-in fixtures are test harnesses. The real product goal is plain-language scenario creation → runtime execution → improvement.

**Note:** TypeScript now has a separate `AGENT_TASK_REGISTRY` for built-in agent tasks with deterministic evaluation, in addition to `SCENARIO_REGISTRY` for game scenarios.

## 2. Scenario Family Registry

Both packages define the same 11 scenario families. Family metadata is registered at import time.

| Family | Python | TypeScript | Evaluation Mode | Output Modes |
|--------|:------:|:----------:|-----------------|-------------|
| `game` | ✅ `ScenarioInterface` | ✅ `ScenarioInterface` | `tournament` | `json_strategy` |
| `agent_task` | ✅ `AgentTaskInterface` | ✅ (type guards only) | `llm_judge` | `free_text`, `code`, `json_schema` |
| `simulation` | ✅ `SimulationInterface` | ✅ (spec + creator) | `trace_evaluation` | `action_trace` |
| `artifact_editing` | ✅ `ArtifactEditingInterface` | ✅ (spec + creator) | `artifact_validation` | `artifact_diff` |
| `investigation` | ✅ `InvestigationInterface` | ✅ (spec + creator) | `evidence_evaluation` | `action_trace` |
| `workflow` | ✅ `WorkflowInterface` | ✅ (spec + creator) | `workflow_evaluation` | `action_trace` |
| `negotiation` | ✅ `NegotiationInterface` | ✅ (spec + creator) | `negotiation_evaluation` | `action_trace` |
| `schema_evolution` | ✅ `SchemaEvolutionInterface` | ✅ (spec + creator) | `schema_adaptation` | `action_trace` |
| `tool_fragility` | ✅ `ToolFragilityInterface` | ✅ (spec + creator) | `drift_adaptation` | `action_trace` |
| `operator_loop` | ✅ `OperatorLoopInterface` | ✅ (spec + creator) | `judgment_evaluation` | `action_trace` |
| `coordination` | ✅ `CoordinationInterface` | ✅ (spec + creator) | `coordination_evaluation` | `action_trace` |

**Python** defines full ABCs per family with typed interface classes. **TypeScript** defines type markers, Zod schemas, and runtime type guards for all 11 families (`isGameScenario`, `isAgentTask`, `isSimulation`, `isNegotiation`, etc. in `family-interfaces.ts`) but uses structural typing rather than runtime class hierarchies.

## 3. Creation Pipeline Components

The creation pipeline takes a plain-language description through: **classify → design → spec → codegen → validate → register**.

### Python (`autocontext/`)

| Component | `agent_task` | `simulation` | `artifact_editing` | `investigation` | `workflow` | `negotiation` | `schema_evolution` | `tool_fragility` | `operator_loop` | `coordination` |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Designer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Spec schema | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Codegen | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ disabled | ✅ |
| Creator | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ disabled | ✅ |
| Validator | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Pipeline (`FamilyPipeline`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Family classifier | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Note:** Only `agent_task` has a dedicated validator module (`agent_task_validator.py`). All other families validate via their `FamilyPipeline.validate_spec()` and `validate_source()` methods. `operator_loop` is the explicit exception on the runtime side: the family metadata, spec, and pipeline exist, but code generation and creator scaffolding are intentionally disabled.

### TypeScript (`ts/`)

| Component | `agent_task` | `simulation` | `artifact_editing` | `investigation` | `workflow` | `negotiation` | `schema_evolution` | `tool_fragility` | `operator_loop` | `coordination` |
|-----------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Designer | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Spec schema (Zod) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Codegen | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Creator | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Validator | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Pipeline (`FamilyPipeline`) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Family classifier | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |

**Key gap:** TypeScript has **no codegen modules** for any family. Python generates executable Python source code per family; TS creators produce spec scaffolds but not runnable source.

## 4. Plain-Language Creation Flows

How a user goes from a text description to a runnable scenario.

### Python

| Path | Command | What it does | Families supported |
|------|---------|-------------|-------------------|
| **Template scaffolding** | `autoctx new-scenario --template <t> --name <n>` | Scaffolds from built-in templates (`content-generation`, `prompt-optimization`, `rag-accuracy`) | `agent_task` only |
| **Solve on demand** | `autoctx solve --description "..."` | NL → legacy `ScenarioCreator` → generic scenario spec → codegen → validate → register → run GenerationRunner → export package | Legacy generic `ScenarioInterface` path; not family-aware |
| **MCP tool** | `autocontext_solve_scenario` | Same solve-manager pipeline exposed via MCP server | Same legacy generic solve path |
| **Custom loader** | Automatic on startup | Scans `knowledge/_custom_scenarios/` and registers persisted scenarios | All families with persisted artifacts |

**Python's public solve path is still legacy:** `autoctx solve` and `autocontext_solve_scenario` currently use the older generic `ScenarioCreator`, not the richer family-specific creators. The family-specific Python creators do exist internally for most families, but that is not yet the path exposed by the public solve/MCP surface.

### TypeScript

| Path | Command | What it does | Families supported |
|------|---------|-------------|-------------------|
| **Template scaffolding** | `autoctx new-scenario --template <t> --name <n>` | Scaffolds from built-in templates (`content-generation`, `prompt-optimization`, `rag-accuracy`) into `knowledge/_custom_scenarios/` | `agent_task` only |
| **NL creation** | `autoctx new-scenario --description "..."` | NL → lightweight `createScenarioFromDescription()` → produces `name`, `family`, `taskPrompt`, `rubric` | All families (spec only) |
| **From spec** | `autoctx new-scenario --from-spec <file>` | Validates and echoes spec | All families |
| **Solve on demand** | Via `SolveManager` (CLI `solve` not yet a subcommand, but accessible via MCP/server) | NL → `createScenarioFromDescription()` → check `SCENARIO_REGISTRY` → run `GenerationRunner` if found | **Only `game` family** |
| **Custom loader** | `loadCustomScenarios()` | Scans `knowledge/_custom_scenarios/` and registers agent-task specs | `agent_task` (other types stored but not runnable) |

**TypeScript solve collapses to game-only execution:** The `SolveManager.runJob()` looks up the created scenario name in `SCENARIO_REGISTRY` (which only contains `grid_ctf`). If the name isn't found, it persists a scaffold and throws an error. Even when a non-game family is correctly classified and designed, the execution path fails because `GenerationRunner` requires a `ScenarioInterface` (game contract). This is the gap documented in **AC-434**.

**TypeScript `new-scenario` doesn't materialize artifacts:** The `--description` path produces a spec but does not persist a runnable artifact under `knowledge/_custom_scenarios/`. This is the gap documented in **AC-433**.

## 5. Runtime Execution Support

**This is the table that matters.** Can a user describe something in plain language and have the agent build, run, and iteratively improve it?

| Family | Python Runtime | TypeScript Runtime | Gap |
|--------|:--------------:|:------------------:|-----|
| `game` | ✅ `GenerationRunner` (tournament + Elo) | ✅ `GenerationRunner` (tournament + Elo) | Parity |
| `agent_task` | ✅ `ImprovementLoop` + `LLMJudge` + `TaskRunner` | ✅ `ImprovementLoop` + `judge()` + `TaskRunner` | Parity |
| `simulation` | ✅ Via custom codegen → `ScenarioInterface` subclass | ❌ No codegen; creator produces spec only | **AC-434** |
| `artifact_editing` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `investigation` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `workflow` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `negotiation` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `schema_evolution` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `tool_fragility` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |
| `operator_loop` | ❌ Scaffolding intentionally disabled | ❌ No codegen | **AC-432**, **AC-434** |
| `coordination` | ✅ Via custom codegen | ❌ No codegen | **AC-434** |

**Summary:** Python has internal family-specific creation/runtime support for most custom families, but the public `solve`/MCP path still uses a legacy generic scenario creator and `operator_loop` remains intentionally unsupported. TypeScript correctly classifies and designs specs, but for 9 of 11 families the result **cannot actually execute** because there is no codegen step to turn the spec into runnable code. In both packages, the user-facing plain-language story is therefore narrower than the internal family metadata suggests.

## 6. Explicit Limitations & Mismatches

### TypeScript is missing:

1. **Codegen pipeline** — No `*_codegen.ts` modules exist. Python generates executable `.py` source per family; TS has no equivalent.
2. **Runtime execution for 9 families** — `simulation`, `artifact_editing`, `investigation`, `workflow`, `negotiation`, `schema_evolution`, `tool_fragility`, `operator_loop`, `coordination` are design-only.
3. **`new-scenario` artifact materialization** — Does not persist durable scenario artifacts (AC-433).
4. **`solve` family awareness** — Collapses to game-only execution (AC-434).
5. **Spec auto-heal** — Python has `spec_auto_heal.py`; TS does not.
6. **Agent task revision** — Python has `agent_task_revision.py`; TS does not.
7. **Scenario templates** — Python has a `templates/` library (content-generation, prompt-optimization, rag-accuracy); TS has none.

### Python is missing:

1. **`resource_trader` fixture** — TS-only built-in game scenario; not ported to Python.
2. **`word_count` fixture** — TS-only deterministic agent task; not ported to Python.
3. **`AGENT_TASK_REGISTRY`** — TS has a separate registry for built-in agent tasks with algorithmic evaluation; Python does not distinguish these from custom agent tasks.
4. **Family-aware public solve/MCP path** — `autoctx solve` and `autocontext_solve_scenario` still use the legacy generic `ScenarioCreator`, not the richer family-specific creation pipeline.
5. **Executable `operator_loop` scaffolding** — Family metadata exists, but runtime scaffolding is intentionally disabled.

### Both packages share:

- Same 11 family names and type markers
- Same family classification logic
- Same custom scenario persistence layout (`knowledge/_custom_scenarios/<name>/`)
- Same migration SQL (cross-compatible)
- Solve-on-demand MCP entrypoints, but with different runtime depth and naming conventions

## 7. Follow-up Issues

| Issue | Summary | Status |
|-------|---------|--------|
| **AC-432** | Decide operator_loop support scope | Backlog |
| **AC-433** | TS `new-scenario` must materialize runnable artifacts, not just specs | Backlog |
| **AC-434** | TS `solve` must honor family-aware created scenarios instead of collapsing to game-only | Backlog |
| *New* | Align Python `solve`/MCP with the family-specific creator pipeline, or explicitly document the legacy generic scope | To create |
| *New* | Add codegen modules to TypeScript for non-game families (prerequisite for AC-434) | To create |
| *New* | Port scenario templates to TypeScript (`content-generation`, `prompt-optimization`, `rag-accuracy`) | To create |
| *New* | Add spec auto-heal to TypeScript | To create |
| *New* | Port `resource_trader` and `word_count` built-in scenarios to Python | To create |

---

*Last updated: 2026-03-26*
