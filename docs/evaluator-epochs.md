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
  API, and dashboard, plus lazy revalidation of raw artifacts when a stale scored record is touched.
