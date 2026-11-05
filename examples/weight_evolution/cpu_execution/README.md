# Paired CPU search and reference execution

Milestone 16 runs the existing constructed controls through the unchanged native
CPU workflow and a separately pinned measured workflow. It fixes one task from
each of three control families, one inference/order-seed repeat, three policies
and four cases per role. The same checkpoint bytes, options, three-round search,
selection, scoring, resource caps and joint C/W/D confirmation rule apply to both
profiles. GPU execution remains deferred.

See [M16 validation](MILESTONE16_VALIDATION.md) and the
[machine-readable results](MILESTONE16_RESULTS.json) for implementation,
compatibility evidence and remaining gaps.

The complete schedule is declared before inference: **18 searches** (nine per
profile), then **six conditional practice-reference slots**. Each profile gets
its three exhaustive references only if all nine corresponding searches
completed. A completed search can have a confirmation veto; its reference remains
eligible because references evaluate practice only and never select a candidate
again. Each task has one reference per profile, shared across its three policy
runs.

Run these commands from the repository's `autocontext/` Python package directory:

```bash
uv run python ../examples/weight_evolution/cpu_execution/build_bundle.py \
  --output ../runs/cpu-execution-bundle
uv run python ../examples/weight_evolution/cpu_execution/validate.py \
  --bundle ../runs/cpu-execution-bundle
uv run python ../examples/weight_evolution/cpu_execution/run.py \
  --bundle ../runs/cpu-execution-bundle --output ../runs/cpu-execution-campaign
uv run python ../examples/weight_evolution/cpu_execution/report.py \
  --bundle ../runs/cpu-execution-bundle --output ../runs/cpu-execution-campaign
```

Building and validating perform no inference. The bundle pins the fixed control
assets, both runtime identities, failure contract, full search capacity including
possible audits, and separate maximum reference capacity. There are no public
commands for injecting failures or running arbitrary worker programs.

Each child attempt first receives an immutable parent claim. Measured children
use versioned operation claims, outcomes and results; returned process evidence
is retained before the corresponding result. Native comparison children keep
their original journals. Measured workflow projections live below `projection/`
and are never presented as native execution journals. A failure before child
bootstrap records the observed parent exception class and invents no worker
measurements.
Separate validation checks distinguish a returned outcome from an accepted
operation. Missing inference outcomes leave model execution unknown unless
another retained observation establishes that execution occurred.

The parent journal has 24 fixed slots. An ineligible reference publishes a blocked
decision with zero charge and no child execution. Every authorized child claim
consumes its full capacity without refunds, including failed or uncertain work.
The report distinguishes that charge from usage verified for completed children
and gives an explicit count of rows whose actual usage is unknown.

Calling `run.py` again verifies all retained evidence before later work. It can
publish a missing parent result from a valid retained child/outcome, but never
dispatches a previously claimed child. A partial child stays failed or uncertain;
only other previously unclaimed schedule entries may execute. Every retained
prefix is checked again before the next dispatch, including failed work and
rehashed artifacts. Source, checkpoint, manifest, plan or evidence drift fails
closed. Missing live observations are never fabricated during replay.

Eligible reference plans are written to `plans/<profile>.json` after completed
source selections have been verified. They bind the complete corresponding
nine-search source ledger and contain only practice inputs. The measured
reference adapter also verifies the three actual source search journals for each
reference and their source checkpoint stores. The original confirmation/audit
inputs never become reference requests.

The report retains all 18 searches and six reference slots by family. It compares
native receipt semantics, trial/proposal histories, selection, gate decisions,
reference scores and verified usage. Profile/journal identities and telemetry
are excluded from parity. Missing pairs are unavailable, not matching zeroes.
Successful parity does not require the confirmation gate to pass or an audit to
run, and does not establish an adaptive-search advantage or a pooled family
winner. Phase coverage distinguishes practice, restoration, confirmation, audit
and reference observations; absent measurements remain unknown.

M15's measured profile pins the entire weight-evolution Python source inventory.
Adding the M16 modules therefore creates a new measured profile even though the
existing files are unchanged. Replaying original M15 acceptance requires its
original pinned source snapshot; these commands do not relabel that evidence as
the current profile. M11–M14 source pins remain separate and unchanged.

This bounded integration run adds no 72-search pilot, positive joint-gate
redesign, live proposer, larger model, TypeScript execution parity or promotion.
It cannot recover the original M13 failure cause. Qwen GPU acceptance and full
base-versus-patch benchmark reproduction remain deferred.
