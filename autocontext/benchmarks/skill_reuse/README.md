# Schema-migration reuse study (AC-1029)

This study provides a frozen experiment protocol, disjoint data splits and a
four-arm runner. The original checked-in source and playbook are hand-authored
integration fixtures; their reports always say `infrastructure_only`. A separate
[bounded OpenRouter run](results/2026-09-22-openrouter-frozen/heldout/REPORT.md)
contains actual model calls on synthetic tasks. It passed the narrow quality and
coverage screens, but did **not** establish total-lifecycle economic benefit or
independent generalization; production activation remains prohibited.

## Predeclared experiment

Use the existing pure `profile-v1-to-v2` task, objective verifier, Docker bridge,
and AC-1020 router. Every arm has the same public task contract, no tools, and
the same per-request time/token/spend ceilings:

| Arm | Model | Learned context | Executable skill |
|---|---|---|---|
| baseline | reference | none | none |
| textual | reference | frozen playbook | none |
| executable | reference fallback | same frozen playbook on fallback | frozen candidate |
| cheap_textual | distinct cheaper model | same frozen playbook | none |

The model identifiers, endpoints, prices, output/input bounds, timeout, playbook,
skill, source evidence and discovery ledger are supplied explicitly before
freezing. Both representations must use the same exported training packet.
The evaluation runner does not synthesize or alter its frozen candidates.
A separate budgeted `synthesize` command authors both representations from the
same packet and retains model prompts, replies, receipts and failed attempts.
The record attests the requested inputs, not a proof of how a model internally
used them. Record model, prompt, request/response artifact references and
unsuccessful candidates in
`learning.json`. Never supply evaluation files or verifier implementation to
the authoring process. Normal public task instructions remain available to all arms.

The [machine-readable protocol](protocol.json) predeclares:

- At least 12 supported and 12 shifted held-out cases, with four distinct
  sampling groups in each cohort. The included corpus has four training,
  four development and 24 evaluation cases. Canonical inputs, IDs and family
  groups cannot overlap between splits.
- Paired identical cases, seeded shuffled case order, and Latin rotation of arm
  positions separately within each cohort. One frozen model draw per arm/case.
- Exact successful transformations on supported inputs and explicit model
  abstention on unsupported inputs. A rejected wrong proposal is counted as
  containment, **not** a correct answer. Shifted cases cover unseen versions,
  fields, strict type confusion and structurally similar unsupported objects.
- Executable route quality at least 0.95 in each cohort, paired group-bootstrap
  lower bound at least -0.02 versus both textual controls, skill coverage at
  least 0.5 on supported cases, and observed raw skill proposal precision 1.0.
  Bootstrap uses 2,000 resamples with seed 1029, resampling paired family means;
  this gives groups equal weight and does not treat sibling cases as independent.
- One skill attempt plus at most one model dispatch per request, 30 seconds,
  20,000 reserved tokens and $1 declared model cost. Final-evaluation caps:
  400 possible model dispatches, 4 million reserved/accounted tokens, $100
  declared model cost, one hour. Admission reserves a whole four-arm pair.
  Preflight, verification and model startup share the existing request deadline;
  process cleanup retains the route's separately bounded grace periods.
  The absolute study deadline is also passed into every route. Ledger writes
  cannot restart that deadline; expiry/cancellation after a write records a
  known non-dispatch and releases the attempt without invoking the route.
- Reuse horizons 1, 2, 5, 10, 25, 100 and 1,000; unsupported-contract workload
  frequencies 0%, 10%, 50% and 100%. The conditional economic screen requires
  at least 5% lower total cost than both textual controls at 100 tasks and 10%
  shifted inputs, with no automatic activation. A resource crossing is reported
  separately from an economic break-even. These are frozen-artifact fallback
  projections, not observed future executions or a retuning experiment.

Freeze before evaluation. Development may be inspected for changes, but any
change requires a new freeze. Once held-out outcomes have been inspected, use
a fresh, previously uninspected held-out corpus for a new candidate. A freeze
directory cannot silently rerun or resume a partial evaluation. A new directory
is not evidence that the same cases are fresh; independent corpus stewardship
is an operational requirement. The public synthetic fixtures establish plumbing
and bounded task transfer, not private or broad generalization.

## Run the integration fixture

From `autocontext/`, with Docker and the pinned runtime image prepared as in the
[executable-skill guide](../../docs/executable-skills.md):

```bash
uv sync --frozen --group dev
uv run --frozen python -m benchmarks.skill_reuse.demo --output /tmp/ac1029-demo
```

