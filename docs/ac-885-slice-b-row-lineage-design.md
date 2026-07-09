# AC-885 Slice B: persisted and derived evaluator-epoch lineage

Date: 2026-07-09
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: Slice B of four. Slice 1 (identity + comparability guard) is merged (PR #1204). Slice C
(promotion lifecycle) and Slice D (surfacing + lazy re-score) remain.

## Purpose

Slice 1 gave the LLM-judge path an evaluator epoch (`JudgeResult.evaluator_epoch`), propagated it
through `AgentTaskResult` into `ImprovementResult`, guarded the improve loop against cross-epoch
comparison, and serialized it into the task-queue result JSON. Slice B extends that lineage to the
records that persist or derive from judge scores, so a stored generation, an exported training
example, a calibration report, and a rubric-drift snapshot each record which evaluator produced the
numbers they carry.

Lineage is attached only where a judge or rubric evaluator produced the score. The main run loop is
tournament and Elo based (game scenarios) and carries no rubric or judge, so tournament-scored rows
(`matches`, the tournament `generations` write) and human-scored rows (`human_feedback`) are left
unstamped: an epoch column there would always be null. This slice stamps the judge-scored write
sites and the derived analytics that consume them.

## Decisions of record

1. **generations gets an `evaluator_epoch` column, stamped only at the judge-scored write sites.**
   The two agent-task write sites (`cli.py` `_run_agent_task`, `knowledge/solve_task_execution.py`)
   write from an `ImprovementResult` that Slice 1 already stamped, so `result.evaluator_epoch` is
   in scope. The tournament write site (`loop/stages.py`) and the `matches` table stay null and
   untouched.
2. **TypeScript gets the parity column but never populates it.** `generations` is a schema-parity
   shared table, so the identical column is added on the TS side, but TS has no agent-task-direct
   generations write path, so the TS column is always null. This is a documented asymmetry, not a
   blocker.
3. **`rubric_calibration.run_judge_calibration` threads its already-computed epoch.** It makes a
   live judge call whose `JudgeResult.evaluator_epoch` is currently discarded; this is the cheapest
   win and needs no migration.
4. **`RunFacet.evaluator_epoch` is sourced from the run's best-scoring generation row.** The facet's
   `best_score` comes from a specific generation; its epoch is that generation's epoch (null for
   tournament or absent). This ties the facet's score to the evaluator that produced it and is the
   source the second-order consumers read.
5. **Analytics and drift record the epoch and flag mixed-epoch aggregates; the math is unchanged.**
   `CalibrationSample`/`CalibrationOutcome` (AC-260) and `RubricSnapshot`/`DriftWarning` (AC-259)
   carry the epoch(s) they cover, and set a `mixed_epoch` marker when an aggregate spans more than
   one epoch. The drift and calibration statistics are not re-grouped in this slice; enforcement
   (grouping or refusing cross-epoch aggregates) is a deferred follow-on.
6. **Training-export reads the new column through.** `TrainingRecord` gains a nullable
   `evaluator_epoch`; `export_training_data` copies `gen["evaluator_epoch"]`. This delivers the
   AC-885-named "exported training examples that depend on evaluator scores." Match export stays
   unstamped (tournament).

## Architecture

### Component 1: the `generations.evaluator_epoch` column (the anchor)

- Python migration `016_generation_evaluator_epoch.sql`: `ALTER TABLE generations ADD COLUMN
evaluator_epoch TEXT`. TS migration with the next TS number, adding the identical column. The
  `migration_ledgers.py` cross-map (Python <-> TS migration numbers) gets the new pairing.
- `bootstrap_schema.py` adds the column to the generations create statement; the TS bootstrap
  mirrors it. `GenerationMetricsRow` (`storage/row_types.py`) and the TS
  `UpsertGenerationRecordOpts` gain `evaluator_epoch: str | None` / `string | null`.
- `upsert_generation()` (`storage/sqlite_store.py`) accepts and writes the column; the TS
  `upsertGenerationRecord` mirrors the parameter (always null on the TS side).
- `storage-schema-parity.test.ts` continues to pass: both migration sets produce the identical
  `generations` columns.

### Component 2: stamp the two agent-task write sites

At `cli.py` `_run_agent_task` and `knowledge/solve_task_execution.py`
`run_task_like_scenario`, the guarded `upsert_generation(...)` calls pass
`evaluator_epoch=result.evaluator_epoch` from the `ImprovementResult` in scope. No other write site
is touched. The tournament path passes nothing (defaults to null).

### Component 3: `rubric_calibration` epoch

`run_judge_calibration` builds an `LLMJudge` and calls `judge.evaluate(...)`, discarding the
result's epoch. It threads the epoch onto the returned `CalibrationReport` as a new nullable field
`evaluator_epoch`. Because all samples in one calibration run use the same rubric, provider, and
model, they share one epoch. No migration (the calibration report is an in-memory/JSON artifact).

### Component 4: `RunFacet.evaluator_epoch`

`RunFacet` (`analytics/facets.py`) gains `evaluator_epoch: str | None`. When the facet is built from
a run's generation rows, it records the epoch of the row that produced `best_score`. If that row has
no epoch (tournament) or no rows exist, the facet epoch is null.

