# Evaluator epochs

**Status:** shipped (AC-885 Slice 1). Companion to the design note
`docs/ac-885-evaluator-epochs-design.md`.

## What an evaluator epoch is

An **evaluator epoch** is a content-addressed identity for the evaluator that produced a score:
the compiled rubric plus the judge provider and judge model. It answers a single question about any
LLM-judge score: "which criteria produced this number?" Two scores are comparable only when their
evaluator epochs are equal. Change the rubric text, the judge provider, or the judge model and you
mint a new epoch, which makes every prior score non-comparable to the new ones.

The epoch is a hash, not a structured contract:

```
evaluator_epoch = sha256(canonical_json({rubric_hash, judge_provider, judge_model}))
rubric_hash     = sha256(rubric_text)
```

`rubric_text` is the collapsed rubric prompt. `LLMJudge` already accepts a bare string, a
`RubricSpec`, or a dict and collapses all three to one prompt string, so the same criteria hash
identically regardless of the input shape they arrived in. The canonical JSON is keys-sorted with
no whitespace (Python `separators=(",", ":")`, `ensure_ascii=False`; the TypeScript mirror builds
the object with keys in the same sorted order), so the two languages produce a byte-identical
`epoch_id`. That cross-language determinism is the one property pinned by a shared parity fixture
(see below).

The public surface, identical in both languages, lives in
`autocontext/src/autocontext/execution/evaluator_epoch.py` and `ts/src/judge/evaluator-epoch.ts`:

- `compute_evaluator_epoch(rubric_text, judge_provider, judge_model)` returns the
  `EvaluatorEpoch` value (its `epoch_id` is the comparison key; the components are retained for
  operator-visible surfacing and debugging).
- `are_comparable(a, b)` is strict `epoch_id` equality, with `None` equal only to `None`.

## What mints a new epoch, and what does not

Only three inputs participate in the hash: the rubric content, the judge provider, and the judge
model. Sampling configuration (`judge_samples`, `judge_temperature`) is deliberately excluded.
Sampling is within-epoch variance: it is the same evaluator scored more or fewer times, owned by
the AC-881 noise-calibration layer, not a different evaluator. Folding sampling into the epoch would
fragment lineage on every temperature tweak and defeat the point. So a rubric edit or a judge-model
swap re-baselines the loop; a sampling change does not.

## The null-legacy fallback

A score with no evaluator lineage carries `evaluator_epoch = None`, read as "unknown / legacy."
The comparability rule is deliberately narrow: `None` is comparable only to another `None`. A null
baseline versus a freshly-stamped epoch therefore reads as cross-epoch and triggers a one-time
re-baseline, exactly as any other epoch change would.

Two consequences worth stating plainly:

1. Pre-epoch scores (records created before this slice, or any path that never computes an epoch)
   stay mutually comparable to each other and are never silently compared against a stamped score.
2. Game scenarios (tournament / Elo backpressure in `autoctx run`) have no rubric and no judge, so
   their scores stay `None` throughout and the guard is a no-op there. Epoch tracking touches the
   LLM-judge improve-loop path only; it does not reach into the game-score gate, which was never a
   rubric or judge comparison in the first place.

Epoch computation never crashes scoring. If the epoch cannot be computed (missing or malformed
rubric, for instance), the judge degrades to `None` (logged at debug) rather than failing the
evaluation.

## The improve-loop re-baseline behavior

`LLMJudge` stamps `JudgeResult.evaluator_epoch` from the rubric it actually used plus the configured
judge provider and model. That epoch flows through `AgentTaskResult.evaluator_epoch` into
`ImprovementLoop`, which already tracks a running baseline across rounds (`best_score`, and
`last_unvetoed_score` for the delta check). This slice tracks the epoch that produced the current
baseline alongside those scores, as an in-memory `str | None`, and carries it onto the
`ImprovementResult` and the trajectory metadata `task_runner` persists.

When a round's epoch is not comparable to the running baseline's epoch, the loop does not compare
across epochs and does not silently overwrite the baseline. Instead it:

