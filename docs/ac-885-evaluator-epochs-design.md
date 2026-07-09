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
`anchor_model`. The main scoring path (`JudgeResult`, the `generations` row, the backpressure/
advancement gate) carries no evaluator lineage today, so a rubric or judge change mid-run is
compared as if nothing changed. This slice closes that gap for the judge path and the gate.

## Decisions of record

1. **Epoch identity = rubric content + judge model, not sampling.**
   `evaluator_epoch = sha256(canonical_json({rubric_hash, judge_provider, judge_model}))`.
   `judge_samples` / `judge_temperature` are deliberately excluded: they are within-epoch
   variance, owned by the AC-881 noise-calibration layer, not a new evaluator.
2. **Guard behavior = re-baseline + flag stale.** When the gate would compare across epochs, the
   prior-epoch baseline is flagged stale and excluded, and the gate re-baselines under the new
   active epoch. Never compares across epochs, never silently overwrites the baseline, never
   hard-freezes the run.
3. **Parity via shared fixture, not schema codegen.** The critical guarantee is cross-language
   hash determinism; a byte-identical `epoch_id` fixture pins it (AC-877/881 precedent). The
   epoch is a hash primitive, not a structured contract artifact, so it stays out of the
   harness-optimization JSON-schema codegen.
4. **Minimal persistence.** One migration (016) adds `evaluator_epoch TEXT NULL` to the
   `generations` row. Matches, feedback, drift snapshots, calibration reports, and training
   exports are NOT stamped in this slice (deferred to Slice B).
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

- `compute_evaluator_epoch(rubric_hash, judge_provider, judge_model) -> EvaluatorEpoch`
- `are_comparable(a: str | None, b: str | None) -> bool`: strict `epoch_id` equality; `None`
  equals only `None`.

`epoch_id` is `sha256` over **canonical JSON** of `{"judge_model", "judge_provider",
"rubric_hash"}` (keys sorted, no whitespace: `separators=(",", ":")` in Python;
`JSON.stringify` over a sorted-key object in TS). Canonicalization is the known cross-language
determinism gotcha and is the single thing the parity fixture guards.

`rubric_hash` is derived from the compiled rubric. `LLMJudge` already accepts
`str | RubricSpec | dict` and collapses to a prompt; the hash is taken over the compiled/
normalized rubric form so the same criteria hash identically regardless of input shape. A bare
string rubric hashes its normalized text.

### Component 2: stamping the judge output

`JudgeResult` (`execution/judge.py`) gains `evaluator_epoch: str | None = None`. `LLMJudge`
computes the epoch from the rubric used for that evaluation plus the configured judge
provider/model, and stamps it on the `JudgeResult` it returns. When a single `LLMJudge` scores
against different rubrics, each result carries the epoch for the rubric it actually used (the
epoch is a pure function of its inputs, so it may be memoized per distinct input tuple). The TS judge (`judge/llm-judge.ts`) mirrors this so a TS-scored record
and a Py-scored record under the same evaluator share an `epoch_id`.

### Component 3: active-epoch persistence

Migration `016_generation_evaluator_epoch.sql` adds `evaluator_epoch TEXT NULL` to the
`generations` table. The generation writer (`loop/stages.py`) records the epoch of the evaluator
that produced that generation's scores onto the row. The run's **active epoch** is simply the
epoch on the most recent generation. `GenerationMetricsRow` (`storage/row_types.py`) gains the
matching optional field; the SQLite mixin reads/writes it. TS storage-schema parity
(`ts/migrations/`, `storage-schema-parity.test.ts`) gets the mirrored column.

### Component 4: the comparability guard

In the backpressure/advancement gate path (`loop/stages.py`), each score compared across
generations carries its epoch (read from the generation rows). When the current active epoch
differs from a prior baseline's epoch:

1. the prior-epoch baseline is flagged stale and excluded from the comparison,
2. the gate re-baselines under the active epoch (the new epoch's first generation becomes the
   baseline; advance/retry/rollback is computed within-epoch only),
3. an operator event is emitted with a new taxonomy reason key `evaluator_epoch_rebaseline`,
   registered in the gate taxonomy so `test_gate_taxonomy` passes.

The guard is a small pure helper (given the trajectory of `(score, epoch)` pairs and the active
epoch, return the within-epoch baseline and the set of stale-flagged prior scores) plus its wiring
into the gate. Keeping the decision pure keeps it unit-testable in isolation from the loop.

## Data flow

```
rubric + judge cfg ──▶ compute_evaluator_epoch ──▶ EvaluatorEpoch.epoch_id
                                                        │
LLMJudge.evaluate ──▶ JudgeResult{score, evaluator_epoch}
                                                        │
loop/stages writes generation row {mean_score, best_score, evaluator_epoch}
                                                        │
backpressure gate reads trajectory of (score, epoch) ──▶ guard:
    active_epoch == baseline_epoch ? compare normally
                                   : flag baseline stale, re-baseline, emit rebaseline event
```

## Error handling and edge cases

- **Missing rubric / bare-string rubric:** hash the normalized text; never crash the judge on
  epoch computation. A failure to compute the epoch degrades to `None` (legacy), logged at debug,
  rather than failing scoring.
- **Legacy rows (`evaluator_epoch = None`):** comparable only to null; the first stamped
  generation after upgrade reads as cross-epoch and re-baselines once. Documented behavior.
- **Cross-language:** the shared fixture asserts byte-identical `epoch_id`; canonical JSON is the
  only serialization path, exercised by the fixture with unicode and key-order-sensitive inputs.

## Testing

- **Shared-fixture parity** (`fixtures/.../evaluator-epoch-cases.json`): a set of
  `{inputs, expected_epoch_id}` cases asserted byte-identical in Python and TypeScript. Includes a
  unicode-in-rubric case and cases that differ only by judge_model / rubric_hash to prove each
  input participates in the hash.
- **Unit (both languages):** `are_comparable` truth table including null semantics; stamping on
  `JudgeResult`; the pure guard helper (within-epoch baseline selection + stale flagging).
- **Required regression test (AC-885 criterion 4):** run a scenario, score generation 1 under
  epoch-1; change the evaluator (swap judge model or rubric) so epoch-2 becomes active on
  generation 2; assert the epoch-1 baseline is flagged stale/excluded, the gate re-baselines under
  epoch-2, and the `evaluator_epoch_rebaseline` event is emitted.
- Existing gates unaffected: `test_module_size_limits` (new modules, not appended),
  `test_gate_taxonomy` (new reason key registered), storage-schema parity, ruff/mypy/lint,
  Py/TS suites, `uv.lock` unchanged.

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

- Score-bearing records include evaluator lineage (JudgeResult + generations row) in Python and
  TypeScript surfaces, with a documented null-legacy fallback. (AC-885 #1, partial: judge path.)
- Changing a rubric/judge cannot silently overwrite the active scoring baseline (re-baseline
  guard). (AC-885 #2.)
- At least one regression test shows a record scored under an old evaluator is flagged/excluded
  after a new epoch is active. (AC-885 #4.)
- Documentation covers epoch interaction with calibration anchors and rubric drift. (AC-885 #6,
  partial.)

Criteria #3 (reports/exports distinguish active vs stale) and #5 (full re-score path) are Slice
B/D scope and are noted as deferred, not silently dropped.
