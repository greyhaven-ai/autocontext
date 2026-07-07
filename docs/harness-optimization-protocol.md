# Harness-optimization protocol

autocontext optimizes the harness that runs a task, not just the strategy inside
it. A candidate is one proposed change to a mechanism (a prompt playbook, a
deterministic code path, a tool wrapper, a context policy, a judge policy, or a
mix) that targets a specific surface of the harness. Before a candidate is
evaluated, it emits a **CandidateEvidence** artifact: a structured record of the
hypothesis behind the change, the concrete changes it makes, the fix and
regression cases it expects to move, its cost expectation, and its cross-language
parity status.

The schema is the single source of truth. It lives at
`ts/src/harness-optimization/contract/json-schemas/candidate-evidence.schema.json`
and generates both packages' types: the TypeScript `CandidateEvidence` interface
(`ts/src/harness-optimization/contract/generated-types.ts`) and the Python
`CandidateEvidence` pydantic model
(`autocontext/src/autocontext/harness_optimization/contract/models.py`). A CI
drift gate rejects any hand-edit to the generated files, so the two languages can
never disagree on the contract.

## Canonical example

The record below is the shared fixture-of-record at
`fixtures/harness-optimization/candidate-evidence/valid-full.json`. Both packages
load and validate this same file in their test suites.

```json
{
  "schema_version": 1,
  "candidate_id": "cand-full-002",
  "parent_frontier_id": "frontier-001",
  "mechanism_name": "require-target-theorem-present",
  "mechanism_type": "mixed",
  "target_surface": "evaluator",
  "hypothesis": "Requiring the target theorem to be present catches false-pass truncations.",
  "changes": "Add a post-compile check that the named theorem exists in the output, plus a playbook note describing the failure mode.",
  "changed_artifacts": ["src/autocontext/loop/loop_driver.py", "knowledge/lean/playbook.md"],
  "fix_cases": ["divergence-empty-file", "truncated-claude-p-output"],
  "regression_cases": ["convective_eq_fderiv", "divergence-integrability"],
  "observed": "Dry-run over the divergence seed now fails the compile-only oracle as expected.",
  "validation_plan": "Re-run the divergence seed and confirm the empty-file case now fails, then replay the 60 banked sub-lemmas for regressions.",
  "cost_expectation": {
    "extra_tokens": 1200,
    "extra_calls": 1,
    "extra_seconds": 4.5
  },
  "leakage_scope": ["public-fixtures", "banked-sub-lemmas"],
  "parity": {
    "python": "implemented",
    "typescript": "implemented",
    "schema_hash": "9f8e7d6c5b4a"
  }
}
```

## Reading and validating the artifact

Both packages expose the same read/write/validate surface. Reading validates the
record against the schema and raises (Python) or throws (TypeScript) on an
invalid artifact.

Python:

```python
from autocontext.harness_optimization.evidence import (
    read_candidate_evidence,
    write_candidate_evidence,
)

evidence = read_candidate_evidence("candidate.json")  # raises on invalid input
print(evidence.candidate_id, evidence.mechanism_type)
write_candidate_evidence(evidence, "out/candidate.json")  # stable key order + trailing newline
```

TypeScript:

```ts
import {
  readCandidateEvidence,
  writeCandidateEvidence,
} from "autoctx/harness-optimization/evidence";

const evidence = readCandidateEvidence("candidate.json"); // throws on invalid input
console.log(evidence.candidate_id, evidence.mechanism_type);
writeCandidateEvidence(evidence, "out/candidate.json"); // 2-space indent + trailing newline
```

## Promotion score (AC-877)

Once candidates have been evaluated, the promotion gate needs a single comparable
number per candidate. The **PromotionScore** artifact carries that number together
with the raw components it was computed from, the named weight set (and its
version), and the same cross-language parity block as CandidateEvidence. Its schema
lives at `ts/src/harness-optimization/contract/json-schemas/promotion-score.schema.json`
and generates both packages' types under the same drift gate.

The score is a fresh blend of five components under four named weights:

