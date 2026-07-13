# AC-885 Slice D2b: persist a re-score (promote + archive)

Date: 2026-07-12
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice D2b of Slice D. Slice 1, B, C1, C2, C3, D1, D2a are merged (#1204, #1208, #1210, #1211, #1212, #1213, #1214).

## Purpose

D2a added `autoctx rescore`, which re-runs the current evaluator against a stale generation's original
artifact and reports the fresh score, report-only. D2b adds the persistence path: an `--apply` flag that
promotes a fresh active-epoch re-score onto the generation as its score of record, while archiving the
original score's lineage so nothing is silently lost. This closes the last AC-885 thread.

## Decision of record: promote + archive (not append-only)

`--apply` archives the original `(score, epoch, quarantined)` into a new `generation_score_revisions`
table, then updates the generation to the fresh active-epoch score and clears its quarantine, all
atomically. The generation row becomes current and trusted, so D1 surfacing (`show`/`status`) shows
`current` and training-export includes it; the original is preserved verbatim in the archive.

This is consistent with the AC-885 thesis (no SILENT cross-epoch overwrite): a promotion is explicit
(opt-in `--apply`, default stays report-only), narrow (only generations whose fresh epoch equals the
active epoch), and audited (the original is archived, so it is reversible in principle). The rejected
alternative (append-only audit that never touches the live row) was declined because nothing yet
consumes the revisions, so it would be a persisted no-op until an unscheduled follow-up.

## Decisions of record

1. **New shared table `generation_score_revisions`, Python-written, TS schema-parity only.** The table
   exists in both packages' migrations (Python 018 + TS 016, byte-identical) so cross-package databases
   stay schema-compatible; only Python writes to it, matching the C1-D2a "registry/judge path is
   Python-only" asymmetry. It is a SHARED table in the parity manifest, not python-only.
2. **`--apply` promotes only `revalidated` generations whose fresh epoch equals the active epoch**
   (`status == "revalidated"` and `new_matches_active`). A drifted re-score (fresh epoch != active,
   because the current spec no longer reproduces the active epoch) is NEVER promoted: it is reported
   with the D2a drift warning but not written. Skipped/error generations are not written.
3. **Atomic per-generation promote.** Each promote reads the generation row, inserts the archive
   revision, and updates the generation (`best_score`, `evaluator_epoch`, `quarantined = NULL`) in one
   transaction. This is per-`(run_id, generation_index)`, so it never over-clears quarantine on other
   rows (unlike the epoch-scoped `clear_quarantine_for_epoch`, which is for the promotion workflow).
4. **`best_score` is the score of record.** `show`/`status`/`run_status`/training-export all surface
   `best_score`; the promote updates `best_score` (and the row's `evaluator_epoch` + `quarantined`).
   `mean_score`, `elo`, and tournament counters are run-time metadata and are left untouched; the
   archive preserves the original `best_score` as `previous_score`.
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
def persist_rescore_revision(
    self, run_id: str, generation_index: int, new_score: float, new_epoch: str,
    *, created_by: str | None = None,
) -> bool:
    """Promote a re-score onto a generation, archiving the original. Returns False if the row is absent.

    In one transaction: read the current generation row; insert an archive row into
    ``generation_score_revisions`` (revision_epoch/score = the new values; previous_* = the archived
    original); update the generation's ``best_score`` = new_score, ``evaluator_epoch`` = new_epoch,
    ``quarantined`` = NULL. Per-(run_id, generation_index); no other row is touched.
    """
```

Optional typed read `list_rescore_revisions(run_id, generation_index) -> list[GenerationScoreRevisionRow]`
(a new `TypedDict` in `row_types.py`) for tests and later consumers.

### D2b.3: the `--apply` CLI wiring (`cli_rescore.py`)

- Add `apply: bool = typer.Option(False, "--apply", help="Persist matching re-scores...")` and
  `by: str = typer.Option("", "--by", help="Reviewer identity recorded on applied revisions")`.
- After `reports` is built and before output: when `apply`, for each `rep` with `rep.status ==
"revalidated"` and `rep.new_matches_active`, call `store.persist_rescore_revision(run_id,
rep.generation_index, rep.new_score, rep.new_epoch, created_by=by or None)`; collect the applied
  generation indices.
- Output: `--json` payload gains a top-level `"applied"` (list of promoted generation indices) and each
  generation dict gains `"applied": bool`. The rich table gains an `Applied` column and the summary line
  reports how many were promoted vs re-scored. Update the module docstring + summary so the
  "writes nothing" language is conditional on `--apply`.
- `--apply` changes nothing about the fail-safe contract: only revalidated+matches-active generations
  are written; drifted, skipped, and error generations are never persisted.
- Contract: add the `--apply` and `--by` flags to the `rescore` entry in `docs/cli-contract.json`.

## Data flow

```
operator: autoctx rescore <run_id> --apply [--by jay]
  -> D2a: re-score stale generations under the current evaluator -> reports
  -> for each report where revalidated AND new_matches_active:
       persist_rescore_revision(run_id, gen, new_score, active_epoch, created_by=by)
         [txn] archive original (score,epoch,quarantined) into generation_score_revisions
               update generations SET best_score=new, evaluator_epoch=active, quarantined=NULL
  -> print report with an Applied column / applied[] list
  => show/status now show the generation as `current`; training-export includes it; history in revisions
```

## Error handling and edge cases

- **`--apply` on a drifted re-score** (`new_matches_active` False): reported with the drift warning, not
  written. The operator must reconcile the spec with the active epoch first (or promote a new epoch via
  the C-slice workflow).
- **`--apply` with no qualifying generation:** nothing is written; the report still prints.
- **Missing generation row at write time** (raced deletion): `persist_rescore_revision` returns False;
  the CLI records it as not-applied for that generation. No crash.
- **Re-applying** the same generation: appends another revision (full history; the archive `previous_*`
  reflects whatever the row held at that apply, which after a prior apply is the already-promoted
  value). Idempotent in effect on the generations row (same active epoch), additive in the archive.
- **Report-only (no `--apply`):** unchanged from D2a, writes nothing.

## Testing

- Migration/parity (TS): schema-parity gate covers the new shared table (add to manifest); ledger
  tests assert TS 016 <-> Python 018 pairing (Python-owned-then-TS marks TS applied without re-run;
  TS-owned-then-Python re-runs safely via `IF NOT EXISTS`); the new table exists once, not duplicated.
- Migration/parity (Python): `test_cross_runtime_migration_ledgers` reconciles both ledgers with the
  new pairing; `test_sqlite_store_bootstrap` bootstrap-then-migrate equivalence holds with the new
  table.
- Storage: `persist_rescore_revision` archives the original, updates `best_score`/`evaluator_epoch`,
  clears `quarantined`, is atomic, returns False for a missing row; `mean_score`/`elo` untouched.
- CLI: `rescore --apply` on a matching stale generation promotes it (generation now `current`, quarantine
  cleared, one revision row with the archived original) and the output marks it applied; `--apply` on a
  DRIFTED re-score does NOT write (generation untouched, no revision); default (no `--apply`) writes
  nothing (D2a preserved); `--by` is recorded on the revision.
- Gates: module-size, serde-convention, cli-contract parity (the updated `rescore` flags), schema-parity,
  the TS ledger tests, full Python + TS suites, lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "Persisting a re-score (Slice D2b)" subsection: `--apply`
promotes a matching re-score onto the generation and clears quarantine, the original is archived in
`generation_score_revisions`, only active-epoch matches are promoted, and the table is Python-written /
TS-schema-parity. Update the D2a section's "writes nothing" note to "writes nothing without `--apply`".
CHANGELOG entry.

## Deferred / out of scope

- Teaching reads to prefer the latest revision without promoting (not needed once promote updates the
  live row).
- A `revisions`/history CLI or API surface (the table is queryable; a dedicated view is future work).
- Reverting a promotion from the archive (the data supports it; no CLI in this slice).
- TS write path or a TS `rescore` command (Python-only, documented intentional gap).

## Acceptance criteria advanced by this slice

- AC-885 "Add a lazy re-score or revalidation path for raw artifacts when stale scored records are
  touched": `rescore --apply` persists the revalidated score under the active epoch, closing the
  re-score thread (D2a report + D2b persist).
- AC-885 "Changing a rubric/judge/harness evaluator cannot silently overwrite the active scoring
  baseline": the promote is explicit, active-epoch-gated, and archives the original, so no baseline is
  silently overwritten.
