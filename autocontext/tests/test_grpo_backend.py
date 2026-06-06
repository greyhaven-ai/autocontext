"""GRPO/GSPO RLVR backend (wraps mlx-lm-lora; reward = the scenario verifier).

These cover the pure, MLX-free pieces:
- score_completions: the reward adapter that turns model completions into scalar
  rewards via the scenario verifier (execute_match for games, evaluate_output for
  agent tasks), robust to reason-then-construct output.
- grpo_cli_args: maps a variant (grpo / dr_grpo / gspo / dapo) onto mlx-lm-lora CLI
  flags (--importance-sampling-level sequence = GSPO, --grpo-loss-type dr_grpo, etc.).
- render_reward_module / build_prompt_rows: dataset + reward-file generation.
- GRPOBackend registry registration.
"""

from __future__ import annotations

from autocontext.scenarios.agent_task import AgentTaskResult


class _AgentScenario:
    """Agent-task scenario: score = (#points)/10 (cap 1.0); parses JSON from output."""

    name = "toy_agent"
    description = "toy"

    def initial_state(self, seed=None):
        return {"seeded": True}

    def get_task_prompt(self, state=None):
        return "Build a set of integers as JSON."

    def evaluate_output(self, output, state, **kwargs):
        # state is REQUIRED (no default), mirroring AgentTaskInterface; a caller that
        # omits it raises TypeError. Compliant evaluators also use state.
        import json

        assert state == {"seeded": True}, "scenario state must be passed through to evaluate_output"
        try:
            pts = json.loads(output).get("points", [])
        except Exception:
            pts = []
        return AgentTaskResult(score=min(1.0, len(pts) / 10.0), reasoning="")


class _GameScenario:
    name = "toy_game"
    description = "toy game"

    def initial_state(self, seed=None):
        return {}

    def execute_match(self, strategy, seed=0):
        from autocontext.scenarios.base import Result

        pts = strategy.get("points", []) if isinstance(strategy, dict) else []
        return Result(score=min(1.0, len(pts) / 10.0), summary="m")


# ---------------------------------------------------------------------------
# score_completions: the reward adapter
# ---------------------------------------------------------------------------


def test_score_completions_agent_task_handles_reason_then_construct() -> None:
    from autocontext.training.autoresearch.grpo_backend import score_completions

    comps = [
        'reasoning first\n{"points": [1,2,3,4,5]}',  # 5 pts -> 0.5, despite the prose prefix
        '{"points": [1,2]}',  # 2 pts -> 0.2
        "no json here",  # unparseable -> 0.0
    ]
    assert score_completions(_AgentScenario(), comps) == [0.5, 0.2, 0.0]


def test_score_completions_game_via_execute_match() -> None:
    from autocontext.training.autoresearch.grpo_backend import score_completions

    comps = ['think\n{"points": [1,2,3,4,5,6,7,8,9,10]}', "garbage"]
    assert score_completions(_GameScenario(), comps) == [1.0, 0.0]


def test_score_completions_is_robust_to_verifier_errors() -> None:
    from autocontext.training.autoresearch.grpo_backend import score_completions

    class _Boom:
        name = "boom"

        def evaluate_output(self, output, state=None, **kwargs):
            raise RuntimeError("verifier blew up")

    # a parseable construction whose verifier throws scores 0.0, never propagates
    assert score_completions(_Boom(), ['{"points": [1]}']) == [0.0]


# ---------------------------------------------------------------------------
# grpo_cli_args: variant -> mlx-lm-lora flag mapping
# ---------------------------------------------------------------------------