```
score = dense_quality_score
      + sparse_success_weight * sparse_success_rate
      - token_cost_weight     * tokens_per_million
      - error_weight          * error_rate
      - variance_weight       * score_variance
```

Dense quality is the reward, the weighted sparse success rate adds to it, and the
weighted token cost, error rate, and score variance are penalties. The scorer is a
pure function of components and weights, so the same inputs always produce the same
number. A shared numeric fixture
(`fixtures/harness-optimization/promotion-score/score-cases.json`) pins the exact
expected scores and beat decisions, and both packages load it to prove they compute
identical results.

### The never-stale rule

A candidate beats an incumbent only when its score exceeds the incumbent's by more
than a margin, and the comparison always recomputes BOTH scores from raw components
under the SAME weight version. Never compare a fresh challenger score against a
stored incumbent score: a stored score may have been computed under an older weight
version, and mixing weight versions makes the comparison meaningless. `beats_incumbent`
(Python) and `beatsIncumbent` (TypeScript) enforce this by taking components (not
scores) and rescoring both sides in place. The threshold is strict: a challenger
whose recomputed margin exactly equals the minimum margin does not beat the incumbent.

### Tuning the weights

The weights are the policy knob, and they carry a version tag precisely so a
retune is an explicit, recorded event rather than a silent drift. When the dev set
is small, sparse per-case success is noisy: a couple of extra solved cases can swing
`sparse_success_rate` sharply, so a large `sparse_success_weight` lets sparse success
dominate a blend that should still be anchored by the dense quality signal. Keep the
sparse weight modest relative to the dense signal on small dev sets, and only raise
it once the case count is large enough for the success rate to be stable. Bump the
`weight_version` whenever any weight changes so that no incumbent recorded under the
old weights is ever compared under the new ones.

The python advancement gate's harness-promotion path is opt-in (`harness_promotion`
defaults to `None`) and is not yet wired into the live tournament loop; that
live-loop wiring lands with a later issue.

### Reading and validating the artifact

Both packages validate a PromotionScore against the shared schema and expose the
pure scorer. Python:

```python
from autocontext.harness_optimization.contract.models import PromotionScore
from autocontext.harness_optimization.scoring import beats_incumbent, harness_promotion_score

score = PromotionScore(**data)  # raises ValidationError on an invalid record
fresh = harness_promotion_score(score.components, score.weights)
advances = beats_incumbent(challenger.components, incumbent.components, weights, min_margin=0.05)
```

TypeScript:

```ts
import { validatePromotionScore } from "autoctx/harness-optimization/contract/validators";
import { beatsIncumbent, harnessPromotionScore } from "autoctx/harness-optimization/scoring";

const result = validatePromotionScore(data); // { valid: false, errors } on an invalid record
const fresh = harnessPromotionScore(components, weights);
const advances = beatsIncumbent(challengerComponents, incumbentComponents, weights, 0.05);
```

## Repair gates (AC-878)

Some harness failures are not a reasoning problem: a tool-call JSON is malformed,
an artifact landed at the wrong path, a run claims done without meeting its
completion conditions, or an agent is stuck repeating one no-op action. A
**repair gate** fixes these deterministically, before the candidate is scored.

A repair gate is different from the other two ways autocontext recovers a run:

- A **prompt playbook** teaches the model, in natural language, how to avoid a
  failure next time. It is advisory and its effect is probabilistic.
- A **model retry** re-runs the model (often with feedback). It costs another
  generation and its result is non-deterministic.
- A **repair gate** is a pure, bounded function over recorded state. It never
  calls a model, never reads answer hints, never touches the filesystem, and is
  fully replayable: the same recorded state always yields the same decision. It
  only ever applies _structural_ fixes that cannot change task content.

Because a repair gate cannot invent task content, it is safe to run on every
candidate once enabled. Each repair inspects the state it is handed, decides
whether a known failure mode is present, and returns a schema-valid
**RepairResult** (`status` is `applied`, `skipped`, or `not_applicable`). The
gate owns any side effect the decision implies (recording the repaired string,
recording a relocation target); the repairs themselves only decide.

### The repairs

