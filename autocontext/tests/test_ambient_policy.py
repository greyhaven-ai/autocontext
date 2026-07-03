from __future__ import annotations

import pytest

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.policy import PolicyDecision, budget_allows, decide, effective_autonomy


def _charter(autonomy: str = "propose", target_autonomy: str | None = None) -> Charter:
    return Charter(
        tier="oss",
        autonomy=autonomy,  # type: ignore[arg-type]
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                min_dataset_records=10,
                eval_suite="grid_ctf_holdout",
                autonomy=target_autonomy,  # type: ignore[arg-type]
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=10.0),
    )


def test_effective_autonomy_global_default() -> None:
    assert effective_autonomy(_charter("train"), "t1") == "train"


def test_effective_autonomy_target_override_wins() -> None:
    assert effective_autonomy(_charter("propose", target_autonomy="full"), "t1") == "full"


def test_effective_autonomy_unknown_target_raises() -> None:
    with pytest.raises(KeyError):
        effective_autonomy(_charter(), "missing")


@pytest.mark.parametrize(
    ("autonomy", "action", "allowed", "requires_approval"),
    [
        ("propose", "train", True, True),
        ("propose", "promote", True, True),
        ("train", "train", True, False),
        ("train", "promote", True, True),
        ("full", "train", True, False),
        ("full", "promote", True, False),
    ],
)
def test_decide_matrix(autonomy: str, action: str, allowed: bool, requires_approval: bool) -> None:
    decision = decide(_charter(autonomy), action, "t1")  # type: ignore[arg-type]
    assert isinstance(decision, PolicyDecision)
    assert decision.allowed is allowed
    assert decision.requires_approval is requires_approval
    assert decision.reason


def test_budget_allows_within_window() -> None:
    budgets = CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=10.0)
    assert budget_allows(budgets, used_gpu_hours_in_window=5.0, requested_gpu_hours=3.0) is True
    assert budget_allows(budgets, used_gpu_hours_in_window=5.0, requested_gpu_hours=3.1) is False
