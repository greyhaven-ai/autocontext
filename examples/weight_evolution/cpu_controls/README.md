# CPU transfer controls

Milestone 13 checks whether the CPU search and diagnostic workflow distinguishes
a weight repair that transfers, a baseline that is already correct, and a
practice shortcut that fails confirmation. The adaptive, fixed-order and seeded
random policies retain the existing search budget and joint confirmation rule.
GPU execution remains deferred.

See the [validation record](MILESTONE13_VALIDATION.md) and
[recorded results](MILESTONE13_RESULTS.json) for the bounded fixture and pilot.

| Family | Baseline accuracy | Designated W edit on practice | Designated W edit on confirmation |
| --- | ---: | ---: | ---: |
| Transfer repair | 50% | 100% | 100% |
| Correct baseline | 100% | 50% | 50% |
| Practice shortcut | 50% | 100% | 0% |

These are analytical properties of constructed tasks. Generator unit checks
independently verify the expectations on all three splits. The measured campaign
keeps its original confirmation and audit rules; its exhaustive references use
only practice cases. Neither workflow evaluates unselected options on held-out
cases or feeds expected-control metadata back into search.

Each family declares four options per C/W/D mechanism and tries three. Two W
options have the designated behavior; the other two tie the baseline on these
cohorts. Search receives the ordinary options and practice history, without the
family labels or expected-control metadata.

The transfer repair is a positive control for the **W confirmation component**.
The original joint gate still requires C, W and D to meet its minimum gain.
Improving W does not by itself open an audit. References evaluate only practice;
they never evaluate an unselected option on confirmation or audit.

## Presets

| Preset | Task instances | Repeats per task | Policy searches | Shared references |
| --- | ---: | ---: | ---: | ---: |
| `fixture` | 6: two per family | 1 | 18 | 6 |
| `pilot` | 12: four per family | 2 | 72 | 24 |

Both presets use eight cases per practice, confirmation and audit cohort. Seeds,
family membership, options, model artifacts, source identities and capacities
are pinned when the bundle is built. Reports keep families separate and average
repeats within task instances where appropriate. They do not select a pooled
winner or add inferential statistics.

## Run a campaign

Linux or macOS is required for the worker's POSIX resource limits. Run these
commands from the repository's `autocontext/` directory. Install the locked
development environment first. Use a fresh bundle directory; repeated `run`
verifies and resumes existing evidence.

```bash
uv sync --locked --group dev

uv run python ../examples/weight_evolution/cpu_controls/build_bundle.py \
  --preset fixture --output ../runs/cpu-controls/bundle

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle validate

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle run --output ../runs/cpu-controls/campaign

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle report --output ../runs/cpu-controls/campaign
```

`validate` and `report` perform no model execution and make no output changes.
The campaign keeps every scheduled policy search, including failures and vetoes.
Failures retain their reservations and are not silently retried.

## Diagnose the completed campaign

Plan creation verifies every completed source search and freezes its selections
before any new reference runs. A completed veto is eligible; an incomplete or
failed source search cannot be used to construct this plan. Keep the bundle,
campaign output, plan and diagnostic output in separate directories.

```bash
uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle diagnostic-plan \
  --campaign-output ../runs/cpu-controls/campaign --plan ../runs/cpu-controls/plan

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle diagnostic-validate \
  --campaign-output ../runs/cpu-controls/campaign --plan ../runs/cpu-controls/plan

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle diagnostic-run \
  --campaign-output ../runs/cpu-controls/campaign --plan ../runs/cpu-controls/plan \
  --output ../runs/cpu-controls/diagnostic

uv run python ../examples/weight_evolution/cpu_controls/run.py \
  --bundle ../runs/cpu-controls/bundle diagnostic-report \
  --campaign-output ../runs/cpu-controls/campaign --plan ../runs/cpu-controls/plan \
  --output ../runs/cpu-controls/diagnostic
```

One reference is shared across the three policies for each task/repeat. It runs
the baseline once and all twelve C/W/D options once: thirteen practice batches.
The existing five checkpoints are independently verified, with those checks
charged to the `materializations` meter; no new edit is created. Reference costs
remain separate from the original search budgets.

The diagnostic report contains `diagnostics`, with the retained reference and
search rows and a digest binding the original diagnostic evidence. Its quality
summaries appear only under `transfer.family_results`, with family-specific
opportunities, observed confirmation outcomes and calibration against the
declared controls. It does not pool practice gaps across families. Missing
references retain their denominators and reservations; their quality and actual
unknown charges remain unknown. Replaying a complete diagnostic or reading its
report performs no new inference and leaves the original campaign unchanged.

See [MILESTONE13_VALIDATION.md](MILESTONE13_VALIDATION.md) for measured results,
validation evidence and remaining gaps, including the retained partial pilot.

The earlier M11/M12 contracts and evidence remain intact. These small CPU
controls test this workflow's behavior; they do not establish language-model or
benchmark performance. Qwen/GPU acceptance, the published base-versus-patch
reproduction, live proposal models and TypeScript parity remain deferred.
