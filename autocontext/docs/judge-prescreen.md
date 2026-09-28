# Judge pre-screen: capture, sufficiency and offline replay

This is Phase 0–1 tooling for the judge pre-screen described in `docs/internal/judge-prescreen-design.md`. The
pre-screen is a small local classifier that, in later phases, may skip the LLM judge on improvement-loop rounds it is
confident will fail. Nothing here changes how the loop behaves.

## Capture

- **Turning it on.** Set `AUTOCONTEXT_JUDGE_LEDGER_ENABLED=true` to record every real judge call made by
  `autoctx run` in the `judge_ledger` table of the run database. Capture is off by default.
- **What each row holds:**
  - the task prompt, the output and the judge's score and dimension scores;
  - the evaluator epoch;
  - links to the previous judged round.
- **Privacy.** Rows hold prompt and output text, so they stay in the local database. Reports carry only counts,
  hashes and aggregates.
- **When capture fails,** the write is logged and the run continues.

## Commands

```bash
# How much eligible data exists per evaluator epoch and scenario family, and the value ceiling
uv run autoctx prescreen sufficiency

# Pre-registered data generation (costs model calls; read the protocol first)
uv run autoctx prescreen datagen benchmarks/judge_prescreen/protocol.json --out benchmarks/judge_prescreen/results/<date>

# Offline replay of the model ladder and the Phase 1 gate (needs: pip install 'autocontext[prescreen]')
uv run autoctx prescreen replay --family datagen --epoch <evaluator epoch from the sufficiency report>
```

## What counts

A round is **eligible** when every one of these holds:

- it is round 2 or later;
- the previous round in the same loop was judged, parsed and failed;
- it is not the last allowed round;
- its own verdict parsed;
- its evaluator epoch is known.

Phase 1 needs at least 300 eligible rounds under one epoch, and a value ceiling of at least 15%. The value ceiling is
eligible failing rounds divided by all judged rounds.

The replay gate passes when a model skips at least 20% of eligible calls at a skip precision of at least 0.95,
with a Wilson lower bound of at least 0.90. The simpler P1 model is preferred unless another model beats it on log-loss.
