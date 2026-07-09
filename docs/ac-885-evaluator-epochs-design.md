# AC-885 Slice 1: Evaluator epoch identity + comparability guard

Date: 2026-07-09
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: the first of four slices (A + comparability guard). Slices B/C/D are deferred (see Section 8).

## Purpose

Give every score produced by the main evaluation path a content-addressed identity for
"the criteria that produced it," so scores from different rubrics or judges are never silently
compared. Two scores are comparable only when their evaluator epochs are equal.

This generalizes a mechanism autocontext already has and relies on, but only in the ambient
trainer: `eval_fingerprint()` (`ambient/evaluate.py`) hashes `provider | model | rubric_hash | ...`
into an epoch, and `promote.py` refuses to compare candidate scores across a mismatched
`anchor_model`. The main LLM-judge path (`JudgeResult`, produced by `LLMJudge`, and the
round-to-round baseline comparison inside `ImprovementLoop`) carries no evaluator lineage today,
so a rubric or judge change is compared as if nothing changed. This slice closes that gap for the
judge path and the improve loop.

Integration note (result of tracing the code): the `autoctx run` backpressure gate compares
tournament/Elo scores for game scenarios and never touches a rubric or judge, so it carries no
evaluator epoch. LLM-judge scores are compared in `ImprovementLoop`
(`execution/improvement_loop.py`), which already treats a vetoed round as not a legitimate
baseline (AC-750) and reasons about candidates "collected under the previous objective set." The
epoch guard extends that existing "only a legitimate round is a baseline" logic, so the improve
loop, not the run-loop gate, is the correct home.

## Decisions of record

1. **Epoch identity = rubric content + judge model, not sampling.**
   `evaluator_epoch = sha256(canonical_json({rubric_hash, judge_provider, judge_model}))`.
   `judge_samples` / `judge_temperature` are deliberately excluded: they are within-epoch
   variance, owned by the AC-881 noise-calibration layer, not a new evaluator.
2. **Guard behavior = re-baseline + flag stale.** When the improve loop would compare a round
   against a baseline from a different epoch, the prior-epoch baseline is flagged stale and
   excluded, and the loop re-baselines under the new active epoch. Never compares across epochs,
   never silently overwrites the baseline, never hard-freezes the loop.
3. **Parity via shared fixture, not schema codegen.** The critical guarantee is cross-language
   hash determinism; a byte-identical `epoch_id` fixture pins it (AC-877/881 precedent). The
   epoch is a hash primitive, not a structured contract artifact, so it stays out of the
   harness-optimization JSON-schema codegen.
4. **No migration in this slice.** The epoch rides in memory on the improve loop's round baseline
   and on the records the loop already persists (`JudgeResult`, `ImprovementResult`, and the
   trajectory metadata `task_runner` writes). No SQLite migration is needed here because the
   judge-score baseline comparison is in `ImprovementLoop`, not the `generations` table. Stamping
   the `generations` / `matches` / `human_feedback` rows (with a migration) is deferred to Slice B.
5. **Legacy fallback = null is comparable only to null.** A pre-epoch score carries
   `evaluator_epoch = None` ("unknown/legacy"); it is comparable only to another null. A null
   baseline versus a newly-stamped epoch reads as cross-epoch and triggers a one-time re-baseline.

## Concept

An **evaluator epoch** is a content-addressed identity for the evaluator (rubric + judge) that
produced a score. Changing the compiled rubric, the judge provider, or the judge model mints a
new epoch and makes prior scores non-comparable. Changing sampling config does not.

Value type:

```
EvaluatorEpoch {
  epoch_id: str          # sha256 hex of the canonical components below
  rubric_hash: str       # content hash of the compiled rubric
  judge_provider: str
  judge_model: str
}
```

`epoch_id` is the comparison key; the components are retained for operator-visible surfacing and
debugging.

## Architecture

### Component 1: the epoch primitive (shared, both languages)

New module in each language, each a focused unit well under the 800-line module cap:

