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
- `eval_adapter.py` — TournamentEvalAdapter bridging EvaluationRunner → TournamentSummary
- Feature-gated behind `MTS_USE_GENERATION_PIPELINE=false`

---

## Direction 1: Close Pipeline Gaps + Delete Monolith — **HIGH PRIORITY**

**Status**: Planning (see `2026-02-18-phase5-pipeline-parity.md`)

The pipeline path silently drops 5 behaviors present in the monolith:
1. **Controller chat checkpoint** — interactive agent chat between agent gen and tournament
2. **Controller gate override** — operators can force advance/rollback after tournament
3. **PrimeIntellect warm provisioning** — remote executor pre-warm before tournament
4. **`agents_started` + `role_completed` events** — dashboard/WebSocket consumers
5. **`created_tools` in `generation_completed` event** — event payload mismatch
6. **Rollback retry_note** — `attempt` count missing from rollback skill lesson

Once fixed: flip the flag to default=True, validate, then delete ~355 lines of duplicate monolithic code.

---

## Direction 2: ArtifactStore Dead Code Cleanup — **LOW PRIORITY**

**Status**: Deferred (near-zero duplication remaining)

The ArtifactStore → VersionedFileStore delegation was already completed in Phase 3.
Remaining cleanup:
- Delete `_prune_playbook_versions` no-op (3 lines, 0 callers)
- Consider removing unused facade methods: `rollback_playbook`, `playbook_version_count`, `read_playbook_version` (0 production callers, ~14 test call sites)
- Fix `restore_knowledge_snapshot` to go through `write_playbook()` for versioning consistency

**Estimated effort**: 30 minutes. Can be done opportunistically.

---

## Direction 3: TournamentRunner → EvaluationRunner — **NOT YET**

**Status**: Blocked on two prerequisite fixes

Three blockers prevent clean replacement:
1. **ScenarioEvaluator replay type mismatch** — stores `dict` via `.model_dump()`, but `replay_to_narrative()` needs typed model. Fix: preserve typed replay in `metadata["replay_object"]`.
2. **TournamentEvalAdapter double-execution bug** — runs 2N matches (once for ExecutionOutputs, once via EvaluationRunner). Fix: rewrite adapter to collect outputs in a single pass.
3. **9 files would change** with moderate blast radius.

TournamentRunner is only 64 lines with 1 production instantiation. Cost of keeping it is near-zero.

**Recommended sequence**: Fix bugs #1 and #2 as isolated changes (unblocks eventual migration), then do the full replacement in a future phase.