1. flags the prior-epoch baseline stale and excludes it from the delta comparison,
2. re-baselines under the new active epoch (the current round becomes the baseline; the
   `max_score_delta` and best-tracking checks are computed within-epoch only), and
3. emits an operator-visible `ImprovementLoopEvent` with reason `evaluator_epoch_rebaseline`
   carrying `stale_epoch` and `new_epoch`.

This extends the existing AC-750 rule ("only a non-vetoed round is a legitimate baseline") with
"and only a same-epoch round." The decision is a small pure helper (`resolve_epoch_rebaseline`),
unit-testable in isolation from the loop. The reason string
`EVALUATOR_EPOCH_REBASELINE = "evaluator_epoch_rebaseline"` is an event reason, not a new gate: the
new symbols deliberately avoid the `gate` / `guard` / `validator` naming tokens so the AC-484
taxonomy test is not triggered.

There is no SQLite migration in this slice. The epoch rides in memory on the loop's round baseline
and on the records the loop already persists. Stamping the `generations` / `matches` /
`human_feedback` rows with a migration is deferred to Slice B.

## Persisted and derived lineage (Slice B)

Slice 1 kept the epoch in memory on the loop's round baseline. Slice B writes it down: the epoch
that produced a score now rides on the persisted rows and on the records derived from them, so
lineage survives a process restart and travels with exports. The stamping rule is uniform
throughout: only a genuine LLM-judge score carries an epoch. Tournament-scored and human-scored
records stay `None`, exactly as the null-legacy fallback prescribes (a game score has no rubric and
no judge, so it has no evaluator identity to record).

**The `generations` column.** Migration 016 (Python) and migration 014 (TypeScript) add a nullable
`evaluator_epoch` column to `generations`, byte-identical across the two schemas per the parity
constraint. Python stamps it only at the two agent-task write sites (`cli._run_agent_task` and
`solve_task_execution`), reading `ImprovementResult.evaluator_epoch`. The tournament write site and
the `matches` rows are deliberately left null: those are Elo scores, not judge scores. The
TypeScript `generations` column is always null in practice, because the TypeScript package has no
agent-task-direct write path that produces a judge score. The column exists in the TypeScript schema
for parity (so the two migrations stay in lockstep and a future TypeScript writer has the slot), but
nothing populates it today. That asymmetry is intentional and load-bearing: it keeps the schemas
equal without inventing a TypeScript write site that does not exist.

**`rubric_calibration`.** `run_judge_calibration` threads the judge's own epoch onto
`CalibrationReport.evaluator_epoch`, taken from the judge result it already computed. The calibration
report is a judge score by construction, so it always has an epoch to record.

**`RunFacet`.** A run facet sources its `evaluator_epoch` from the best-scoring generation row's
epoch, the same row whose score the facet already surfaces. If that row is a tournament or legacy
row, the facet epoch is `None`, consistent with the row it points at.

**Training-export read-through.** `TrainingRecord.evaluator_epoch` (Python and TypeScript) is a
read-through of the stamped `generations` value, so an exported training example carries the lineage
of the score it was selected on. `MatchRecord` is left unstamped, matching the tournament decision.

**The `mixed_epoch` flag.** Two derived records aggregate across many samples, and an aggregate can
span more than one evaluator class. Following the comparability rule above, `None` (unknown / legacy)
is its own class, comparable only with `None`: `mixed_epoch` is set whenever an aggregate spans more
than one class, so a known epoch mixed with a null is flagged, while an all-null or empty aggregate is
not. `CalibrationSample.evaluator_epoch` records each sample's epoch, and
`CalibrationRound.mixed_epoch` is set when the round's samples span more than one class. The same
shape carries to rubric drift: `RubricSnapshot.evaluator_epochs` lists the known epochs present in the
snapshot, `RubricSnapshot.has_unknown_epoch` records whether any null-epoch facet is present, and
`RubricSnapshot.mixed_epoch` (with `DriftWarning.mixed_epoch`) flags the mixed case. A baseline
comparison warning reflects the classes of both snapshots, not the current snapshot alone.
These fields record lineage only. They do not change any arithmetic: the calibration means and the
drift statistics are computed exactly as before (pinned by a byte-identical-mean regression), and
the flag is a lineage annotation, not a filter. Enforcement (refusing to aggregate across epochs, or
partitioning by epoch before the math) is deferred to a later slice; Slice B's job is to make the
mixing visible, not to act on it.

