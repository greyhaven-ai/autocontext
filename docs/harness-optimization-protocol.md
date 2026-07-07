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
languages can never disagree on a record.

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
   identical order.

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
