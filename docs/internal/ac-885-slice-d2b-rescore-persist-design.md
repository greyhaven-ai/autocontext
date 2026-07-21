# AC-885 Slice D2b: persist a re-score (append-only audit)

Date: 2026-07-12
Status: revised after code review (originally promote + archive; see below)
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice D2b of Slice D. Slice 1, B, C1, C2, C3, D1, D2a are merged (#1204, #1208, #1210, #1211, #1212, #1213, #1214).

## Purpose

D2a added `autoctx rescore`, which re-runs the current evaluator against a stale generation's original
artifact and reports the fresh score, report-only. D2b adds the persistence path: an `--apply` flag that
records a fresh active-epoch re-score as an APPEND-ONLY audit revision, preserving lineage without ever
mutating the live score. This closes the last AC-885 thread.

## Decision of record: append-only audit (revised from promote + archive)

`--apply` appends one row to a new `generation_score_revisions` table capturing the fresh
`(revision_epoch, revision_score)` and archiving the generation's current `(evaluator_epoch, best_score,
quarantined)` as the `previous_*` columns. It does NOT modify the `generations` row, its quarantine
marker, or any derived table.

This is deliberately non-destructive. The original draft PROMOTED the re-score onto the `generations`
row (updating `best_score`/`evaluator_epoch`, clearing quarantine). Code review found three consequences
of mutating the live row plus its derived state: (1) the read-then-write archive had a TOCTOU race under
concurrency; (2) clearing quarantine on a score whose active epoch could shift mid-scoring would let a
stale score become trusted training data; (3) `knowledge_snapshots` (a separate cache that drives
cross-run inheritance, search/list rankings, and default skill export) would desync from the mutated
`generations.best_score`. Append-only dissolves all three: nothing live or derived is touched, so there
is no race, no trust change, and no desync. The cost is that reads/exports do not automatically reflect
the recorded re-score; teaching a consumer to prefer the latest active-epoch revision is possible future
work.

## Decisions of record

1. **New shared table `generation_score_revisions`, Python-written, TS schema-parity only.** The table
   exists in both packages' migrations (Python 018 + TS 016, byte-identical) so cross-package databases
   stay schema-compatible; only Python writes to it, matching the C1-D2a "registry/judge path is
   Python-only" asymmetry. It is a SHARED table in the parity manifest, not python-only.
2. **`--apply` records only `revalidated` generations whose fresh epoch equals the active epoch**
   (`status == "revalidated"` and `new_matches_active`). A drifted re-score (fresh epoch != active,
   because the current spec no longer reproduces the active epoch) is NEVER recorded: it is reported
   with the D2a drift warning but not written. Skipped/error generations are not written.
3. **Single atomic `INSERT ... SELECT`.** Recording pulls the generation's current `(evaluator_epoch,
best_score, quarantined)` into the `previous_*` columns and inserts the `revision_*` values in one
   statement, so there is no read-then-write gap and a concurrent writer cannot be lost. The `SELECT`
   matches no row when the generation is absent, so nothing is inserted (returns False).
4. **The live score of record is never changed.** `show`/`status`/`run_status`/training-export continue
   to surface the original `generations.best_score`; `--apply` only appends an audit revision. Nothing in
   the `generations` row, its quarantine marker, or `knowledge_snapshots` is touched, so this cannot
   poison training-export trust or cross-run rankings.
5. **Default stays report-only.** Without `--apply`, `rescore` behaves exactly as D2a (writes nothing).
   The module docstring and the summary line become conditional on `--apply`.

## Architecture

### D2b.1: the table (migrations + schema parity + bootstrap)

`autocontext/migrations/018_generation_score_revisions.sql` and its byte-identical twin
`ts/migrations/016_generation_score_revisions.sql`:

```sql
CREATE TABLE IF NOT EXISTS generation_score_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL,
    revision_epoch TEXT NOT NULL,
    revision_score REAL NOT NULL,
    previous_epoch TEXT,
    previous_score REAL,
    previous_quarantined INTEGER,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (run_id, generation_index)
        REFERENCES generations(run_id, generation_index) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_generation_score_revisions_run_gen
    ON generation_score_revisions(run_id, generation_index);
```

`IF NOT EXISTS` is required so a Python migration re-applied over a TS-created table (or vice versa)
does not raise "table already exists". Cross-package plumbing to add:

- Python ledger `storage/migration_ledgers.py` `TYPESCRIPT_TO_PYTHON_BASELINES`:
  `"016_generation_score_revisions.sql": ("018_generation_score_revisions.sql",)`.
- TS ledger `ts/src/storage/storage-migration-workflow.ts` `TYPESCRIPT_TO_PYTHON_MIGRATION_BASELINES`:
  `"016_generation_score_revisions.sql": ["018_generation_score_revisions.sql"]`.
- TS parity manifest `ts/src/storage/schema-parity-manifest.ts` `SCHEMA_PARITY_SHARED_TABLES`: add
  `"generation_score_revisions"` (alphabetical, between `generation_recovery` and `generations`).
- Python `storage/bootstrap_schema.py`: add the same `CREATE TABLE IF NOT EXISTS` + index block (the
  pip-install fallback schema), and append `"018_generation_score_revisions.sql"` to
  `_BOOTSTRAP_MIGRATIONS`.

### D2b.2: the write method (`storage/sqlite_store.py`)

```python
def record_rescore_revision(
    self, run_id: str, generation_index: int, new_score: float, new_epoch: str,
    *, created_by: str | None = None,
) -> bool:
    """Append an audit revision recording a re-score. Returns False if the generation is absent.

    A single atomic ``INSERT ... SELECT``: pull the generation's current ``(evaluator_epoch, best_score,
    quarantined)`` into the ``previous_*`` columns and insert ``revision_epoch/score`` = the new values.
    Does NOT modify the generations row, its quarantine marker, or any derived table. The SELECT matches
    no row when the generation is absent, so nothing is inserted.
    """
