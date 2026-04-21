# Escalation Sweep Harness

Release-validation helper: run every "Scenarios"-state Linear issue through
`autoctx solve` and classify failures into known buckets.

## Prerequisites

- `jq` and Python 3.11+ on PATH.
- `autoctx` CLI installed (either a published release `pip install autocontext==0.4.4`
  or run the checked-out source via `cd autocontext && uv run autoctx ...`).
- An agent provider. By default the harness uses `AUTOCONTEXT_AGENT_PROVIDER=claude-cli`,
  which invokes the locally-authenticated `claude` binary (Anthropic subscription) — no
  API key needed. Override with `AUTOCONTEXT_AGENT_PROVIDER=anthropic` (+ `ANTHROPIC_API_KEY`),
  `agent_sdk`, `pi`, etc. if you want to exercise a different path.
- Linear API key either in `$LINEAR_API_KEY` or at
  `~/.config/linear/credentials.toml` under a `greyhaven = "<key>"` entry.

## Usage

```bash
# 1. Fetch the current manifest of scenarios in the "Scenarios" workflow state.
python scripts/escalation-sweep/fetch_manifest.py .sweep/0.4.4/manifest.json

# 2. Run the sweep. One solve per scenario, 2 generations each by default.
bash scripts/escalation-sweep/run_sweep.sh \
    .sweep/0.4.4/manifest.json \
    .sweep/0.4.4/results \
    --gens 2 --timeout 600

# 3. Classify + tally.
python scripts/escalation-sweep/summarize.py .sweep/0.4.4/results
```

Expect ~5-10 min per scenario. Runs serially by design.

Each scenario runs inside its own isolated workspace under
`.sweep/<release>/workspaces/<identifier>/`, with dedicated database, runs,
knowledge, and skills roots. The summarized solve JSON stays in
`.sweep/<release>/results/`.

## Failure buckets

The summarizer groups non-zero exits into:

| Bucket                         | Meaning                                                  |
| ------------------------------ | -------------------------------------------------------- |
| `classifier_low_confidence`    | `LowConfidenceError` — keyword miss + LLM fallback also failed |
| `designer_intent_drift`        | `validate_intent` rejected the spec (AC-242 / AC-574)    |
| `designer_parse_failure`       | Spec/source/execution validation errored out             |
| `claude_cli_timeout`           | Subprocess or provider timed out                         |
| `scenario_execution_failed`    | Scenario built but generations errored                   |
| `unknown`                      | Didn't match any pattern — eyeball the raw output        |

Successes are split into:

| Bucket               | Meaning                                            |
| -------------------- | -------------------------------------------------- |
| `success`            | Completed via the keyword classifier path           |
| `llm_fallback_fired` | Succeeded, AC-580 LLM fallback classified the family |

Artifacts persist in:

- `.sweep/<release>/results/` for per-scenario solve output, metadata, and summary
- `.sweep/<release>/workspaces/<identifier>/` for the isolated run workspace used by that scenario
