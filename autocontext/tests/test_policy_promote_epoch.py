"""AC-885 Slice C2: the autonomy dial recognizes the ``promote_epoch`` action.

``promote_epoch`` follows the same rules as ``promote``: propose and train
autonomy require approval, full autonomy is autonomous. ``decide`` needs no
logic change because its ``train`` branch already treats any non-``train``
action as requiring approval.
"""

from __future__ import annotations

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.policy import decide


def _charter(autonomy: str) -> Charter:
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
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=10.0),
    )


def test_promote_epoch_autonomy_matrix() -> None:
    assert decide(_charter("propose"), "promote_epoch", "t1").requires_approval is True
    assert decide(_charter("train"), "promote_epoch", "t1").requires_approval is True
    assert decide(_charter("full"), "promote_epoch", "t1").requires_approval is False
