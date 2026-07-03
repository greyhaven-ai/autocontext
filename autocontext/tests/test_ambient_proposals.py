from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.proposals import CharterProposal, ProposalError, ProposalStore, apply_proposal


def _charter() -> Charter:
    return Charter(
        tier="oss",
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
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=10.0),
    )


def _add_target_proposal(name: str = "t2") -> CharterProposal:
    return CharterProposal(
        proposal_id="p-1",
        kind="add_target",
        payload=dict(
            name=name,
            kind="task_family",
            selector="lean_putnam_proof",
            base_model="Qwen/Qwen2.5-3B-Instruct",
            method="sft-distill",
            min_dataset_records=1000,
            eval_suite="lean_holdout",
        ),
        rationale="4100 lean traces at 92 percent pass rate",
    )


def test_apply_add_target_returns_new_charter() -> None:
    charter = _charter()
    updated = apply_proposal(charter, _add_target_proposal())
    assert [t.name for t in updated.targets] == ["t1", "t2"]
    assert [t.name for t in charter.targets] == ["t1"]


def test_apply_duplicate_target_name_raises() -> None:
    with pytest.raises(ProposalError, match="already exists"):
        apply_proposal(_charter(), _add_target_proposal(name="t1"))


def test_apply_update_budgets() -> None:
    proposal = CharterProposal(
        proposal_id="p-2",
        kind="update_budgets",
        payload=dict(gpu_hours_per_window=4.0, window_hours=24, disk_quota_gb=50.0),
        rationale="raise training budget",
    )
    updated = apply_proposal(_charter(), proposal)
    assert updated.budgets.gpu_hours_per_window == pytest.approx(4.0)


def test_store_append_pending_and_mark(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.jsonl")
    store.append(_add_target_proposal())
    assert [p.proposal_id for p in store.pending()] == ["p-1"]
    store.mark("p-1", "applied")
    assert store.pending() == []


def test_store_survives_torn_trailing_line(tmp_path: Path) -> None:
    store = ProposalStore(tmp_path / "proposals.jsonl")
    store.append(_add_target_proposal())
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write('{"proposal_id": "p-torn", "kin')  # crash mid-append
    assert [p.proposal_id for p in store.pending()] == ["p-1"]
    store.mark("p-1", "applied")
    assert store.pending() == []