The default is the four development cases. Add `--split heldout` to exercise all
24 synthetic evaluation cases. The demo starts a loopback HTTP fixture, runs
real SDK workers and the real Docker skill, and writes frozen inputs, per-route
traces, a call ledger, episodes, `summary.json` and `REPORT.md`. Fixture receipt
counters and model names are synthetic. No provider credentials or paid API are
needed. Fixture mode rejects external endpoints and cannot become live evidence.
The alternative `fixtures/packet_candidate_{playbook.md,skill.py,learning.json}`
was authored from the same four exported training inputs and public contract.
It is **development-only**: its author previously inspected the public held-out
cases and its agent-authoring resource use is unknown. It cannot be used as live
evidence. `fixtures/post_candidate_corpus.json` was assembled *after* this
candidate without evaluating its outcomes; it preserves the training/development
packet and substitutes 24 new held-out cases across eight disjoint groups. The
same agent prepared both sides, so this is a bounded synthetic pilot design, not
an independently stewarded or representative promotion test. Do not modify the
candidate after inspecting results on this corpus; commission an independent
protected corpus and document authoring costs before any broader efficacy claim.

```bash
uv run --frozen pytest tests/test_skill_reuse_study.py benchmarks/policy_reuse/test_run.py
AUTOCONTEXT_RUN_DOCKER_TESTS=1 uv run --frozen pytest \
  tests/test_skill_reuse_study.py::test_real_docker_four_arm_study \
  tests/test_skill_reuse_study.py::test_real_docker_packet_candidate_development
```

## Prepare a bounded live study

First export only the permitted learning data:

```bash
uv run --frozen python -m benchmarks.skill_reuse.study learning-packet \
  --corpus benchmarks/skill_reuse/fixtures/corpus.json \
  --output /tmp/ac1029-training.json
```

Prepare a learned playbook and `choose_action(state)` candidate from that packet,
using equivalent documented discovery budgets. Retain all successful and failed
discovery/development/verification calls in the ledger. All four arms must have
an explicit setup entry; unmeasured human or resource cost is `null`, not zero.
Shared discovery work is charged fully to each consuming arm when comparing
independent deployments, never divided by four. Any completed post-freeze
development run is retained in final accounting.
Deployment projections pay this arm's final evaluation cost before future tasks;
shared qualification overhead must also be entered in the discovery ledger.
Observed resource crossings describe the measured evaluation sequence, not
post-qualification deployment. The learner's evidence references
and packet digest are bound to the freeze. The example
[learning record](fixtures/learning.json) shows the schema, but its fixture origin
cannot authorize a live run.

Create `models.json` with `evidence_kind: "live"` and `reference` / `cheaper`
[ModelTarget configurations](../../docs/skill-routing.md). Use distinct pinned
model identifiers, equal input/output/time limits and lower declared maximum
cost for the cheaper control. API credentials are read only through the named
environment variables; credential values never belong in these files. Remote
model aliases cannot prove immutable provider weights; document that limitation.

