# AC-885 Slice D1: stale-epoch surfacing (read-only)

Date: 2026-07-10
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice D1 of Slice D. Slice 1, B, C1, C2, C3 are merged (#1204, #1208, #1210, #1211, #1212).

## Purpose

Slices 1 through C3 built evaluator-epoch identity, persisted lineage, the per-scenario registry, the
promotion workflow, and training-export enforcement. What remains from the AC-885 acceptance criteria
("Reports and exports distinguish active-epoch scores from stale-epoch scores"; "surface stale
evaluator lineage in CLI/API/dashboard outputs") is that an operator reading a finished run cannot see
whether a generation's score is still comparable to the scenario's active evaluator epoch. The score
row already persists `evaluator_epoch` and `quarantined`, but every read/render surface drops them.

D1 surfaces that lineage read-only. It does not re-score anything: lazy re-score / revalidation of raw
artifacts is deferred to D2.

## Decisions of record

1. **Staleness is a four-state classification, not a boolean.** A pure `classify_epoch_lineage`
   returns `current` / `stale` / `unknown` / `no_active_epoch`. Only `stale` (both row and active
   epoch non-null and different, per `are_comparable`) is warning-worthy. A `None` row epoch is
   `unknown` (legacy / pre-slice, or a game/no-judge run), never `stale`, so old runs and tournament
   scores are not flooded with false warnings. When the scenario has no promoted active epoch the
   state is `no_active_epoch` and nothing is asserted. This honors the documented null-legacy rule.
2. **Fix the epoch-drop at the plumbing layer once.** `SQLiteStore.run_status()` currently SELECTs an
   explicit column list that omits the epoch; extend it to carry `evaluator_epoch` + the already-
   persisted `quarantined` bit. That single fix feeds CLI `show`/`status`/`watch` together. The HTTP
   handler has its own inline SQL and is extended in parallel.
3. **Python surfaces are full; TS surfaces carry the field only.** The evaluator-epoch registry is
   Python-only (C1/C2/C3 all marked TS `intentional_gap`), so TS has no active epoch to classify
   against. D1 fixes the TS DTO mappers to stop dropping the persisted `evaluator_epoch` / `quarantined`
   fields (parity of shape), but the stale-vs-active classification and warnings stay Python-only, a
   documented intentional gap consistent with the prior slices. No registry is ported to TS.
4. **Reuse existing vocabulary, do not invent.** The comparison reuses `are_comparable` +
   `active_for`. The HTTP warning mirrors the existing `stale_score` notebook-warning shape
   (`{warning_type, description, ...}`). The classification names sit alongside the analytics
   `mixed_epoch` concept rather than replacing it.
5. **Read-only.** No write path, no judge call, no re-score. D1 only reads the registry and renders.

## Architecture

### D1.1: staleness classifier + registry-aware annotator

- Pure classifier in `execution/evaluator_epoch.py` (co-located with `are_comparable`, a leaf module):
  ```python
  EpochLineageStatus = Literal["current", "stale", "unknown", "no_active_epoch"]

  def classify_epoch_lineage(row_epoch: str | None, active_epoch: str | None) -> EpochLineageStatus:
      if active_epoch is None:
          return "no_active_epoch"
      if row_epoch is None:
          return "unknown"
      return "current" if are_comparable(row_epoch, active_epoch) else "stale"
  ```
- Registry-aware annotator in a NEW module `execution/epoch_lineage.py` (imports both the classifier
  and the registry, so it cannot live in the leaf `evaluator_epoch.py` without a cycle):
  ```python
  def annotate_status_rows(
      rows: list[dict[str, Any]],
      scenario: str | None,
      registry: EvaluatorEpochRegistry,
  ) -> tuple[list[dict[str, Any]], str | None]:
      """Return (rows each with an added ``evaluator_epoch_status`` key, active_epoch_id).

      Reads the scenario's active epoch ONCE, then classifies each row. Returns copies; does not
      mutate the inputs. ``scenario is None`` -> active_epoch None -> every row ``no_active_epoch``.
      """
  ```
  Rows lacking an `evaluator_epoch` key (e.g. a legacy caller) are treated as `evaluator_epoch = None`.

### D1.2: plumbing — carry the epoch through reads

- `SQLiteStore.run_status()` (`storage/sqlite_store.py`): add `evaluator_epoch, quarantined` to the
  SELECT column list. The returned dicts gain both keys. This is additive; existing consumers ignore
  the extra keys.

### D1.3: Python CLI render (`show`, `status`)

Both build a registry from settings (`EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")`)
and call `annotate_status_rows` with the run's scenario. `show` already loads `run` (scenario in
hand); `status` must additionally call `store.get_run(run_id)` to obtain the scenario.

- `--json`: each generation dict gains `evaluator_epoch`, `evaluator_epoch_status`, `quarantined`
  (as a bool); top-level payload gains `active_evaluator_epoch`.
- rich table: add one compact `Lineage` column rendering `ok` (current) / `stale` / `legacy`
  (unknown) / `-` (no_active_epoch), and mark quarantined rows. When any row is `stale` or
  quarantined, print a single yellow warning line after the table naming the active epoch (short
  8-char prefix) so the operator knows the numbers are not directly comparable.

### D1.4: Python HTTP render (`GET /runs/{run_id}/status`)

`server/cockpit_api.py` already has the run's scenario. Add `_get_epoch_registry(request)` (mirrors
`_get_artifacts`, builds the registry from `app.state.app_settings.knowledge_root`). Extend the inline
generation SQL to select `evaluator_epoch, quarantined`; each generation dict gains
`evaluator_epoch`, `evaluator_epoch_status`, `quarantined`. The response gains `active_evaluator_epoch`
and a `warnings` list with one `{warning_type: "stale_epoch", generation, evaluator_epoch,
active_evaluator_epoch, description}` entry per stale generation (mirrors the `stale_score` shape).

