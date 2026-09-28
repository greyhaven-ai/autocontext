# Judge pre-screen: capture, sufficiency and offline replay

This is Phase 0–1 tooling for the judge pre-screen described in `docs/internal/judge-prescreen-design.md`. The
pre-screen is a small local classifier that, in later phases, may skip the LLM judge on improvement-loop rounds it is
confident will fail. Nothing here changes how the loop behaves.

## Install

Capture, sufficiency and data generation need nothing extra. The replay needs the `prescreen` extra, which adds
scikit-learn, numpy and scipy:

```bash
# In a repository checkout, from autocontext/
uv sync --extra prescreen

# From PyPI
pip install 'autocontext[prescreen]'
```

## Capture

- **Turning it on.** Set `AUTOCONTEXT_JUDGE_LEDGER_ENABLED=true` to record every real judge call made by
  `autoctx run` in the `judge_ledger` table of the run database. Capture is off by default.
- **What each row holds:**
  - the task prompt, the output and the judge's score and dimension scores;
  - the judge identity, the evaluator epoch, the judge's full serving spec and its execution provenance, which
    includes the number of judge samples;
  - links to the previous judged round.
- **Privacy.** Rows hold prompt and output text, so they stay in the local database. Reports carry only counts,
  hashes and aggregates.
- **When capture fails,** the write is logged and the run continues.

## Judge identity

Evidence for a pre-screen is certified per **(judge identity, scenario family)**. The judge identity is the sha256 of
the judge's serving spec without its task-specific fields: the compiled rubric, the calibration examples, the pinned
dimensions and the evaluation context hash. What remains is the judge's provider, model, prompt template and score
transformations. Changing any of those needs new evidence; changing a task or its rubric does not.

The evaluator epoch hashes the whole spec, so it differs per task and between round 1 and the rounds after it. It is
kept for audit. A verdict that carries no serving spec has no judge identity, and its round is never eligible.

## Commands

Each command prints one JSON document on stdout. Report paths and progress go to stderr.

```bash
# Eligible data per judge identity and scenario family, and the value ceiling
uv run autoctx prescreen sufficiency

# Pre-registered data generation (costs model calls; read the protocol first)
uv run autoctx prescreen datagen benchmarks/judge_prescreen/protocol.json --out benchmarks/judge_prescreen/results/<date>

# Offline replay of the model ladder and the Phase 1 gate (needs the prescreen extra)
uv run autoctx prescreen replay --family datagen --judge-identity <judge_identity from the sufficiency report>
```

## What counts

A round is **eligible** when every one of these holds:

- it is round 2 or later;
- the previous round in the same loop was judged, parsed and failed;
- it is not the last allowed round;
- its own verdict parsed;
- its judge identity is known.

Phase 1 needs at least 300 eligible rounds per (judge identity, scenario family), and a value ceiling of at least 15%.
The value ceiling is eligible failing rounds divided by all judged rounds of that judge identity and family.

The replay gate passes when a model skips at least 20% of eligible calls at a skip precision of at least 0.95, with
both the Wilson and the task-clustered lower bounds at least 0.90, and its skipped rounds span at least 12 distinct
tasks. The simpler P1 model is preferred unless another model beats it on log-loss.

## Limitations

- **Spend caps.** Data generation stops at its call cap on every provider. It also stops at its cap on input plus
  output tokens on every provider that reports token usage: the Anthropic and OpenAI-compatible API providers do, and
  the CLI runtime bridges do not, so there the call cap is the only bound. A dollar cap binds only where a provider
  reports cost. The direct API providers report none, so the committed protocol sets no dollar cap; a protocol that
  sets one stops as `cost_not_reported` at the first call that reports no cost.
- **Required concepts.** The structural feature `required_concept_coverage` is always 1.0 in Plan 1's data, because
  neither data generation nor `autoctx run` passes required concepts to the improvement loop.
