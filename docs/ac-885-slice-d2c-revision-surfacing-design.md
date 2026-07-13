# AC-885 Slice D2c: surface recorded re-scores in show/status

Date: 2026-07-13
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice D2c of Slice D. Slice 1, B, C1, C2, C3, D1, D2a, D2b are merged (#1204, #1208, #1210, #1211, #1212, #1213, #1214, #1216).

## Purpose

D2b's `rescore --apply` records active-epoch re-scores as append-only audit revisions in
`generation_score_revisions`, but nothing reads them: an operator looking at a stale run cannot see that
a re-score was recorded. D2c makes the audit trail consumable by surfacing, per generation, the latest
recorded active-epoch revision inline in the run views operators already use (`show`, `status`, and the
HTTP cockpit `run_status`). This closes the D2b loop and is read-only, no new schema.

## Decisions of record

1. **Surface only active-epoch revisions.** For each generation, the LATEST revision whose
   `revision_epoch` equals the scenario's active epoch is surfaced. Non-active-epoch revisions (drifted
   re-scores are never recorded anyway) are ignored. With no active epoch, nothing is surfaced.
2. **The live score is untouched (D2b invariant holds).** The generation stays stale: `show`/`status`
   still show the original `best_score`/`evaluator_epoch` as the score of record. The revision is shown
   ALONGSIDE it ("re-scored to X under the active epoch"), never replacing it. This is a read enrichment.
3. **Reuse the D1 annotation seam.** The revision fields are added right after the D1 epoch-lineage
   annotation, inside the two existing shared helpers, so the CLI and HTTP surfaces stay consistent.
4. **Read-only, Python-only.** No writes, no new schema. No TypeScript change: the revision data lives in
   a Python-written table that the TS `run_status` does not query, so this is a Python-only read
   enrichment, consistent with the rescore/registry Python-only asymmetry (as D1's classification was).
5. **Fields always present, consistent shape.** Every generation dict gains `has_active_revision`
   (bool), `revised_score`, `revised_by`, `revised_at` (null when no active-epoch revision exists), so
   the JSON shape does not vary by whether a revision exists.

## Architecture

### D2c.1: the store read + the pure field helper

- `SQLiteStore.latest_active_revisions(run_id, active_epoch) -> dict[int, GenerationScoreRevisionRow]`:
  one query returning, per `generation_index`, the most recent (highest `id`)
  revision whose `revision_epoch == active_epoch` for the run. Empty dict when `active_epoch` is None or
  no revisions match.
- A pure `revision_fields(revision: GenerationScoreRevisionRow | None) -> dict[str, Any]` (in
  `execution/epoch_lineage.py`, next to the D1 annotation) returning:
  ```python
  {
      "has_active_revision": revision is not None,
      "revised_score": revision["revision_score"] if revision else None,
      "revised_by": revision["created_by"] if revision else None,
      "revised_at": revision["created_at"] if revision else None,
  }
  ```
  Each render site merges these onto its generation dict, keyed by its own index field (CLI rows use
  `generation_index`, HTTP dicts use `generation`).

### D2c.2: CLI wiring (`show`, `status`)

- Extend `cli_run_inspect.annotate_run_status_rows(settings, scenario, rows, store, run_id)` (add
  `store` + `run_id`): after `annotate_status_rows`, when `active_epoch_id` is not None fetch
  `store.latest_active_revisions(run_id, active_epoch_id)` and, for each row,
  `row.update(revision_fields(revs.get(row["generation_index"])))`; when None, merge
  `revision_fields(None)` onto every row so the fields are always present. `show` (cli_run_inspect) and
  `status` (cli.py) both call it, threading their existing `store` + `run_id`.
- `--json`: each generation dict gains `has_active_revision`, `revised_score`, `revised_by`,
  `revised_at`.
- Rich table: add a compact `Revised` column showing `revised_score` (or `-`); when any row has a
  recorded active-epoch revision, print a one-line note ("N generation(s) have an active-epoch re-score
  recorded via `rescore --apply`; the live score of record is unchanged").

### D2c.3: HTTP wiring (`GET /api/cockpit/runs/{run_id}/status`)

Extend `server/run_status_lineage.build_run_status_generations(gen_rows, scenario, request, store, run_id)`
(add `store` + `run_id`; the cockpit `run_status` handler has both): after the D1 annotation, merge
`revision_fields(...)` onto each generation dict keyed by `generation`. The response gains the four
fields per generation. (No new warning type; the existing `stale_epoch` warning is unchanged.)

## Data flow

```
operator: autoctx show <run_id> --json   (or status, or GET /api/cockpit/runs/{id}/status)
  -> run_status(run_id) -> rows ; annotate_status_rows -> evaluator_epoch_status + active_epoch
  -> latest_active_revisions(run_id, active_epoch) -> {gen_idx: latest active-epoch revision}
  -> each row += {has_active_revision, revised_score, revised_by, revised_at}
  => a stale generation shows: live best=0.90/e1 (stale) + revised 0.55 (by jay, 2026-07-13)
```

## Error handling and edge cases

- **No active epoch:** `latest_active_revisions` returns empty; every row gets `has_active_revision:
false` and null revision fields.
- **No revision for a generation:** same null fields for that row (the common case for a run never
  re-scored).
- **Multiple revisions for a generation** (re-applied): the LATEST active-epoch one wins (`ORDER BY id
DESC`).
- **A revision under a non-active epoch:** ignored (only `revision_epoch == active_epoch` is surfaced).
- **Missing run:** unchanged from D1 (`show` not-found exit 1; `status` empty-success; HTTP 404).

## Testing

- Store: `latest_active_revisions` returns the latest active-epoch revision per generation, filters out
  non-active-epoch revisions, returns empty for no active epoch / no matches.
- Pure helper: `revision_fields(None)` and `revision_fields(row)` field shapes.
- CLI: `show`/`status` `--json` carry the four fields; a generation with a recorded active-epoch
  revision surfaces `revised_score`; the rich table shows the `Revised` value and the note.
- HTTP: `GET /api/cockpit/runs/{id}/status` carries the four fields; the revised generation surfaces
  `revised_score`.
- Regression: record a revision via `record_rescore_revision`, then assert `show`, `status`, and
  `run_status` all report `revised_score` for that generation while the live `best_score` is unchanged.
- Gates: module-size, serde-convention, cli-contract parity (no new command/flags, unchanged),
  schema-parity (no schema change), full Python suite, lockfiles unchanged.

## Documentation

`docs/evaluator-epochs.md` gains a "Surfacing recorded re-scores (Slice D2c)" subsection: `show` /
`status` / `run_status` now show, per generation, the latest active-epoch re-score recorded by
`rescore --apply` (score, who, when) alongside the unchanged live score; only active-epoch revisions are
surfaced; read-only and Python-only. Update the D2b "Not in this slice" note (reading from the archive)
to point at D2c. CHANGELOG entry.

## Deferred / out of scope

- A full revision-history dump (all revisions, not just the latest active-epoch one), e.g.
  `rescore --history`, remains future work.
- Teaching training-export to prefer the latest active-epoch revision over the live score (a trust
  change, out of scope for a read-only surfacing slice).
- TypeScript surfacing (revision data is Python-only).

## Acceptance criteria advanced by this slice

- AC-885 "surface stale evaluator lineage in CLI/API/dashboard outputs so operators know when numbers
  are not directly comparable": `show`/`status`/`run_status` now also show when a stale score has a
  recorded active-epoch re-score, so the operator sees both the stale number and the fresh one.
