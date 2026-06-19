"""OPD/GKD + GRPO matched-compute experiment protocol (AC-798)."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean
from typing import Any

ARMS = (
    "grpo",
    "full_opd",
    "positive_opd",
    "mixed_positive_opd_grpo",
)

REQUIRED_METRICS = (
    "final_score",
    "heldout_score",
    "response_length",
    "diversity",
    "entropy",
    "kl",
    "token_pressure",
    "cost_time",
)


def build_experiment_matrix(
    scenario: str,
    *,
    seeds: list[int] | tuple[int, ...] = (0, 1, 2),
    steps: list[int] | tuple[int, ...] = (1000, 2000),
    prompts: int = 384,
    student_model: str = "Qwen/Qwen2.5-1.5B-Instruct",
    teacher_model: str = "Qwen/Qwen2.5-3B-Instruct",
) -> dict[str, Any]:
    runs: list[dict[str, Any]] = []
    for max_steps in steps:
        for seed in seeds:
            for arm in ARMS:
                runs.append(
                    _run_spec(
                        arm,
                        scenario=scenario,
                        seed=seed,
                        max_steps=max_steps,
                        prompts=prompts,
                        student_model=student_model,
                        teacher_model=teacher_model,
                    )
                )
    return {
        "schema_version": 1,
        "scenario": scenario,
        "matched_compute": {"n_prompts": prompts, "steps": list(steps), "arms": list(ARMS)},
        "seed_notes": f"{len(seeds)} seeds: {', '.join(str(seed) for seed in seeds)}",
        "required_metrics": list(REQUIRED_METRICS),
        "promotion_policy": "Do not promote mixed mode unless held-out score improves without collapse.",
        "runs": runs,
    }


def _run_spec(
    arm: str,
    *,
    scenario: str,
    seed: int,
    max_steps: int,
    prompts: int,
    student_model: str,
    teacher_model: str,
) -> dict[str, Any]:
    mode = "grpo" if arm == "grpo" else "gkd"
    mixture = "positive_opd=0.5,grpo=0.5" if arm == "mixed_positive_opd_grpo" else ""
    pressure = arm in {"positive_opd", "mixed_positive_opd_grpo"}
    command = (
        "python -m autocontext.training.autoresearch.trl_backend "
        f"--mode {mode} --scenario {scenario} --student-model {student_model} "
        f"--teacher-model {teacher_model} --n-prompts {prompts} --max-steps {max_steps} --seed {seed}"
    )
    if pressure:
        command += " --positive-pressure"
    if mixture:
        command += f" --training-mixture {mixture}"
    return {
        "arm": arm,
        "scenario": scenario,
        "seed": seed,
        "max_steps": max_steps,
        "n_prompts": prompts,
        "student_model": student_model,
        "teacher_model": teacher_model,
        "trl_mode": mode,
        "positive_pressure": pressure,
        "training_mixture": mixture,
        "command": command,
        "required_metrics": list(REQUIRED_METRICS),
    }


def summarize_mixture_results(rows: list[dict[str, Any]], *, min_heldout_delta: float = 0.01) -> dict[str, Any]:
    arms: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        arms[str(row.get("arm", ""))].append(row)
    summaries = {arm: _summarize_arm(items) for arm, items in sorted(arms.items())}
    promotion = _promotion_decision(summaries, min_heldout_delta=min_heldout_delta)
    return {"schema_version": 1, "arms": summaries, "promotion": promotion}


def _summarize_arm(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "seed_count": len({row.get("seed") for row in rows}),
        "mean_final_score": _mean(rows, "final_score"),
        "mean_heldout_score": _mean(rows, "heldout_score"),
        "mean_response_length": _mean(rows, "response_length"),
        "mean_diversity": _mean(rows, "diversity"),
        "mean_entropy": _mean(rows, "entropy"),
        "mean_kl": _mean(rows, "kl"),
        "mean_token_pressure": _mean(rows, "token_pressure"),
        "mean_cost_time": _mean(rows, "cost_time"),
        "collapse_detected": any(_collapse(row) for row in rows),
    }


def _promotion_decision(summaries: dict[str, dict[str, Any]], *, min_heldout_delta: float) -> dict[str, Any]:
    mixed = summaries.get("mixed_positive_opd_grpo")
    baselines = [summary for arm, summary in summaries.items() if arm != "mixed_positive_opd_grpo"]
    if not mixed or not baselines:
        return {"promote_mixed": False, "reason": "missing_comparison"}
    if mixed.get("collapse_detected"):
        return {"promote_mixed": False, "reason": "collapse_detected"}
    mixed_score = float(mixed.get("mean_heldout_score") or 0.0)
    best_baseline = max(float(summary.get("mean_heldout_score") or 0.0) for summary in baselines)
    if mixed_score >= best_baseline + min_heldout_delta:
        return {"promote_mixed": True, "reason": "heldout_improved_without_collapse"}
    return {"promote_mixed": False, "reason": "heldout_not_improved"}


def render_protocol_report(matrix: dict[str, Any]) -> str:
    lines = [
        "# OPD/GKD + GRPO mixture experiment protocol",
        "",
        "Matched-compute arms: GRPO, full OPD/GKD, positive-pressure OPD, and mixed positive-pressure OPD + GRPO.",
        "Compare against AC-787/AC-789 methodology where applicable.",
        "",
        "Required result fields:",
    ]
    lines.extend(f"- {metric}" for metric in matrix["required_metrics"])
    lines.extend(["", "Promotion rule: " + matrix["promotion_policy"], "", "Run commands:"])
    lines.extend(f"- `{run['command']}`" for run in matrix["runs"])
    return "\n".join(lines)


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if isinstance(row.get(key), int | float)]
    return round(fmean(values), 6) if values else None


def _collapse(row: dict[str, Any]) -> bool:
    entropy = row.get("entropy")
    diversity = row.get("diversity")
    if isinstance(entropy, int | float) and entropy < 0.5:
        return True
    if isinstance(diversity, int | float) and diversity < 0.05:
        return True
    return bool(row.get("collapse_detected"))