## Epoch lifecycle (Slice C1)

Slice B recorded which epoch produced a score. Slice C decides what to do about it: which epoch a
scenario currently trusts, and which epochs are merely candidates awaiting promotion. Slice C1 lays
the foundation for that decision. It builds the per-scenario registry, the mechanical trigger that
populates it, and the marker on scores that a non-active epoch produces. The enforcement that
consumes the marker and the promotion workflow that clears it arrive in later sub-slices (see below).

**The per-scenario registry.** `EvaluatorEpochRegistry`
(`autocontext/src/autocontext/execution/evaluator_epoch_registry.py`) holds one record per (scenario,
epoch) pair, stored file-per-record under a per-scenario subdirectory, mirroring how the model
registry keeps its records. Each `EvaluatorEpochRecord` carries the epoch identity (the `epoch_id`
plus the rubric hash and judge provider and model that produced it) and an `activation_state` in
`{candidate, active, disabled}`. The invariant is one active epoch per scenario: `active_for` returns
it, and `activate` promotes a target epoch while demoting the prior active one to disabled. The
demotion is a state change, not a delete, so a rollback is reversible, and `activate` loads the
target first and no-ops on a missing id so a bad id can never leave a scenario with zero active
epochs.

**The mechanical observe trigger.** `observe` (and its id-only convenience `observe_id`, keyed on the
`epoch_id` string alone) is the only way epochs enter the registry, and its rule is purely
mechanical: the first epoch a scenario ever sees auto-activates (the bootstrap epoch becomes active
with no ceremony), and any subsequent, different epoch is registered as a candidate. A known epoch is
returned unchanged. The trigger is keyed per scenario, so two scenarios bootstrap independently. No
score is compared and no judgment is made here; the registry simply records what it has seen and
which epoch was first.

**The `quarantined` marker.** A generation score produced under an epoch that is not the scenario's
active one is stamped `quarantined` on its `generations` row (the nullable `quarantined` column added
by migration 017 in Python and migration 015 in TypeScript, byte-identical across the two schemas per
the parity constraint). The two agent-task write sites call the `observe_epoch_quarantined` helper,
which observes the score's epoch and reports whether it is non-active: a candidate or disabled epoch's
scores are marked quarantined, while the bootstrap or active epoch's scores are not. A score with no
epoch (tournament or legacy, epoch `None`) has nothing to observe and is never quarantined.

The marker is recorded here, not yet acted on. Slice C1 makes the fact visible on the row; it does
not change any loop or aggregation behavior. The enforcement that consumes the marker (ambient-promote
refusal and progression exclusion of quarantined scores) and the promotion workflow that turns a
candidate into the active epoch (and so clears the quarantine going forward) arrive in Slices C2 and
C3.

## Promotion (Slice C2)

Slice C1 left the registry able to record a candidate epoch but with no way to promote one. Slice C2
adds the promotion decision itself: given a candidate epoch and the calibration evidence for it,
decide whether to activate it, and if so record why and clean up after the activation. The operation
is `promote_evaluator_epoch(...)` in
`autocontext/src/autocontext/execution/evaluator_epoch_promotion.py`. It is pure with respect to
scoring: the caller supplies the `CalibrationReport` (produced by `run_judge_calibration`), the
`AlignmentTolerance`, and the scenario `Charter`, and the operation reads and writes only the registry
(and, on activation, the quarantine).

**The decision matrix.** Two inputs drive the outcome: whether the candidate's calibration alignment
passes the supplied tolerance (`tolerance.check(report.alignment)["passes"]`), and the autonomy dial
for the `promote_epoch` action (`decide(charter, "promote_epoch", scenario)`). The dial has the usual
three positions: propose and train require human approval, full is autonomous. The operation returns
one of five outcomes:

1. `noop`: the candidate id is missing from the registry or is already the active epoch, so there is
   nothing to promote.
