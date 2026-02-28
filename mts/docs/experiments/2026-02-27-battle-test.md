# Battle Test: Phase A E2E Validation

**Date:** 2026-02-27
**Operator:** Cirdan
**Environment:** Sandbox (Docker), Anthropic API (claude-sonnet-4-20250514)
**Commit:** `07a679b` (main, post-merge of PRs #14-19)
**Test Scripts:** `smoke_test.py`, `smoke_test_loop.py`

## Objective

Validate the full MTS Phase A stack with real API calls after merging all 5 feature PRs (#14-19). Unit tests passed (1108), but no end-to-end testing with actual LLM providers had been done.

## Test 1: Full Stack Smoke Test (`smoke_test.py`)

**What:** Provider → Judge → Runtime → TaskRunner → Notifications, each stage with real Anthropic API calls.

| Stage | Result | Notes |
|-------|--------|-------|
| AnthropicProvider.complete() | ✅ PASS | Response received, correct model reported |
| LLMJudge.evaluate() | ✅ PASS | Score: 0.30, dimensions: {clarity: 0.8, completeness: 0.6, factual_accuracy: 0.2} |
| DirectAPIRuntime generate+revise | ✅ PASS | Both generated and revised text |
| TaskRunner.run_once() + CallbackNotifier | ✅ PASS | Score: 0.90, threshold_met event fired |

**Bugs found before passing:**
1. `test_get_provider_ollama` — used `monkeypatch.setenv("MTS_JUDGE_PROVIDER", "ollama")` but `AppSettings` doesn't auto-read env vars (Pydantic). Fixed: pass `judge_provider="ollama"` directly.
2. Smoke test had wrong attribute names: `dimensions` → `dimension_scores`, `count_pending_tasks()` → `pending_task_count()`. Documentation gaps, not code bugs.

**Interesting finding:** The judge scored an RLM description at 0.30 because it correctly flagged "Alex Zhang, Oct 2025" as unverifiable. factual_accuracy: 0.2. The judge is doing its job.

## Test 2: ImprovementLoop E2E (`smoke_test_loop.py`)

**What:** Full generate→judge→revise→judge cycle with a real LinkedIn post task. Concrete `AgentTaskInterface` implementation using Anthropic for both generation and judging.

### Run 1 (threshold 0.85)
- **Result:** Hit threshold in round 1 (score 0.85). No revision needed.
- **Takeaway:** Even a "deliberately mediocre" system prompt produces decent Sonnet output. Need lower thresholds or harder tasks to exercise revision.

### Run 2 (threshold 0.95)
- **Result:** 🔴 Round 1 scored 0.00 — **judge parse failure**
- **Root cause:** Sonnet omitted `<!-- JUDGE_RESULT_START/END -->` markers. The judge prompt only said "use markers" without showing the expected JSON format.
- **Cascade failure:** score 0.00 + empty reasoning → `revise_output()` got no useful feedback → returned identical output → loop detected `revised == current_output` → broke after 1 round
- **Fix applied:** Added explicit JSON example in judge prompt

### Run 3 (threshold 0.95, with prompt fix)
- **Result:** 3 rounds completed. Round 1: 0.88, Round 2: 0.00 (parse failure again), Round 3: 0.88
- **Takeaway:** Prompt fix reduced but didn't eliminate parse failures. ~20-30% failure rate with Sonnet.
- **Fix applied:** Added retry (up to 2 attempts) on parse failure before returning 0.00

## Bugs Discovered

| # | Severity | Description | Status | Issue |
|---|----------|-------------|--------|-------|
| 1 | 🔴 High | Judge omits RESULT markers ~20-30% of the time | Mitigated (retry + prompt fix) | MTS-12 |
| 2 | 🟡 Medium | Parse failure score (0.00) poisons ImprovementLoop | Mitigated (retry) | MTS-13 |
| 3 | 🟢 Low | `test_get_provider_ollama` used env vars wrong | Fixed | — |
| 4 | 🟢 Low | API attribute name mismatches in documentation | Noted | — |

## What's Validated ✅

- Provider → real API calls work
- Judge → structured scoring with dimension breakdown
- Runtime → generate + revise produce different outputs
- TaskRunner → queue → dequeue → generate → judge → store result
- ImprovementLoop → multi-round with actual LLM revisions
- Notifications → CallbackNotifier fires on threshold_met
- Marker-based parsing → works when model complies (~70-80%)

## What Needs More Testing

1. **Priority queue ordering** — enqueue at different priorities, verify order (MTS-14)
2. **Provider error mid-loop** — timeout, rate limit, network failure (MTS-15)
3. **Concurrent queue access** — two runners, same DB (MTS-14)
4. **Human feedback calibration** — feed examples, verify score adjustment
5. **Multi-sample judge** — run judge 3x, average, see if it stabilizes parse failures

## Key Metrics

- **API calls made:** ~15 (across all test runs)
- **Estimated cost:** ~$0.15 (Sonnet, short prompts)
- **Parse failure rate:** 2/6 judge calls = 33% (before retry fix)
- **Parse failure rate with retry:** Not yet measured (need larger sample)
- **Time per improvement round:** ~5-8 seconds (single judge call + revision)

## Recommendations

1. **MTS-12 (fallback parser) is the #1 priority** — reduces parse failure from ~30% to near 0%
2. **MTS-13 (loop resilience)** next — handles remaining edge cases
3. MTS-14 and MTS-15 are important but less urgent — the happy path works
4. Consider switching to `claude -p --json-schema` for structured output when using ClaudeCLIRuntime

## Commits

- `87efabc` — fix: provider test env var bug + add e2e smoke test
- `07a679b` — fix: judge parse reliability + retry on marker failure (includes smoke_test_loop.py)