### D1.5: TS DTO carry (no classification)

- `ts/src/cli/run-inspection-command-workflow.ts`: add `evaluator_epoch: string | null` and
  `quarantined: number | null` to the `RunInspectionGeneration` interface and carry them from the
  source `GenerationRow` in the mapper (the row already supplies them via `SELECT *`). JSON output
  then includes the persisted lineage instead of silently dropping it.
- `ts/src/server/cockpit-api.ts`: `formatGenerationStatus` carries `evaluator_epoch` + `quarantined`.
- No active-epoch classification, no warnings on TS (registry is Python-only; documented gap).

## Data flow

```
operator: autoctx show <run_id> --json
  -> store.get_run -> scenario
  -> store.run_status -> rows carrying evaluator_epoch + quarantined
  -> annotate_status_rows(rows, scenario, EvaluatorEpochRegistry(...))
       -> active = registry.active_for(scenario); each row.evaluator_epoch_status = classify(...)
  -> JSON: generations[].{evaluator_epoch, evaluator_epoch_status, quarantined}, active_evaluator_epoch

GET /runs/{id}/status
  -> same annotate -> generations[] + active_evaluator_epoch + warnings[stale_epoch...]
```

## Error handling and edge cases

- **No active epoch for the scenario** (registry empty, or a game/no-judge run): every row classifies
  `no_active_epoch`; no warning banner; `active_evaluator_epoch` is null.
- **Legacy rows** (`evaluator_epoch` null) with an active epoch present: `unknown`, rendered `legacy`,
  not counted as stale, no warning.
- **Registry read failure** in the CLI/HTTP: surfaces as the normal command/endpoint error; this is an
  operator read path, not the score-persist hot path, so it does not need to fail closed.
- **`status` without a run** / unknown scenario: `get_run` returns None -> existing not-found handling;
  when scenario is None, annotation yields `no_active_epoch` for all rows.

## Testing

- Unit (`classify_epoch_lineage`): the four states, including legacy-None -> `unknown`, both-None or
  no-active -> `no_active_epoch`, equal -> `current`, differ -> `stale`.
- Unit (`annotate_status_rows`): reads active once, annotates each row, returns copies, handles
  `scenario=None`.
- **Regression (AC requirement):** seed a generation under epoch e1 (active in the registry), promote
  e2 active, assert `show`/`status` (and the HTTP handler) flag the e1 generation `stale` and report
  `active_evaluator_epoch == e2`.
- HTTP: `GET /runs/{id}/status` includes the fields, `active_evaluator_epoch`, and a `stale_epoch`
  warning when a generation is stale.
- TS: `RunInspectionGeneration` / `formatGenerationStatus` now carry `evaluator_epoch` + `quarantined`.
- Existing gates: module-size, gate-taxonomy, ruff/mypy, tsc/lint, cli-contract parity (unchanged: no
  new commands), schema-parity (unchanged: no new columns), lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "Slice D1: stale surfacing" subsection: the four-state
classification, the Python-full / TS-field-only asymmetry, the `stale_epoch` HTTP warning, and that
lazy re-score is D2. CHANGELOG entry (additive output fields on `show`/`status`/`run_status`).

## Deferred / out of scope (D2 and beyond)

- Lazy re-score / revalidation of raw artifacts when a stale scored record is touched (D2).
- Porting the evaluator-epoch registry to TypeScript (classification stays Python-only).
- A separate web dashboard frontend (the cockpit HTTP API is the dashboard backend and is covered).
- Surfacing epoch lineage on `list` (run-level; epoch is per-generation) or `replay` (raw replay JSON).

## Acceptance criteria advanced by this slice

- AC-885 "surface stale evaluator lineage in CLI/API/dashboard outputs so operators know when numbers
  are not directly comparable": `show`/`status`/`run_status` render the four-state lineage + active
  epoch + stale warnings.
- AC-885 "Reports and exports distinguish active-epoch scores from stale-epoch scores": the CLI/API
  render distinguishes `current` from `stale` (exports were handled in C3).
- AC-885 "At least one regression test demonstrates that a record scored under an old evaluator is
  flagged after a new evaluator epoch is promoted": the promote-then-flag regression test.
