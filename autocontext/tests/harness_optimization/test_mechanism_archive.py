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
    render_archive_digest,
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

    elif op == "query_by_failure_family":
        result = query(archive, failure_family=case["failure_family"])
        # failure_family narrows orphans only; the frontier stays whole.
        assert [m.mechanism_id for m in result["frontier"]] == case["expected_frontier_ids"]
        assert [m.mechanism_id for m in result["orphans"]] == case["expected_orphan_ids"]

    elif op == "query_by_surface":
        result = query(archive, target_surface=case["target_surface"])
        assert [m.mechanism_id for m in result["frontier"]] == case["expected_frontier_ids"]
        assert [m.mechanism_id for m in result["orphans"]] == case["expected_orphan_ids"]

    elif op == "rescue_noop":
        rescued = rescue_orphan(archive, case["orphan_id"], case["into_frontier_id"])
        # An unknown orphan id leaves the archive unchanged: identical ids and no
        # new rescued_into_frontier_id set on anyone.
        assert [m.mechanism_id for m in rescued.orphans] == case["expected_orphan_ids"]
        before = {m.mechanism_id: m.rescued_into_frontier_id for m in archive.orphans}
        after = {m.mechanism_id: m.rescued_into_frontier_id for m in rescued.orphans}
        assert after == before

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
        max_entries = case["max_entries"]
        digest = render_archive_digest(archive, max_entries=max_entries)
        assert isinstance(digest, str)
        assert case["expected_header"] in digest
        lines = digest.splitlines()

        # Split into the frontier and orphan sections by their headers.
        frontier_start = lines.index("frontier:")
        orphan_start = lines.index("orphans (reusable):")
        frontier_items = [ln for ln in lines[frontier_start + 1 : orphan_start] if ln.startswith("  - ")]
        orphan_items = [ln for ln in lines[orphan_start + 1 :] if ln.startswith("  - ")]

        # Boundedness: never more than max_entries items per section.
        assert len(frontier_items) <= max_entries
        assert len(orphan_items) <= max_entries
        assert len(frontier_items) == case["expected_frontier_count"]

        # The orphan section lists exactly the expected ids in rank order.
        id_to_name = {o["mechanism_id"]: o["mechanism_name"] for o in _FIXTURE["seed"]["orphans"]}
        expected_names = [id_to_name[i] for i in case["expected_orphan_ids_in_order"]]
        rendered_orphan_names = [ln.split(" [", 1)[0].removeprefix("  - ") for ln in orphan_items]
        assert rendered_orphan_names == expected_names

        # With max_entries=1 each section holds at most one item and the orphan
        # section is exactly the top-ranked not-rescued orphan. This fails if the
        # per-section cap is ever dropped.
        if max_entries == 1:
            assert len(frontier_items) <= 1
            assert len(orphan_items) <= 1
            assert case["expected_orphan_ids_in_order"] == ["orphan-ac880-A"]
            assert rendered_orphan_names == [id_to_name["orphan-ac880-A"]]

        # When the fixture pins the exact string, the render must match it
        # character-for-character (parity with the TypeScript renderer).
        if "expected_digest" in case:
            assert digest == case["expected_digest"]

        # Rescued orphans are excluded entirely from the reusable section.
        orphan_section = "\n".join(lines[orphan_start:])
        for excluded_id in case["expected_excludes"]:
            assert id_to_name[excluded_id] not in orphan_section

    else:  # pragma: no cover - guards against an unhandled fixture op
        pytest.fail(f"unhandled op: {op}")


def test_digest_headers_only_for_non_positive_max_entries() -> None:
    # Mirrors the TypeScript suite: a non-positive max_entries emits the section
    # headers with no items.
    digest = render_archive_digest(_seed_archive(), max_entries=0)
    assert digest == "mechanism archive digest\nfrontier:\norphans (reusable):"