The reviewed OpenRouter pair in `openrouter-models.json` uses
`anthropic/claude-sonnet-4.6` (reference, [$3/$15 per million input/output
tokens](https://openrouter.ai/anthropic/claude-sonnet-4.6)) and
`anthropic/claude-haiku-4.5` (cheaper, [$1/$5](https://openrouter.ai/anthropic/claude-haiku-4.5)).
The config pins OpenRouter's Anthropic provider, disables backup-provider
fallback and caps its input/output unit prices; a mutable upstream route remains
a limitation. No credential values are stored. `openrouter-protocol.json` bounds
synthesis to three invocations and $0.15, including one failed preparation call;
development requires at most 16 model calls ($0.80 at the per-request ceiling)
and the 24-case final comparison at most 96 calls and $4 of reserved model spend.
The combined caps are below $5 of model credits, excluding local resources and
credit-purchase fees. Paired calls have equal 8,192-input/1,024-output/20-second
model limits under a shared 30-second request deadline. All costs remain subject
to provider receipts; actual local/Docker rates are still unmeasured.

The `synthesize` command makes two separately ledgered single-dispatch model
calls. It passes the same exported training packet and public task contract to
both authors, not development/held-out inputs or verifier-only sources. The
failed initial call is retained with `--prior-attempt`; every output directory
must be new. From `autocontext/`, the bounded preparation used:

```bash
doppler run --project autocontext-development --config dev \
  --only-secrets OPENROUTER_API_KEY --no-fallback --no-liveness-ping -- \
  uv run --frozen python -m benchmarks.skill_reuse.study synthesize \
  --protocol benchmarks/skill_reuse/openrouter-protocol.json \
  --corpus benchmarks/skill_reuse/fixtures/post_candidate_corpus.json \
  --models benchmarks/skill_reuse/openrouter-models.json \
  --prior-attempt benchmarks/skill_reuse/results/2026-09-22-openrouter-learning \
  --output benchmarks/skill_reuse/results/2026-09-22-openrouter-learning-v2
```

Do not repeat this command as a free replay: it spends credits. The persisted
`learning.json`, call ledger and prompt/response files document the observed
synthesis attempt, while local authoring overhead remains unknown.

```bash
uv run --frozen python -m benchmarks.skill_reuse.study freeze \
  --protocol benchmarks/skill_reuse/protocol.json \
  --corpus /path/to/study-corpus.json --models /path/to/models.json \
  --learning /path/to/learning.json --skill /path/to/skill.py \
  --playbook /path/to/playbook.md --output /tmp/ac1029-frozen
uv run --frozen python -m benchmarks.skill_reuse.study run \
  --frozen /tmp/ac1029-frozen --split development
uv run --frozen python -m benchmarks.skill_reuse.study run \
  --frozen /tmp/ac1029-frozen --split heldout
```

The final commands perform model calls against the configured endpoints. Freeze
hashes code, dependency lockfile, interpreter/platform, pinned Docker image,
router/evaluator, all input artifacts and candidate manifest. They are integrity
checks, not signed attestations. Evaluation uses an isolated inactive bundle
through the same router as serving and never promotes or changes its active pointer.
Cancellation, unverified cleanup/accounting and interruptions produce incomplete
evidence, with reservations retained in the ledger. No replacement cases or
selective retries are added after results are observed.
Unreconciled attempts make the affected cost totals, lifecycle resources per
success, projections and resource crossings unknown. Completed-case counts and
latency remain descriptive; full reservations are retained separately. A partial
run cannot report a pending call as zero cost or qualify as complete evidence.

## Reporting and remaining work

The report includes per-cohort correctness, rejected harmful proposals,
conditional paired uncertainty, skill precision/coverage, fallback, calls,
tokens, p50/p95 latency, setup-inclusive resources per success, first-use cost,
observed resource crossings and projected horizons. It reuses AC-1018's
nearest-rank quantiles and paired bootstrap through a shared statistics module;
the original pilot's archived measurements are unchanged.

The Docker runner records candidate-child CPU seconds and peak RSS using a
separate parent process (`wait4`); they are `null` if the receipt is missing or
invalid. These figures exclude Docker daemon/CLI, descendants and remote model
resources, and are not whole-container billing. The route still reports model
usage and wall time. `local_route_cost_per_second_usd` and
`isolated_skill_cost_per_second_usd` in the frozen protocol are optional operator
billing assumptions: the former prices end-to-end route time for every arm, the
latter prices elapsed Docker execution (including its launch and cleanup).
The generic Anthropic/OpenAI-compatible adapters return usage without invoiced
per-call dollars. The OpenRouter-scoped route additionally validates its billed
credit cost and rejects BYOK, malformed receipts and unverified provider changes.
A credit-cost receipt is a per-call spend observation, not a final invoice or
whole-lifecycle dollar estimate. Declared model rates remain conditional price
assumptions. Discovery, failed attempts, development,
evaluation and shared qualification costs require explicit ledger entries; any
unknown dollar field remains `null` throughout amortization. Record capacity,
idle-time and recurring audit/retuning allocations in those rates/entries before
claiming an economic result. A conditional economic pass is not a production go.

The original public held-out fixtures had been inspected before candidate
authoring. The new synthetic corpus was assembled before the training-only
model calls and its outcomes were not inspected until the frozen paired run.
It was not independently stewarded. The live four-arm pilot completed 24 paired
cases: executable and matched textual arms each served 24/24, baseline 22/24,
cheap textual 3/24. Provider-reported model credits across synthesis, development
and held-out calls totaled $0.147607; no entire-workload cost is known. See the
[bounded report](results/2026-09-22-openrouter-frozen/heldout/REPORT.md).
An independent protected corpus, full local/Docker and recurring lifecycle cost
allocation, and independent candidate replication remain for stronger claims.
AC-1021 separately owns promotion and rollback. Neither the scripts nor this
result enables production or closes that issue.

Intervals describe case/group variation conditional on one frozen candidate and
model draws, not repeated synthesis reliability. Small-sample p95 and binomial
intervals are descriptive. Learned judges, caching, work skipping and tool changes
are excluded so this study isolates representation and the cheaper-model control.
Python first: TypeScript parity is deferred because the reused candidate and
Docker routing boundary is Python-only.