- **tool_call_json**: restructures malformed tool-call JSON: strips a code
  fence, drops a trailing comma before a closer, closes a single truncated
  brace/bracket. It is string-aware and never guesses or alters a field value.
- **artifact_landing**: detects "right content, wrong path" by matching existing
  produced content against the expected contract, and returns the correct path as
  a relocation target. It never fabricates artifact content.
- **finish_guard**: rejects a done claim whose completion conditions are not met.
  It validates completion; it never fabricates completion.
- **loop_guard**: detects a stuck run of identical trailing actions and signals a
  break. Python-only for now: it is intentionally omitted from the default repair
  set so the default set stays identical across Python and TypeScript until the
  TypeScript mirror lands.

### Opt-in flags (default off)

The gate is off by default and changes nothing about a default run. Two settings
turn it on, and BOTH must agree before any repair runs:

- `harness_repair_gates_enabled` (bool, default `false`): the global switch.
- `harness_repair_gate_scenarios` (comma-separated allowlist, default empty): the
  scenarios the gate is active for. An empty allowlist means no scenario is active
  even when the global flag is on.

`repair_gate_active_for(settings, scenario)` (Python) and
`repairGateActiveFor(config, scenario)` (TypeScript) are the sole opt-in decision.
Callers check it and only build and run a gate when it returns true, so with the
flags off the wired seams are byte-unchanged no-ops. The seams wired today are the
pre-validation `stage_repair` in the Python generation pipeline (before staged
validation) and the malformed-envelope path in the TypeScript `ClaudeCLIRuntime`
parse step; both are guarded by the active-for check.

### Events

The gate emits exactly one event per repair on the `repair` channel:
`repair_applied` when a repair's status is `applied`, and `repair_skipped` for
both `skipped` and `not_applicable`. The payload is `{ "scenario": <name>,
"result": <RepairResult> }`: the RepairResult stays a self-contained, schema-valid
object under `result`, and the scenario rides alongside as a sibling so consumers
can attribute the repair without changing the RepairResult schema.

### Deferred: artifact reassembly

A fifth repair, reassembling a partially written artifact from a recorded
tool-call trace, is deliberately deferred: it needs a recorded tool-call trace
that the harness does not yet capture. It is a follow-up, not part of this gate.

## Leakage audit (AC-879)

A harness optimization run can accidentally learn from data it was never
supposed to see: a proposer that peeks at a holdout split, a web fetch that
pulls the answer, a required source that was never proven clean. The
**IntegrityMetadata** artifact declares what a run was allowed to touch, and a
deterministic post-proposal audit checks the declaration against what the run
actually read. Its schema lives at
`ts/src/harness-optimization/contract/json-schemas/integrity-metadata.schema.json`
and generates both packages' types under the same drift gate: the TypeScript
`IntegrityMetadata` interface, the Python `IntegrityMetadata` pydantic model, and
the `validateIntegrityMetadata` validator.

IntegrityMetadata records the run identity and its data policy: `run_id`; `mode`
(`verified` or `exploratory`); `allowed_sources`, `forbidden_sources`, and
`required_sources` (source ids the run may read, must never read, and must prove
clean before advancing); `web_policy` (`blocked`, `allowlist`, or `open`) with an
optional `web_allowlist` of permitted hosts; `split_ids` (the benchmark or test
split manifests in play); `prompt_provenance` (where the proposer prompts came
from, optional in the schema but required by the verified gate); and
`adapter_capabilities` (what the runtime can enforce, for example filesystem
sandboxing or network blocking). It also carries the computed `leakage_status`
and `contamination_reasons` so a persisted record explains its own verdict.

### The three-status audit

`audit_leakage` (Python) and `auditLeakage` (TypeScript) take the metadata plus a
sequence of observed `AccessRecord`s (each an `{resource, source_id, kind}`
triple, where kind is `file`, `trace`, `web`, or `split`) and return a
`LeakageAudit` of `status` plus `reasons`. Both are pure functions over the
declared policy and the supplied access log: no filesystem or network access, so
the same inputs always produce the same verdict. The audit runs two
contamination passes and, only if both are clean, one proven-clean check:

