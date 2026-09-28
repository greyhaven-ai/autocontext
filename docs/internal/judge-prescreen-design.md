# Judge pre-screen: skip confidently failing judge rounds

Date: 2026-09-27
Status: scope approved in brainstorming (through gated activation); pending written review
Linear: none yet (proposed as a new AC ticket)
Scope: Python only. Covers capture, offline replay, shadow mode, and gated activation of one behavior: skipping the LLM judge on a later improvement-loop round when a calibrated fast model is confident the round will fail. TypeScript parity is deferred (see Non-goals).

## Purpose

`ImprovementLoop` (`autocontext/src/autocontext/execution/improvement_loop.py`) judges every round.

- It runs up to `max_rounds=5` rounds, and each round calls `task.evaluate_output(...)`. That costs `judge_samples` × up to two provider calls (`judge.py:304-373`).
- Until AC-1026 lands, the verifier cache cannot replay results that carry an identity, so in practice every round is judged afresh.
- Most rounds before the last one score below `quality_threshold=0.9`.

This design adds a **pre-screen**: a small, local, calibrated classifier that predicts whether the judge will pass a round.

- On a later round where it is confident the revision will still fail, the loop skips the judge and revises from the last judged feedback. The loop already does this after a judge parse failure (`improvement_loop.py:322-331`).
- The pre-screen never accepts an output and never produces a score.
- It is certified only against the evaluator epoch it was trained on.

