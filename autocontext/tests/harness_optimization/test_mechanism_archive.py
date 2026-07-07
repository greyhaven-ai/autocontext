import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.harness_optimization.contract.models import (
    FrontierMechanism,
    OrphanMechanism,
)
from autocontext.harness_optimization.mechanism_archive import (
    MechanismArchive,
    add_orphan,
    prune_orphans,
    query,
    rank_orphans,
    rescue_orphan,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "harness-optimization" / "mechanism-archive" / "archive-cases.json"

_FIXTURE = json.loads(FIX.read_text())


def _seed_archive() -> MechanismArchive:
    seed = _FIXTURE["seed"]
    return MechanismArchive(
        frontier=tuple(FrontierMechanism.model_validate(item) for item in seed["frontier"]),
        orphans=tuple(OrphanMechanism.model_validate(item) for item in seed["orphans"]),
    )


def _case_id(case: dict[str, Any]) -> str:
    return str(case["name"])


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=[_case_id(c) for c in _FIXTURE["cases"]])
def test_archive_case(case: dict[str, Any]) -> None:
    archive = _seed_archive()
    op = case["op"]

    if op == "add_orphan":
        added = add_orphan(archive, OrphanMechanism.model_validate(case["orphan"]))
        assert len(added.orphans) == case["expected_orphan_count"]
        assert added.orphans[-1].mechanism_id == case["expected_added_orphan_id"]
        # Input archive is not mutated.
        assert len(archive.orphans) == len(_FIXTURE["seed"]["orphans"])

    elif op == "query_by_type":
        result = query(archive, mechanism_type=case["mechanism_type"])
        assert [m.mechanism_id for m in result["frontier"]] == case["expected_frontier_ids"]
        assert [m.mechanism_id for m in result["orphans"]] == case["expected_orphan_ids"]

    elif op == "rank_orphans":
        ranked = rank_orphans(archive.orphans)
        assert [m.mechanism_id for m in ranked] == case["expected_order"]

    elif op == "rescue":
        rescued = rescue_orphan(archive, case["orphan_id"], case["into_frontier_id"])
        target = next(m for m in rescued.orphans if m.mechanism_id == case["orphan_id"])
        assert target.rescued_into_frontier_id == case["expected_rescued_into_frontier_id"]
        ranked = rank_orphans(rescued.orphans)
        assert [m.mechanism_id for m in ranked] == case["expected_order"]
        assert ranked[-1].mechanism_id == case["expected_last_orphan_id"]

    elif op == "prune":
        pruned = prune_orphans(archive.orphans, case["max_orphans"])
        assert [m.mechanism_id for m in pruned] == case["expected_surviving_ids"]

    elif op == "digest":
        pytest.skip("digest is driven by task 4")

    else:  # pragma: no cover - guards against an unhandled fixture op
        pytest.fail(f"unhandled op: {op}")