- **Pass 1, forbidden source read**: any access record whose `source_id` is a
  forbidden source is a contamination.
- **Pass 2, web policy**: under a `blocked` policy any web read is a
  contamination; under an `allowlist` policy a web read whose host is not in the
  allowlist is a contamination; an `open` policy permits any host.

Holdout/forbidden detection is source-id-attribution-based: an access record must
carry the forbidden `source_id` to be flagged, so `split_ids` and
`adapter_capabilities` are declarative metadata, not audited.

If any pass fires, the status is `contaminated` and every reason is listed. If
both passes are clean, the audit applies the **required-source proven-clean
rule**: a required source is proven clean when it is a declared allowed source OR
it appears in the access log. Any required source that is neither is unproven, so
the status is `unknown` (the run cannot be shown clean, but no contamination was
observed either). Only when every required source is proven clean is the status
`clean`. `render_leakage_report` (Python) and `renderLeakageReport` (TypeScript)
render a short human-readable report of the policy (including `required_sources`
and `web_allowlist`) plus the computed status and reasons.

### Verified fail-closed and the exploratory override

`evaluate_leakage_gate` (Python) and `evaluateLeakageGate` (TypeScript) turn a
`LeakageAudit` plus the run mode and prompt provenance into a
`LeakageGateDecision` of `advance`, `non_promotion_grade`, and `rationale`. Like
the repair gate, the leakage gate is caller-gated: it never reads settings, so a
default run is unaffected until a caller invokes it.

A **verified** run fails closed. It advances only when the audit status is
`clean` AND the prompt provenance is non-empty. A non-clean status (contaminated
or unknown) or a missing provenance blocks the run (`advance` is false) and marks
it `non_promotion_grade`. Provenance is enforced by the gate rather than the
schema precisely so an exploratory run can omit it while a verified run cannot.

An **exploratory** run takes the operator override: it always advances, but it is
always stamped `non_promotion_grade` regardless of the audit, so a deliberate
peek can proceed for investigation without ever polluting the promotion set.

A contaminated or blocked verified attempt is **discarded** as
non-promotion-grade. This is not evidence that the model or the harness failed:
it only means the result cannot be trusted as a clean measurement, so it must not
enter the promotion set. Framing these as discarded rather than as failures keeps
the leakage signal honest and separate from the quality signal.

### The worked cases

A shared fixture
(`fixtures/harness-optimization/leakage-cases/leakage-cases.json`) pins eight
cases that both packages load to prove they compute identical statuses, reason
counts, and gate decisions:

- **clean_run**: a verified run reads only `train-split` and an allowlisted
  `docs.example.com` host, so the audit is `clean` and the gate advances it
  promotion-grade.
- **holdout_file_touch**: a verified run reads the forbidden `holdout-split`
  file, pass 1 marks it `contaminated`, and the gate blocks it, so the attempt is
  discarded.
- **web_contaminated**: a verified run fetches `evil.example.com` under a
  `blocked` web policy, pass 3 marks it `contaminated`, and the gate blocks it, so
  the attempt is discarded.
- **missing_provenance**: a verified run is audit-`clean` but declares no
  `prompt_provenance`, so the gate fails closed on missing provenance and discards
  it, showing the gate enforces provenance the schema leaves optional.
- **exploratory_override**: an exploratory run peeks at the forbidden
  `holdout-split`, so the audit is `contaminated`, but the override advances it
  non-promotion-grade.
- **unknown_required**: a verified run declares `secret-eval` required but never
  reads it and it is not an allowed source, so the audit is `unknown` and the
  gate blocks it.
- **allowlist_violation**: a verified run fetches `other.example.com` under an
  `allowlist` policy that lists only `docs.example.com`, so pass 2 marks it
  `contaminated` and the gate blocks it.
- **bare_host_with_path_allowed**: a verified run reads an allowlisted host given
  as the bare `docs.example.com/guide` (host plus path, no scheme), which both
  languages parse to `docs.example.com`, so the audit is `clean` and the gate
  advances it promotion-grade.

