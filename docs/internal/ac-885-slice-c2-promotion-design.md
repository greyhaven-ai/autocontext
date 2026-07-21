# AC-885 Slice C2: evaluator-epoch promotion workflow

Date: 2026-07-10
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice C2 of Slice C. Slice 1, Slice B, and Slice C1 are merged (#1204, #1208, #1210).

## Purpose

Slice C1 stood up the per-scenario evaluator-epoch registry (candidate/active/disabled), the
mechanical `observe` trigger, and the `quarantined` marker on scores produced under a non-active
epoch, but nothing promotes a candidate. C2 adds the promotion workflow: given a candidate epoch and
a calibration report (alignment/bias/variance vs human anchors, epoch-tagged since Slice B), decide
via the autonomy dial whether to activate it, record the promotion metadata and the human decision,
and retroactively clear the quarantine on the promoted epoch's prior scores.

The operation is pure and takes the calibration report as input (the caller runs
`run_judge_calibration`), so the whole decision matrix is unit-testable without an LLM. The trigger
that calls it (a CLI approve/reject, an ambient stage) is deferred to C3.

## Decisions of record

1. **C2 is the evaluator-epoch promotion workflow; `promote.py` is deferred.** Generalizing the
   ambient model-promotion `promote.py` cross-anchor refusal to `are_comparable(epoch)` needs the
   ambient evaluate stage to stamp `evaluator_epoch` on `DistilledModelRecord` eval metadata, a
   separate deeper change. C2 builds only the evaluator-epoch promotion operation.
2. **Quarantine is cleared retroactively on promotion, scoped by scenario.**
   `promote_evaluator_epoch` runs an UPDATE that clears `generations.quarantined` for the promoted
   epoch's rows, scoped to the scenario's runs so a same-content epoch id in another scenario is
   untouched. The stored column stays the source of truth for consumers. The TaskRunner task-queue
   JSON quarantine snapshot is not retroactively cleared (a JSON blob, not a queryable column): a
   documented limitation.
3. **The operation takes the calibration report as input.** It does not run calibration itself,
   keeping it pure and its decision matrix fully unit-testable.
4. **Promotion mutations go through the C1 per-scenario lock + atomic writes.** The registry
   `promote` method activates and stamps metadata inside the `fcntl` lock, carrying the C1
   concurrency lesson forward (no unlocked check-then-write).
5. **Python-only.** The promotion workflow is ambient/lifecycle with no TS mirror; the
   quarantine-clear is a Python storage method, not a schema change. No parity surface.
6. **The trigger is C3.** C2 is written-but-not-yet-triggered: the CLI approve/reject and any
   ambient stage that runs calibration and calls the operation are C3. C2 delivers and thoroughly
   tests the operation, the autonomy gate, and the quarantine-clear storage method.

## Architecture

### C1: autonomy gate for epoch promotion

`ambient/policy.py`: extend `Action = Literal["train", "promote"]` to include `"promote_epoch"`.
`decide()` already branches on the action string; `"promote_epoch"` follows the same rule as
`"promote"` (autonomy `propose`/`train` require approval; `full` is autonomous). A one-line literal
extension plus, if `decide` special-cases actions, the same branch as `promote`.

### C2: the promotion operation

New module `execution/evaluator_epoch_promotion.py`:

- `PromotionOutcome` (pydantic or frozen dataclass): `outcome: Literal["activated", "pending_review",
"rejected", "blocked", "noop"]`, `reason: str`, `record: EvaluatorEpochRecord | None`.
- `ReviewerDecision` (value type): `outcome: Literal["approved", "rejected"]`, `reviewed_by: str`,
  `reviewed_at: str`.
- `promote_evaluator_epoch(registry, scenario, candidate_epoch_id, *, calibration_report,
tolerance, charter, reviewer_decision=None, sqlite=None, now_fn=_default_now) -> PromotionOutcome`:
  1. Load the candidate record. If missing or already active, return `noop`.
  2. `calibration_passes = calibration_report is not None and
tolerance.check(calibration_report.alignment)["passes"]`.
  3. `policy = decide(charter, "promote_epoch", scenario)`.
     - `policy.requires_approval` and `reviewer_decision is None`: stamp `requires_review=True` on the
       candidate's `promotion` metadata (via `registry.register`), return `pending_review`.
     - `reviewer_decision` present: `approved` -> activate; `rejected` -> stamp the rejection decision,
       return `rejected` (record stays candidate/disabled).
     - not `requires_approval` (autonomy `full`): activate iff `calibration_passes`, else `blocked`.
  4. Activate path: build the promotion metadata, call `registry.promote(scenario,
candidate_epoch_id, promotion=metadata)`, then if `sqlite` is not None call
     `sqlite.clear_quarantine_for_epoch(scenario, candidate_epoch_id)`. Return `activated`.

- Promotion metadata dict: `{ "source_patch": None, "calibration_anchors": <num_anchors count>,
"alignment_delta": {"mean_absolute_error", "bias", "correlation"}, "variance_delta": {...},
"requires_review": bool, "decision": {"reviewed_by", "reviewed_at", "outcome"} | None,
"promoted_at": now, "previous_active": <prior active epoch_id or ""> }`. `calibration_anchors`
  is the `CalibrationReport.num_anchors` count (0 when no report), and the deltas come from the
  report's `alignment` and `variance`.

### Registry `promote`

`EvaluatorEpochRegistry.promote(scenario, epoch_id, *, promotion: dict[str, Any]) -> None`: inside
the per-scenario `fcntl` lock, run the same demote-not-delete activation as `activate` (prior active
-> disabled, target -> active) AND set `record.promotion = promotion` on the target, writing
atomically. Do not duplicate the `activate` critical section; factor a `_locked` helper both call so
they cannot diverge (addresses the C1 observe/observe_id duplication note by not repeating it here).

### Quarantine-clear storage method

`SQLiteStore.clear_quarantine_for_epoch(scenario: str, epoch_id: str) -> int`:

```sql
UPDATE generations SET quarantined = NULL
WHERE evaluator_epoch = ?
  AND run_id IN (SELECT run_id FROM runs WHERE scenario = ?)
```

Returns the number of rows cleared. Scenario-scoped so a content-hash epoch shared across scenarios
only clears the promoted scenario's rows. (Confirm `runs` has a `scenario` column; it does per the
schema.)

