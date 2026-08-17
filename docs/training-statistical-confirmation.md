# Trainer-local statistical confirmation

Autoresearch checkpoint selection uses a reproducible promotion artifact instead of keeping every numerically higher average. This gate is local to the trainer; it does not replace the candidate → shadow → active deployment lifecycle.

`TrainingPromotionProtocol` supports:

- a cheap matched-seed/fixture screen;
- adaptive confirmation batches while the confidence interval overlaps the configured minimum effect;
- early rejection or acceptance when the interval is decisive;
- an optional held-out matched check;
- an `inconclusive` result when the confirmation budget is exhausted;
- binding validity, parse, and required-dimension regression checks; and
- a one-pair deterministic mode for noiseless scenarios and tests.

Each `TrainingPromotionArtifact` contains the raw incumbent/challenger trials, evaluator epoch, verifier digest, cohort, fixtures, seeds, scores, validity and dimension observations, stopping rationale, confidence interval, and total evaluation cost. Passing the stored trials and protocol back to `evaluate_training_promotion` reproduces the decision.

`TrainingRunner` persists artifacts under `promotion/experiment_<n>.json` inside its workspace. Its default deterministic gate requires a `0.01` minimum effect and rejects validity regressions. Adaptive mode is available through `TrainingConfig(promotion_mode="adaptive", ...)` and requires a matched-trial executor supplied to `TrainingRunner`; absence of that executor is an explicit `infrastructure_error`, never an average-score fallback.

The full autoresearch trainer remains Python-only, so TypeScript parity is intentionally deferred.