def _arg_map(args: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, a in enumerate(args):
        if a.startswith("--") and i + 1 < len(args) and not args[i + 1].startswith("--"):
            out[a] = args[i + 1]
    return out


def _common_kwargs(**over):
    base = dict(
        base_model="mlx-community/Qwen2.5-1.5B-Instruct-4bit",
        data_dir="d",
        reward_file="r.py",
        reward_name="autocontext_verifier",
        adapter_dir="a",
        group_size=8,
        iters=100,
        batch_size=2,
        learning_rate=1e-5,
        train_type="lora",
    )
    base.update(over)
    return base


def test_grpo_cli_args_base_flags_present() -> None:
    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    m = _arg_map(grpo_cli_args(variant="grpo", **_common_kwargs()))
    assert m["--train-mode"] == "grpo"
    assert m["--train-type"] == "lora"
    assert m["--reward-functions-file"] == "r.py"
    assert m["--reward-functions"] == "autocontext_verifier"
    assert m["--group-size"] == "8"
    assert m["--model"].endswith("Qwen2.5-1.5B-Instruct-4bit")


def test_grpo_cli_args_gspo_sets_sequence_importance_sampling() -> None:
    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    m = _arg_map(grpo_cli_args(variant="gspo", **_common_kwargs()))
    assert m["--importance-sampling-level"] == "sequence"
    assert m["--grpo-loss-type"] == "grpo"


def test_grpo_cli_args_dr_grpo_sets_loss_type() -> None:
    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    m = _arg_map(grpo_cli_args(variant="dr_grpo", **_common_kwargs()))
    assert m["--grpo-loss-type"] == "dr_grpo"
    assert m["--importance-sampling-level"] == "token"


def test_grpo_cli_args_plain_grpo_is_token_level() -> None:
    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    m = _arg_map(grpo_cli_args(variant="grpo", **_common_kwargs()))
    assert m["--importance-sampling-level"] == "token"
    assert m["--grpo-loss-type"] == "grpo"


def test_grpo_cli_args_dapo_enables_clip_higher() -> None:
    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    args = grpo_cli_args(variant="dapo", **_common_kwargs())
    assert "--epsilon-high" in args  # DAPO clip-higher


def test_grpo_cli_args_rejects_unknown_variant() -> None:
    import pytest

    from autocontext.training.autoresearch.grpo_backend import grpo_cli_args

    with pytest.raises(ValueError, match="variant"):
        grpo_cli_args(variant="bogus", **_common_kwargs())


# ---------------------------------------------------------------------------
# reward-file + dataset generation
# ---------------------------------------------------------------------------


def test_render_reward_module_emits_compilable_registration() -> None:
    from autocontext.training.autoresearch.grpo_backend import render_reward_module

    src = render_reward_module("capset_n4", register_import="from capset_scenario import register; register(4)")
    compile(src, "<reward>", "exec")  # must be valid Python
    assert "register_reward_function" in src
    assert "capset_n4" in src
    assert "score_completions" in src
    assert "from capset_scenario import register; register(4)" in src


def test_build_prompt_rows_shape() -> None:
    from autocontext.training.autoresearch.grpo_backend import build_prompt_rows

    rows = build_prompt_rows(_AgentScenario(), 3)
    assert len(rows) == 3
    assert all(set(r.keys()) == {"prompt", "answer"} for r in rows)
    assert rows[0]["prompt"] == "Build a set of integers as JSON."


# ---------------------------------------------------------------------------
# backend registration
# ---------------------------------------------------------------------------


def test_grpo_backend_registered() -> None:
    from autocontext.training.backends import default_backend_registry

    backend = default_backend_registry().get("grpo")
    assert backend is not None
    assert backend.name == "grpo"


def test_grpo_metrics_has_all_summary_keys() -> None:
    """Reviewer P1: train.py's format_summary always prints peak_memory_mb / num_steps /
    num_params_m / depth, so the metrics dict must carry them or a successful run crashes
    while printing the summary (TrainingRunner then treats the baseline as failed)."""
    from autocontext.training.autoresearch.grpo_backend import _grpo_metrics

    m = _grpo_metrics(
        {"avg_score": 0.4, "valid_rate": 0.9},
        iters=120,
        training_seconds=12.3,
        peak_memory_mb=512.0,
        variant="gspo",
    )
    for key in ("avg_score", "valid_rate", "peak_memory_mb", "num_steps", "num_params_m", "depth", "training_seconds"):
        assert key in m, f"missing summary key: {key}"
    assert m["num_steps"] == 120.0
    assert m["avg_score"] == 0.4