## Mechanism archive (AC-880)

A harness optimization run produces two kinds of mechanism over time: the ones
that cleared the promotion gate and now define the working harness, and the ones
that were gated out. Throwing the second kind away loses real signal. A mechanism
that was rolled back at generation 5 because it regressed a holdout trace can be
exactly the piece a later candidate needs once a neighbouring surface has moved.
The **mechanism archive** keeps both kinds so the proposer can learn from the full
history, not just the surviving frontier.

The archive keeps two record types, each generated from its own canonical schema
under the same drift gate as the rest of the protocol. A **FrontierMechanism**
(`ts/src/harness-optimization/contract/json-schemas/frontier-mechanism.schema.json`)
is a mechanism that was promoted: it carries its `gate_decision`, the
`affected_surfaces` it touches, the `regression_risks` it accepted, a
`support_count` of how many candidates lean on it, and the
`promoted_at_generation` it entered the frontier. An **OrphanMechanism**
(`ts/src/harness-optimization/contract/json-schemas/orphan-mechanism.schema.json`)
is a mechanism that was rejected or not promoted: alongside the same identity and
surface fields it carries a `failure_family` (why the surface rejected it), a
`rejection_reason`, a `retry_count`, and an optional `rescued_into_frontier_id`.
Both schemas generate the TypeScript interface, the Python pydantic model, and a
`validateFrontierMechanism` / `validateOrphanMechanism` validator, so the two
languages can never disagree on a record. Optional fields follow an omit-none
serialization convention: Python callers dump records with `exclude_none` so an
absent optional is omitted from the shared shape rather than emitted as `null`,
which keeps the TypeScript AJV validation happy.

Every record carries the same two lineage fields that tie the archive back to the
rest of the protocol. `candidate_evidence_id` points at the CandidateEvidence
(AC-876) the mechanism was born from, so a promoted or orphaned mechanism can be
traced to the hypothesis and changes that proposed it. `parent_frontier_id` points
at the frontier mechanism this one was built on, so the archive records a lineage
of successive changes rather than a flat list. An orphan preserves both fields
after rollback, which is what makes it rescuable later: it still knows where it
came from and what it was trying to extend.

### The engine

The archive is a pure in-memory value with no IO. `MechanismArchive` holds two
immutable tuples, `frontier` and `orphans`, and every operation returns a new
archive rather than mutating in place. The engine surface (Python in
`autocontext/src/autocontext/harness_optimization/mechanism_archive.py`, mirrored
in TypeScript at `ts/src/harness-optimization/mechanism-archive.ts`) is:

- **add_frontier** / **addFrontier**: append a promoted mechanism to the frontier.
- **add_orphan** / **addOrphan**: append a gated-out mechanism to the orphans.
  Adding a frontier never removes an orphan and adding an orphan never touches the
  frontier, so the two lists accumulate independently.
- **rescue_orphan** / **rescueOrphan**: mark an orphan as rescued into a named
  later frontier mechanism. The orphan stays in `orphans` with its
  `rescued_into_frontier_id` set, so the rescue is auditable and history is
  preserved rather than rewritten. An unknown id returns the archive unchanged.
- **query** / **query**: filter both lists by any combination of `mechanism_type`,
  `target_surface`, and `failure_family`. The first two facets narrow frontier and
  orphans alike; `failure_family` narrows orphans only, since the frontier has no
  failure family. A missing facet applies no filter.
- **rank_orphans** / **rankOrphans**: order orphans most-reusable first (see below).
- **prune_orphans** / **pruneOrphans**: keep the top `max_orphans` orphans by reuse
  rank, in rank order, so a long-running archive stays bounded without dropping the
  most reusable history. A negative bound clamps to zero.
- **render_archive_digest** / **renderArchiveDigest**: render a bounded, ranked
  summary for proposer prompts (see below).

### The ranking rule

`rank_orphans` orders orphans so the most reusable ones come first, by an
ascending sort over a four-part key:

1. **not-rescued before rescued**: an orphan that has not yet been rescued is a
   live reuse opportunity and sorts ahead of one already folded into a frontier. A
   missing or empty `rescued_into_frontier_id` counts as not-rescued.
