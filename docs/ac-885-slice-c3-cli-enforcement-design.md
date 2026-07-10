# AC-885 Slice C3: promotion trigger (CLI) + quarantine enforcement

Date: 2026-07-10
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice C3 of Slice C. Slice 1, B, C1, C2 are merged (#1204, #1208, #1210, #1211).

## Purpose

Slice C1 stood up the evaluator-epoch registry and the `quarantined` marker; C2 built the
`promote_evaluator_epoch` decision engine, but nothing triggers it and nothing consumes the
quarantine marker. C3 gives the lifecycle end-to-end teeth: a human-facing CLI that lists candidate
epochs and approves or rejects them (the trigger for C2's operation), and a first enforcement
consumer that excludes quarantined scores from trusted training-data export.

## Decisions of record

1. **The CLI is a human-override trigger.** `autoctx epoch approve`/`reject` build a
   `ReviewerDecision` and call `promote_evaluator_epoch` with `calibration_report=None` (the human
   is the authority; C2 records the decision and, per the C2 fix, contributes no misleading
   calibration evidence). The charter is loaded (a required `--charter` option) to supply
   the `decide()` autonomy read and to resolve the policy `target_name`.
2. **Scenario and policy target_name are distinct (C2 lesson).** The CLI has the real scenario (the
   registry/SQLite key); it resolves the charter `target_name` by matching each target's
   `split_role_selector(selector)` scenario to the requested scenario. Never pass the scenario as the
   charter target name.
3. **Enforcement is training-export exclusion, scoped and opt-out-able.** `export_training_data`
   excludes generation rows whose `quarantined` marker is truthy by default, with an
   `include_quarantined` flag to opt in. A score produced under an unpromoted/candidate evaluator is
   not trusted training data. The TS export mirror gets the same option for contract parity (its
   `quarantined` is always null per the Slice B/C1 asymmetry, so the exclusion is a no-op there).
4. **The ambient-promote refusal stays deferred.** Refusing a model scored under a non-active
   evaluator epoch needs the `promote.py` generalization (the ambient evaluate stage stamping
   evaluator_epoch on model eval metadata), a separate follow-on. C3 does not touch `promote.py`.
5. **Registry access via the C1/C2 lock-held API only.** The CLI reads via `snapshot_for_scenario` /
   `active_for` and mutates only via `promote_evaluator_epoch` (which uses `registry.promote` /
   `stamp_promotion` under the per-scenario lock). No direct unlocked writes.

## Architecture

### C3.1: training-export quarantine exclusion

- Python `training/export.py` `export_training_data(sqlite, artifacts, run_id=None, scenario=None,
include_matches=False, kept_only=False, include_quarantined=False)`: in the `TrainingRecord`
  yield loop, skip a generation row when `bool(gen.get("quarantined"))` and not
  `include_quarantined`. Match records are unaffected (tournament, never quarantined).
- TS `ts/src/training/export-records-workflow.ts`: add an `includeQuarantined?: boolean` option and
  the same skip on `generation.quarantined`. (TS never populates `quarantined`, so the branch is a
  no-op, but the option exists for parity.)
- Tests: a quarantined generation is excluded by default; `include_quarantined=True` includes it; a
  non-quarantined generation is always exported.

### C3.2: the epoch CLI

New module `autocontext/src/autocontext/cli_epoch.py`, registered on the main Typer `app`
(`cli.py` is near the module-size cap, so the commands live in their own module):

- `epoch list [--scenario S]`: enumerate registry records (scenario, epoch_id,
  activation_state, and the promotion block when present) as JSON to stdout. Reads via a lock-held
  `EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs").snapshot_for_scenario(...)`
  (or all scenarios when `--scenario` is omitted, by scanning the registry root's scenario subdirs).
  `list` takes no `--charter` (it neither promotes nor resolves an autonomy target).
- `epoch approve <scenario> <epoch_id> --charter PATH [--by USER]` and
  `epoch reject <scenario> <epoch_id> --charter PATH [--by USER]` (`--charter` is required):
  1. Load the charter (the required `--charter` option) via `load_charter`.
  2. `target_name = _resolve_charter_target(charter, scenario)`; error clearly if no target's
     selector scenario matches.
  3. Build `ReviewerDecision(outcome="approved"|"rejected", reviewed_by=USER, reviewed_at=now)`.
  4. Call `promote_evaluator_epoch(registry, scenario, epoch_id, target_name=target_name,
calibration_report=None, tolerance=AlignmentTolerance.default_for_domain(scenario),
charter=charter, reviewer_decision=decision, sqlite=SQLiteStore(settings.db_path))`.
  5. Print the `PromotionOutcome` (outcome, reason) as JSON; exit non-zero on `noop` for a missing
     candidate so scripts can detect it.
- Helper `_resolve_charter_target(charter, scenario) -> str`: iterate `charter.targets`, return the
  first `t.name` whose `split_role_selector(t.selector)` scenario equals `scenario`; raise a
  `typer.BadParameter`-style clear error if none.

## Data flow

```
operator: autoctx epoch list --scenario grid_ctf
   -> EvaluatorEpochRegistry.snapshot_for_scenario -> JSON of candidate/active/disabled records

operator: autoctx epoch approve grid_ctf <epoch> --charter ambient-charter.yaml --by jay
   -> load_charter -> _resolve_charter_target(charter, "grid_ctf") -> "competitor-local"
   -> promote_evaluator_epoch(reg, scenario="grid_ctf", epoch, target_name="competitor-local",
        calibration_report=None, reviewer_decision=approved, sqlite=...)
        -> C2: activate under lock + clear quarantine
   -> print PromotionOutcome

training export: export_training_data(..., include_quarantined=False)
   -> skip generation rows with quarantined truthy (unpromoted-evaluator scores excluded)
```

## Error handling and edge cases

- **No matching charter target for the scenario:** `approve`/`reject` error clearly (cannot resolve
  the autonomy target); no registry mutation.
- **Missing candidate epoch:** `promote_evaluator_epoch` returns `noop`; the CLI prints it and exits
  non-zero so a script sees the failure.
- **No charter configured:** `approve`/`reject` require a charter (for `decide` + target
  resolution); `--charter` is a required option for approve/reject, and the target selector must be scenario-scoped (`role@scenario`). `list` does not need
  a charter.
- **Registry IO failure in the CLI:** surfaces as a CLI error (operator-facing, unlike the
  score-persist hot path which fails closed); the operator retries.
- **include_quarantined default is False:** existing export callers change behavior (quarantined
  rows now excluded). This is the intended enforcement; documented in the CHANGELOG.

## Testing

- Training-export: quarantined generation excluded by default, included with the flag, non-quarantined
  always exported (Python + a TS test asserting the option exists and excludes a quarantined row).
- CLI: `epoch list` prints the registry records; `approve` on a candidate calls
  `promote_evaluator_epoch` with a distinct scenario vs target_name (a realistic charter target like
  `competitor-local` with selector `competitor@grid_ctf`, scenario `grid_ctf`), activates it, and the
  outcome is printed; `reject` records the rejection and does not activate; `approve` of a missing
  candidate exits non-zero; `_resolve_charter_target` matches by selector and errors on no match.
- Existing gates: module-size (new `cli_epoch.py`), gate-taxonomy, ruff/mypy, the serde-convention
  test (full-suite gate, C1 lesson), Python and TS export suites, lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "trigger and enforcement (Slice C3)" subsection: the epoch
CLI (list/approve/reject as the human trigger, charter-based target resolution), training-export
quarantine exclusion (default-off include flag), and that the ambient-promote refusal remains
deferred. CHANGELOG entry (noting the export behavior change).

## Deferred / out of scope

- The ambient-promote refusal (the `promote.py` generalization + model-eval-epoch stamping).
- Other enforcement consumers (run facets, reports, canonical progression beyond training export).
- Running calibration at CLI-approve time (the CLI approve is a pure human override).
- Slice D (CLI/API/dashboard stale-epoch surfacing + lazy re-score) beyond this approve/reject CLI.

## Acceptance criteria advanced by this slice

- AC-885 "surface stale evaluator lineage in CLI outputs so operators know when numbers are not
  directly comparable": the `epoch list` CLI surfaces candidate/active state and promotion metadata.
- AC-885 "mark or filter stale scores": training-export excludes quarantined scores.
- AC-885 promotion decision + human-review requirement: the CLI is the human trigger that records the
  approve/reject decision through C2's operation.