### Component 5: analytics/calibration lineage + mixed-epoch flag

`CalibrationSample` (`analytics/calibration.py`) carries `evaluator_epoch` from its source
`RunFacet`. A `CalibrationRound`/`CalibrationOutcome` that aggregates samples across more than one
distinct non-null epoch sets `mixed_epoch: bool = True`. The existing sampling and calibration
computation is unchanged; the flag is a lineage signal only.

### Component 6: rubric-drift lineage + mixed-epoch flag

`RubricSnapshot` (`analytics/rubric_drift.py`) records the set (or the single value) of evaluator
epochs among the facets it aggregates, plus a `mixed_epoch` marker when that set has more than one
non-null value. `DriftWarning` carries the marker through so an operator sees when a drift signal is
computed across differing evaluators. `compute_snapshot` and the drift comparison math are unchanged.

### Component 7: training-export read-through

`TrainingRecord` (`training/types.py`) gains `evaluator_epoch: str | None = None`.
`export_training_data` (`training/export.py`) sets it from `gen["evaluator_epoch"]` (present once
Component 1 lands; null for tournament rows). `MatchRecord` is not changed (matches are tournament).
The TS `export-records-workflow.ts` mirror gets the field for contract parity, noting the TS
generations column is always null so TS-exported records carry null there today.

## Data flow

```
ImprovementResult.evaluator_epoch (Slice 1)
   -> upsert_generation(evaluator_epoch=...) at the 2 agent-task write sites   [Component 1/2]
        -> generations.evaluator_epoch column
             -> RunFacet.evaluator_epoch (epoch of the best-scoring row)        [Component 4]
                  -> CalibrationSample.evaluator_epoch + mixed_epoch flag       [Component 5]
                  -> RubricSnapshot epochs + mixed_epoch flag                    [Component 6]
             -> export_training_data -> TrainingRecord.evaluator_epoch          [Component 7]

LLMJudge.evaluate (in run_judge_calibration) -> JudgeResult.evaluator_epoch
   -> CalibrationReport.evaluator_epoch                                         [Component 3]
```

## Error handling and edge cases

- **Legacy rows / null epoch:** existing generation rows predate the column and read as null; a
  facet or export over them carries null, consistent with the Slice 1 null-legacy semantics.
- **Mid-run evaluator change (rebaseline):** a run can contain generation rows under different
  epochs. `RunFacet` uses the best-scoring row's epoch, so it is well-defined. Analytics that
  aggregate multiple facets/rows with differing epochs set `mixed_epoch`.
- **Tournament runs:** every generations/matches row epoch is null; facets and exports over them are
  null; `mixed_epoch` stays false (a set of nulls is not "mixed").
- **TS always-null column:** documented; TS parity tests assert the column exists, not that it is
  populated.

## Testing

- **Migration + parity:** Python and TS migrations add the identical `generations.evaluator_epoch`
  column; `storage-schema-parity.test.ts` passes; a migration-idempotency/rollback check per the
  repo's migration test pattern.
- **Write-site stamping:** a test that an agent-task run persists `generations.evaluator_epoch` equal
  to the `ImprovementResult` epoch, and that a tournament run leaves it null.
- **rubric_calibration:** `run_judge_calibration` returns a `CalibrationReport` whose
  `evaluator_epoch` equals `compute_evaluator_epoch(rubric, provider, model).epoch_id`.
- **RunFacet:** facet built from generation rows records the best-scoring row's epoch; null when
  tournament.
- **Analytics/drift mixed-epoch:** an aggregate over one epoch sets `mixed_epoch=False`; an aggregate
  over two distinct epochs sets `mixed_epoch=True`; the computed statistics are byte-identical to
  before the field was added (regression pin).
- **Training-export:** an exported record from a judge-scored generation carries the epoch; from a
  tournament generation carries null.
- Existing gates: module-size, gate-taxonomy (no new gate/guard/validator symbols), ruff/mypy/lint,
  Python and TS suites, lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "persisted and derived lineage" section: which records
carry the epoch and which deliberately do not (tournament, human), the always-null TS generations
column, the `mixed_epoch` flag semantics on analytics/drift, and how training-export carries
lineage. CHANGELOG entry.

## Deferred to later slices

- Enforcement of cross-epoch refusal or grouping in drift and calibration (this slice records and
  flags only).
- `matches` and `human_feedback` epochs (never: not judge-scored).
- AC-881 `CalibrationReport` (pure noise-floor statistics, no rubric concept to attach to).
- Package export/import epoch (`knowledge/package.py`), not currently reachable.
- CLI, API, and dashboard stale-epoch surfacing and lazy re-score (Slice D).

## Acceptance criteria advanced by this slice

- AC-885 #1 (score-bearing records carry lineage): extends from the judge path to persisted
  generations (judge-scored), calibration reports, run facets, and analytics/drift snapshots.
- AC-885 #3 (reports and exports distinguish evaluator lineage): training-export and the analytics
  `mixed_epoch` flag are the first concrete delivery; full active-vs-stale reporting is Slice D.
- AC-885 #6 (documentation): the persisted-lineage section.