```

Optional typed read `list_rescore_revisions(run_id, generation_index) -> list[GenerationScoreRevisionRow]`
(a new `TypedDict` in `row_types.py`) for tests and later consumers.

### D2b.3: the `--apply` CLI wiring (`cli_rescore.py`)

- Add `apply: bool = typer.Option(False, "--apply", help="Record active-epoch re-scores as audit revisions...")` and
  `by: str = typer.Option("", "--by", help="Reviewer identity recorded on the audit revisions")`.
- After `reports` is built and before output: when `apply`, for each `rep` with `rep.status ==
"revalidated"` and `rep.new_matches_active`, call `store.record_rescore_revision(run_id,
rep.generation_index, rep.new_score, rep.new_epoch, created_by=by or None)`; collect the recorded
  generation indices.
- Output: `--json` payload gains a top-level `"applied"` (list of recorded generation indices) and each
  generation dict gains `"applied": bool`. The rich table gains a `Recorded` column and the summary line
  reports how many audit revisions were recorded (noting the live score was not changed). Update the
  module docstring + summary so the "writes nothing" language is conditional on `--apply`.
- `--apply` changes nothing about the fail-safe contract: only revalidated+matches-active generations
  are recorded; drifted, skipped, and error generations are never written.
- Contract: add the `--apply` and `--by` flags to the `rescore` entry in `docs/cli-contract.json`.

## Data flow

```
operator: autoctx rescore <run_id> --apply [--by jay]
  -> D2a: re-score stale generations under the current evaluator -> reports
  -> for each report where revalidated AND new_matches_active:
       record_rescore_revision(run_id, gen, new_score, active_epoch, created_by=by)
         [1 stmt] INSERT INTO generation_score_revisions
                  SELECT ..revision_epoch/score.., evaluator_epoch, best_score, quarantined, by
                  FROM generations WHERE run_id=? AND generation_index=?
  -> print report with a Recorded column / applied[] list
  => generations row + quarantine + knowledge_snapshots UNCHANGED; the revision is a pure audit record
```

## Error handling and edge cases

- **`--apply` on a drifted re-score** (`new_matches_active` False): reported with the drift warning, not
  written. The operator must reconcile the spec with the active epoch first (or promote a new epoch via
  the C-slice workflow).
- **`--apply` with no qualifying generation:** nothing is written; the report still prints.
- **Missing generation row at write time** (raced deletion): the `INSERT ... SELECT` matches no row, so
  `record_rescore_revision` returns False and the CLI records it as not-applied. No crash.
- **Re-applying** the same generation: appends another revision (full history). Because the generation is
  never mutated, each revision archives the same unchanged current `previous_*` values.
- **Report-only (no `--apply`):** unchanged from D2a, writes nothing.

## Testing

- Migration/parity (TS): schema-parity gate covers the new shared table (add to manifest); ledger
  tests assert TS 016 <-> Python 018 pairing (Python-owned-then-TS marks TS applied without re-run;
  TS-owned-then-Python re-runs safely via `IF NOT EXISTS`); the new table exists once, not duplicated.
- Migration/parity (Python): `test_cross_runtime_migration_ledgers` reconciles both ledgers with the
  new pairing; `test_sqlite_store_bootstrap` bootstrap-then-migrate equivalence holds with the new
  table.
- Storage: `record_rescore_revision` archives the generation's current `(evaluator_epoch, best_score,
quarantined)` as `previous_*` and inserts the `revision_*` values, leaves the generation row
  UNCHANGED, is a single atomic statement, and returns False for a missing row.
- CLI: `rescore --apply` on a matching stale generation records an audit revision (generation row
  UNCHANGED, one revision row with the archived current values) and the output marks it recorded;
  `--apply` on a DRIFTED re-score does NOT write (no revision); default (no `--apply`) writes nothing
  (D2a preserved); `--by` is recorded on the revision.
- Gates: module-size, serde-convention, cli-contract parity (the updated `rescore` flags), schema-parity,
  the TS ledger tests, full Python + TS suites, lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "Persisting a re-score (Slice D2b)" subsection: `--apply`
records a matching re-score as an append-only audit revision in `generation_score_revisions` without
changing the live score or quarantine, only active-epoch matches are recorded, and the table is
Python-written / TS-schema-parity. Update the D2a section's "writes nothing" note to "writes nothing
without `--apply`". CHANGELOG entry.

## Deferred / out of scope

- Teaching reads/exports to prefer the latest active-epoch revision over the live score (the append-only
  design does not do this yet; a possible future slice).
- A `revisions`/history CLI or API surface (the table is queryable; a dedicated view is future work).
- The promote-onto-the-live-row variant (updating `generations.best_score`, clearing quarantine,
  reconciling `knowledge_snapshots`), declined for this slice on the consistency grounds above.
- TS write path or a TS `rescore` command (Python-only, documented intentional gap).

## Acceptance criteria advanced by this slice

- AC-885 "Add a lazy re-score or revalidation path for raw artifacts when stale scored records are
  touched": `rescore --apply` records the revalidated active-epoch score as an audit revision, closing
  the re-score thread (D2a report + D2b persist).
- AC-885 "Changing a rubric/judge/harness evaluator cannot silently overwrite the active scoring
  baseline": recording is explicit, active-epoch-gated, and never mutates the live score, so no baseline
  is overwritten at all.