2. `pending_review`: the autonomy dial requires approval and no reviewer decision was supplied yet.
   The candidate stays a candidate, its promotion metadata (with `requires_review` true and no
   decision) is recorded so a reviewer can see the calibration evidence, and the active pointer is
   untouched.
3. `activated`: either the autonomy dial is full and the calibration passes tolerance, or a reviewer
   approved the promotion. The candidate becomes the active epoch (the prior active one is demoted to
   disabled by the registry, reversibly), the promotion metadata is stamped, and the quarantine on the
   promoted epoch's prior scores is cleared (see below).
4. `blocked`: the autonomy dial is full but the calibration did not pass tolerance, so the operation
   refuses to activate. The candidate stays a candidate.
5. `rejected`: a reviewer supplied a rejecting decision. The decision is recorded on the candidate but
   it is not activated.

**The recorded human decision.** When a reviewer weighs in, the operation records a `ReviewerDecision`
(outcome `approved` or `rejected`, plus `reviewed_by` and `reviewed_at`) inside the promotion metadata
alongside the calibration deltas (alignment mean-absolute-error, bias, correlation, and the variance
delta), the anchor count, the `requires_review` flag, the promotion timestamp, and the previous active
epoch id. So a promotion carries both the machine evidence (calibration) and the human evidence
(who decided, when, and which way) in one record.

Two behaviors surfaced in review are worth stating plainly:

- **A reviewer-approved promotion overrides the calibration decision.** If a reviewer approves, the
  candidate is activated even when the calibration did not pass tolerance: the human is the higher
  authority. The calibration deltas are still recorded in the promotion metadata, so an override is
  visible after the fact (you can see that a human promoted an epoch whose calibration was out of
  tolerance), not silently discarded.
- **A reviewer-rejected epoch stays a candidate, it is not disabled.** A rejection records the decision
  and leaves the epoch in state `candidate`, so it remains re-promotable later (a better calibration
  run, or a reviewer changing their mind, can promote it) and its scores stay quarantined in the
  meantime. Rejection is "not now," not "never."

**Retroactive quarantine clearing.** When an epoch is activated, its prior scores were stamped
`quarantined` while it was a candidate (per Slice C1). Those markers are now stale: the epoch is the
active one, so its scores are canonical. The operation clears them retroactively via
`SQLiteStore.clear_quarantine_for_epoch(scenario, epoch_id)`, a scenario-scoped `UPDATE` that unmarks
only the promoted epoch's rows under that scenario and leaves every other scenario's rows untouched.
Clearing runs only on the `activated` outcome; `pending_review`, `blocked`, `rejected`, and `noop`
leave the quarantine in place. The clear is a documented limitation in one respect: it clears the
persisted `generations` rows in SQLite, but it does not retroactively rewrite the TaskRunner
task-queue-JSON snapshot, so a score that was serialized into that snapshot as quarantined keeps its
stale marker there. The SQLite row is the source of truth for enforcement; the JSON snapshot is a
point-in-time artifact and is not reconciled.

**What is deferred.** Slice C2 is the promotion decision as a pure operation. The trigger that calls
it (a CLI subcommand or an ambient stage that runs a calibration and then promotes) is deferred to C3,
as is generalizing `promote.py`'s cross-anchor refusal onto this path. Slice C2 makes promotion
decidable and recordable; wiring it into an operator-facing entry point is the next sub-slice.

## Trigger and enforcement (Slice C3)

Slice C2 made promotion a pure, recordable operation but left it without an operator-facing entry
point, and Slice C1's `quarantined` marker was still recorded but not acted on. Slice C3 supplies
both: a human trigger for the promotion workflow, and the first enforcement that consumes the marker.

**The `autoctx epoch` CLI (the human trigger).** `autocontext/src/autocontext/cli_epoch.py` adds an
`autoctx epoch` command group with three subcommands:

- `epoch list [--scenario NAME]` prints the evaluator-epoch records (candidate, active, disabled) as
  JSON, over one scenario or all scenarios in the registry. It is read-only: it surfaces the
  candidates awaiting a decision without changing anything.
