# Pinned evaluation datasets

The [dataset lock](datasets.lock.json) authenticates the following public test
cohorts as data. Dataset scripts are never imported. The three raw JSONL assets
total 1,403,413 bytes; benchmark questions and answers are not bundled in Git.

| Dataset | Immutable source | Problems | Raw SHA-256 |
| --- | --- | ---: | --- |
| MATH-500 | [HuggingFaceH4/MATH-500, `6e4ed1a2`](https://huggingface.co/datasets/HuggingFaceH4/MATH-500/blob/6e4ed1a2a79af7d8630a6b768ec859cb5af4d3be/test.jsonl) | 500 | `35dc41080a3680858b27fa7e0533d2d547825316fc5dafe5d316f4ccc5a06132` |
| GSM8K | [OpenAI grade-school-math, `3101c7d5`](https://github.com/openai/grade-school-math/blob/3101c7d5072418e28b9008a6636bde82a006892c/grade_school_math/data/test.jsonl) | 1,319 | `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14` |
| IFEval | [Google Research, `e6890f85`](https://github.com/google-research/google-research/blob/e6890f85757dd84e27ca6df2dd30651dafad28e0/instruction_following_eval/data/input_data.jsonl) | 541 | `67ffeee0fcb87c317c5b08a2de85557b4a7e96ada6178aa645b4954fe4b53d49` |

The source repositories retain their dataset licenses and terms. Consult the
[MATH-500 card](https://huggingface.co/datasets/HuggingFaceH4/MATH-500),
[OpenAI release](https://github.com/openai/grade-school-math/tree/3101c7d5072418e28b9008a6636bde82a006892c),
and [IFEval release](https://github.com/google-research/google-research/tree/e6890f85757dd84e27ca6df2dd30651dafad28e0/instruction_following_eval)
for provenance and attribution. Synthetic unit-test fixtures are authored for
autocontext, not copied benchmark items.

## Normalization and identity

Normalization preserves source row order and exact prompt text:

- MATH-500 uses `unique_id`, `problem` and `answer`, matching the upstream
  reproduction's preparation function.
- GSM8K uses `gsm8k_test_{index}` IDs. Gold is the text after the single `####`
  separator, stripped of surrounding whitespace and commas, matching upstream.
- IFEval uses `ifeval_{key}` IDs and preserves each instruction ID with its
  corresponding keyword arguments. Its source keys remain stable even if the
  unreleased upstream preparation used different IDs.

The canonical cohort digest hashes the ordered normalized problem objects,
including their gold answers or instruction metadata, as sorted-key compact
JSON. Each problem has `problem_id`, `text`, `answer` and `instructions` fields.
Empty instruction lists and null answers remain explicit. Source bytes, parsed
count and normalized digest must all match the lock before a cohort is accepted.
Duplicate JSON keys/IDs, invalid scoring fields and unsafe file paths fail.

For source equivalence, all 1,319 ordered OpenAI JSONL question/answer records
were compared with the [Hugging Face `openai/gsm8k` main/test parquet](https://huggingface.co/datasets/openai/gsm8k/blob/740312add88f781978c0658806c59bc2815b9866/main/test-00000-of-00001.parquet)
at revision `740312add88f781978c0658806c59bc2815b9866`; they are identical.
The comparison parquet SHA-256 is
`ee7b8da9e381df27b9e3f7758a159ab2bdaa4dbaa910546cbbc47e0cb44e4f59`.
This check used a temporary PyArrow 21.0.0 environment; normal fetching and
verification need only the standard library and the existing Pydantic dependency.

## Scope and remaining fidelity gaps

These snapshots define a repeatable reproduction cohort now. The source
experiment did not release content-pinned benchmark snapshots, so equivalence to
its private files is not established by matching dataset names. Inference
requests contain only IDs and prompts; gold answers and instruction metadata
belong to the trusted evaluator and are not sent to the target model.

The lock declares all three cohorts `final_test`. They are suitable for frozen
base-versus-published-patch replay. They must not become practice inputs to a
future editing agent. Practice/discovery snapshots and the original 13-word
overlap audit remain unresolved. The optional exact/NFKC whitespace-insensitive
overlap helper diagnoses repeated text; it does not prove absence of training
contamination or implement that 13-word audit.
