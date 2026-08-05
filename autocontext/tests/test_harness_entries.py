"""Tests for typed harness entries (AC-898)."""

from __future__ import annotations

import pytest
from autocontext.knowledge.harness_entries import (
    SCOPE_ORDER,
    HarnessEdit,
    HarnessEntry,
    HarnessRefinement,
)
from pydantic import ValidationError


def _entry(**overrides: object) -> HarnessEntry:
    data: dict[str, object] = {
        "id": "harness_abc12345",
        "kind": "policy",
        "scope": "run",
        "title": "Prefer coset seeds",
        "content": "Seed the search from the n=4 coset construction.",
    }
    data.update(overrides)
    return HarnessEntry.model_validate(data)


class TestModels:
    def test_entry_defaults(self) -> None:
        entry = _entry()
        assert entry.expected_outcome == ""
        assert entry.outcome == "pending"
        assert entry.version == 1

    def test_entry_rejects_unknown_kind(self) -> None:
        with pytest.raises(ValidationError):
            _entry(kind="vibe")

    def test_entry_rejects_unknown_scope(self) -> None:
        with pytest.raises(ValidationError):
            _entry(scope="galaxy")

    def test_edit_requires_action(self) -> None:
        with pytest.raises(ValidationError):
            HarnessEdit.model_validate({"kind": "policy"})

    def test_scope_order_is_monotone(self) -> None:
        assert SCOPE_ORDER["run"] < SCOPE_ORDER["scenario_family"] < SCOPE_ORDER["global"]

    def test_refinement_round_trips_json(self) -> None:
        refinement = HarnessRefinement(
            id="refinement_00000001",
            scope="run",
            summary="test",
            applied_edits=[],
            created_at="2026-08-05T00:00:00+00:00",
        )
        parsed = HarnessRefinement.model_validate_json(refinement.model_dump_json())
        assert parsed == refinement