- `epoch approve SCENARIO EPOCH_ID --charter PATH [--by WHO]` approves a candidate and activates it.
- `epoch reject SCENARIO EPOCH_ID --charter PATH [--by WHO]` rejects a candidate; it stays a
  candidate and its scores stay quarantined (rejection is "not now," not "never," per Slice C2).

Both approve and reject are **pure human overrides**. They call `promote_evaluator_epoch` with
`calibration_report=None` and a `ReviewerDecision` carrying the outcome, the reviewer identity
(`--by`, defaulting to `operator`), and the timestamp. Because the human is the higher authority
(the C2 rule), an approval activates the candidate on the reviewer's word alone, with no calibration
evidence supplied on this path, and the decision is recorded on the epoch record so the override is
visible after the fact. This is the operator's manual trigger, distinct from an ambient stage that
would run a calibration and promote autonomously.

**Charter-based target_name resolution (distinct from the scenario key).** The `SCENARIO` argument is
the registry and sqlite key (for example `grid_ctf`). The promotion policy, however, is looked up by
the charter target NAME, and the two are deliberately distinct keys. The CLI resolves the target name
from the scenario by scanning the charter's targets and matching each target's selector through
`split_role_selector`: the target whose selector binds the given scenario supplies the target name
passed into `promote_evaluator_epoch`. A valid charter forbids a target name that collides with a
registered scenario, so passing the scenario in as the target name would raise inside the autonomy
`decide` call; resolving through the selector keeps the two key spaces separate (the C2 lesson). If no
charter target selects the scenario, the command fails with a clear error rather than guessing.

**Training-export quarantine exclusion (the first enforcement).** `export_training_data` (Python) and
its TypeScript mirror now exclude quarantined generation scores by default. The parameter is
`include_quarantined`, defaulting to `False`: a generation whose Slice C1 `quarantined` marker is set
(a score produced under a non-active, unpromoted evaluator epoch) is skipped, so training data is not
drawn from an evaluator the scenario has not promoted. Passing `include_quarantined=True`
(`includeQuarantined` in TypeScript) restores the old behavior and keeps the quarantined rows. This is
the enforcement Slice C1 deferred: the marker recorded on the row now changes what the export yields.

Matches are NOT affected by this exclusion. A tournament match carries no evaluator epoch (it is an
Elo score, not a judge score, so it has no evaluator identity to quarantine), so `include_matches`
emissions are unchanged; only judge-scored generations can carry a `quarantined` marker and only they
are filtered.

**What remains deferred.** The other enforcement consumer, the ambient-promote refusal (an ambient
promote stage declining to act on a quarantined-only signal), is still deferred, as is generalizing
`promote.py`'s cross-anchor refusal onto this path. Slice C3 lands the operator-facing trigger and the
training-export enforcement; the autonomous ambient trigger and the remaining refusals follow later.

## Slice D1: stale surfacing

Slices 1 through C3 built the epoch identity, the persisted lineage, the per-scenario registry, and
the promotion workflow, but every read surface still dropped `evaluator_epoch` and `quarantined`
before it reached an operator. Slice D1 closes that gap, read-only: an operator looking at a
finished run can now see whether a generation's score is still comparable to the scenario's active
evaluator epoch. It does not re-score or revalidate anything; that lazy re-score of raw artifacts is
deferred to D2 (see "Deferred slices" below).

**The four-state classification.** `classify_epoch_lineage(row_epoch, active_epoch)`
(`execution/evaluator_epoch.py`) is a pure function returning one of `current`, `stale`, `unknown`,
`no_active_epoch`:

- `no_active_epoch`: the scenario has no promoted active epoch (nothing to compare against, for
  example a game or no-judge run). Nothing is asserted.
- `unknown`: an active epoch exists, but the row carries no epoch of its own. This is a legacy or
  pre-slice row, not a stale one, and it is deliberately not flagged: a `None` row epoch is `unknown`,
  never `stale`, so old runs and tournament scores are not flooded with false warnings.
- `current`: both epochs are known and equal (per `are_comparable`).
- `stale`: both epochs are known and different. This is the only warning-worthy state.