2. **support_count descending**: an orphan that more candidates leaned on is more
   likely to be worth reviving. A missing support_count counts as 0.
3. **retry_count ascending**: among equally supported orphans, the one that needed
   fewer retries is the cleaner candidate.
4. **mechanism_id ascending**: a stable tiebreak so both languages produce the
   identical order. `mechanism_id` is assumed ASCII so the cross-language string
   comparison in the tiebreak orders identically in Python and TypeScript.

`prune_orphans` uses exactly this order, so pruning keeps the orphans a proposer
is most likely to reuse and discards the stale, low-support, many-retry tail.

### The bounded proposer digest

`render_archive_digest` turns the archive into the string that actually reaches a
proposer prompt. It is deliberately bounded and ranked so the proposer sees
evidence-backed mechanisms above stale or noisy ones and never sees more than
`max_entries` entries per section. The digest has a fixed shape a TypeScript port
reproduces character-for-character: a header line, a `frontier:` section listing up
to `max_entries` frontier mechanisms in frontier order, then an
`orphans (reusable):` section listing up to `max_entries` not-rescued orphans in
`rank_orphans` order. Rescued orphans are excluded from the reusable section
because they already live in the frontier lineage, and a non-positive `max_entries`
emits the section headers with no items. The bound is the point: a proposer prompt
gets the highest-support frontier and the most reusable orphans, capped, instead of
an unbounded dump that would bury the signal.

### The worked cases

A shared fixture
(`fixtures/harness-optimization/mechanism-archive/archive-cases.json`) pins a seed
archive plus a set of cases that both packages load to prove they compute
identical results: appending an orphan grows the orphan list, a `mechanism_type`
query filters both lists together, `rank_orphans` orders the seed orphans
most-reusable first, a rescue sets the frontier id and sinks the rescued orphan to
the tail of the reuse order, prune keeps the top-ranked orphans, and the digest
renders the same bounded, ranked proposer feed on both sides.

## Noise calibration (AC-881)

A harness optimization gate advances a candidate when its margin over the
incumbent clears a threshold. But a margin is only meaningful relative to the
noise in the score series that produced it. A 0.01 improvement is real signal on
a metric whose runs vary by 0.002 and pure chance on a metric whose runs vary by
0.05. **Noise calibration** turns a raw score series into an estimate of that
noise floor so a gate can state, in its own rationale, whether the current margin
sits above or below the noise it is competing with.

### The report

`compute_calibration` (Python in
`autocontext/src/autocontext/harness_optimization/calibration.py`, mirrored in
TypeScript at `ts/src/harness-optimization/calibration.ts`) takes a score series
plus the scenario id, the currently configured promotion margin
(`current_min_delta`), and a cost-budget ceiling on trials (`max_trials`), and
returns a `CalibrationReport`. Its fields are:

- `scenario_id`, `sample_size` (n): identity and how many samples fed the estimate.
- `mean`, `variance`, `std_dev`: the series centre and spread.
- `standard_error`: the standard error of the mean, the noise on the headline number.
- `recommended_min_delta`: the margin a gate should require to beat the noise.
- `recommended_trial_count`: how many trials to run so the current margin is
  resolvable, clamped to the cost budget.
- `current_min_delta`: the margin currently configured, echoed back for the citation.
- `margin_vs_noise`: `above_noise` or `below_noise`.
- `sparse_metric_too_noisy`: whether the sparse headline metric is too noisy to gate on.

### The formulas

The arithmetic is fixed so the Python and TypeScript ports agree to 1e-9:

- **Sample variance** uses `ddof=1` (divide by `n - 1`), and is 0 when `n < 2`.
  `std_dev` is its square root.
- **standard_error** is `std_dev / sqrt(n)`, and is 0 when `n < 2`.
- **recommended_min_delta** is `noise_multiplier * standard_error` (default
  multiplier 2.0), so the recommended margin is two standard errors of the mean.
