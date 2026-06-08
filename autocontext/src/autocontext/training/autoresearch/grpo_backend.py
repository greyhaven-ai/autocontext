"""GRPO/GSPO RLVR backend: online RL from the scenario verifier (wraps mlx-lm-lora).

The other backends use the verifier OFFLINE (filter/condition/weight an SFT dataset).
This backend uses it ONLINE as a reward: mlx-lm-lora samples a group of completions
per prompt, scores each with ``score_completions`` (the scenario's ``execute_match`` /
``evaluate_output``), and takes a GRPO-family policy-gradient step. The verifier is the
reward, so no labelled answers are needed.

Variant selection (verified against mlx-lm-lora 2.1.0 flags):
- ``grpo``    : token-level importance sampling, GRPO loss (baseline).
- ``dr_grpo`` : Dr.GRPO loss (removes GRPO length / std-normalization bias).
- ``gspo``    : sequence-level importance sampling (Qwen GSPO; the recommended default
                for stability, esp. on longer generations).
- ``dapo``    : GRPO loss + clip-higher (decoupled upper clip epsilon).

Gated behind the ``grpo`` optional-dependency extra (``mlx-lm-lora``). The actual RL run
needs a capable-enough base and an in-reach scenario (small/weak bases hit a documented
capability ceiling); see the validation notes in the PR.
"""

from __future__ import annotations

import importlib.util
import json
from typing import Any

from autocontext.training.autoresearch.sequence_format import extract_json_object

HAS_MLX_LM_LORA = importlib.util.find_spec("mlx_lm_lora") is not None

# Capable instruct base by default (small bases hit the RLVR capability ceiling).
DEFAULT_BASE_MODEL = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
DEFAULT_VARIANT = "gspo"
REWARD_NAME = "autocontext_verifier"

# variant -> mlx-lm-lora GRPO flags. GSPO is sequence-level importance sampling;
# Dr.GRPO swaps the loss; DAPO adds clip-higher (handled in grpo_cli_args).
_VARIANT_FLAGS: dict[str, dict[str, str]] = {
    "grpo": {"--grpo-loss-type": "grpo", "--importance-sampling-level": "token"},
    "dr_grpo": {"--grpo-loss-type": "dr_grpo", "--importance-sampling-level": "token"},
    "gspo": {"--grpo-loss-type": "grpo", "--importance-sampling-level": "sequence"},
    "dapo": {"--grpo-loss-type": "grpo", "--importance-sampling-level": "token"},
}
_DAPO_EPSILON_HIGH = 0.28  # DAPO clip-higher default


# ---------------------------------------------------------------------------
# Reward adapter (the core: model completions -> scalar verifier rewards)
# ---------------------------------------------------------------------------
def score_completions(scenario: Any, completions: list[str], *, answers: list[str] | None = None, seed: int = 0) -> list[float]:
    """Score each completion with the scenario verifier; the GRPO reward function.

    Robust to reason-then-construct output (extracts the JSON construction via the
    shared ``extract_json_object``). Game scenarios are scored by ``execute_match``,
    agent-task scenarios by ``evaluate_output``. Unparseable output or a throwing
    verifier scores 0.0 (never propagates, so one bad rollout can't kill training).

    When ``answers`` is given (aligned to ``completions``), each completion is verified
    against ITS OWN instance state (decoded from the answer field that
    :func:`build_prompt_rows` wrote), so prompt-diverse GRPO scores each rollout against
    the instance it was sampled for. Without it, a single resolved state is used.
    """
    is_game = hasattr(scenario, "execute_match")
    shared_state = {} if is_game else _resolve_state(scenario, seed)
    scores: list[float] = []
    for i, completion in enumerate(completions):
        strategy = extract_json_object(completion)
        if not isinstance(strategy, dict):
            scores.append(0.0)
            continue
        state = _decode_answer_state(answers[i]) if answers and i < len(answers) else shared_state
        try:
            if is_game:
                scores.append(float(scenario.execute_match(strategy, seed=seed).score))
            else:
                scores.append(float(scenario.evaluate_output(output=json.dumps(strategy), state=state).score))
        except Exception:
            scores.append(0.0)
    return scores