A registry-aware `annotate_status_rows(rows, scenario, registry)` (new module
`execution/epoch_lineage.py`, kept separate from the leaf `evaluator_epoch.py` because it depends on
`EvaluatorEpochRegistry` and co-locating would cycle) reads the scenario's active epoch once, then
classifies each row and returns annotated copies without mutating the input.

**Fields added to the read surfaces.** `SQLiteStore.run_status()` now carries `evaluator_epoch` and
`quarantined` in every generation row it returns, which is the single plumbing fix that feeds the CLI
`show` and `status` commands. Both commands' `--json` output gains, per generation,
`evaluator_epoch`, `evaluator_epoch_status`, and `quarantined` (as a bool), plus a top-level
`active_evaluator_epoch`. The rich table gains a compact `Lineage` column rendering `ok` (current),
`stale`, `legacy` (unknown), or `-` (no active epoch), with quarantined rows marked; when any row is
stale or quarantined, a single yellow warning line prints after the table naming the active epoch's
short prefix.

**The HTTP `stale_epoch` warning.** `GET /api/cockpit/runs/{run_id}/status` (the cockpit API) carries the same
per-generation fields plus `active_evaluator_epoch`, and adds a `warnings` list with one entry per
stale generation, shaped like the existing `stale_score` warning:

```
{"warning_type": "stale_epoch", "generation": ..., "evaluator_epoch": ..., "active_evaluator_epoch": ..., "description": "..."}
```

**Python-full, TS-field-only.** The evaluator-epoch registry is Python-only (the C1/C2/C3 sections
above each mark the TS side an intentional gap), so TS has no active epoch to classify against. Slice
D1 fixes the TS DTO mappers (`RunInspectionGeneration` in `run-inspection-command-workflow.ts`, and
`formatGenerationStatus` in `cockpit-api.ts`) to stop dropping the persisted `evaluator_epoch` and
`quarantined` fields, so the shape is at parity. The stale-vs-active classification and the
`stale_epoch` warning stay Python-only: this is a documented, intentional gap consistent with the
prior slices, not an oversight.

**What remains deferred.** Slice D1 is read-only surfacing. Lazy re-score, that is, revalidating a
raw artifact and recomputing its score when a stale scored record is actually touched, is Slice D2
and is not implemented here.

## On-demand re-score (Slice D2a)

Slice D1 surfaced whether a generation's score is stale relative to its scenario's active evaluator
epoch, but stopped at surfacing: it did not re-score anything. Slice D2a adds the re-score /
revalidation path itself, report-only: `autoctx rescore <run_id> [--generation N] [--json]`
(`autocontext/src/autocontext/cli_rescore.py`) re-runs the CURRENT evaluator against a stale
generation's ORIGINAL stored competitor artifact and reports what it scores today, without touching
the database.

**Why the current evaluator, not the historical one.** The registry stores only the active
`epoch_id`, not the rubric text, provider, or model behind it, so a historical active rubric cannot
be reconstructed. Re-score therefore always runs under the scenario's CURRENT evaluator (its current
`spec.judge_rubric` plus the configured judge provider and model) and reports whether the freshly
computed epoch matches the registry's active epoch (`new_matches_active`). That is the
operator-meaningful question, and it fails safe even when the current spec has drifted from the
active epoch: the re-score still runs, and the report shows the mismatch explicitly rather than
hiding it.