The approach, and several of its constraints, come from the jev-experiments "Reflex" work (`greyhaven-ai/jev-experiments`: `results/headroom-notes.md`, PR #12). That work learned Claude Code's skill routing from Claude Code's own history, and found four things that shape this design:

- **A teacher's recorded decisions are a usable training signal.** A fast model can take over the share of decisions it is calibrated to get right. The metric that matters is coverage at a target precision, with the threshold chosen on training data only. That makes the threshold evidence rather than "model confidence as permission" (`autocontext/docs/skill-routing.md:10`).
- **Data volume, not model design, was the binding constraint.**

  - Five model-design experiments came back flat.
  - A learning curve then showed the router still improving at 1,100 examples.
  - Decision-model (Jev) features hurt below about 70 examples and helped from about 136 up.

  So this design measures volume and the learning curve before anything else.

- **The target drifts.** Recent examples taught the router 12–14 points more than older ones. Here the judge's identity (its evaluator epoch) changes whenever its spec changes. Certification is therefore per epoch, and retraining is routine.
- **Shadow before steering.** The router ran in a no-effect shadow trial before activation was considered. Skipped decisions need audits, because a skipped round has no ground truth.

## Decisions of record

1. **Scope runs through gated activation** (user decision, 2026-09-27). Each phase has a go/no-go gate, and a later phase happens only if the evidence supports it.
2. **One direction only.** The pre-screen may skip the judge only on rounds it predicts will fail.
   - It never accepts an output and never supplies a score.
   - A skipped round never enters best-output selection, plateau detection, rebaseline, the 0.90–0.92 confirmation rule, the verifier cache, or persisted scores.
   - Reason: the judge's verdict is the quality gate, and its reasoning drives revision. A wrong skip costs one extra revision round; a wrong acceptance would ship an output nobody judged.
3. **Round eligibility.** A round may be skipped only if all of the following hold:
   - it is round 2 or later, because round 1 has no judged feedback to reuse;
   - the previous round was judged, so there is at most one skip in a row and feedback is never more than one revision stale;
   - it is not the last allowed round (`round_num < max_rounds`);
   - it is not a confirmation round following a judged pass;
   - neither the required-targets check nor a verifier-cache replay has already decided it (those paths already cost nothing);
   - the loop's current evaluator epoch is not None and equals the pre-screen's bound epoch.
4. **Certification is per evaluator epoch; training may span epochs.**
   - Labels from older epochs may be used as training data, because a predictor is not evidence.
   - The skip threshold and every gate below use verdicts from the current epoch only.
   - When the epoch changes, the pre-screen drops to shadow mode until it is re-certified.
   - This follows `autocontext/docs/judge-serving-identity.md`: scores from different epochs are never compared.
5. **Local features first.** The model uses only local features:

   - **Structural:** round number, the previous judged score and dimension scores, output length, edit size against the previous output, and required-concept coverage.
   - **Text:** TF-IDF of the output and the task prompt.

   Decision-model (Jev) question answers are an optional feature block behind a flag, for later. They send output text to a third party, and in the Reflex learning curve they only paid off with a few hundred examples.

6. **A new optional extra.** scikit-learn and numpy are not core dependencies (`autocontext/pyproject.toml`). The pre-screen ships as `autocontext[prescreen]`. Without the extra, the loop behaves exactly as it does today.
7. **Activation goes through the harness-optimization protocol, not a new gate.**
   - Turning the pre-screen on is a candidate of type `judge_policy` (`ts/src/harness-optimization/contract/json-schemas/candidate-evidence.schema.json`).
   - The candidate carries CandidateEvidence and IntegrityMetadata, with training and evaluation splits kept disjoint.
   - It is scored with a PromotionScore, and its margin is set by noise calibration (AC-881; `docs/internal/harness-optimization-protocol.md`).
   - Guards specific to the pre-screen, listed under Phase 3, are hard preconditions on top.
8. **Fail closed means "judge".** Any pre-screen problem makes the loop judge the round normally. That includes an error, a missing model, a feature failure, an epoch mismatch, or a timeout.
9. **Data stays local.** The capture ledger stores output text in the local run database, inside the same trust boundary as `agent_outputs`. Reports and evidence artifacts carry only ids, hashes and aggregates.

## System overview

```
ImprovementLoop, round N
  ├─ required targets missing?  ── yes → fail the round (existing, free)
  ├─ verifier-cache replay?     ── yes → replay the verdict (existing, free)
  ├─ round eligible and pre-screen active?
  │      predict P(fail) ── at or above threshold → skip the judge; revise from the last judged feedback
  │                      └─ otherwise           → judge (existing)
  └─ after a real judge call → JudgeLedger.record(...)   (Phase 0 capture)
```

New code lives under `autocontext/src/autocontext/prescreen/`, plus small changes to the loop.

### 1. Judge ledger (`prescreen/ledger.py`, migration `022_judge_ledger.sql`)

The ledger writes one row per real judge evaluation inside the loop, right after `task.evaluate_output` returns. Cache replays and missing-target rounds get no row. Each row holds:

- **Identity:** run id, task or scenario id, scenario family, round number, `evaluator_epoch`, rubric hash, task prompt hash, and `fixture_provenance`.
- **The item:** the task prompt text, or a pointer to where it can be rebuilt; the output text; and the output hash.
- **The verdict:** score, dimension scores, `disagreement`, `internal_retries`, parse method, the threshold used, and `passed = score >= quality_threshold`.
- **Context:** the previous judged round's score and dimension scores, plus the loop settings (`max_rounds`, `judge_samples`).
- **Pre-screen fields**, null until Phase 2: prediction, probability, `eligible`, `would_skip`, `skipped`, and model id.

The ledger is passed to `ImprovementLoop` as an optional `judge_ledger` parameter. It defaults to None, which captures nothing.

The ledger fills a gap: today no per-round training set exists. Only each loop's best output is stored, and intermediate outputs and raw verdicts are discarded.

### 2. Dataset and features (`prescreen/dataset.py`)

- Builds labelled examples from ledger rows for one scenario family. The label is `passed`.
- Groups examples by task, which is the unit the bootstrap resamples, and orders them in time by run.
- Hashes the feature spec, so a model is bound to exactly the features it was trained on.

### 3. Models (`prescreen/models.py`)

The models form a ladder, as in the Reflex work:

- **P0:** the base rate.
- **P1:** structural features only, in a logistic regression. The previous judged score is expected to be the strongest single signal.
- **P2:** TF-IDF of the output and the task prompt.
- **P3:** structural and TF-IDF features together in one logistic regression. The text block's weight and C are tuned by log-loss on a 70/30 time split of the training window, the recipe of the Reflex hybrid router (R6).
- **P4:** optional, behind a flag. P3 plus decision-model question answers.

### 4. Calibration and the skip rule (`prescreen/calibrate.py`)

- Calibration uses the last 30% of the training window, and only eligible rounds count.
- The skip threshold is the lowest predicted-fail probability at which the judge actually failed at least 95% of those rounds.
- At least 20 rounds must fall at or above the threshold. If that can't be met, there is no threshold and nothing is skipped.
- The target precision is a parameter with a default of 0.95.

### 5. Replay (`prescreen/replay.py`, `autoctx prescreen replay`)

The replay evaluates each model offline over expanding folds, ordered in time by run, and reports:

- agreement and log-loss on `passed`, and AUROC;
- the **skip rate**: the share of eligible judge calls that would be skipped;
- the **skip precision**: the share of skipped rounds that the judge failed, with Wilson intervals;
- a **learning curve** on a fixed recent holdout, halving the training window each step and comparing random samples with the most recent ones;
- the **value ceiling**: eligible failing rounds as a share of all judge calls. No pre-screen could save more than this.

Confidence intervals come from a paired bootstrap that resamples whole tasks (`harness/benchmark_stats.py:paired_interval`). Reports go to `knowledge/analytics/prescreen/`.

### 6. Model identity and storage

A trained pre-screen is stored as a `DistilledModelRecord` (`training/model_registry.py`):

- `backend="sklearn-logreg"` and `runtime_types=["prescreen"]`;
- a `metadata["prescreen"]` binding, like the existing skill-routing binding, holding:
  - the bound evaluator epoch, scenario family and quality threshold;
  - the feature-spec hash;
  - the skip threshold and target precision;
  - the training manifest: ledger row ids and their date range.

The record is published inactive. The `auto_activate=True` path used by `autoctx train` is not used.

### 7. Loop integration (`execution/improvement_loop.py`)

**New settings.**

- `ImprovementLoop` gains two optional constructor parameters, `judge_ledger` and `prescreen`. Both default to None.
- A `prescreen_mode` setting takes `off` (the default), `shadow` or `active`.

**Shadow mode.** The pre-screen predicts on every eligible round, the judge still runs, and the ledger records both.

**Active mode.** An eligible round whose predicted-fail probability is at or above the skip threshold does not call `task.evaluate_output`. Instead:

- The round is recorded with a new flag, `RoundResult.judge_skipped=True`.
- Its score is NaN in memory and `null` when serialized. Every place that reads `RoundResult.score` must handle this; the implementation plan audits them, including `task_runner.py:66-117`.
- Revision uses the last judged feedback through `_apply_revision_feedback`, with an added note that this revision is predicted to stay below the threshold.
- The output verifier (`improvement_loop.py:461-480`) still runs. If it vetoes, its message is added to the feedback, as today.
- Skipped rounds count toward `max_rounds`, but not toward the abort after three consecutive parse failures.
- A new `ImprovementLoopEvent("judge_prescreened")` lets consumers see each skip.

### 8. Audit judging and the serving monitor (`prescreen/monitor.py`)

Once the pre-screen is active, a skipped round has no ground truth. So a random 10% of rounds the pre-screen would skip are judged anyway ("audit judging"), much like AC-1023's 20% random audits.

The monitor suspends the pre-screen, returning the loop to judging every round, if any of these happens:

- over a rolling window of at least 30 audited rounds, the Wilson lower bound on skip precision falls below 0.90;
- the evaluator epoch changes;
- over at least 12 tasks, the loop's success rate (tasks reaching the threshold) falls below the lower bound of the cohort that earned promotion.

When a suspension comes from a breach, the monitor writes a `negative_result.json` entry (`docs/negative-result-ledger.md`). When it comes from an epoch change, the entry is marked `retest_due`.

### 9. Retraining

- Retraining runs weekly, or after 150 new eligible ledger rows for the family (the Phase 2 volume), whichever comes first.
- It favors recent rows, through a recency window or weighting. The replay's recent-against-random curve decides which.
- Each retrained model is a new candidate that goes through shadow mode, then the Phase 3 gate, then activation. It never replaces the active model silently.

## Phases and gates

**Phase 0: capture and sufficiency.** Ship the ledger and its migration, with no change in behavior. The phase ends with a sufficiency report for each (evaluator epoch, scenario family) pair, covering:

- judged rounds, eligible rounds and eligible failing rounds;
- the pass rate;
- the value ceiling.

Move to Phase 1 once one family meets both conditions:

- at least 300 eligible judged rounds under a single epoch;
- a value ceiling of at least 15% of its judge calls.

The development machine is far below that today: its `runs/autocontext.sqlite3` holds 24 runs and 39 generations. So Phase 0 includes a pre-registered data-generation run over existing agent-task scenarios, following the `protocol.json` pattern. The sufficiency report sets its size, and its model spend is capped before it starts.

**Phase 1: offline replay.** Move to Phase 2 when the best model, on held-out folds:

- skips at least 20% of eligible judge calls;
- achieves a skip precision of at least 0.95, with a Wilson lower bound of at least 0.90;
- beats P1 (structural features only) on log-loss. Otherwise P1 is carried forward as the simpler model.

The learning curve decides how often to retrain, and whether P4 is worth testing. A no-go writes a negative-result ledger entry and stops the work.

**Phase 2: shadow.** Move to Phase 3 after at least 150 eligible live rounds under the current epoch, if:

- the Wilson lower bound on live skip precision is at least 0.90;
- the live skip rate falls within the Phase 1 interval.

**Phase 3: gated activation.** A paired A/B runs on a cohort of tasks disjoint from the training data. Each task runs once with the pre-screen off and once with it active. The cohort must have at least 12 tasks spread over at least 4 scenario groups (the AC-1021 minimums), made larger if Phase 1's variance calls for it.

Promotion requires the harness-optimization gate plus these hard guards:

- **Final best score:** the 95% paired-bootstrap lower bound on (active − off) is at least −0.02. This is the non-inferiority margin AC-1021 uses.
- **Success rate:** the paired-bootstrap lower bound on the difference is at least −0.05.
- **Judge calls per task:**
  - they drop by at least 15%;
  - the paired-bootstrap upper bound on (active − off) is below 0.
- **Total model tokens per task (judge plus revision):**
  - the point estimate falls;
  - the upper bound of the relative change is at most +5%. Wrong skips add revision rounds, so this guard catches savings that are eaten up by extra revisions.

The evidence artifacts are:

- **CandidateEvidence**, with `mechanism_type: judge_policy`.
- **IntegrityMetadata**, whose split ids separate the ledger rows used for training from the evaluation cohort.
- **A PromotionScore**, whose components are:
  - dense quality: the mean final best score;
  - sparse success: the success rate;
  - token cost: total tokens per task;
  - error rate: the share of aborted loops;
  - variance: the variance of the score.

**Phase 4: serving.** The pre-screen runs in active mode, with audit judging and the monitor, and is retrained as described in section 9.

## Delivery

Each phase gets its own implementation plan, written only after the previous phase's gate passes:

- **Plan 1, Phases 0–1:** the ledger and migration, the sufficiency report, the pre-registered data-generation run, the dataset, the models, calibration, and replay. None of this changes loop behavior.
- **Plan 2, Phase 2:** the `prescreen_mode` setting and shadow predictions in the loop.
- **Plan 3, Phases 3–4:** active skipping, `RoundResult.judge_skipped` and its consumers, audit judging, the monitor, the harness-optimization evidence, and retraining.

## Error handling

- **Pre-screen unavailable:** the loop judges the round and logs the reason once per loop. This covers:
  - the extra not being installed;
  - no active model for the epoch and family;
  - a load or feature error;
  - inference taking longer than 50 ms.
- **Epoch is None:** never skip. This happens when a hook rewrote the prompt or the response (`judge.py:457-477`).
- **Ledger write failure:** log it and continue. Capture must never break a run.
- **A skipped round's output is vetoed by the verifier:** the veto message joins the feedback, and the round stays unscored.

## Testing

- **Ledger:** with a fake task judge, there is one row per real judge call and none for cache replays or missing targets. The migration applies cleanly.
- **Eligibility:** each rule gets its own test:
  - round 1;
  - consecutive skips;
  - the last round;
  - a confirmation round;
  - precedence of the cache and of missing targets;
  - epoch mismatch;
  - epoch None.
- **Skipped rounds stay unscored:** they are left out of best-output selection, plateau detection, rebaseline and confirmation. They serialize as `score: null` with `judge_skipped: true`.
- **Calibration and replay:**
  - synthetic data with a known skip precision;
  - bootstrap intervals;
  - reproducible sampling for the learning curve.
- **Monitor:** it suspends on a precision breach, an epoch change, and a success-rate breach.
- **Fail closed:** each of the unavailable-pre-screen cases judges the round.

## Risks

1. **Too little volume per epoch.**
   - Any change to the judge's spec mints a new epoch and resets certification, even adding one calibration example.
   - Mitigation: training may reuse older epochs' verdicts (Decision 4), and re-certification only needs the Phase 2 volume under the new epoch.
   - If epochs change faster than 150 eligible rounds can accumulate, the pre-screen will rarely be active. Phase 0's report will show this.
2. **Stale feedback degrades revisions.** Staleness is limited by allowing only one skip in a row, and Phase 3's final-score and success-rate guards measure the effect.
3. **A skipped round might have passed.** Its output is revised instead, and it can never become the best output. The precision target limits how often this happens, and Phase 3 measures it.
4. **Label noise near the threshold.** The judge's pass/fail call near 0.9 is noisy, which is why the loop already asks a second round to confirm scores of 0.90–0.92. Confident-fail predictions sit far from that boundary by construction.
5. **Small value.** If most loops finish in one or two rounds, few rounds are eligible. Phase 0's value ceiling measures this before any modelling starts.

## Alternatives considered

- **Replace the judge with a distilled model** (`judge_provider="mlx"`). Rejected: it changes the evaluator epoch and loses the per-round reasoning. It is a different project.
- **Let the pre-screen accept confident passes.** Rejected: it would ship outputs nobody judged, which contradicts the fail-closed design.
- **Adaptive sampling:** stop drawing extra judge samples once the pre-screen and the first sample agree. Deferred: it saves nothing at the default `judge_samples=1`. It can reuse the same ledger and models if multi-sample judging becomes common.
- **Measurement and shadow only.** The user chose to include activation. The phase gates keep that choice reversible.

## Non-goals

- **TypeScript parity.** Python ships first, per the "Parity-Last Changes" section of AGENTS.md. The TypeScript loop keeps judging every round, and the PR must say so.
- **Cost claims beyond what Phase 3 measures,** in line with the "no economic claims" language in `autocontext/benchmarks/skill_reuse/`.
- **GPU training or the ambient trainer.** A small logistic model on CPU is enough. The ambient trainer (`docs/internal/ambient-trainer-design.md`) could host retraining later.

## Related

- **jev-experiments:**
  - `results/headroom-notes.md`: the learning curve and the blind judge audit;
  - `results/hybrid-notes.md`: the R6 hybrid recipe;
  - PR #11: the live shadow trial.
- **autocontext:**
  - `autocontext/docs/judge-serving-identity.md` (AC-1022);
  - `docs/internal/harness-optimization-protocol.md` (AC-877 to AC-882);
  - `autocontext/docs/policy-candidates.md` and `autocontext/docs/skill-routing.md` (AC-1019, AC-1020);
  - `autocontext/src/autocontext/execution/execution_policy_promotion.py` (AC-1021);
  - `autocontext/src/autocontext/cli_human_labels.py` (AC-1023);
  - `docs/negative-result-ledger.md`.
