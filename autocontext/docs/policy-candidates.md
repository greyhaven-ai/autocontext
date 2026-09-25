# Scoped executable policy candidates

AC-1019 adds an opt-in Python API for producing **inactive candidates** from
explicitly selected GridCTF training traces. It follows AC-1018's limited finding:
reuse may pay off in a stable one-action simulator. It does not claim non-game
transfer, useful general reasoning, or production readiness.

The scope follows the [AC-1018 pilot report](https://github.com/greyhaven-ai/autocontext/blob/164805c5e6d8179264cde2f69e3dcd95ebc905dc/autocontext/benchmarks/policy_reuse/REPORT.md).

## Run the integration example

From `autocontext/`, on a host supported by the existing local policy isolation:

```bash
uv run --frozen python ../examples/policy_candidates/run.py --output /tmp/ac1019-demo
```

Use a new output directory each time. The example executes two successful
training traces and one unsuccessful trace, selects their IDs explicitly,
replays a **fixture synthesis response**, and evaluates four fresh inputs plus
two changed-contract counterexamples. It reloads the stored candidate and invokes
it once. This is a deterministic integration demonstration, not a live model
benchmark. Scores, rejection reasons, source lineage and an unchanged-active-pointer
check are recorded under the output directory.

The example explicitly initializes an empty baseline in its new local store.
The synthesis API itself requires an existing baseline and never bootstraps or
changes serving state.

## Embed the workflow

Use `synthesize_grid_candidate` from `autocontext.execution.policy_candidates`.
Provide an existing `LLMProvider`, its provider identity, a `ContextBundleStore`,
run ID, trace pool, selected successful IDs, optional failed training IDs, and a
predeclared list of `CandidateCase` objects. It makes one `complete()` call with
4,096 requested output tokens; no workflow retries or iterative refinement use
the evaluation cases. Provider transports retain their configured timeout/retry
behavior. Reported usage and served/requested model identities remain distinct;
missing cost is null. Configure the provider's timeout before calling.

`trace_from_grid_match` consumes a `PolicyMatchResult` dictionary and explicit
seed/split/group provenance. It rejects conflicting provenance already present
in the row. It derives the recurring observe/validate/execute/verify sequence
from completed one-action match records and regenerates only the public numeric
observation from the seed. It never copies raw messages, actions, error text,
replays, final answers, or arbitrary extra fields into the synthesis prompt.
The raw row is referenced by SHA-256. This bounded adapter is not a general
free-text sanitizer or automatic procedure miner. Callers remain responsible
for authentic trace provenance and complete protected/group memberships.

This uses AC-730's evidence references, reusable behavior and exclusion
conventions. The existing general operational-memory compiler is TypeScript;
this change does not introduce a competing memory-pack store or port that compiler.

`SelectionExclusions` carries protected trace IDs, groups, seeds and input hashes.
Held-out/test memberships visible in the pool are also excluded. Training and
evaluation must have disjoint groups, seeds, IDs and numeric inputs; two distinct
successful training groups are required, with at most 32 selected training traces.
Supply group IDs for near-duplicates
that share a task even if their numeric inputs differ. Protected test material
cannot be used for this developmental candidate screen.

## Contract and execution

The v1 manifest binds the existing `SkillReference` executable payload and
`ArtifactProvenance` to the scenario version, full input/output schemas,
applicability, resource limits, empty external dependency/capability grants,
training evidence, synthesis prompt/model identity and evaluation hashes. It uses
the existing canonical JSON implementation. Nested legacy models are stored as
canonical JSON strings; accessors return fresh objects. The digest covers every
manifest field. `inspect_grid_candidate` also verifies prompt, source and
evaluation references when loading the content-addressed bundle.

The model supplies only `propose_action(state)`. Trusted code adds
`choose_action(state)` with an explicit contract guard. Invocation verifies the
expected manifest digest, scenario/schema/verifier compatibility and caller
resource limits. Invalid inputs or a changed contract return a typed abstention
before generated code executes. Other executions use `PolicyExecutor.execute_action`
and its existing killable, bounded, JSON-only child boundary. The private verifier
seed is not an input. Returned actions must be finite and satisfy the exact
three-field schema and combined action limit before the trusted verifier scores
them. Caller capabilities are not copied into the policy namespace.

Defaults are two seconds, 256 MiB and 64 KiB of result output per case, with at
most 32 cases. Limits cannot exceed the caller's limits. Existing local isolation
is containment with restricted Python, not a new filesystem/network security
boundary; see the executor documentation before considering mutually untrusted
tenants. Unsupported isolation fails closed and is recorded as failed evidence.

## Candidate lifecycle and limitations

The immutable manifest, sanitized synthesis prompt and all case evidence are
stored as an inert `tool_spec` component named `trace_derived_policy_candidate`
in the **existing ContextBundleStore candidate lifecycle**. Successful screens
leave the candidate `proposed`; syntax, isolation, schema, quality or abstention
failures leave it `rejected`. Rejected source and scoped negative evidence remain
inspectable. No `HarnessEntryStore.apply`, artifact activation, active-pointer
write, or automatic routing occurs. The surrounding bundle keeps its existing
evaluator epoch; the separate offline verifier identity is bound in the candidate
manifest and is not represented as production judge evidence.

The workflow calls the existing refinement response parser and policy executor;
it does not implement another refinement loop. Fresh cases screen simple replay
or memorization failures but do not prove generalization. Constant policies can
pass because the GridCTF objective favors them. Counterexamples test explicit
contract invalidation; there is no learned applicability classifier. Evaluation
cases are developmental evidence, not protected promotion evidence. Repeated
experimentation needs new independent evidence before adoption.

The store's existing optimistic parent check applies if another writer changes
the baseline during synthesis; retry explicitly against the new baseline.
Transport failures before source is returned propagate without creating a code
candidate. This API is intended for controlled, serialized experiments.

AC-1028 owns helper/runtime integration, AC-1020 routing and fallback, AC-1021
production promotion, and AC-1029 non-game evidence. Python orchestration and the
GridCTF adapter ship first. TypeScript workflow/serving parity is deferred because
this is an opt-in experiment; the canonical versioned manifest is the integration
contract for those follow-ups.
