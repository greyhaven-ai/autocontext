"""TRL cross-platform backend seams (CI-safe: TRL does the numerics, so the config /
dataset / reward builders need no torch/trl and run everywhere; the trainer-instantiating
runner imports them lazily)."""

from __future__ import annotations

import pytest

from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class _TargetScenario(AgentTaskInterface):
    """Tiny state-parameterized agent task: output must echo state['target']."""

    name = "trl_target"
    description = "echo the target integer"

    def initial_state(self, seed: int | None = None) -> dict:
        return {"target": (seed or 0) % 3}

    def get_task_prompt(self, state: dict | None = None) -> str:
        return f'return JSON {{"x": N}} with N={(state or {}).get("target")}'

    def evaluate_output(self, output: str, state: dict | None = None, **kwargs: object) -> AgentTaskResult:
        import json

        try:
            x = json.loads(output).get("x")
        except Exception:
            x = None
        return AgentTaskResult(score=1.0 if x == (state or {}).get("target") else 0.0, reasoning="")

    def get_rubric(self) -> str:
        return "1 if x == target else 0"

    def describe_task(self) -> str:
        return self.description


# ---------------------------------------------------------------------------
# Config kwargs builders
# ---------------------------------------------------------------------------


def test_gkd_config_kwargs_encode_on_policy_reverse_kl_defaults() -> None:
    from autocontext.training.autoresearch.trl_backend import build_gkd_config_kwargs

    cfg = build_gkd_config_kwargs(output_dir="/tmp/out", teacher_model="Org/Teacher-7B")
    assert cfg["teacher_model_name_or_path"] == "Org/Teacher-7B"
    assert cfg["lmbda"] == 1.0  # fully on-policy student rollouts
    assert cfg["beta"] == 1.0  # reverse KL
    assert cfg["output_dir"] == "/tmp/out"


def test_grpo_config_kwargs_have_generation_and_kl_fields() -> None:
    from autocontext.training.autoresearch.trl_backend import build_grpo_config_kwargs

    cfg = build_grpo_config_kwargs(output_dir="/tmp/out", num_generations=6)
    assert cfg["num_generations"] == 6
    assert cfg["beta"] == 0.0  # TRL KL-free default
    assert "max_completion_length" in cfg and "max_prompt_length" in cfg


# ---------------------------------------------------------------------------
# Dataset row builders
# ---------------------------------------------------------------------------


def test_chat_dataset_rows_are_messages_for_gkd() -> None:
    from autocontext.training.autoresearch.trl_backend import build_chat_dataset_rows

    rows = build_chat_dataset_rows(_TargetScenario(), 3)
    assert len(rows) == 3
    assert all(r["messages"][0]["role"] == "user" for r in rows)
    assert all(isinstance(r["messages"][0]["content"], str) and r["messages"][0]["content"] for r in rows)


def test_prompt_dataset_rows_have_prompt_and_answer_for_grpo() -> None:
    from autocontext.training.autoresearch.trl_backend import build_prompt_dataset_rows

    rows = build_prompt_dataset_rows(_TargetScenario(), 3)
    assert len(rows) == 3
    assert all("prompt" in r and "answer" in r for r in rows)


# ---------------------------------------------------------------------------
# Reward function (reuses the tested score_completions)
# ---------------------------------------------------------------------------


def test_reward_func_scores_against_per_instance_answer() -> None:
    import json

    from autocontext.training.autoresearch.trl_backend import make_reward_func

    reward = make_reward_func(_TargetScenario())
    answer = json.dumps({"target": 2})
    rewards = reward(
        prompts=["p", "p"],
        completions=['{"x": 2}', '{"x": 0}'],
        answer=[answer, answer],
    )
    assert rewards == [1.0, 0.0]  # first matches target 2, second does not


# ---------------------------------------------------------------------------
# Mode validation (runs before any heavy import)
# ---------------------------------------------------------------------------


def test_run_trl_training_rejects_unknown_mode() -> None:
    from autocontext.training.autoresearch.trl_backend import run_trl_training

    with pytest.raises(ValueError, match="mode"):
        run_trl_training(mode="bogus", scenario_name="trl_target", output_dir="/tmp/x")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Time-budget callback (needs transformers; skip if absent)
# ---------------------------------------------------------------------------


def test_time_budget_callback_stops_when_exceeded() -> None:
    pytest.importorskip("transformers")
    from transformers import TrainerControl

    from autocontext.training.autoresearch.trl_backend import make_time_budget_callback

    cb = make_time_budget_callback(0.0)  # already exhausted
    control = TrainerControl()
    cb.on_step_end(args=None, state=None, control=control)
    assert control.should_training_stop is True


# ---------------------------------------------------------------------------
# Backend registration (CI-safe: find_spec only, no platform lock)
# ---------------------------------------------------------------------------


def test_trl_backend_registered_and_cross_platform() -> None:
    from autocontext.training.backends import default_backend_registry

    backend = default_backend_registry().get("trl")
    assert backend is not None
    assert backend.name == "trl"
    assert "trl" in default_backend_registry().list_names()
    assert "trl" in str(backend.default_checkpoint_dir("grid_ctf"))