- `autocontext/src/autocontext/execution/evaluator_epoch.py`
- `ts/src/judge/evaluator-epoch.ts`

Public surface (identical semantics both languages):

- `compute_evaluator_epoch(rubric_text, judge_provider, judge_model) -> EvaluatorEpoch` (hashes
  `rubric_text` internally to produce `rubric_hash`, then the `epoch_id`)
- `are_comparable(a: str | None, b: str | None) -> bool`: strict `epoch_id` equality; `None`
  equals only `None`.

`rubric_hash = sha256(rubric_text)`. `epoch_id` is `sha256` over **canonical JSON** of
`{"judge_model", "judge_provider", "rubric_hash"}` (keys sorted, no whitespace: Python
`separators=(",", ":")`, `ensure_ascii=False`; TS `JSON.stringify` over an object whose keys are
inserted in that sorted order, which yields the same bytes and raw unicode). Canonicalization is
the known cross-language determinism gotcha and is the single thing the parity fixture guards.

`rubric_text` is the collapsed rubric prompt. `LLMJudge` already accepts `str | RubricSpec | dict`
and collapses it to `self.rubric` (a prompt string); the hash is taken over that collapsed form so
the same criteria hash identically regardless of input shape.

### Component 2: stamping the judge output

`JudgeResult` (`execution/judge.py`) gains `evaluator_epoch: str | None = None`. `LLMJudge`
computes the epoch from the rubric used for that evaluation plus the configured judge
provider/model, and stamps it on the `JudgeResult` it returns. When a single `LLMJudge` scores
against different rubrics, each result carries the epoch for the rubric it actually used (the
epoch is a pure function of its inputs, so it may be memoized per distinct input tuple). The TS judge (`judge/llm-judge.ts`) mirrors this so a TS-scored record
and a Py-scored record under the same evaluator share an `epoch_id`.

### Component 3: active-epoch tracking (in memory, no migration)

The improve loop already tracks a running baseline across rounds (`best_score`, and
`last_unvetoed_score` for the delta check). This slice tracks the **epoch that produced the
current baseline** alongside it, as an in-memory `str | None`. The epoch for a round is computed
from the evaluator that scored it (the round's `JudgeResult.evaluator_epoch`, itself computed by
`LLMJudge`). The epoch is carried onto the `ImprovementResult` and the trajectory metadata that
`task_runner` already persists, so a stored improve result records which evaluator scored it. No
SQLite migration is added in this slice (see Decision 4); stamping the `generations` / `matches`
rows is Slice B.

### Component 4: the comparability guard

Inside `ImprovementLoop` (`execution/improvement_loop.py`), the round-to-round baseline update
(`effective_score > best_score`, and the `last_unvetoed_score` baseline) becomes epoch-aware.
When the current round's epoch differs from the baseline's epoch:

1. the prior-epoch baseline is flagged stale and excluded from the delta comparison,
2. the loop re-baselines under the current epoch (this round becomes the new baseline; the
   `max_score_delta` and best-tracking checks are computed within-epoch only),
3. an operator-visible `ImprovementLoopEvent` is emitted with reason `evaluator_epoch_rebaseline`.

The decision is a small pure helper: given the baseline `(score, epoch)`, the current round's
`(score, epoch)`, and the loop config, return whether to re-baseline and the stale-flagged prior
score. Keeping it pure makes it unit-testable in isolation from the loop, and it extends the
existing AC-750 "only a non-vetoed round is a legitimate baseline" rule with "and only a same-epoch
round." The new symbols avoid the `gate` / `guard` / `validator` naming tokens so the AC-484
taxonomy test (`test_gate_taxonomy`) is not triggered; `evaluator_epoch_rebaseline` is an event
reason string, not a new gate implementation.

## Data flow