def _decode_answer_state(answer: Any) -> dict:
    """Decode the per-instance state JSON written into a prompt row's ``answer`` field."""
    if isinstance(answer, dict):
        return answer
    if isinstance(answer, str) and answer.strip():
        try:
            obj = json.loads(answer)
            return obj if isinstance(obj, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _resolve_state(scenario: Any, seed: int) -> dict:
    """Best-effort initial state for an agent-task scenario's evaluate_output(state=...)."""
    init = getattr(scenario, "initial_state", None)
    if callable(init):
        for call in (lambda: init(seed), lambda: init()):
            try:
                result = call()
                return result if isinstance(result, dict) else {}
            except TypeError:
                continue
            except Exception:
                return {}
    return {}


# ---------------------------------------------------------------------------
# CLI args, dataset, and reward-file generation (pure)
# ---------------------------------------------------------------------------
def grpo_cli_args(
    *,
    base_model: str,
    data_dir: str,
    reward_file: str,
    reward_name: str,
    adapter_dir: str,
    variant: str = DEFAULT_VARIANT,
    group_size: int = 8,
    iters: int = 100,
    batch_size: int = 2,
    learning_rate: float = 1e-5,
    train_type: str = "lora",
    epsilon: float = 0.2,
    epsilon_high: float | None = None,
    num_layers: int = 8,
    resume_adapter_file: str | None = None,
) -> list[str]:
    """Build the ``mlx_lm_lora.train`` argument list for a GRPO-family run.

    ``resume_adapter_file`` resumes from a prior adapter (the distillation cold-start),
    so RLVR continues from it instead of restarting from the base model -- the chaining
    that makes the R1 recipe (distill -> RLVR) end-to-end.
    """
    if variant not in _VARIANT_FLAGS:
        raise ValueError(f"unknown GRPO variant {variant!r}; expected one of {sorted(_VARIANT_FLAGS)}")
    args = [
        "--model",
        base_model,
        "--train",
        "--train-mode",
        "grpo",
        "--train-type",
        train_type,
        "--data",
        data_dir,
        "--adapter-path",
        adapter_dir,
        "--reward-functions-file",
        reward_file,
        "--reward-functions",
        reward_name,
        "--group-size",
        str(group_size),
        "--iters",
        str(iters),
        "--batch-size",
        str(batch_size),
        "--learning-rate",
        str(learning_rate),
        "--num-layers",
        str(num_layers),
        "--epsilon",
        str(epsilon),
    ]
    for flag, value in _VARIANT_FLAGS[variant].items():
        args += [flag, value]
    eps_high = epsilon_high if epsilon_high is not None else (_DAPO_EPSILON_HIGH if variant == "dapo" else None)
    if eps_high is not None:
        args += ["--epsilon-high", str(eps_high)]
    if resume_adapter_file:
        args += ["--resume-adapter-file", resume_adapter_file]
    return args


def build_prompt_rows(scenario: Any, n_prompts: int) -> list[dict[str, str]]:
    """Build GRPO prompt rows, one per seed, so GRPO trains over DIVERSE instances.

    For each seed the scenario's ``initial_state(seed)`` produces an instance state and
    ``get_task_prompt(state)`` its prompt; the state is serialized into the ``answer``
    field so the reward can verify each completion against its own instance (see
    ``score_completions``).

    Invariant: a given prompt is always paired with ONE canonical state (the first one
    seen for it). Otherwise a scenario whose prompt does not reflect the varied state
    would emit identical prompts with differing states, and the reward would hand GRPO
    contradictory rewards for the same visible (prompt, completion) -- a hidden label the
    model cannot observe. So genuinely-diverse prompts (state reflected in the text) keep
    their own states, while fixed prompts collapse to a single consistent state (the
    prior single-prompt behavior).
    """
    from autocontext.training.autoresearch.mlxlm_backend import scenario_task_prompt

    get_task_prompt = getattr(scenario, "get_task_prompt", None)
    canonical_state: dict[str, dict] = {}
    rows: list[dict[str, str]] = []
    for i in range(max(0, n_prompts)):
        state = _resolve_state(scenario, i)
        if callable(get_task_prompt):
            try:
                prompt = str(get_task_prompt(state))
            except Exception:
                prompt = scenario_task_prompt(scenario)
        else:
            prompt = scenario_task_prompt(scenario)
        # first state seen for a prompt wins, so identical prompts never carry conflicting state
        state = canonical_state.setdefault(prompt, state)
        rows.append({"prompt": prompt, "answer": json.dumps(state, sort_keys=True)})
    return rows


def render_reward_module(scenario_name: str, *, reward_name: str = REWARD_NAME, register_import: str | None = None) -> str:
    """Generate the Python reward-functions file mlx-lm-lora loads at runtime.

    The file is thin: it registers a reward function that delegates to
    :func:`score_completions` (the tested core), so the construction-scoring logic is
    not duplicated in generated code. ``register_import`` lets a consumer register its
    own scenario into SCENARIO_REGISTRY before lookup (built-ins need no import).
    """
    registration = (register_import + "\n") if register_import else ""
    return (
        '"""Auto-generated reward functions for mlx-lm-lora GRPO (autocontext verifier)."""\n'
        "from mlx_lm_lora.trainer.grpo_reward_functions import register_reward_function\n"
        "from autocontext.scenarios import SCENARIO_REGISTRY\n"
        "from autocontext.training.autoresearch.grpo_backend import score_completions\n"
        f"{registration}"
        f"_SCENARIO = SCENARIO_REGISTRY[{scenario_name!r}]()\n\n\n"
        f"@register_reward_function({reward_name!r})\n"
        # **kwargs (not a fixed answers/types signature): mlx-lm-lora's trainer calls the
        # reward with answer= (singular) at runtime, despite the documented `answers`
        # type alias; only `completions` is needed, so absorb the rest defensively.
        "def _autocontext_verifier_reward(prompts=None, completions=None, **kwargs):\n"
        # mlx-lm-lora passes the per-completion instance states as answer= (singular);
        # forward them so each rollout is verified against its own diverse instance.
        "    answers = kwargs.get('answer') or kwargs.get('answers')\n"
        "    return score_completions(_SCENARIO, completions or [], answers=answers)\n"
    )


# ---------------------------------------------------------------------------
# Training run (gated; requires mlx-lm-lora + a base model)
# ---------------------------------------------------------------------------
def run_grpo_training(
    *,
    scenario_name: str,
    output_dir: Any,
    base_model: str = DEFAULT_BASE_MODEL,
    variant: str = DEFAULT_VARIANT,
    iters: int = 100,
    batch_size: int = 2,
    learning_rate: float = 1e-5,
    group_size: int = 8,
    train_type: str = "lora",
    num_layers: int = 8,
    n_prompts: int = 64,
    register_import: str | None = None,
    resume_adapter_file: str | None = None,
    assess_samples: int = 8,
    assess_temperature: float = 0.7,
    assess_top_k: int = 0,
    time_budget: int = 3600,
    memory_limit_mb: int = 16384,
    **_ignored: Any,
) -> dict[str, float]:
    """Run a GRPO-family RLVR finetune via mlx-lm-lora, scoring with the scenario verifier."""
    import subprocess
    import sys
    import time
    from pathlib import Path

    if not HAS_MLX_LM_LORA:
        raise RuntimeError("mlx-lm-lora is required for the GRPO backend; install with: uv pip install mlx-lm-lora")

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.mlxlm_backend import scenario_task_prompt

    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")
    scenario = SCENARIO_REGISTRY[scenario_name]()

    out = Path(output_dir)
    data_dir = out / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    rows = build_prompt_rows(scenario, n_prompts)
    n_val = max(group_size, 2)
    (data_dir / "train.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (data_dir / "valid.jsonl").write_text("\n".join(json.dumps(r) for r in rows[:n_val]) + "\n", encoding="utf-8")

    reward_file = out / "autocontext_reward.py"
    reward_file.write_text(render_reward_module(scenario_name, register_import=register_import), encoding="utf-8")

    adapter_dir = out / "adapters"
    args = grpo_cli_args(
        base_model=base_model,
        data_dir=str(data_dir),
        reward_file=str(reward_file),
        reward_name=REWARD_NAME,
        adapter_dir=str(adapter_dir),
        variant=variant,
        group_size=group_size,
        iters=iters,
        batch_size=batch_size,
        learning_rate=learning_rate,
        train_type=train_type,
        num_layers=num_layers,
        resume_adapter_file=resume_adapter_file,
    )
    started = time.perf_counter()
    result = subprocess.run(
        [sys.executable, "-m", "mlx_lm_lora.train", *args],
        capture_output=True,
        text=True,
        timeout=max(time_budget, 60),
    )
    if result.returncode != 0:
        raise RuntimeError(f"mlx-lm-lora GRPO training failed (exit {result.returncode}):\n{result.stderr[-2000:]}")

    # Assess the trained adapter in-scenario (reuse the mlx-lm assess path).
    from autocontext.training.autoresearch.mlxlm_backend import _assess_mlxlm
    from autocontext.training.autoresearch.train import _peak_memory_mb

    assess = _assess_mlxlm(
        base_model=base_model,
        adapter_dir=adapter_dir,
        scenario=scenario,
        task_prompt=scenario_task_prompt(scenario),
        n_samples=assess_samples,
        temperature=assess_temperature,
        top_k=assess_top_k,
        score_conditioned=False,
    )
    return _grpo_metrics(
        assess,
        iters=iters,
        training_seconds=time.perf_counter() - started,
        peak_memory_mb=min(_peak_memory_mb(), float(memory_limit_mb)),
        variant=variant,
    )


def _grpo_metrics(
    assess: dict[str, float], *, iters: int, training_seconds: float, peak_memory_mb: float, variant: str
) -> dict[str, float]:
    """Assemble the full metrics dict train.py's format_summary requires.

    The summary always prints peak_memory_mb / num_steps / num_params_m / depth, so a
    GRPO run must carry them or it crashes while printing the summary (and TrainingRunner
    then treats the run as failed). LoRA adapter params are small/model-dependent, and
    there is no from-scratch model depth, so num_params_m/depth are reported as 0.
    """
    variants = ["grpo", "dr_grpo", "gspo", "dapo"]
    return {
        "avg_score": float(assess.get("avg_score", 0.0)),
        "valid_rate": float(assess.get("valid_rate", 0.0)),
        "training_seconds": float(training_seconds),
        "peak_memory_mb": float(peak_memory_mb),
        "num_steps": float(iters),
        "num_params_m": 0.0,
        "depth": 0.0,
        "variant": float(variants.index(variant)) if variant in variants else -1.0,
    }
