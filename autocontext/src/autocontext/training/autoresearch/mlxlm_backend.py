"""mlx-lm LoRA/DoRA fine-tuning backend for autoresearch.

Instead of training a from-scratch GPT (the `mlx`/`cuda` backends), this fine-tunes a
*pretrained* mlx-lm model with LoRA/DoRA on the curated, optionally score-conditioned
records. It uses the base model's own tokenizer and a natural-language
prompt/completion format with completion-only loss (``--mask-prompt``), so the model
starts from a strong prior over JSON / numbers / structure rather than learning the
format from scratch.

Reuses the shared record curation (``data_selection``), the scenario interface
(``get_task_prompt`` / ``evaluate_output`` / ``execute_match``), the robust strategy
parser (``extract_json_object``), and the quality bucketing (``score_to_quality_bucket``). It
does NOT use the autoresearch ``<|...|>`` BPE token contract.

Gated behind the ``mlxlm`` optional-dependency extra (``mlx-lm``).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.sequence_format import (
    NUM_QUALITY_BUCKETS,
    extract_json_object,
    resolve_scenario_context,
    score_to_quality_bucket,
)
from autocontext.training.model_defaults import MLXLM_DEFAULT_BASE_MODEL

HAS_MLXLM = importlib.util.find_spec("mlx_lm") is not None

# Small instruct base with a 4-bit MLX conversion (QLoRA-friendly). Overridable.
# Sourced from the mlx-free shared module so backends.py can report it without importing mlx.
DEFAULT_BASE_MODEL = MLXLM_DEFAULT_BASE_MODEL


# ---------------------------------------------------------------------------
# Pure data conversion (records -> mlx-lm completions format)
# ---------------------------------------------------------------------------


def _quality_prefix(quality: int | None, num_buckets: int) -> str:
    """Natural-language quality directive prepended to the prompt when conditioning."""
    if quality is None:
        return ""
    return f"Target quality: {quality} out of {num_buckets - 1} (higher is better).\n"


def format_assess_prompt(tokenizer: Any, task_prompt: str, *, score_conditioned: bool) -> str:
    """Render the assessment prompt through the model's chat template.

    mlx-lm's LoRA trainer applies the instruct chat template to prompt/completion records, and
    the serving path (``MLXLMProvider.format_mlxlm_prompt``) does the same. Assessment must match:
    feeding an instruct model a RAW prompt yields prose, not the JSON the verifier can score, so
    the in-training metric reads ~0. Falls back to the raw text if the tokenizer has no chat
    template (a base, non-instruct model)."""
    prefix = _quality_prefix(NUM_QUALITY_BUCKETS - 1, NUM_QUALITY_BUCKETS) if score_conditioned else ""
    content = prefix + task_prompt
    try:
        rendered = tokenizer.apply_chat_template(
            [{"role": "user", "content": content}], add_generation_prompt=True, tokenize=False
        )
        return str(rendered)
    except Exception:
        return content


def build_completion_record(
    *,
    task_prompt: str,
    strategy_json: str,
    quality: int | None = None,
    num_buckets: int = NUM_QUALITY_BUCKETS,
    reasoning: str | None = None,
) -> dict[str, str]:
    """Build one mlx-lm ``{"prompt", "completion"}`` record.

    The prompt is the scenario task instruction (optionally prefixed with a quality
    directive for score-conditioned training). The completion is the strategy JSON,
    optionally preceded by the teacher's rationale (reason-then-construct): with
    completion-only loss the model trains to produce the reasoning and then the
    construction. A falsy ``reasoning`` yields the bare strategy JSON (answer-only).
    """
    completion = f"{reasoning}\n{strategy_json}" if reasoning else strategy_json
    return {"prompt": _quality_prefix(quality, num_buckets) + task_prompt, "completion": completion}


def records_to_completions(
    records: list[dict[str, Any]],
    *,
    task_prompt: str,
    score_conditioned: bool = False,
    num_buckets: int = NUM_QUALITY_BUCKETS,
) -> list[dict[str, str]]:
    """Convert training records into mlx-lm completion records."""
    out: list[dict[str, str]] = []
    for record in records:
        quality = score_to_quality_bucket(float(record.get("score", 0.0)), num_buckets=num_buckets) if score_conditioned else None
        strategy = record["strategy"]
        # Game scenarios carry a JSON strategy object; agent-task scenarios carry the raw text
        # output. Use the text directly as the completion (json.dumps would quote/escape it).
        strategy_text = strategy if isinstance(strategy, str) else json.dumps(strategy, sort_keys=True)
        # Dataset-style agent tasks (e.g. GSM8K) carry a per-record prompt -- the specific problem
        # this solution solves -- so each completion trains on its own instruction. Single-task
        # scenarios omit it and share the one scenario-level task_prompt.
        record_prompt = record.get("prompt") or task_prompt
        out.append(
            build_completion_record(
                task_prompt=record_prompt,
                strategy_json=strategy_text,
                quality=quality,
                num_buckets=num_buckets,
                reasoning=str(record.get("reasoning") or "") or None,
            )
        )
    return out


def write_completion_dataset(
    records: list[dict[str, Any]],
    data_dir: Path,
    *,
    task_prompt: str,
    score_conditioned: bool = False,
    num_buckets: int = NUM_QUALITY_BUCKETS,
    val_fraction: float = 0.1,
) -> tuple[int, int]:
    """Write ``train.jsonl`` + ``valid.jsonl`` (mlx-lm requires both) and return their sizes."""
    comps = records_to_completions(records, task_prompt=task_prompt, score_conditioned=score_conditioned, num_buckets=num_buckets)
    n_val = max(1, int(len(comps) * val_fraction)) if len(comps) > 1 else 0
    # Hold out the TAIL for validation. Records arrive elite-first (curated highest
    # score first), so the strongest examples stay in train; we validate on the rest.
    train = comps[: len(comps) - n_val] or list(comps)
    val = comps[len(comps) - n_val :] if n_val else train[:1]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "train.jsonl").write_text("\n".join(json.dumps(c) for c in train) + "\n", encoding="utf-8")
    (data_dir / "valid.jsonl").write_text("\n".join(json.dumps(c) for c in val) + "\n", encoding="utf-8")
    return len(train), len(val)


def scenario_task_prompt_and_state(scenario: Any) -> tuple[str, Any]:
    """Resolve the task instruction and, for agent-task scenarios, the state it was built from.

    Agent-task scenarios (``get_task_prompt`` + ``initial_state``) must pass that SAME state back
    into ``evaluate_output(output, state)`` at scoring time, so the state is resolved once and
    returned alongside the prompt. Returns ``(prompt, None)`` for scenarios without an agent-task
    interface (e.g. games scored via ``execute_match``, which take no state).
    """
    get_task_prompt = getattr(scenario, "get_task_prompt", None)
    initial_state = getattr(scenario, "initial_state", None)
    if callable(get_task_prompt) and callable(initial_state):
        try:
            state = initial_state()
            return str(get_task_prompt(state)), state
        except Exception:
            pass
    return resolve_scenario_context(scenario), None


def scenario_task_prompt(scenario: Any) -> str:
    """Resolve the natural-language task instruction for a scenario (state-agnostic)."""
    return scenario_task_prompt_and_state(scenario)[0]


# ---------------------------------------------------------------------------
# Training (gated; requires mlx-lm + a base model)
# ---------------------------------------------------------------------------


def run_mlxlm_training(
    *,
    scenario_name: str,
    data_path: Path,
    output_dir: Path,
    time_budget: int,
    memory_limit_mb: int,
    train_steps: int = 100,
    batch_size: int = 4,
    learning_rate: float = 1e-4,
    base_model: str = DEFAULT_BASE_MODEL,
    fine_tune_type: str = "lora",
    num_layers: int = 8,
    assess_samples: int = 8,
    assess_temperature: float = 0.0,
    assess_top_k: int = 0,
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    dedupe_near_threshold: float = 1.0,
    score_conditioned: bool = False,
    augmenter_spec: str = "",
    collect_samples_path: Path | None = None,
) -> dict[str, float]:
    """Fine-tune a pretrained mlx-lm model with LoRA/DoRA and assess it in-scenario.

    When ``collect_samples_path`` is set, the in-scenario assessment also writes its
    generated ``{strategy, score}`` samples there, so the ReST-EM self-improving loop can
    keep the elite and retrain on them (the adapter analogue of the mlx collect path)."""
    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.data_selection import prepare_training_records
    from autocontext.training.autoresearch.train import _all_records, _peak_memory_mb, _preflight_backend_deps

    _preflight_backend_deps("mlxlm")
    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")
    scenario = SCENARIO_REGISTRY[scenario_name]()

    records = prepare_training_records(
        _all_records(data_path),
        augmenter_spec=augmenter_spec,
        elite_fraction=elite_fraction,
        dedupe=dedupe,
        near_threshold=dedupe_near_threshold,
    )
    task_prompt, eval_state = scenario_task_prompt_and_state(scenario)

    output_dir.mkdir(parents=True, exist_ok=True)
    data_dir = output_dir / "data"
    n_train, _ = write_completion_dataset(records, data_dir, task_prompt=task_prompt, score_conditioned=score_conditioned)
    adapter_dir = output_dir / "adapters"

    command = [
        sys.executable,
        "-m",
        "mlx_lm.lora",
        "--model",
        base_model,
        "--train",
        "--data",
        str(data_dir),
        "--adapter-path",
        str(adapter_dir),
        "--fine-tune-type",
        fine_tune_type,
        "--iters",
        str(train_steps),
        "--batch-size",
        str(batch_size),
        "--num-layers",
        str(num_layers),
        "--learning-rate",
        str(learning_rate),
        "--mask-prompt",  # completion-only loss
    ]
    started = time.perf_counter()
    result = subprocess.run(command, capture_output=True, text=True, timeout=max(time_budget, 1), check=False)
    if result.returncode != 0:
        raise RuntimeError(f"mlx-lm LoRA training failed (exit {result.returncode}):\n{result.stderr[-2000:]}")

    metrics = _assess_mlxlm(
        base_model=base_model,
        adapter_dir=adapter_dir,
        scenario=scenario,
        task_prompt=task_prompt,
        eval_state=eval_state,
        n_samples=assess_samples,
        temperature=assess_temperature,
        top_k=assess_top_k,
        score_conditioned=score_conditioned,
        collect_path=collect_samples_path,
    )
    return {
        "avg_score": metrics["avg_score"],
        "valid_rate": metrics["valid_rate"],
        "val_loss": float("nan"),
        "training_seconds": time.perf_counter() - started,
        "peak_memory_mb": min(_peak_memory_mb(), float(memory_limit_mb)),
        "num_steps": float(train_steps),
        "num_records": float(n_train),
        "num_params_m": 0.0,  # LoRA adapter params are small / model-dependent
        "depth": 0.0,
    }


def _mlxlm_generate_texts(
    model: Any,
    tokenizer: Any,
    prompts: list[str],
    *,
    sampler: Any,
    max_tokens: int,
) -> list[str]:
    """Generate one completion per prompt, batched when possible.

    Assessment draws many samples per round; generating them one at a time is the
    minutes-vs-hours bottleneck for real ``autoctx self-improve`` runs. When mlx-lm exposes
    ``batch_generate`` and there is more than one prompt, decode the whole batch in a single
    forward pipeline (each prompt tokenized to ids via the tokenizer's chat-formatted string).
    Any failure -- an mlx-lm without ``batch_generate``, an API change, or a returned count that
    doesn't line up with the inputs -- falls back to a resilient per-prompt ``generate`` loop, so
    the path degrades to correct-but-slower and never regresses or drops the one-text-per-prompt
    contract callers rely on (a failed generation yields ``""`` -> scored as a bad sample).
    """
    n = len(prompts)
    if n == 0:
        return []
    if n > 1:
        try:
            from mlx_lm import batch_generate  # type: ignore[import-not-found]

            token_ids = [tokenizer.encode(p) for p in prompts]
            resp = batch_generate(model, tokenizer, token_ids, max_tokens=max_tokens, sampler=sampler, verbose=False)
            texts = list(resp.texts)
            if len(texts) == n:
                return texts
        except Exception:
            pass  # version-robust: fall through to the sequential path
    from mlx_lm import generate  # type: ignore[import-not-found]

    out: list[str] = []
    for p in prompts:
        try:
            out.append(generate(model, tokenizer, prompt=p, max_tokens=max_tokens, verbose=False, sampler=sampler))
        except Exception:
            out.append("")
    return out


def _assess_mlxlm(
    *,
    base_model: str,
    adapter_dir: Path,
    scenario: Any,
    task_prompt: str,
    n_samples: int,
    temperature: float,
    top_k: int,
    score_conditioned: bool,
    eval_state: Any = None,
    collect_path: Path | None = None,
) -> dict[str, float]:
    from mlx_lm import load  # type: ignore[import-not-found]
    from mlx_lm.sample_utils import make_sampler  # type: ignore[import-not-found]

    loaded = load(base_model, adapter_path=str(adapter_dir))
    model, tokenizer = loaded[0], loaded[1]
    base_prompt = format_assess_prompt(tokenizer, task_prompt, score_conditioned=score_conditioned)
    is_game = hasattr(scenario, "execute_match")
    has_task_prompt = callable(getattr(scenario, "get_task_prompt", None))
    # Honor the requested assessment sampling (temp<=0 => greedy; top_k truncation).
    sampler = make_sampler(temp=max(float(temperature), 0.0), top_k=int(top_k))

    # Resolve every sample's (instruction, state, chat prompt) up front so generation can run as a
    # single batch -- the assessment bottleneck -- instead of one forward pass at a time. Game
    # scenarios reuse one prompt; dataset-style agent tasks (e.g. GSM8K) draw a possibly-different
    # problem per sample so the loop explores the distribution; resolve state + prompt TOGETHER so
    # evaluate_output scores against -- and the collected sample carries -- the exact problem it was
    # generated for (fixed single-task scenarios just reuse one problem).
    specs: list[tuple[str, Any, str]] = []  # (sample_instr, state_i, chat_prompt)
    for i in range(max(1, n_samples)):
        if is_game:
            specs.append((task_prompt, None, base_prompt))
        else:
            try:
                state_i = scenario.initial_state(seed=i)
            except Exception:
                state_i = eval_state
            sample_instr = scenario.get_task_prompt(state_i) if has_task_prompt else task_prompt
            chat_prompt = format_assess_prompt(tokenizer, sample_instr, score_conditioned=score_conditioned)
            specs.append((sample_instr, state_i, chat_prompt))

    texts = _mlxlm_generate_texts(
        model, tokenizer, [chat_prompt for _instr, _state, chat_prompt in specs], sampler=sampler, max_tokens=512
    )

    scores: list[float] = []
    valid = 0
    collected: list[dict[str, Any]] = []  # {prompt, strategy, score} for the ReST-EM self-improving loop
    for (sample_instr, state_i, _chat_prompt), text in zip(specs, texts, strict=True):
        try:
            if is_game:
                # Reason-trained models emit `rationale\n{...}`; extract the trailing
                # JSON object robustly (a bare extract_strategy returns None on prose).
                strategy: Any = extract_json_object(text)
                if strategy is None:
                    continue
                score = scenario.execute_match(strategy, seed=0).score
            else:
                # Agent-task scenarios score the raw text; the text IS the sample to retrain on.
                strategy = text
                score = scenario.evaluate_output(output=text, state=state_i).score
            # Count valid only after scoring succeeds, so a scoring error (caught below) can't
            # inflate valid_rate while no sample was actually collected.
            valid += 1
            scores.append(score)
            if collect_path is not None:
                # Carry the per-sample prompt so a later ReST-EM round retrains the answer against
                # the problem it was scored on, not the fallback scenario prompt.
                collected.append({"prompt": sample_instr, "strategy": strategy, "score": float(score)})
        except Exception:
            continue
    if collect_path is not None:
        collect_path.parent.mkdir(parents=True, exist_ok=True)
        collect_path.write_text("\n".join(json.dumps(s) for s in collected) + "\n", encoding="utf-8")
    return {
        "avg_score": sum(scores) / len(scores) if scores else 0.0,
        "valid_rate": valid / n_samples if n_samples > 0 else 0.0,
    }
