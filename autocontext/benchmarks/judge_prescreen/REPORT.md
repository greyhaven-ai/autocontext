# Judge pre-screen data generation — September 28, 2026

**Decision: Phase 0 no-go. The replay does not run, and the pre-screen stops here for
this configuration.** With one strong model drafting, revising and judging, a
revision that fails the judge is rarely followed by another failure. The design
lets a pre-screen skip only those rounds, so a perfect pre-screen could skip at
most 3.9% of judge calls. The Phase 1 go rule asks for 15%.

## Protocol and execution

The [protocol](protocol.json) (v2) and the 24-task [corpus](tasks.jsonl) were
committed with the tooling in #1420 before any run. Both runs used main at
`4103e48fe`, which also carries #1421's fix to the loop's baseline. One provider
served drafting, revision and judging: Claude Sonnet 5 through OpenRouter
(`AUTOCONTEXT_JUDGE_PROVIDER=openrouter`). The protocol's bare `claude-sonnet-5`
resolved to `anthropic/claude-sonnet-5`, as each summary's `served_model`
records. The caps were 1,500 provider calls and 3,000,000 tokens, with no dollar
cap. Neither cap was approached.

- **Run 1** ([results](results/2026-09-28/)) stopped at loop 97 of 120. OpenRouter
  refused a call with HTTP 402 (`in_flight_budget_exhausted`) because the account
  had run out of credit, so the summary records `error:ProviderError`. The run is
  kept as it ended.
- **Run 2** ([results](results/2026-09-28-run2/)) repeated the protocol in full
  after a top-up and stopped as `corpus_exhausted`. It wrote to its own ledger, so
  the two runs are never pooled.

## Results

|                                                      | Run 1 (partial) | Run 2 (complete) | Phase 1 needs                 |
| ---------------------------------------------------- | --------------- | ---------------- | ----------------------------- |
| Loops                                                | 97              | 120              |                               |
| Provider calls / tokens                              | 295 / 572,528   | 362 / 720,869    |                               |
| Judged rounds (parse failures)                       | 144 (2)         | 178 (5)          |                               |
| Judged pass rate                                     | 0.73            | 0.75             |                               |
| Eligible rounds (tasks)                              | 37 (13)         | 41 (12)          | at least 300                  |
| Eligible rounds the judge failed (tasks)             | 6 (3)           | 7 (3)            | at least 12 tasks among skips |
| Value ceiling                                        | 4.2%            | 3.9%             | at least 15%                  |
| Evaluator epochs pooled under the one judge identity | 53              | 59               |                               |

Both runs produced the same judge identity (`fd1ba53a…`). In run 2, 76 of the 120
loops passed in round 1, 44 reached round 2, 12 reached round 3 and 2 reached round 4. None reached round 5. The account balance fell by $4.79 during run 2. Other
clients share that key, so this is an upper bound on the run's cost.

All three requirements fail, and more volume would not rescue them:

- **Volume.** Each loop contributes at most three eligible rounds. Reaching 300
  would take most loops failing four rounds in a row, and in run 2, 63% passed at
  once.
- **Value.** 34 of run 2's 41 eligible rounds passed: a revision made after a
  failed judgment usually fixes the output. The seven that failed again are the
  only rounds the design would ever let a pre-screen skip.
- **Spread.** Those failures came from three tasks in each run (five distinct
  tasks across both), far from the 12 distinct tasks the gate asks for.

This is Risk 5 of the design ("small value"), caught by the pre-registered gate
before any modelling.

## What this does and does not show

It shows that Sonnet 5 on this authored corpus leaves too few stubborn failures
for a judge pre-screen to pay off. The repetitions were real samples despite the
temperature-0 defaults: in run 2, 23 of the 24 tasks produced five distinct
round-1 outputs, and the other produced three.

It says nothing about a cheaper model drafting under a strong judge, about harder
or longer tasks, about multi-sample judging, or about organic traffic. Any of
these could raise the share of rounds that fail twice. It also confirms the
2026-09-28 amendment on real data: each run pooled 53 and 59 evaluator epochs into
one judge identity, and no ledger row lacked one.

## Next

- Capture stays opt-in. Setting `AUTOCONTEXT_JUDGE_LEDGER_ENABLED=1` for
  `autoctx run` accumulates organic rounds, and `autoctx prescreen sufficiency`
  reports their value ceiling at any time, at no model cost.
- A new protocol, for example a cheaper drafting model under the same judge, is
  worth pre-registering only if organic data shows a ceiling well above 4%.
- There is no negative-result ledger entry. That ledger's schema records search
  branches (branch, hypothesis node, generation, seeds and probes), so this
  report is the record of the no-go.

## Artifacts and reproducibility

Each results directory holds the protocol copy, `identity.json` (protocol and
corpus sha256, git commit, run prefix), `progress.json`, `summary.json` and the
sufficiency report:

- Run 1: [summary](results/2026-09-28/summary.json), [identity](results/2026-09-28/identity.json), [sufficiency](results/2026-09-28/sufficiency-20260928T175307425691Z.json)
- Run 2: [summary](results/2026-09-28-run2/summary.json), [identity](results/2026-09-28-run2/identity.json), [sufficiency](results/2026-09-28-run2/sufficiency-20260928T191220737593Z.json)

These files hold counts, hashes and identities only. The ledgers themselves, with
prompts and outputs, stay in the local run databases that produced them and are
not committed (design Decision 9).

From `autocontext/`, with `OPENROUTER_API_KEY` in the environment:

```bash
AUTOCONTEXT_JUDGE_PROVIDER=openrouter uv run autoctx prescreen datagen \
  benchmarks/judge_prescreen/protocol.json \
  --out benchmarks/judge_prescreen/results/new-run \
  --db-path runs/prescreen-datagen-new-run.sqlite3
uv run autoctx prescreen sufficiency \
  --db-path runs/prescreen-datagen-new-run.sqlite3 \
  --out benchmarks/judge_prescreen/results/new-run
```

A new run records its own identity. Model outputs differ from run to run, so the
counts will too.
