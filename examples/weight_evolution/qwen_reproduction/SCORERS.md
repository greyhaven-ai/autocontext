# Pinned CPU scoring environment

The scorer is separate from the CUDA inference image. It runs on saved raw
responses, with answers and instruction metadata supplied only to evaluation.
Fetching checker files is data-only; `load_pinned_scorer` is the explicit step
that executes the verified official checker source.

The [scorer lock](scorer.lock.json) binds the [installation manifest](scorer-requirements.txt),
21 exact package versions and their dependency closure, three official IFEval
modules, the source license, and the NLTK English sentence-tokenizer tables.
Install with artifact hash checking in a separate Python 3.13 environment. From
`autocontext/`:

```bash
uv venv --python 3.13 ../examples/weight_evolution/qwen_reproduction/.scorer/.venv
uv pip install \
  --python ../examples/weight_evolution/qwen_reproduction/.scorer/.venv/bin/python \
  --require-hashes \
  -r ../examples/weight_evolution/qwen_reproduction/scorer-requirements.txt
```

Use that environment's Python, with `PYTHONPATH=src`, for offline report scoring.
The main autocontext dependency lock and the inference image are unchanged.
`load_scorer_lock` verifies the sibling installation manifest's hash. Scorer
initialization checks Python's minor version and every installed distribution
version, authenticates all source/resource bytes, and checks the sentence and
word tokenizers before any cohort is scored. It performs no downloads.

The Python API separates acquisition, verification and execution:

```python
from pathlib import Path
from autocontext.weight_evolution.scorer_runtime import (
    fetch_scorer_assets, load_pinned_scorer, load_scorer_lock, verify_scorer_assets,
)

example = Path("../examples/weight_evolution/qwen_reproduction")
lock = load_scorer_lock(example / "scorer.lock.json")
assets = example / ".scorer-assets"
fetch_scorer_assets(lock, assets)  # Explicit network acquisition; stores opaque bytes.
verify_scorer_assets(lock, assets)  # Offline and data-only.
scorer = load_pinned_scorer(lock, assets)  # Executes authenticated official checker code.
print(scorer.provenance())
```

Call `scorer.validate_cohort(dataset, verified_dataset.problems)` before grading
any responses. Every math gold answer must parse and every IFEval instruction
must be available and constructible, including when a model emits no answer.
`score_sample(dataset, problem, sample)` retains zero-score missing/wrong answers
while surfacing dependency, data and checker failures as infrastructure errors.
Scoring runs in the main thread so math-verify's bounded signal timeouts work.

## Source fidelity and deterministic resources

The released [mathqa.py](https://github.com/RikiyaT/lm_understand-logs/blob/4d09ba6d6a7e891dd8063974dfe81b3d78e3494f/exp8_math/code/mathqa.py)
explicitly names math-verify **0.9.0**, latex2sympy2_extended **1.11.0** and
antlr4-python3-runtime **4.13.2**. Those versions are retained. SymPy 1.14.0 and
the remaining package versions are declared reproduction choices because the
source inherited them from an unspecified environment. Official math-verify is
used for boxed symbolic equivalence; no string-equality fallback is substituted.

The [official Google IFEval code](https://github.com/google-research/google-research/tree/e6890f85757dd84e27ca6df2dd30651dafad28e0/instruction_following_eval)
is pinned to the same revision as the dataset. All 25 registered instruction
classes are loaded unchanged, without adding the asset directory to `sys.path`.
The checker files retain their Apache-2.0 notices, and their LICENSE is acquired
alongside them. This replaces the original experiment's unreleased `weightenv`
adapter with a declared official implementation; exact adapter parity is not
asserted.

The official utility references the legacy NLTK English Punkt pickle URI. The
pinned NLTK 3.10.4 redirects it to non-pickle `punkt_tab` tables. The complete
[NLTK archive](https://github.com/nltk/nltk_data/blob/550b6625bcef1f2abff2ff770a5a0d272c9c6b2a/packages/tokenizers/punkt_tab.zip)
and each of the four extracted English tables are independently hashed.
Only those verified tables are placed in a private temporary resource directory;
ambient NLTK resource paths and caches cannot supply a substitute. Language
detection uses a private factory seeded with 0. Checker randomness also uses
seed 0, with ambient state restored afterward. These deterministic settings are
recorded deviations because the source did not pin them.

Package versions and installed-wheel artifact hashes are different checks:
`--require-hashes` authenticates installation artifacts, while the scorer checks
installed distribution versions and its own source/resources. A local runtime
receipt is not remote image or installed-wheel-byte attestation.

## CPU validation performed

Using the locked dependencies in a temporary Python 3.13.1 environment:

- All **500 MATH-500 gold answers**, **1,319 GSM8K gold answers**, and **541 IFEval
  instruction rows** passed scorer preflight.
- Symbolic equality, fraction/decimal equivalence, wrong-answer and missing-box
  cases passed with real math-verify.
- Keyword, sentence-count, word-count and deterministic language cases passed
  with the official IFEval classes and offline NLTK resources.
- Every one of the **25 IFEval instruction families** was exercised using its
  actual pinned dataset arguments and a synthetic response.

These are grader integration checks, not model benchmark results. The remaining
GPU execution and original-adapter fidelity gaps still apply.