- **recommended_trial_count** is `clamp(ceil((std_dev / current_min_delta) ** 2), 1, max_trials)`.
  The `(std_dev / current_min_delta) ** 2` term is how many samples it takes for
  the standard error to shrink below the margin the gate wants to resolve. The
  clamp keeps that in `[1, max_trials]` so the cost budget is respected and an
  expensive run is never silently increased past its ceiling. When
  `current_min_delta` or `std_dev` is 0 the count falls back to `max_trials`.

With fewer than 2 samples the report has zero variance and cites `above_noise` as an
"insufficient data" degenerate case, so a single-sample or empty series never reads
as a real margin over the noise floor. autocontext assumes mechanism scores are
finite and bounded; extreme overflow inputs are out of scope for the calibration
arithmetic.

### Reading the report

Two fields carry the decision-relevant signal:

- **margin_vs_noise** is `above_noise` when `current_min_delta >= recommended_min_delta`
  and `below_noise` otherwise. A `below_noise` margin means the gate is trying to
  resolve a difference smaller than the noise on the metric, so a passing candidate
  may just be a lucky draw. `cite_margin_vs_noise` renders this as a single line,
  for example `margin 0.005000 is below_noise (recommended >= 0.017889)`.
- **sparse_metric_too_noisy** is set when the coefficient of variation of the mean
  (`standard_error / abs(mean)`) exceeds a threshold (default 0.25), or when the
  mean is 0 but the standard error is not. It flags that the sparse headline metric
  is too noisy to gate on directly.

### Sparse headline versus dense verifier

A sparse headline metric (for example a single pass/fail success rate over a
handful of runs) has few effective samples, so its standard error stays large and
`margin_vs_noise` easily reads `below_noise`. A dense verifier metric (per-step or
per-check scores aggregated over many observations) has many more effective
samples for the same wall-clock cost, so its noise floor is far lower and small
real improvements become resolvable. When `sparse_metric_too_noisy` is set, treat
the sparse metric as secondary and optimize a denser verifier signal instead: the
sparse headline is too noisy to gate on at the current sample size, and pushing on
it risks advancing on noise. The dense signal gives the gate a margin it can
actually trust, and the sparse metric can be checked as a secondary confirmation.

### Caller-gated citation

The citation is opt-in and caller-gated, so the default gate behavior is
unchanged. `evaluate_advancement` gains a trailing keyword-only `calibration`
parameter; when a caller passes a `CalibrationReport`, the one-line
`cite_margin_vs_noise` string is appended to the rationale's `proxy_signals` and
nothing else changes. When it is omitted (the default) the rationale is
byte-identical to before. The caller decides whether to build a report and pass
it, gated by the `harness_calibration_enabled` setting (default off);
`evaluate_advancement` itself never reads settings.

### The worked cases

A shared fixture
(`fixtures/harness-optimization/calibration-cases/calibration-cases.json`) pins a
set of score series and the report each should produce, which both packages load
to prove they compute identical statistics, recommendations, and rendered text.

## Cross-package parity (AC-882)

Every artifact above is generated once and consumed twice: a python pydantic
model and a TypeScript interface plus AJV validator, from the same canonical
schema. That only stays honest if both packages are proven to agree on real
records. `fixtures/harness-optimization/parity-manifest.json` is the single
source that lists all seven artifacts (candidate-evidence, promotion-score,
repair-result, integrity-metadata, frontier-mechanism, orphan-mechanism, and
calibration-report) together with each one's schema file and its clean and
invalid fixtures. Both packages iterate that one manifest: the python suite at
`autocontext/tests/harness_optimization/test_parity_suite.py` and the TypeScript
suite at `ts/tests/harness-optimization/parity-suite.test.ts`. For every artifact
in the manifest, every clean fixture must validate and every invalid fixture must
be rejected, in BOTH languages. A record that one side accepts and the other
rejects fails the build.

### The membership guard

The manifest cannot be allowed to fall behind the schemas. Both suites carry a
membership guard that reads `ts/src/harness-optimization/contract/json-schemas`
and asserts that every `*.schema.json` file (except the `_aggregate` bundle) has
a manifest entry. Adding a new schema without a manifest entry, or without at
least one clean and one invalid fixture, fails CI, so a future artifact cannot
ship contract types without also shipping cross-language parity fixtures.