**What it does.** By default the command targets every stale generation in the run (its own epoch
known and different from the scenario's active epoch); `--generation N` targets one specific
generation regardless of staleness, bounding LLM cost to what the operator actually asks for. For
each target it re-scores the ORIGINAL stored competitor artifact through the scenario task's own
`evaluate_output`, the same path a fresh run scores through, and reports the original score/epoch
next to the new score/epoch, plus `was_stale`, `new_matches_active`, and `score_delta`.

**The five fail-safe statuses.** Every generation report carries exactly one of:

- `revalidated`: re-scored successfully under the current evaluator.
- `skipped_no_artifact`: no stored competitor output for this generation.
- `skipped_no_active_epoch`: the scenario has no promoted active evaluator epoch to compare against.
- `skipped_no_evaluator`: the scenario has no reconstructable rubric judge (a game or non-agent-task
  scenario), or the evaluator produced no epoch.
- `error`: the evaluator raised (for example a provider error); the message is carried in the
  report, and other generations still report normally.

Only a missing run is a hard failure (exit 1, matching `show`); every other failure mode degrades to
a per-generation skip and the command still exits 0.

**It writes no score or lineage.** No `upsert_generation`, no registry activation/promotion, no
quarantine clear, anywhere in this path. It opens only an existing store (a missing database is
reported as not-found, never created) and runs the configured evaluator hooks so the re-score matches
production. The command fetches, re-scores in memory, and prints, either a Rich table (which surfaces
the old / new epoch and a warning when the fresh epoch does not match the active one) or a `--json`
payload of `{run_id, scenario, active_evaluator_epoch, generations: [...]}`.

**Python-only.** Like the `epoch` CLI, `rescore` depends on the Python-only LLM-judge and
evaluator-epoch registry path, so it has no TypeScript equivalent; this is a documented intentional
gap in `docs/cli-contract.json`, not an oversight.

**What remains deferred.** Persisting a re-score (an `--apply` flag that writes the fresh score
somewhere durable, backed by a net-new `generation_score_revisions`-style table so the original
score's lineage is not clobbered) is Slice D2b. Auto-re-scoring on every read (`show`/`status`) was
considered and rejected: it would fire paid LLM calls on reads and make them slow and
nondeterministic, which is why `rescore` is an explicit operator-triggered command instead.

## Relationship to the ambient `eval_fingerprint` / `anchor_model`

autocontext already had this mechanism, but only in the ambient trainer. `eval_fingerprint()`
(`ambient/evaluate.py`) combines the anchor provider, anchor model, a hash of the rubric, the eval
suite name, and generation-mode flags into a pipe-joined fingerprint string (only the rubric
component is itself hashed), and `promote.py` refuses to compare candidate scores across a
mismatched `anchor_model`. The evaluator
epoch generalizes the same idea (a content-addressed evaluator identity that scores must match to be
comparable) and brings it to the main LLM-judge path, which previously carried no evaluator lineage:
a rubric or judge change was compared as if nothing had changed. The ambient fingerprint and the
evaluator epoch are the same pattern applied in two places; this slice closes the gap on the judge
path and the improve loop. It stays distinct from AC-881 noise calibration, which measures
within-epoch variance rather than deciding cross-epoch comparability.

## Deferred slices

This slice is the first of four. Slice B is now shipped (see "Persisted and derived lineage" above).
The remaining slices are explicitly deferred, not dropped:

- **Slice B (stamp the rest, shipped):** the `generations` column, calibration reports, rubric-drift
  snapshots, and training-export examples carry the epoch, with the migration and parity surface
  that implies. Tournament-scored `matches` and human-scored `human_feedback` records are left
  unstamped by design.
- **Slice C (epoch lifecycle):** candidate-to-active epoch promotion with promotion metadata
  (source patch, calibration anchors used, alignment / bias / variance deltas, human-review
  requirement, decision); generalizes `promote.py`'s cross-anchor refusal. C1 (the per-scenario
  registry, the mechanical observe trigger, and the `quarantined` marker) is shipped (see "Epoch
  lifecycle (Slice C1)" above); the enforcement that consumes the marker and the promotion workflow
  land in C2 and C3.
- **Slice D (surfacing + lazy re-score):** stale-epoch warnings in CLI / status / replay, REST /
  API, and dashboard, plus revalidation of raw artifacts when a stale scored record is touched.
  D1 (read-only surfacing: the four-state classification, the `show` / `status` / `run_status`
  fields, and the `stale_epoch` HTTP warning) is shipped, see "Slice D1: stale surfacing" above. D2a
  (the on-demand, report-only `autoctx rescore` command) is shipped, see "On-demand re-score (Slice
  D2a)" above. D2b (persisting a re-score behind an `--apply` flag and a score-revisions table)
  remains.
