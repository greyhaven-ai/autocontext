# AC-885 Slice D2a: on-demand re-score (report-only)

Date: 2026-07-11
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: sub-slice D2a of Slice D. Slice 1, B, C1, C2, C3, D1 are merged (#1204, #1208, #1210, #1211, #1212, #1213).

## Purpose

D1 surfaced whether a generation's score is stale relative to its scenario's active evaluator epoch.
The last AC-885 thread is a re-score / revalidation path: re-run the current evaluator against a stale
generation's ORIGINAL artifact and report what it scores today. D2a delivers that path report-only: it
performs no database writes and does not overwrite any score. Persisting a re-score (which needs
net-new schema to avoid clobbering the original score's lineage) is deferred to D2b.

## Constraints that shaped the design (from the code map)

1. **The registry stores only the active `epoch_id`,** not the rubric text / provider / model (the
   score-write path uses `observe_id`, which leaves those empty). So D2a cannot reconstruct a
   historical active rubric. It re-scores under the CURRENT evaluator (the scenario's current
   `spec.judge_rubric` + settings judge) and reports whether the freshly computed epoch matches the
   registry's active epoch. This is the operator-meaningful question and fails safe when the current
   spec has drifted from the active epoch.
2. **Persistence would destroy lineage.** One row per `(run_id, generation_index)`, `upsert_generation`
   COALESCE-overwrites in place, and there is no score-history table. Report-only sidesteps this
   entirely; D2b adds the schema.

## Decisions of record

1. **Explicit trigger, not auto-on-read.** A new `autoctx rescore <run_id> [--generation N]` command.
   Auto-re-scoring on every `show`/`status` would fire paid LLM calls on reads and make them slow and
   nondeterministic. The operator "touches" a stale record by invoking `rescore`.
2. **Read-only.** No `upsert_generation`, no registry write, no quarantine clear. D2a fetches, re-scores
   in memory, and prints. (`evaluate_output` itself performs no DB write.)
3. **Faithful re-score via the scenario's own evaluator.** The re-score reuses the scenario task's
   `evaluate_output`, the same path a fresh run scores through (it builds the `LLMJudge` from
   `spec.judge_rubric` + `settings.judge_model` + provider and stamps the epoch). D2a does not
   re-implement scoring.
4. **Default target is the run's stale generations;** `--generation N` targets one specific generation
   regardless of staleness. This bounds LLM cost (re-scoring only what is stale) unless the operator
   asks for a specific generation.
5. **Fail-safe, never crash.** Every failure mode degrades to a per-generation skip with a reason and
   exit 0. Only a missing run is a not-found error (exit 1), matching `show`.

## Architecture

### D2a.1: the pure revalidation core (`execution/rescore.py`)

```python
RescoreStatus = Literal[
    "revalidated", "skipped_no_artifact", "skipped_no_active_epoch", "skipped_no_evaluator", "error"
]

@dataclass(frozen=True, slots=True)
class GenerationRevalidation:
    generation_index: int
    status: RescoreStatus
    reason: str
    original_score: float | None
    original_epoch: str | None
    new_score: float | None
    new_epoch: str | None
    active_epoch: str | None
    was_stale: bool           # original and active both non-null and different
    new_matches_active: bool  # new_epoch and active_epoch both non-null and equal
    score_delta: float | None # new_score - original_score when both present

def revalidate_one(
    generation_index: int,
    original_score: float | None,
    original_epoch: str | None,
    active_epoch: str | None,
    artifact: str | None,
    score_fn: Callable[[str], tuple[float | None, str | None]] | None,
) -> GenerationRevalidation: ...
```

`revalidate_one` skip precedence: `active_epoch is None` -> `skipped_no_active_epoch`; `score_fn is None`
(scenario has no reconstructable rubric judge) -> `skipped_no_evaluator`; falsy `artifact` ->
`skipped_no_artifact`; `score_fn` raises -> `error` (message carries the exception); `score_fn` returns
a `None` epoch -> `skipped_no_evaluator`; otherwise -> `revalidated`. The derived fields (`was_stale`,
`new_matches_active`, `score_delta`) are computed once from the inputs. `score_fn` is injected so the
core is pure and fully unit-testable with a fake scorer (no providers, no network, no paid calls).

### D2a.2: the CLI command (`cli_rescore.py`, registered on the main app)

`autoctx rescore <run_id> [--generation N] [--json]`:

1. Load settings + store; `store.get_run(run_id)` -> scenario (None -> not-found, exit 1).
2. `active_epoch = EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs").active_for(scenario)`
   `.epoch_id` (or None).
3. `rows = store.run_status(run_id)` (carries `evaluator_epoch` since D1). Select targets: the row for
   `--generation N` if given, else rows that are stale (`evaluator_epoch != active_epoch`, both
   non-null).
4. Build the score function: `score_fn = _build_score_fn(scenario, settings)`, which returns `None`
   when the scenario is not an agent-task (game scenario, unregistered), else a closure over the
   scenario task:
   ```python
   cls = SCENARIO_REGISTRY[scenario]; task = cls()
   state = task.prepare_context(task.initial_state())
   def score(artifact: str) -> tuple[float | None, str | None]:
       result = task.evaluate_output(artifact, state)
       return result.score, result.evaluator_epoch
   ```
   (`_is_agent_task` is the existing `cli.py` predicate; reuse it.)
5. Fetch artifacts: `{gen_idx: content}` from `store.get_agent_outputs_by_role(run_id, "competitor")`.
6. Per target row, call `revalidate_one(...)`; collect `GenerationRevalidation`s.
7. `--json`: `{run_id, scenario, active_evaluator_epoch, generations: [<revalidation dicts>]}`. Rich
   table otherwise (gen, original score/epoch8, new score/epoch8, delta, stale?, matches-active?,
   status), with a one-line summary.

### D2a.3: contract + registration

`rescore` is a new top-level command, so add it to `docs/cli-contract.json` (`python: yes`,
`typescript: intentional_gap` with a reason: the re-score path depends on the Python-only judge +
evaluator-epoch registry, matching the epoch CLI). Register `app.command("rescore")` in `cli.py`.

## Data flow

```
operator: autoctx rescore <run_id>
  -> get_run -> scenario ; registry.active_for(scenario) -> active_epoch
  -> run_status(run_id) -> rows (evaluator_epoch each) ; select stale rows
  -> score_fn = task.evaluate_output(...) closure (or None for game scenarios)
  -> artifacts = get_agent_outputs_by_role(run_id, "competitor")
  -> per row: revalidate_one(gi, orig_score, orig_epoch, active_epoch, artifact, score_fn)
  -> report old vs new score + epoch, was_stale, new_matches_active, delta, status  (NO writes)
```

## Error handling and edge cases

- **Missing run:** not-found, exit 1 (like `show`).
- **Scenario has no active epoch:** every target row -> `skipped_no_active_epoch` (nothing is stale to
  revalidate).
- **Game / non-agent-task scenario** (score_fn None): `skipped_no_evaluator` (no rubric judge).
- **No stored competitor output** for a generation: `skipped_no_artifact`.
- **Evaluator raises** (provider error, malformed rubric): `error` with the message; other generations
  still report. Exit 0.
- **Current spec no longer reproduces the active epoch:** the re-score still runs under the current
  evaluator; `new_matches_active` is False and the report shows the new epoch differs from active, so
  the operator sees the drift explicitly.
- **`--generation N` not present in the run:** not-found for that generation, exit 1.

## Testing

- Unit (`revalidate_one` + derived fields): every status branch (revalidated, no_artifact,
  no_active_epoch, no_evaluator via None score_fn and via None epoch, error via raising score_fn), plus
  `was_stale` / `new_matches_active` / `score_delta` truth values, all with a fake in-memory `score_fn`
  (no providers).
- Integration (CLI): seed a run with a stale generation and a stored competitor output, patch
  `get_provider` with a deterministic fake provider (existing test pattern, no network) so
  `evaluate_output` returns a fixed score, invoke `rescore --json`, and assert the report shows the
  original vs new score, the new epoch, and `was_stale` true. Plus fail-safe cases: no active epoch,
  no competitor artifact, missing run (exit 1).
- Gates: module-size, gate-taxonomy, ruff/mypy, the serde-convention test, cli-contract parity (the new
  `rescore` entry), schema-parity (unchanged: no new columns), lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "Slice D2a: on-demand re-score (report-only)" subsection: the
`rescore` command, that it re-scores under the current evaluator and reports drift vs the active epoch,
the fail-safe skip statuses, that it writes nothing, and that persisting a re-score is D2b. CHANGELOG
entry (new `rescore` command).

## Deferred / out of scope (D2b and beyond)

- Persisting a re-score: the `generation_score_revisions` table, an `--apply` flag, clearing quarantine
  after a successful active-epoch re-score, and TS parity for the new table.
- Auto-on-read re-score (rejected: paid LLM calls on reads).
- Reconstructing a historical active rubric when the current spec has drifted (needs registry backfill
  of rubric text; a separate piece).
- TS parity for the `rescore` command (Python-only, documented intentional gap).

## Acceptance criteria advanced by this slice

- AC-885 "Add a lazy re-score or revalidation path for raw artifacts when stale scored records are
  touched": `autoctx rescore` re-runs the current evaluator against a stale generation's raw artifact
  and reports the fresh score and epoch, on operator demand.