### What parity guarantees

The parity coverage is split across two kinds of fixture:

- **Structure**: python and TS agree on the schema, the required fields, and the
  enum values. This is enforced upstream by the byte-diff drift gate
  (`npm run check:harness-optimization-schemas`), which regenerates both packages'
  types and the synced python schemas and fails on any hand-edit, and downstream
  by the manifest's clean and invalid fixtures validating identically on both
  sides.
- **Arithmetic**: python and TS agree on the numeric computations. The promotion
  score and beat decisions are pinned by
  `fixtures/harness-optimization/promotion-score/score-cases.json`, and the
  calibration statistics and rendered citations by
  `fixtures/harness-optimization/calibration-cases/calibration-cases.json`. Both
  packages load these numeric fixtures and must reproduce the pinned values within
  a 1e-9 tolerance.

### Intentionally unsupported behavior

Parity is a claim about the contract and the pure computations, not about every
pipeline hook. Some behavior is python-side only by design, and the parity suite
does not assert a TS equivalent for it:

- TS runners and adapters may DECLARE source policy and contamination status
  (they carry the IntegrityMetadata fields), but they do not enforce every
  python-only hook, per AC-879. The leakage audit and gate functions exist in both
  languages, but their wiring into a live run is python-side.
- The leakage stage (`autocontext/src/autocontext/loop/stage_leakage.py`) and the
  calibration citation in `evaluate_advancement` are python-side pipeline wirings.
  Both are opt-in and default-off, and there is no TS pipeline equivalent to
  mirror. The shared, mirrored surface is the pure `audit_leakage` /
  `evaluate_leakage_gate` and `compute_calibration` functions and their fixtures,
  not the python loop that calls them.

### Verifying parity

Run all three from their package directories:

```bash
cd autocontext && uv run pytest
cd ts && npm test
cd ts && npm run lint
```

## Adding a new protocol artifact

A new artifact follows the same contract pattern end to end. The steps, in order:

1. Author the JSON schema under
   `ts/src/harness-optimization/contract/json-schemas/`.
2. Add one `$ref` to `_aggregate.schema.json` so the new schema joins the bundle.
3. Regenerate both packages' types:
   `node scripts/generate-harness-optimization-types.mjs` then
   `node scripts/sync-python-harness-optimization-schemas.mjs`.
4. Wire the AJV validator and its `_TypeCheck` member in `validators.ts`.
5. Add at least one clean and one invalid fixture under
   `fixtures/harness-optimization/<artifact>/`.
6. Add a `parity-manifest.json` entry (name, schema id, schema file, and the
   clean and invalid fixture paths).
7. Add the pydantic model to `test_parity_suite.py`'s `MODELS` map and the
   validator to `parity-suite.test.ts`'s `VALIDATORS` map.
8. Run the pre-merge gate:
   - `uv run --frozen ruff check src tests`
   - `uv run --frozen pytest tests/test_module_size_limits.py tests/test_gate_taxonomy.py tests/harness_optimization`
   - `npm run check:harness-optimization-schemas`
   - `npx vitest run tests/harness-optimization`
   - `npm run lint`

The membership guard means skipping steps 5 through 7 is caught by the build: a
schema with no manifest entry, or a manifest artifact with no model or validator
map member, fails the parity suite.

## The contract pattern

This is the foundation artifact. The later protocol artifacts follow the same
contract pattern (one canonical JSON Schema generating both packages' types, a
shared fixture set, and a thin read/write/validate surface):

- promotion score (AC-877)
- repair result (AC-878)
- integrity metadata (AC-879)
- mechanism archive (AC-880)
- calibration report (AC-881)

Live-loop persistence (writing CandidateEvidence during real generation runs) and
prompt injection (feeding the record into the proposer's context) are a tracked
follow-up and are deliberately not part of this foundation PR. The read/write/
validate API here is enough to persist and inspect an artifact from both sides.
