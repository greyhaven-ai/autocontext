# Schema-migration reuse study (AC-1029)

This first slice provides a frozen experiment protocol, disjoint data splits,
four-arm runner and reports. It does **not** contain a live model efficacy run
or demonstrate economic benefit. The checked-in source and playbook are
hand-authored integration fixtures. Their reports always say `infrastructure_only`.

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
The record attests externally prepared artifacts; the harness does not itself
synthesize them or prove how an external author used the packet. Record model,
prompt, request/response artifact references and unsuccessful candidates in
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
- Reuse horizons 1, 2, 5, 10, 25, 100 and 1,000; unsupported-contract workload
  frequencies 0%, 10%, 50% and 100%. A resource crossing is reported separately
  from an economic break-even. These are frozen-artifact fallback projections,
  not observed future executions or a retuning experiment.

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
uv run python -m benchmarks.skill_reuse.demo --output /tmp/ac1029-demo
```

The default is the four development cases. Add `--split heldout` to exercise all
24 synthetic evaluation cases. The demo starts a loopback HTTP fixture, runs
real SDK workers and the real Docker skill, and writes frozen inputs, per-route
traces, a call ledger, episodes, `summary.json` and `REPORT.md`. Fixture receipt
counters and model names are synthetic. No provider credentials or paid API are
needed. Fixture mode rejects external endpoints and cannot become live evidence.

```bash
uv run pytest tests/test_skill_reuse_study.py benchmarks/policy_reuse/test_run.py
AUTOCONTEXT_RUN_DOCKER_TESTS=1 uv run pytest \
  tests/test_skill_reuse_study.py::test_real_docker_four_arm_study
```

## Prepare a bounded live study

First export only the permitted learning data:

```bash
uv run python -m benchmarks.skill_reuse.study learning-packet \
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

```bash
uv run python -m benchmarks.skill_reuse.study freeze \
  --protocol benchmarks/skill_reuse/protocol.json \
  --corpus /path/to/study-corpus.json --models /path/to/models.json \
  --learning /path/to/learning.json --skill /path/to/skill.py \
  --playbook /path/to/playbook.md --output /tmp/ac1029-frozen
uv run python -m benchmarks.skill_reuse.study run \
  --frozen /tmp/ac1029-frozen --split development
uv run python -m benchmarks.skill_reuse.study run \
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

## Reporting and remaining work

The report includes per-cohort correctness, rejected harmful proposals,
conditional paired uncertainty, skill precision/coverage, fallback, calls,
tokens, p50/p95 latency, setup-inclusive resources per success, first-use cost,
observed resource crossings and projected horizons. It reuses AC-1018's
nearest-rank quantiles and paired bootstrap through a shared statistics module;
the original pilot's archived measurements are unchanged.

The current route measures declared model cost and wall time. It does not measure
Docker CPU/memory, resource billing, remote invoices, or every lifecycle dollar.
Those fields remain `null`. **A quality pass therefore remains economically
inconclusive** in this first slice. Closing AC-1029 requires externally prepared
matched learning artifacts, a bounded live run, measured lifecycle resources and
a reviewed go/no-go report. AC-1021 separately owns production promotion. These
scripts neither enable production nor close that issue.

Intervals describe case/group variation conditional on one frozen candidate and
model draws, not repeated synthesis reliability. Small-sample p95 and binomial
intervals are descriptive. Learned judges, caching, work skipping and tool changes
are excluded so this study isolates representation and the cheaper-model control.
Python first: TypeScript parity is deferred because the reused candidate and
Docker routing boundary is Python-only.
