# Harness Extraction Roadmap

## Completed

### Phase 1: Core Harness Primitives (550 tests → commit f729cef)
Extracted domain-agnostic building blocks into `mts/harness/`:
- `core/` — types, llm_client, subagent, events, controller
- `pipeline/` — gate, trend_gate, retry_context
- `repl/` — types, worker, session

### Phase 2: Composable Infrastructure (→ commit f729cef)
Built composable modules on Phase 1 primitives:
- `scoring/elo.py` — extracted Elo functions
- `core/output_parser.py` — JSON fences, tagged content, delimited sections
- `storage/versioned_store.py` — generic versioned file store
- `evaluation/` — types, protocol (Evaluator), runner (N-trial + Elo)
- `orchestration/` — types (RoleSpec, PipelineConfig), dag (topo sort), engine (DAG-ordered execution)

### Phase 3: Rewiring MTS Internals (→ commit f729cef)
- PipelineEngine-backed orchestrator codepath (feature-gated)
- Harness output_parser adopted in coach + translator
- ScenarioEvaluator adapter bridging ScenarioInterface → Evaluator protocol

### Phase 4: Generation Decomposition (589 tests → commit 61ccda3)
Decomposed the 580-line `GenerationRunner.run()` monolith:
- `stage_types.py` — GenerationContext + StageResult
- `stages.py` — 5 stage functions (knowledge_setup, agent_generation, tournament, curator_gate, persistence)
- `generation_pipeline.py` — orchestrator sequencing all stages
- Feature-gated behind `MTS_USE_GENERATION_PIPELINE=false`

### Phase 5: Pipeline Parity + Monolith Deletion (593 tests → commit caddf0e)
Closed 6 pipeline parity gaps, flipped flag to default=True, deleted ~364 lines of monolithic code:
- Controller chat checkpoint, gate override, PrimeIntellect warm provisioning
- `agents_started` + `role_completed` events, `created_tools` payload, rollback retry_note
- Feature flag removed entirely — pipeline is sole execution path

### Direction 2: ArtifactStore Dead Code Cleanup (580 tests → commit 8113932)
- Deleted `_prune_playbook_versions` no-op (0 callers)
- Deleted 3 dead facade methods: `rollback_playbook`, `playbook_version_count`, `read_playbook_version` (0 production callers)
- Fixed `restore_knowledge_snapshot` versioning bypass — now uses `write_playbook()` for proper archiving

### Direction 3: TournamentRunner → EvaluationRunner (575 tests → commit 10d3838)
Replaced `TournamentRunner` with `EvaluationRunner` as the production execution path:
- Fixed ScenarioEvaluator to preserve `ExecutionOutput` in `EvaluationResult.metadata["execution_output"]`
- Refactored `stage_tournament` to use `ScenarioEvaluator` + `EvaluationRunner` directly
- Eliminated the double-execution bug in `TournamentEvalAdapter` (ran 2N matches instead of N)
- Deleted `TournamentRunner` (63 lines), `TournamentEvalAdapter` (70 lines), and adapter tests (248 lines)
- All tournament scoring now flows through the domain-agnostic harness `EvaluationRunner`

---

## All Planned Directions Complete

The harness extraction is fully done. All MTS-domain execution flows through harness abstractions:
- **Scoring**: `harness/scoring/elo.py`
- **Evaluation**: `harness/evaluation/runner.py` + `ScenarioEvaluator` adapter
- **Output parsing**: `harness/core/output_parser.py`
- **Versioned storage**: `harness/storage/versioned_store.py`
- **Orchestration**: `harness/orchestration/engine.py` + `RoleDAG`
- **Pipeline control**: `harness/pipeline/gate.py`, `trend_gate.py`, `retry_context.py`
- **REPL**: `harness/repl/session.py`, `worker.py`
