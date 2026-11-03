# Qwen editing-session fixtures — milestone 6

This CPU-only example creates context (C), synthetic weight-descriptor (W), and
decoding (D) candidates through bounded editing sessions, seals them, and passes
their exports into the [milestone-5 comparison harness](../qwen_comparison/README.md).
The resulting A/C/W/CW/D report tests the workflow with literal toy responses.
It does not run Qwen, optimize weights, or establish a benchmark improvement.
GPU execution remains deferred.

From the repository's `autocontext/` directory:

```bash
uv sync --locked --group dev
uv run python ../examples/weight_evolution/qwen_sessions/run.py validate
uv run python ../examples/weight_evolution/qwen_sessions/run.py run-fixtures \
  --output /tmp/qwen-editing-sessions
# Same path resumes by verifying every saved claim, result, seal, and report.
uv run python ../examples/weight_evolution/qwen_sessions/run.py run-fixtures \
  --output /tmp/qwen-editing-sessions
```

Use a new output directory for the first run. The command has no live execution
switch and does not download models/data, launch Docker, or provision hardware.
`validate` verifies fixture hashes, split separation, scorer/adapter pins,
candidate lineage, practice access, and prospective search reservations without
creating a session or calling an adapter. Runtime request/result sizes are also
checked when executing each operation.

## What runs

The checked-in fixture commits three practice questions, three confirmation
questions, and six final-audit questions. All are synthetic. IDs and normalized
prompts are disjoint within and across roles. Data bytes have size/SHA-256 pins;
the session spec binds the complete data digest and scorer/adapter identities.
This does not select or authenticate real benchmark training data.

Each C/W/D program executes the same four steps:

1. Inspect the three practice prompts.
2. Edit exactly its allowed surface, creating a parent-linked candidate revision.
3. Record a scripted infrastructure failure during a practice probe.
4. Complete a separate practice probe and export the candidate.

The failed probe consumes its full reservation and produces no score. A completed
probe may score zero: W includes an empty answer and D includes a wrong answer.
Export requires a changed candidate and a completed probe of that exact revision;
it is a seal, not a quality-promotion decision. Confirmation is held out and unused.

Adapters receive only the current candidate, typed operation, selected practice
prompts, and earlier practice feedback. Gold answers and both held-out roles stay
with the controller. These are logical API boundaries for trusted in-process
fixture adapters, not an operating-system sandbox for untrusted programs.

## Budgets and persistence

Each session reserves at most four calls, 65,536 editor-input bytes, 16,384
editor-output bytes, 8,192 probe-input bytes, 8,192 probe-output bytes, and four
**simulated** seconds. Input/output byte accounting uses canonical UTF-8 JSON;
probe bytes are separately counted within the broader editor exchange. This is
explicit fixture accounting, not model tokens, measured hardware time, or a hard
wall-clock deadline. The M5 search-unit cost is the conservative sum of these four
byte counters, including failed operations: **98,304 units per mechanism**.

The controller reserves before dispatch and verifies the complete hash-linked
history on restart. Known terminal failures remain charged. An unresolved claim
blocks redispatch, new operations, and export. A fully published result can be
reconciled into its missing completion receipt without calling the adapter again.
Changing the frozen spec, source closure, result, lineage, counters, or export
causes replay to fail. Keep ambiguous/incomplete evidence for reconciliation;
starting another directory is a separate experiment, not continuation of its budget.

Exports derive practice-source IDs and search cost from the verified journal.
Sealing prohibits new operations; repeating an identical completed operation or
export only verifies and returns its existing result. No caller-supplied success
claim or guessed search cost is accepted as export provenance.

## Comparison handoff

All three sessions seal before the workflow evaluates the final-audit fixture.
The base artifacts, protocol, and prerecorded responses come from the existing
M5 bundle. Its complete plan and evidence identities are pinned by this workflow.
The new C/W/D artifact provenance changes because it now comes from actual fixture
session records. Their semantic contents deliberately match M5's toy candidates.
Every evaluation-cell digest must still match exactly before any source response
is reused. A different candidate requires new evidence; this harness will not
relabel the old responses as its outputs.

CW composes the sealed C and W artifacts without another search session. The
comparison retains the existing matched questions/seeds, scoring, contrasts,
interaction report, and fixture-only status. No audit result is fed back into an
editing session. The output contains:

- `workflow.json`: frozen workflow and coordinator identity.
- `sessions/{C,W,D}/`: manifests, claims, results, receipts, failures, and sealed exports.
- `comparison-plan.json`: the exact M5 plan built from those exports.
- `comparison/`: independently graded JSON/Markdown report and completion hashes.
- `complete.json`: final session/export/report linkage, published last.

Rerunning an intact output regrades/verifies the saved evidence without new adapter
calls or budget resets. Missing or altered report files fail rather than being
silently repaired. The fixture builder writes a fresh bundle only:

```bash
uv run python ../examples/weight_evolution/qwen_sessions/build_fixture.py \
  --output /tmp/qwen-session-fixtures-rebuilt
```

## Remaining work

Real C/W/D candidate generation, tensor editing, inference-backed probes, hard
process/resource enforcement, real benchmark practice/confirmation selection and
contamination review, and repeated-budget optimization comparisons are still
unimplemented. The [GPU acceptance/reproduction gaps](../qwen_reproduction/MILESTONE4_VALIDATION.md)
remain open. TypeScript execution parity is deferred. No promotion, official score,
GPU acceptance, or reproduction success is claimed by these fixtures.

See [milestone-6 validation](MILESTONE6_VALIDATION.md) for local checks and limits.
