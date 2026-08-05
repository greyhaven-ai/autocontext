"""Tests for typed harness entries (AC-898)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.knowledge.harness_entries import (
    SCOPE_ORDER,
    HarnessEdit,
    HarnessEntry,
    HarnessEntryStore,
    HarnessRefinement,
)


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


class TestStoreCrud:
    def test_create_assigns_id_scope_timestamps(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path, now_iso=lambda: "T0")
        refinement = store.apply(
            [HarnessEdit(action="create", kind="fact", title="t", content="c")],
            scope="run",
            summary="first",
            source="run_123",
        )
        [applied] = refinement.applied_edits
        assert applied.applied and applied.error == ""
        entry = store.entries()[0]
        assert entry.id == applied.entry_id and entry.id.startswith("harness_")
        assert entry.scope == "run" and entry.source == "run_123"
        assert entry.created_at == "T0" and entry.updated_at == "T0" and entry.version == 1

    def test_update_bumps_version_and_keeps_unset_fields(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path, now_iso=lambda: "T0")
        created = store.apply(
            [HarnessEdit(action="create", kind="policy", title="t", content="c", expected_outcome="e")],
            scope="run",
        )
        entry_id = created.applied_edits[0].entry_id
        store.apply([HarnessEdit(action="update", kind="policy", id=entry_id, content="c2")], scope="run")
        entry = store.entries()[0]
        assert entry.content == "c2" and entry.title == "t" and entry.expected_outcome == "e"
        assert entry.version == 2

    def test_delete_removes_entry(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        created = store.apply([HarnessEdit(action="create", kind="fact", title="t", content="c")], scope="run")
        entry_id = created.applied_edits[0].entry_id
        store.apply([HarnessEdit(action="delete", kind="fact", id=entry_id)], scope="run")
        assert store.entries() == []

    def test_duplicate_create_and_missing_update_record_errors(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", id="harness_dup", title="t", content="c")], scope="run")
        dup = store.apply([HarnessEdit(action="create", kind="fact", id="harness_dup", title="t", content="c")], scope="run")
        assert not dup.applied_edits[0].applied and dup.applied_edits[0].error == "duplicate_id"
        missing = store.apply([HarnessEdit(action="update", kind="fact", id="harness_nope", content="x")], scope="run")
        assert not missing.applied_edits[0].applied and missing.applied_edits[0].error == "not_found"

    def test_entries_filters_by_kind_and_scope(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", title="f", content="c")], scope="run")
        store.apply([HarnessEdit(action="create", kind="policy", title="p", content="c")], scope="global")
        assert [e.kind for e in store.entries(kind="policy")] == ["policy"]
        assert [e.scope for e in store.entries(scope="global")] == ["global"]


class TestPersistenceHardening:
    def test_corrupt_state_degrades_to_empty(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.state_path.parent.mkdir(parents=True, exist_ok=True)
        store.state_path.write_text("{not json", encoding="utf-8")
        assert store.entries() == []
        store.apply([HarnessEdit(action="create", kind="fact", title="t", content="c")], scope="run")
        assert len(store.entries()) == 1

    def test_state_write_leaves_no_temp_files(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", title="t", content="c")], scope="run")
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_history_appends_and_skips_malformed_lines(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", title="t", content="c")], scope="run", summary="one")
        with store.history_path.open("a", encoding="utf-8") as fh:
            fh.write("{torn line\n")
        store.apply([HarnessEdit(action="create", kind="fact", title="t2", content="c2")], scope="run", summary="two")
        history = store.load_history()
        assert [r.summary for r in history] == ["one", "two"]
