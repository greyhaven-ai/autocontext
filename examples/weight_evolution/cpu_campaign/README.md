# Generated CPU search campaigns

Milestone 11 compares adaptive, fixed-order, and seeded-random search on generated
instances of the tiny token-sum classifier. Each policy searches the same finite
context (C), tensor (W), and decoding (D) option space with three trials per mechanism. All
policies select the earliest practice winner, including the unchanged baseline
(A), restore it with a charged probe, and evaluate the predetermined C+W
composition (CW). GPU execution remains deferred.

Run from `autocontext/` after `uv sync --locked --group dev`:

```bash
# Read-only preflight of the checked-in 3-task × 1-repeat × 3-policy fixture.
uv run python ../examples/weight_evolution/cpu_campaign/run.py validate

# Execute every scheduled CPU search, or resume its retained journal.
uv run python ../examples/weight_evolution/cpu_campaign/run.py run \
  --output ../runs/cpu-campaign-fixture

# Verify retained bytes and regenerate JSON to stdout, without writes or inference.
uv run python ../examples/weight_evolution/cpu_campaign/run.py report \
  --output ../runs/cpu-campaign-fixture

# Build the predeclared 8-task × 3-repeat × 3-policy pilot: 72 bounded searches.
uv run python ../examples/weight_evolution/cpu_campaign/build_bundle.py \
  --preset pilot --output ../runs/cpu-campaign-pilot-bundle
uv run python ../examples/weight_evolution/cpu_campaign/run.py \
  --bundle ../runs/cpu-campaign-pilot-bundle run --output ../runs/cpu-campaign-pilot
```

Use fresh, real paths without symlink ancestors. The builder never runs inference
or filters tasks by their scores. Separate deterministic SHA-256 streams generate
the latent label rule, base checkpoint, context/tensor directions, and prompts;
decoding options are fixed. Labels are
computed from that latent rule; candidates have neutral coordinate/direction
identifiers. The contract pins generator source, checkpoint bytes, edit programs,
case splits, scorer, policies, capacities, seeds, stopping rule, and reporting
rule. Verification regenerates the expected task bytes in memory.
Resume and report verification require the same pinned source and CPU runtime;
changed runtimes cannot silently reinterpret retained evidence.

Practice, confirmation, and audit prompts have distinct normalized token-count
vectors, rejecting permutations and proportional repetitions as aliases. This is
a structural separation check for the toy model, not a semantic independence
guarantee for language tasks. M9/M10's repeated token families remain regression
fixtures and are not counted as these new task instances.

Generation seeds define tasks. Inference seeds define nested repeats and are
paired across policies; they cannot be optimized. Random-search-order seeds have
a separate field and SHA-256 ordering rule. Fixed order and random order ignore
scores when choosing the next option, but use exactly the same practice-based
winner selection as adaptive search. Each C/W/D search arm gets three trials, its own
baseline probe, full practice coverage, and restoration. Input lengths can differ
by chosen context; the report distinguishes common capacity from actual charges.

The campaign reserves each run's conservative full cost before dispatch, including
possible audit. The child journal then records actual operations and CPU receipts.
A failed or uncertain dispatch burns its campaign reservation and is never retried;
other scheduled runs continue. A valid interrupted child prefix can resume at the
first unclaimed operation. Completed children can finish parent publication without
repeating inference. Integrity failures stop validation before new work. The
`report` command never repairs files; `run` can recreate a missing campaign-level
derived report from intact evidence.

Every scheduled run appears in the report, including failures, vetoes, pending and
unstarted work. Failure costs remain unknown rather than zero; full reservations
stay visible. Confirmation compares each selected C/W/D condition with A once. All
three must meet the frozen minimum gain before audit. A veto withholds audit and
does not trigger reselection or another confirmation look.

The eight prespecified comparisons are adaptive minus each control for C/W/CW/D
confirmation accuracy. Repeats are averaged within a task before the shared
paired-statistics helper computes Student-t intervals, with Bonferroni allocation
across eight contrasts. These are model-based descriptive intervals for a small
synthetic family. Overall means/intervals are withheld when any scheduled search
is incomplete or failed; observed pairs remain explicitly listed. Audit scores
are conditional on passing confirmation and remain per-run, without a survivor-only
overall quality average. A null, negative, or inconclusive result is valid.

The workflow reuses the verified tensor registry, bounded CPU subprocess runner,
exact-label scorer, restoration/confirmation coordinator, immutable journal and
locking primitives, and paired statistics. It follows the kernel study's complete
coverage and reservation accounting patterns without reusing its speedup or
promotion schemas. The fixed worker is trusted code, not an arbitrary-code sandbox.

See [validation and remaining gaps](MILESTONE11_VALIDATION.md). Real Qwen/GPU
acceptance, full published reproduction, live proposal models, arbitrary programs,
and TypeScript parity remain deferred. CPU campaign results do not establish
language-model improvement or benchmark performance.