```
rubric + judge cfg  ->  compute_evaluator_epoch  ->  EvaluatorEpoch.epoch_id
                                                          |
LLMJudge.evaluate  ->  JudgeResult{score, evaluator_epoch}
                                                          |
ImprovementLoop round: (effective_score, round_epoch)
                                                          |
baseline update  ->  epoch helper:
    are_comparable(baseline_epoch, round_epoch) ? compare/update baseline normally
                                                : flag baseline stale, re-baseline under
                                                  round_epoch, emit evaluator_epoch_rebaseline
                                                          |
ImprovementResult{best_score, evaluator_epoch}  ->  task_runner persists trajectory metadata
```

## Error handling and edge cases

- **Missing rubric / bare-string rubric:** hash the normalized text; never crash the judge on
  epoch computation. A failure to compute the epoch degrades to `None` (legacy), logged at debug,
  rather than failing scoring.
- **Legacy / null epoch (`evaluator_epoch = None`):** comparable only to null; a null baseline
  versus a newly-stamped epoch reads as cross-epoch and re-baselines once. A game scenario with no
  rubric/judge keeps `None` throughout, so the guard is a no-op there. Documented behavior.
- **Cross-language:** the shared fixture asserts byte-identical `epoch_id`; canonical JSON is the
  only serialization path, exercised by the fixture with unicode and key-order-sensitive inputs.

## Testing

- **Shared-fixture parity** (`fixtures/.../evaluator-epoch-cases.json`): a set of
  `{inputs, expected_epoch_id}` cases asserted byte-identical in Python and TypeScript. Includes a
  unicode-in-rubric case and cases that differ only by judge_model / rubric_hash to prove each
  input participates in the hash.
- **Unit (both languages):** `are_comparable` truth table including null semantics; stamping on
  `JudgeResult`; the pure epoch-baseline helper (re-baseline decision + stale flagging).
- **Required regression test (AC-885 criterion 4):** drive an `ImprovementLoop` where round 1 is
  scored under epoch-1 and the evaluator (judge model or rubric) changes so round 2 is scored under
  epoch-2; assert the epoch-1 baseline is flagged stale/excluded from round 2's delta comparison,
  the loop re-baselines under epoch-2, and the `evaluator_epoch_rebaseline` event is emitted.
- Existing gates unaffected: `test_module_size_limits` (new modules, not appended),
  `test_gate_taxonomy` (new symbols avoid gate/guard/validator tokens), ruff/mypy/lint, Py/TS
  suites, `uv.lock` unchanged.

## Documentation

A short section (ambient-trainer or a new evaluator-epochs doc) covering: what an epoch is, what
mints a new one (rubric or judge model, not sampling), the null-legacy fallback, the re-baseline
guard, and how epochs relate to the existing ambient `eval_fingerprint` / `anchor_model` and to
AC-881 noise calibration. CHANGELOG entry.

## Explicitly deferred (later slices)

- **Slice B (stamp the rest):** matches, human_feedback, calibration reports, rubric-drift
  snapshots, and training-export examples carry the epoch (broader migration + parity surface).
- **Slice C (epoch lifecycle):** candidate-to-active epoch promotion with promotion metadata (source
  patch, calibration anchors used, alignment/bias/variance deltas, human-review requirement,
  decision); generalize `promote.py`'s cross-anchor refusal.
- **Slice D (surfacing + lazy re-score):** stale-epoch warnings in CLI/status/replay, REST/API,
  and dashboard; lazy revalidation of raw artifacts when a stale scored record is touched.

## Acceptance criteria satisfied by this slice

- Score-bearing records include evaluator lineage (`JudgeResult` + `ImprovementResult` /
  trajectory metadata) in Python, plus the TS `JudgeResult` mirror, with a documented null-legacy
  fallback. (AC-885 #1, partial: judge path.)
- Changing a rubric/judge cannot silently overwrite the improve loop's scoring baseline
  (re-baseline guard). (AC-885 #2.)
- At least one regression test shows a record scored under an old evaluator is flagged/excluded
  after a new epoch is active. (AC-885 #4.)
- Documentation covers epoch interaction with calibration anchors and rubric drift. (AC-885 #6,
  partial.)

Criteria #3 (reports/exports distinguish active vs stale) and #5 (full re-score path) are Slice
B/D scope and are noted as deferred, not silently dropped.