## Data flow

```
candidate epoch (C1) + CalibrationReport (caller ran run_judge_calibration, epoch-tagged Slice B)
   -> promote_evaluator_epoch(registry, scenario, candidate, calibration_report, tolerance, charter)
        calibration_passes = tolerance.check(report.alignment).passes
        decide(charter, "promote_epoch", scenario):
            requires_approval + no decision  -> record requires_review, PENDING_REVIEW
            decision approved                -> ACTIVATE
            decision rejected                -> record rejection, REJECTED
            autonomy full + passes           -> ACTIVATE
            autonomy full + fails            -> BLOCKED
        ACTIVATE: registry.promote (under lock: candidate->active, prior->disabled, stamp metadata)
                  sqlite.clear_quarantine_for_epoch(scenario, epoch)  # prior scores now trusted
```

## Error handling and edge cases

- **Missing / already-active candidate:** `noop`.
- **No calibration report (None):** cannot auto-promote; under autonomy `full` returns `blocked`;
  under `propose`/`train` still returns `pending_review` (a human may approve without calibration,
  but the metadata records `calibration_anchors: 0`).
- **Reviewer rejects:** the record stays candidate/disabled; the rejection decision is recorded so a
  later cycle does not re-prompt blindly (the caller/C3 decides re-review policy).
- **Concurrency:** `registry.promote` holds the per-scenario lock; two concurrent promotions of the
  same scenario serialize, and the demote-not-delete keeps a single active.
- **Quarantine-clear when sqlite is None:** skipped (the operation still activates; clearing is a
  best-effort side effect, not a correctness precondition for activation).

## Testing

- `decide(charter, "promote_epoch", target)` autonomy matrix (propose/train require approval; full
  autonomous), mirroring the existing `promote` tests.
- `registry.promote`: activates + stamps metadata under the lock; prior active demoted; a
  self-heal / lock test in the C1 style.
- `clear_quarantine_for_epoch`: clears only the (scenario, epoch) rows; a same-epoch row under a
  different scenario is untouched; returns the count.
- `promote_evaluator_epoch` decision matrix, the core: activated (full+passes, and approved),
  pending_review (requires_approval + no decision), rejected (decision rejected), blocked
  (full+fails, and None report under full), noop (missing/already-active). Assert the record state
  transition, the stamped promotion metadata shape, and that quarantine was cleared on activation.
- Existing gates: module-size, gate-taxonomy (no gate/guard/validator symbols), ruff/mypy, the
  serde-convention test (run the FULL suite in the gate, per the C1 lesson), lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "promotion (Slice C2)" subsection: the promotion operation,
the calibration-tolerance + autonomy-dial decision matrix, the human-decision record, retroactive
quarantine clearing (and the TaskRunner-JSON limitation), and that the trigger (CLI/stage) and the
`promote.py` generalization are deferred. CHANGELOG entry.

## Deferred / out of scope

- The trigger: CLI `approve`/`reject` and any ambient stage running calibration then calling the
  operation (C3).
- The `promote.py` model-promotion generalization to `are_comparable(epoch)` (needs the ambient
  evaluate stage to stamp evaluator_epoch on model eval metadata): a separate follow-on.
- Clearing the TaskRunner task-queue-JSON quarantine snapshot (a JSON blob, not a column).
- Running calibration inside the operation (the caller supplies the report).

## Acceptance criteria advanced by this slice

- AC-885 promotion-metadata requirement (source patch, calibration anchors used, alignment/bias/
  variance deltas, human-review requirement, decision): delivered by the promotion operation's
  metadata and the autonomy-gated human decision.
- AC-885 invalidation rules: an epoch's prior candidate-era scores are un-quarantined (trusted) only
  on explicit promotion; before promotion they stay quarantined (C1).
