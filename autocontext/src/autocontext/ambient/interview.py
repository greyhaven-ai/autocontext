"""interview wizard: turns a short q and a session into a valid charter."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from autocontext.ambient.charter import (
    AutonomyLevel,
    Charter,
    CharterBudgets,
    CharterSource,
    CharterTarget,
    DeploymentTier,
)

PromptFn = Callable[[str, str], str]


class InterviewAnswers(BaseModel):
    tier: DeploymentTier
    autonomy: AutonomyLevel
    enable_otel: bool
    enable_proxy: bool
    first_target_role: str
    base_model: str
    gpu_hours_per_window: float
    window_hours: int
    disk_quota_gb: float


def build_charter(answers: InterviewAnswers) -> Charter:
    sources = [CharterSource(name="native", kind="autocontext")]
    if answers.enable_otel:
        sources.append(CharterSource(name="otel", kind="otel"))
    if answers.enable_proxy:
        sources.append(CharterSource(name="proxy", kind="proxy"))
    role = answers.first_target_role.split("@", 1)[0]
    target = CharterTarget(
        name=answers.first_target_role.replace("@", "-"),
        kind="role",
        selector=answers.first_target_role,
        base_model=answers.base_model,
        min_dataset_records=500,
        eval_suite=f"{role}_holdout",
    )
    budgets = CharterBudgets(
        gpu_hours_per_window=answers.gpu_hours_per_window,
        window_hours=answers.window_hours,
        disk_quota_gb=answers.disk_quota_gb,
    )
    return Charter(tier=answers.tier, autonomy=answers.autonomy, sources=sources, targets=[target], budgets=budgets)


def _yes(value: str) -> bool:
    return value.strip().lower() in ("y", "yes", "true", "1")


def run_interview(prompt: PromptFn) -> InterviewAnswers:
    return InterviewAnswers(
        tier=prompt("deployment tier (oss | hosted-box)", "oss"),  # type: ignore[arg-type]
        autonomy=prompt("autonomy (propose | train | full)", "propose"),  # type: ignore[arg-type]
        enable_otel=_yes(prompt("enable otel source? (y/n)", "n")),
        enable_proxy=_yes(prompt("enable llm proxy source? (y/n)", "n")),
        first_target_role=prompt("first target role (role@scenario)", "competitor@grid_ctf"),
        base_model=prompt("base model", "Qwen/Qwen2.5-3B-Instruct"),
        gpu_hours_per_window=float(prompt("gpu hours per window", "8")),
        window_hours=int(prompt("window hours", "24")),
        disk_quota_gb=float(prompt("disk quota gb", "200")),
    )
