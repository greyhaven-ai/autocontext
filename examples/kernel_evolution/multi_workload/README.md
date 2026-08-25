# Multi-workload kernel evolution study

This example runs variable-shape matrix multiplication, fused
elementwise/reduction, and causal-attention families through the same
`KernelEvolutionRunner`, external evaluator, confirmation, promotion, lineage,
and generation-budget contracts. It then re-evaluates promoted champions on
the other workload families and a second synthetic hardware identity.

Run it from the Python package directory:

```bash
cd autocontext
uv run --frozen python ../examples/kernel_evolution/multi_workload/run.py
```

The output contains the immutable, digest-bound synthetic manifest and every
materialized campaign, final-champion, and transfer contract; each workload's
ordinary kernel run directory; a content-addressed `evidence/` tree with every
raw observation and benchmark report; and `study_report.json`. A successful
portable bundle is committed only by `study_artifacts.json`; the report alone
does not establish complete one-look history. The private `0700` study root is
created exclusively below a retained no-follow output descriptor, and its
descriptor remains open through terminal success or failure. Kernel `runs/`
diagnostics execute from their own descriptor-bound working directory and fail
if its public name changes. Benchmark children inherit that retained root and
change into it before opening the precommitted adapter and problem; public
replacement paths cannot redirect execution. Setup, workload, finalization, or
pre-commit interruption failures produce an anchored `study_failure.json` and
remove any partial success report. The report and marker are published marker-last through
the retained study-root descriptor. The portable inventory is read and replayed
through that same descriptor, and both published files retain exact inode/byte
checks through the final commit. An interruption observed after an exact commit
leaves the success bundle intact without creating a conflicting failure marker.
Platforms without the required directory-relative operations fail closed. After
an unrecoverable process kill, any report without `study_artifacts.json` remains
uncommitted and must not be published.
The study report deliberately has no aggregate scalar score. It embeds each
workload's complete evolution result, final primary/confirmation observations,
generation receipts/failures, stable public shape profile, exact benchmark-case
identities and floors, reference/protocol/hardware identities, bounded wall/cost
usage, promoted/plateau/incomplete outcome, transfer failures, and
specialist/generalizing classification. An artifact can appear in
`portable_champion_artifact_digests` only after it passes every target workload
and the required shape, hardware, and workload-family transfer dimensions.

The checked-in adapter is deterministic synthetic orchestration evidence. Its
latencies are not measurements and must never be cited as accelerator results.
For a production study, replace it with an operator-owned evaluator while
preserving the same report and study models. Every adaptive confirmation,
final primary/confirmation phase, and transfer phase in this conformance run
has its own pre-reserved protocol and plan identity. Each invocation creates a
high-entropy execution identity before reserving those plans, so a fresh study
cannot reuse old evaluator reports, burns, or execution IDs. A measured run also
requires at least one signed final report binding its generation-receipt context,
which prevents cross-study receipt substitution. Pin each trusted reference
and runtime image, keep exact primary/confirmation cases private, preserve that
one-look, one-plan rule, use an OS-isolated protected evaluator boundary, and
publish the resolved accelerator identity and raw receipts with the study.
Parsing measured studies also requires an explicit external evaluator key,
pinned build and boundary digests, and an externally pinned exact evidence-index
digest; embedded receipt or artifact-manifest presence never grants trust.

Every report and raw benchmark receipt binds the immutable manifest, executable
contract set, synthetic backend identity, and warning. `contract-runtime/`
publishes an exact relocatable manifest and byte snapshot of the example, full
first-party `autocontext` source tree, `pyproject.toml`, `uv.lock`, and Python
runtime identity. The external runner requires the adapter, contract helper,
and problem to match their in-memory precommit, then executes them through the
inherited study-root descriptor. Exact reference
source preimages live in `sources/`. `--study-id` and workload IDs are restricted
to safe single path components; existing study directories are never overwritten.

The `runs/` trees are local diagnostics and can contain absolute host command
and filesystem paths. They are excluded from the portable inventory and
identity; publish only the files sealed by `study_artifacts.json` unless those
diagnostics are separately scrubbed. Any missing, changed, symlinked, escaped,
or undeclared file under a portable root invalidates the bundle.

The checked-in runbook caps every family at two proposals, 10,000 generation
tokens, $1 of generation-provider cost, $1 of total workload cost, and 300
seconds of active end-to-end workload time, including its campaign, final
checks, and outgoing transfer evaluations. Generation-provider wall time has
its own 300-second ceiling. The raw evidence index records each evaluation's
wall time and synthetic zero-dollar cost. Derived rejections link to their one
chargeable raw execution instead of double-counting it. Each execution ID is a
canonical commitment to its study/spec epoch, stream sequence, candidate,
incumbent, protocol, and plan, so IDs cannot be swapped between looks or reused
within a shared campaign protocol. Report provenance binds the execution epoch,
exact workload-spec/budget digest, and index digest. Each
run's final reports also bind its generation receipt set to the study, spec,
runner, and budget. `study_artifacts.json` seals the index, report, exact
sources, all reserved contracts, and runtime files by path/digest/size. These
deliberately small values keep CI deterministic; real budgets belong in an
immutable operator manifest. `KernelGenerationBudget` enforces provider usage,
while the study deadline bounds the evaluator and orchestration work around it.
For measured studies, exact externally trusted index bytes are required. Index
wall/cost usage is charged to the corresponding campaign or phase, claimed phase
wall time cannot be lower than either that history or the signed evaluator
transcript, and each authenticated receipt must represent a unique look.
Generation-provider costs still need separately trusted billing receipts when
they are outside the evidence-index trust boundary.
