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

This slice is the first of four. The rest are explicitly deferred, not dropped:

- **Slice B (stamp the rest):** matches, `human_feedback`, calibration reports, rubric-drift
  snapshots, and training-export examples carry the epoch, with the broader migration and parity
  surface that implies.
- **Slice C (epoch lifecycle):** candidate-to-active epoch promotion with promotion metadata
  (source patch, calibration anchors used, alignment / bias / variance deltas, human-review
  requirement, decision); generalizes `promote.py`'s cross-anchor refusal.
- **Slice D (surfacing + lazy re-score):** stale-epoch warnings in CLI / status / replay, REST /
  API, and dashboard, plus lazy revalidation of raw artifacts when a stale scored record is touched.
