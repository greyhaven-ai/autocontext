# CPU search diagnostics

Milestone 12 explains the retained M11 search results using a separate exhaustive
reference over the declared practice options. It distinguishes a search that
missed an available practice improvement from a task with no improving declared
option, and identifies selected practice gains that failed the original
confirmation gate. GPU execution remains deferred.

This workflow requires Linux or macOS for POSIX locking and the CPU worker.

Run from `autocontext/` after `uv sync --locked --group dev`. The commands below
create a fresh three-task M11 fixture, complete its nine policy searches, and
keep the source bundle, source evidence, diagnostic plan, and diagnostic evidence
in separate sibling directories:

```bash
# Build and complete a source campaign using the existing M11 workflow.
uv run python ../examples/weight_evolution/cpu_campaign/build_bundle.py \
  --preset fixture --output ../runs/diagnostic-source-bundle
uv run python ../examples/weight_evolution/cpu_campaign/run.py \
  --bundle ../runs/diagnostic-source-bundle run \
  --output ../runs/diagnostic-source-output

# Verify every source run and freeze the separate reference plan; no new inference.
uv run python ../examples/weight_evolution/cpu_diagnostics/run.py \
  --campaign-bundle ../runs/diagnostic-source-bundle \
  --campaign-output ../runs/diagnostic-source-output \
  --plan ../runs/diagnostic-plan plan

# Read-only verification of the plan and its complete retained source evidence.
uv run python ../examples/weight_evolution/cpu_diagnostics/run.py \
  --campaign-bundle ../runs/diagnostic-source-bundle \
  --campaign-output ../runs/diagnostic-source-output \
  --plan ../runs/diagnostic-plan validate

# Execute or resume the separately charged CPU references.
uv run python ../examples/weight_evolution/cpu_diagnostics/run.py \
  --campaign-bundle ../runs/diagnostic-source-bundle \
  --campaign-output ../runs/diagnostic-source-output \
  --plan ../runs/diagnostic-plan run --output ../runs/diagnostic-output

# Verify retained evidence and print JSON without inference or file repair.
uv run python ../examples/weight_evolution/cpu_diagnostics/run.py \
  --campaign-bundle ../runs/diagnostic-source-bundle \
  --campaign-output ../runs/diagnostic-source-output \
  --plan ../runs/diagnostic-plan report --output ../runs/diagnostic-output
```

An existing complete M11 campaign can replace the first two commands. Every
scheduled source search must have completed, including searches whose
confirmation gate vetoed audit. A failed, pending, or unstarted source search
prevents plan creation. The plan pins the full verified campaign report and
ledger, every original selection and summary, candidate options, practice cases,
paired inference seeds, CPU runtime, and diagnostic implementation. It preserves
all three policies and the full task/repeat roster. Use fresh real paths without
symlink ancestors or overlapping directories; resume and report require the same
pinned source and runtime.

One reference is shared by the three policies for each task/repeat. It evaluates
the unchanged baseline once, then every declared C, W, and D option once, in
declaration order, on the complete practice cohort. The generated M11 family has
four options per mechanism: **13 batches per reference**, each containing every
practice case. The three-task fixture has three references and 156 case-level
inference calls. The original 72-search pilot has 24 references and 1,248 such
calls. The reference preserves each paired inference seed; it performs no proposal
search or seed optimization.

The reference reuses the already verified baseline and W checkpoints from the
source adaptive run. Its separate budget retains the existing `materializations`
meter name and charges one checkpoint verification per declared candidate—five
per reference for this family. It creates no new checkpoint edits or
materializations. These reference charges remain separate from the original
policy search budgets. Worker requests contain prompts and execution settings;
practice labels remain with the coordinator and are bound by the plan's case hash.

The report retains every source run and reference status, original practice
curves, selected options, gaps to the best declared practice option, baseline
retention, and original confirmation outcomes. It also groups options, including
the baseline, that produce identical output vectors on the practice cohort.
Counts and averages are descriptive; repeated
seeds remain nested within task instances and do not create additional independent
tasks. Missing reference evidence remains explicit, and incomplete reference
coverage withholds the aggregate selected-gap mean.

A valid retained prefix resumes only its unclaimed work. Failed or uncertain
operations are never retried; their full reference reservations remain charged
while actual usage may be unknown. Source and retained diagnostic integrity are
checked before further dispatch. The `report` command is read-only and verifies
completed references; an unsealed pending reference remains missing, without
replaying its partial journal or claiming scores. `run` validates a retained
partial prefix before dispatch and can finish permitted publication from intact
retained evidence.

The exhaustive reference does not change a search's selected option or rerun its
confirmation gate. It probes no confirmation or audit cases and makes no
held-out optimum claim. CW remains the original selected composition, without
an exhaustive C×W search. These diagnostics do not promote candidates, establish
benchmark performance, or reproduce Qwen results. Qwen/GPU acceptance, the full
published reproduction, live proposal models, arbitrary programs, and TypeScript
parity remain deferred.

See [milestone 12 validation](MILESTONE12_VALIDATION.md) and the
[machine-readable results](MILESTONE12_RESULTS.json) for measured fixture/pilot
diagnostics and remaining gaps.
