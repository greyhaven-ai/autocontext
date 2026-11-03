# Offline comparison-condition harness

Milestone 5 implements the frozen comparison matrix and synthetic fixture replay.
**GPU execution is deferred.** These fixtures run no model, optimize no context
or weights, and produce no benchmark or retained-learning result.

The [Qwen reproduction](../qwen_reproduction/README.md) remains a separate pinned
base-versus-published-patch protocol. This harness preserves that contract and
reuses its data models, scoring boundaries, canonical identities and shared
paired-statistics helper. It does not turn context changes into reproduction
evidence or use kernel speedup/promotion rules for accuracy comparisons.

## Frozen comparison matrix

| ID | Checkpoint | Target inference context | Decoding |
| --- | --- | --- | --- |
| A | Base | Base | Base |
| C | Base | C* | Base |
| W | W* | Base | Base |
| CW | Exactly W* | Exactly C* | Base |
| D | Base | Base | D* |

C, W and D each change their permitted surface. CW composes the identical C and
W artifacts; it is not a jointly optimized candidate. Seeds, samples per problem,
output caps, tokenizer/runtime/scorer identity and evaluation cases are shared.
D can change temperature, top-p, top-k and presence penalty; it cannot change
context, checkpoints, sample counts or output limits.

Here C*, W* and D* are **hand-authored synthetic stand-ins**, with declared
practice provenance and mock search accounting. No optimizer produced them.
Checkpoint artifacts contain small text descriptors, not tensors. Runtime and
tokenizer digests identify fixture protocols, not an accepted CUDA deployment.
Target runtime context is distinct from an editor's diagnostic memory; this
milestone has no editor or retained-memory experiment.

## Run without a GPU, network or credentials

From `autocontext/`, after the normal locked development setup:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_comparison/run.py validate
uv run --frozen python ../examples/weight_evolution/qwen_comparison/run.py run-fixtures \
  --output ./runs/qwen-comparison-fixture
```

`validate` verifies fixture hashes, strict schemas, coverage, budgets and scorer
inputs, then grades the prerecorded responses in memory. `run-fixtures` performs
the same checks and publishes `report.json`, `report.md` and a final
`complete.json` marker with report/file digests into a new output directory.
The command rejects existing output directories. Neither command has an
`--execute` or GPU option, loads model weights, or calls an inference service.

The default [fixture bundle](fixtures/fixtures.lock.json) pins fixed filenames
`study.json` and `responses.json` by SHA-256 and size. Loading is bounded and
rejects symlinks, repeated JSON keys, altered bytes and cross-study responses.
The bundle contains three synthetic practice problems and six synthetic audit
problems, two seeds, one sample per problem/seed and five conditions: **60 cells**.
The `math500`, `gsm8k` and `ifeval` names select existing scorer routes; the toy
questions are not members of those benchmarks.

The fixture scorer supports signed integer equivalence and three explicit toy
instruction checkers. It reuses the existing boxed-answer extraction and strict
thinking-response boundary. It is not math-verify or the official IFEval grader.
Its identity includes both the toy checker and shared scoring source hashes.

## Integrity, metrics and accounting

Every cell binds condition, checkpoint/context/decoding contents, runtime,
tokenizer, scorer, cohort, question, seed and sample index. Human artifact labels
do not substitute for content identity. Stored cells must exactly match the
matrix derived from the frozen artifacts; changing an arm or its projected
context invalidates the plan. Condition order rotates across problems while
coverage stays identical.

Only problem IDs, question/context text and configuration identities enter
projected requests. Gold answers and instruction metadata stay with the scorer.
Development/evaluation IDs and normalized prompts must be disjoint, including
aliases within either split. Candidate source IDs must refer only to the common
practice pool. These checks verify the fixture declarations, not the origin of
human-authored text, pretraining contamination, or sandboxed editor isolation.

Evidence must cover every planned cell exactly once before any score is computed.
Responses are separately prerecorded, with text hashes and fixture byte counts.
Wrong, empty and length-limited answers stay in the denominator; malformed
evidence or a checker failure stops evaluation rather than becoming score zero.
The bundled catalog includes negative interactions and regressions; no condition
is assumed to improve the model. A boxed answer can score correctly at the cap.

Reports show absolute fixture accuracy, completion/cap-hit rates, byte counts and
declared elapsed values per condition and scorer route. Repeated seeds/samples
are averaged within each problem before the paired contrasts C−A, W−A, CW−C,
CW−W and D−A, plus the factorial interaction CW−C−W+A. Paired t intervals are
descriptive fixture arithmetic, not simultaneous confidence guarantees,
noninferiority gates or uncertainty across independent editing runs.

Counters use **UTF-8 bytes, not Qwen tokens**. The `max_new_tokens` protocol field
is interpreted by this explicitly named fixture tokenizer as an output-byte
cap; the bundled 96-unit cap reserves 1,152 output units per condition and 5,760
in total. Context adds input bytes and those bytes count toward the budget.
Search caps are shared across C/W/D mechanisms, but search usage is declared
fixture data, not measured optimization. CW attributes C+W costs; the study
counts discovery of those shared artifacts only once. This does not establish
equal-cost independent optimization of all five arms.

All report layers preserve fixture labels and explicitly deny model execution,
benchmark, promotion and retained-learning claims. Reports contain identities
and scores; raw response text remains in the fixture evidence file.

## Maintaining the fixtures

[build_fixture.py](build_fixture.py) records the literal study and response
catalog separately from gold-based scoring. To inspect a regenerated bundle:

```bash
uv run --frozen python ../examples/weight_evolution/qwen_comparison/build_fixture.py \
  --output ./runs/qwen-comparison-regenerated
uv run --frozen python ../examples/weight_evolution/qwen_comparison/run.py \
  --fixture-root ./runs/qwen-comparison-regenerated validate
```

The builder requires a new directory and never repairs existing pins silently.
Changing scorer semantics requires deliberately regenerating and reviewing the
fixture pins. Normal replay never changes artifacts or invokes the builder.

## Deferred work

Real comparison adapters, context/weight/decoding search, budgeted editing
sessions, official graders and fresh held-out studies are later work. The
published patch's engineering replay does not establish independently optimized
W*. Future live comparisons must freeze genuine C*/W*/D* before audit exposure,
record actual input/output/editor/probe costs, and validate the corresponding
runtime and artifacts. Retained-knowledge on/off and jointly optimized candidates
require their own conditions and budgets. M4 GPU acceptance and full public
reproduction also remain pending; nothing here dispatches those runs.

See [milestone 5 validation](MILESTONE5_VALIDATION.md) for the checks performed.
