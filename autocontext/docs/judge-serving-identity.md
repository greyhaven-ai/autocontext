# Judge serving identity (AC-1022)

Python `LLMJudge` now emits an immutable, reconstructible `evaluator_spec` JSON
string alongside `evaluator_epoch`. The epoch is SHA-256 of the specification's
canonical UTF-8 JSON. The specification binds the compiled rubric, provider,
served model, prompt-template version, ordered rendered calibration examples,
pinned dimensions, and ordered score-transformation versions.

`JudgeServingSpec.model_validate_json(result.evaluator_spec)` reconstructs the
contract. `spec.require_epoch(result.evaluator_epoch)` checks its identity.
`EvaluatorEpoch.from_spec(spec)` produces the registry record's identity metadata.
The existing registry persists the specification at agent-task score write sites;
it verifies the digest and metadata before writing, refuses replacement of an
existing specification, and quarantines a mismatched manifest.

## Semantic boundary

Serving examples are the **exact rendered prompt strings**, including human
scores, notes, ordering, and the existing 200-character output snippets. Database
IDs, timestamps, arbitrary extra fields, and output text beyond that served
snippet do not participate. Full input examples remain in their original store.
This does not expand the judge's existing exposure to human data. Registry files
use the existing protected knowledge location and atomic temporary-file writes;
manifests contain no provider authentication configuration. Treat manifests and
saved result JSON as having the same sensitivity as the human examples.

Task prompts, agent outputs, reference evidence, and required concepts are
fixture data. `fixture_provenance` records their combined digest outside the
evaluator identity; the existing task/output stores retain the underlying inputs.
Direct CLI/solve runs persist per-round execution and fixture provenance as
`judge_provenance` activity in the existing run database; queued single- and
multi-generation results retain it in their result JSON. Those activity records
contain hashes and execution settings, not a second copy of human examples.

Sampling count, token limit, disagreement threshold, and each attempted request's
model and temperature are explicit `execution_provenance`. They affect execution
variance and cost, rather than defining a new evaluator. Retry and aggregation
*rules* are versioned scoring semantics; the realized requests are provenance.

The Python template version binds all conditional instructions, including
reference checking and example formatting. Its transformation versions bind
marker/JSON/plaintext parsing and clamping, averaging only parseable samples,
factual-dimension defaults, the same-span contradiction cap, and pinned-dimension
zero filling. Change the corresponding version when those implementations change.
These names resolve to the implementation in the version of autocontext that
served the score; archive that package/source revision with evaluation artifacts.

## Migration and evidence reuse

The legacy three-argument `compute_evaluator_epoch` remains byte-stable for
historical records. New serving specifications deliberately mint different IDs,
even with no human examples. Existing active epochs stay active; a new identity
is a candidate requiring the existing promotion workflow. Historical/hash-only
records keep `serving_spec: null`. A hash-only record can receive a manifest only
when the manifest independently reproduces its exact digest; old rubric-only
digests cannot be silently reconstructed as new specifications.

Existing epoch comparison and rebaseline checks therefore reject old evidence
after an example-only change. The improvement loop also salts its local cache
with evaluation inputs and does not reuse a versioned LLM verdict without a
trusted, pre-evaluation expected specification. AC-1026 owns that pinning seam;
until then these verdicts are rejudged conservatively. Changing the examples can
also require rejudging an unchanged output.

Leave-one-out calibration serves a different example set for each anchor. Such
reports now retain per-anchor epoch identities and carry no single aggregate
epoch when identities differ or are unknown. They remain diagnostic alignment
reports and cannot authorize automatic promotion as single-specification
evidence. The independent held-out validation protocol belongs to AC-1025.

AC-1001 owns extension-handler fingerprints. The wire format reserves an
`extension_fingerprint` field. Until that integration exists, detected hook
rewrites of prompts/responses or mixtures of served models produce unknown
lineage with an explicit provenance status. A model-only switch is identified
using the model actually served. No parallel hook registry is introduced.

## Runtime scope and validation

Python serving, result propagation, and registry persistence are implemented
first. TypeScript implements and validates the same immutable wire contract and
shares the canonical byte/digest fixtures. Its live judge continues using its
legacy epoch path; live TypeScript migration and persistence are deferred. Do
not treat the two runtime implementations as the same evaluator: their prompt
and transformation versions must identify their actual semantics.

Regression coverage includes example-only changes, example order, Unicode,
instruction and transformation revisions, immutable persistence, mismatched
promotion evidence, unknown hook effects, fixture/sampling separation, and
unchanged-output rejudging across a specification change.
