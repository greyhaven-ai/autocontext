# AC-885 Slice C: evaluator-epoch lifecycle (candidate to active promotion)

Date: 2026-07-09
Status: approved in brainstorming; pending written review
Linear: AC-885 (Add evaluator epochs and score lineage for evolving rubrics/judges)
Scope: Slice C of four. Slice 1 (identity + comparability guard) and Slice B (persisted + derived
lineage) are merged (#1204, #1208). Slice D (surfacing + lazy re-score) remains.

## Purpose

Slices 1 and B made the evaluator epoch a content-addressed identity carried by scores. Slice C
adds the LIFECYCLE the AC-885 issue asks for ("evaluator changes should have explicit promotion and
invalidation rules"): a rubric or judge change mints a CANDIDATE epoch whose scores are quarantined
from the scenario's trusted progression until the epoch is PROMOTED (calibrated and, per the
autonomy dial, human-reviewed) to ACTIVE. This generalizes the ambient trainer's existing
model-promotion pattern (`promote.py`, which today refuses to compare scores across a mismatched
scalar `anchor_model`) to the evaluator epoch itself.

Today there is no "active vs candidate epoch" state anywhere: the epoch is purely a derived
score-lineage value. Slice C introduces that state, the mechanical trigger that mints candidates,
the quarantine enforcement, and the promotion workflow.

## Decisions of record

1. **Enforce via quarantine, not a hard block.** Scores produced under an epoch that is not the
   scenario's ACTIVE epoch carry a `quarantined` marker and are excluded from cross-epoch/scenario
   trust decisions (ambient model promotion, the scenario's canonical progression, reports). The
   improve loop still iterates within a candidate epoch (Slice 1 re-baseline) so the calibration
   data needed to promote that epoch is produced. Quarantine that hard-blocked the loop would be a
   chicken-and-egg (you need candidate-epoch scores to calibrate the candidate for promotion).
2. **Mechanical trigger; first epoch auto-activates.** The first time a score is produced under an
   epoch that is not the scenario's active epoch, that epoch is auto-registered as a CANDIDATE. A
   scenario's first-ever epoch (no incumbent) auto-activates, mirroring `promote.py`'s
   "incumbent is None then promote". No dependency on the (unbuilt) rubric-patch apply path.
3. **Keyed per scenario; registry mirrors ModelRegistry.** One ACTIVE epoch per scenario. A new
   `EvaluatorEpochRegistry` stores one JSON file per `(scenario, epoch_id)` record, mirroring
   `ModelRegistry`'s register/activate/list_all shape and its demote-not-delete rollback pattern.
4. **Promotion via calibration deltas plus the autonomy dial; human decision recorded.**
   `run_judge_calibration` yields alignment/bias/variance deltas (already epoch-tagged since Slice
   B). Promotion criteria: calibration passes `AlignmentTolerance` and bias-probe drift is within
   tolerance. The autonomy dial governs autonomy: `full` auto-promotes on pass; `propose`/`train`
   require a persisted human decision (`reviewed_by`, `reviewed_at`, `outcome`).
5. **Generalize `promote.py`'s cross-anchor refusal to epoch comparability.** Replace the scalar
   `anchor_model` equality in `_beats_incumbent` with `are_comparable(candidate_epoch,
incumbent_epoch)` (the Slice 1 primitive), and have the ambient promote flow consult the
   evaluator-epoch registry so a model scored under a non-active evaluator epoch is refused
   (`promote_epoch_not_active`).
6. **Python-only except the quarantine marker.** The registry, trigger, promotion workflow, and
   `promote.py` generalization are Python-only (the ambient/promote path has no TS mirror,
   consistent with Slice 1's guard). The `quarantined` marker on persisted generation scores is the
   only schema-parity touch (a `generations` column, like Slice B's).
7. **Built as ordered sub-slices, each its own spec/plan/PR.** C1 (registry + trigger + marker),
   then C2 (promotion workflow + metadata + generalize promote.py), then C3 (enforcement in
   consumers + CLI to list/approve).

## Architecture

### C1: registry, record, mechanical trigger, quarantine marker

- `EvaluatorEpochRecord` (`execution/evaluator_epoch_registry.py`, new): `scenario`, `epoch_id`,
  `rubric_hash`, `judge_provider`, `judge_model`, `activation_state:
Literal["candidate","active","disabled"]`, `created_at`, `promotion: dict | None`.
- `EvaluatorEpochRegistry`: file-per-`(scenario, epoch_id)` JSON store under a registry root.
  `observe(scenario, epoch) -> EvaluatorEpochRecord` implements the mechanical trigger: no active
  for scenario -> register active (bootstrap); epoch == active -> return it; new epoch -> register
  candidate. `active_for(scenario) -> EvaluatorEpochRecord | None`; `list_for_scenario`; `register`;
  `activate(scenario, epoch_id)` (demote-not-delete). Pure, deterministic, no LLM.
- `observe` is called at the scenario-aware layer (improve loop / task_runner / ambient evaluate)
  where both the scenario and the score's epoch are known. A score whose epoch is not the active
  one is stamped `quarantined = True`.
- Persistence: a nullable `quarantined` marker rides the Slice B `generations` lineage (a
  `generations.quarantined` boolean column, Python migration + TS parity column, always null on the
  TS side per the Slice B asymmetry). In-memory results (`ImprovementResult`) carry a `quarantined`
  flag too.

### C2: promotion workflow, metadata, promote.py generalization

- A `promote_evaluator_epoch(registry, scenario, candidate_epoch, calibration_report, charter)`
  operation: checks the calibration deltas against tolerance, consults the autonomy dial via the
  existing `decide()` (extended `Action` to include `"promote_epoch"`), and either activates the
  candidate (autonomy `full` + calibration passes) or records a pending human-review requirement.
  On activation: candidate -> active, prior active -> disabled, quarantine cleared for that epoch's
  scores, `promotion` metadata stamped.
- `promotion` metadata: `{ source_patch | None, calibration_anchors: list[str], alignment_delta,
bias_delta, variance_delta, requires_review: bool, decision: { reviewed_by, reviewed_at, outcome:
"approved"|"rejected"|None }, promoted_at, previous_active }`.
- `promote.py`: `_beats_incumbent` uses `are_comparable(best_epoch, incumbent_epoch)` instead of
  scalar `anchor_model` equality; a new refusal `promote_epoch_not_active` when a candidate model's
  eval epoch is not the scenario's active evaluator epoch.

### C3: enforcement in consumers, CLI

- Ambient promote refuses non-active-epoch eval scores; the scenario canonical-progression /
  reporting paths exclude `quarantined` scores.
- A CLI surface (`autoctx epoch list|approve|reject <scenario>`) to view candidates and record a
  human decision (writing the `promotion.decision` block).

## Data flow

```
score produced (scenario known, epoch from Slice 1)
   -> registry.observe(scenario, epoch)
        first-ever epoch     -> register ACTIVE (bootstrap)
        epoch == active      -> noop
        new epoch            -> register CANDIDATE, stamp score quarantined=True
   -> quarantined scores excluded from cross-epoch trust (ambient promote, canonical progression)
   -> run_judge_calibration(candidate) -> alignment/bias/variance deltas (epoch-tagged, Slice B)
   -> promote_evaluator_epoch: tolerance + autonomy dial
        full + passes        -> activate candidate (prior active -> disabled), clear quarantine
        propose/train        -> record requires_review; human approve/reject via CLI -> decision
```

## Error handling and edge cases

- **Bootstrap (no active epoch):** the first observed epoch auto-activates; its scores are not
  quarantined.
- **Reappearing epoch:** an epoch that was previously active then superseded is still a known
  record; if it is currently disabled and reappears, it is treated as a candidate again (its state
  is looked up by `(scenario, epoch_id)`), not re-bootstrapped.
- **Tournament / no-rubric scenarios:** produce no judge epoch (null); `observe` is a noop for a
  null epoch, and such scores are never quarantined.
- **Legacy scores (pre-Slice-C):** carry `quarantined = None`/false and are treated as trusted
  (the active epoch is bootstrapped from the first observed epoch after upgrade).
- **Calibration unavailable (< 2 anchors):** `run_judge_calibration` returns None; the candidate
  cannot auto-promote and stays pending human review.

## Testing

- C1: `observe` state machine (bootstrap-active, noop-on-active, mint-candidate, reappearing-epoch);
  registry register/activate/demote-not-delete; the `quarantined` marker persists on generation
  rows (Python) with the TS parity column present-but-null; schema-parity stays green.
- C2: `promote_evaluator_epoch` tolerance + autonomy branches (full-auto vs requires-review);
  metadata shape; `promote.py` `_beats_incumbent` uses `are_comparable` (a candidate scored under a
  non-comparable epoch is refused with `promote_epoch_not_active`); a regression test that the prior
  anchor-mismatch behavior is preserved for same-epoch comparisons.
- C3: ambient promote refuses a non-active-epoch model; canonical progression excludes quarantined
  scores; CLI approve/reject writes the decision block. The required AC-885 regression: a record
  scored under a candidate (non-active) epoch is excluded/flagged until the epoch is promoted, then
  trusted.
- No new gate/guard/validator-named symbols; module-size, ruff/mypy/lint, Python and TS suites,
  lockfiles unchanged.

## Documentation

Extend `docs/evaluator-epochs.md` with a "lifecycle" section: candidate vs active state, the
mechanical trigger and bootstrap, quarantine semantics (excluded from trust decisions, not a hard
block), the promotion workflow (calibration deltas + autonomy dial + human decision), and the
`promote.py` generalization. CHANGELOG per sub-slice.

## Deferred / out of scope

- The rubric-patch APPLY path (the `source_patch` promotion metadata is nullable until that path
  exists; `propose_rubric_patches` is experimental and unwired today).
- Drift-signal-triggered candidate minting (the mechanical per-score trigger is authoritative;
  drift is a lagging aggregate).
- Reconciling the ambient per-charter-anchor path and the main per-LLMJudge path beyond the shared
  per-scenario registry key.
- Slice D (CLI/API/dashboard stale-epoch surfacing and lazy re-score) beyond the C3 approve/reject
  CLI.

## Acceptance criteria advanced by this slice

- AC-885 #2 (changing an evaluator cannot silently overwrite the active baseline): promoted to an
  explicit candidate/active lifecycle with quarantine, beyond Slice 1's re-baseline.
- AC-885 promotion-metadata and invalidation-rules requirements (source patch, calibration anchors,
  alignment/bias/variance deltas, human-review requirement, decision; mark/filter stale scores):
  delivered by C2 and C3.
