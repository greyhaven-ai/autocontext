# Playbook Generator — Implementation Roadmap

## Vision
"Describe what you want your agent to do well. MTS figures out how, and gives you the playbook."

Users describe tasks in natural language → MTS generates scenarios → LLM-as-judge scores outputs → tournament grinds through generations → user exports a distilled skill package that makes agents perform better with fewer tokens.

## Current State (as of 2026-03-02)

### Python — Mostly Complete ✅
All foundational pieces exist and are tested:

| Component | Location | Tests | Status |
|-----------|----------|-------|--------|
| `AgentTaskInterface` ABC | `mts/src/mts/scenarios/agent_task.py` | `test_agent_task.py` (8) | ✅ Done |
| `JudgeExecutor` | `mts/src/mts/execution/judge_executor.py` | via integration tests | ✅ Done |
| `LLMJudge` (4-tier fallback) | `mts/src/mts/execution/judge.py` | `test_judge.py` | ✅ Done |
| `ImprovementLoop` | `mts/src/mts/execution/improvement_loop.py` | multiple test files | ✅ Done |
| `TaskRunner` daemon | `mts/src/mts/execution/task_runner.py` | `test_task_runner.py` | ✅ Done |
| Agent task creator pipeline | `mts/src/mts/scenarios/custom/agent_task_*.py` | `test_agent_task_pipeline.py` (23) | ✅ Done |
| Skill export (agent task fields) | `mts/src/mts/knowledge/export.py` | `test_agent_task_export.py` (21) | ✅ Done |
| MCP tools for agent tasks | `mts/src/mts/mcp/tools.py` | `test_mcp_agent_tasks.py` (18) | ✅ Done |
| Human feedback storage | `mts/migrations/006_human_feedback.sql` | `test_human_feedback.py` (12) | ✅ Done |
| Judge calibration from feedback | `mts/src/mts/execution/judge.py` | `test_human_feedback.py` | ✅ Done |
| Orchestrator feedback loop | ? | `test_orchestrator_feedback.py` (4) | ✅ Done |
| **Total agent task tests** | | **86+** | |

### TypeScript — Partial Port
The TS port has the core evaluation loop but is missing:

| Component | Python equivalent | TS Status |
|-----------|-------------------|-----------|
| `AgentTaskInterface` | `scenarios/agent_task.py` | ✅ In `types/index.ts` |
| `ImprovementLoop` | `execution/improvement_loop.py` | ✅ Done |
| `SimpleAgentTask` | `execution/task_runner.py` | ✅ Done |
| `TaskRunner` | `execution/task_runner.py` | ✅ Done |
| `LLMJudge` | `execution/judge.py` | ✅ Done |
| `JudgeExecutor` | `execution/judge_executor.py` | ❌ Not ported |
| Agent task creator | `scenarios/custom/agent_task_creator.py` | ❌ Not ported |
| Agent task designer | `scenarios/custom/agent_task_designer.py` | ❌ Not ported |
| Agent task codegen | `scenarios/custom/agent_task_codegen.py` | ❌ Not ported |
| Agent task validator | `scenarios/custom/agent_task_validator.py` | ❌ Not ported |
| Skill export | `knowledge/export.py` | ❌ Not ported |
| Human feedback storage | `migrations/006_human_feedback.sql` | ❌ Not ported |
| Calibration from feedback | judge.py calibration path | ❌ Not ported |

## Remaining Work

### PR 1: JudgeExecutor + Feedback Storage (TS) — CURRENT
Port to TypeScript:
- `JudgeExecutor` class (thin wrapper, delegates to `AgentTaskInterface.evaluateOutput`)
- Human feedback table migration (`006_human_feedback.sql` → TS migrations)
- `SQLiteStore` methods: `insertHumanFeedback`, `getHumanFeedback`, `getCalibrationExamples`
- Tests

Key files to reference:
- `mts/src/mts/execution/judge_executor.py`
- `mts/migrations/006_human_feedback.sql`
- `mts/src/mts/storage/sqlite_store.py` (feedback methods)
- `mts/tests/test_human_feedback.py`

### PR 2: Agent Task Creator Pipeline (TS)
Port the NL → scenario creation pipeline:
- `AgentTaskSpec` dataclass → Zod schema
- `AgentTaskDesigner` — LLM generates spec from NL description
- `AgentTaskCodegen` — generates TypeScript `AgentTaskInterface` impl from spec
- `AgentTaskValidator` — validates generated code
- `AgentTaskCreator` — orchestrates designer → codegen → validator → instantiate

Key files to reference:
- `mts/src/mts/scenarios/custom/agent_task_spec.py`
- `mts/src/mts/scenarios/custom/agent_task_designer.py`
- `mts/src/mts/scenarios/custom/agent_task_codegen.py`
- `mts/src/mts/scenarios/custom/agent_task_validator.py`
- `mts/src/mts/scenarios/custom/agent_task_creator.py`
- `mts/tests/test_agent_task_pipeline.py`

### PR 3: Skill Export + End-to-End (TS)
- Port `SkillPackage` and `export_skill_package` / `export_agent_task_skill`
- Port `_render_agent_task_markdown` for SKILL.md generation
- Wire MCP tools: `create_agent_task`, `export_skill`
- End-to-end test: NL description → scenario → generations → exported playbook

Key files to reference:
- `mts/src/mts/knowledge/export.py`
- `mts/tests/test_agent_task_export.py`
- `mts/src/mts/mcp/tools.py` (agent task MCP handlers)

## Architecture Notes
- `AgentTaskInterface` is intentionally separate from `ScenarioInterface` — different shapes
- `JudgeExecutor` wraps `AgentTaskInterface.evaluate_output` with context preparation/validation
- Human feedback → calibration examples → injected into judge prompts
- Skill export renders both game scenarios and agent tasks (separate markdown paths)
- The Python codebase is the reference implementation; TS should match behavior
