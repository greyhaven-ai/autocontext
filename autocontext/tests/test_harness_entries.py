"""Tests for typed harness entries (AC-898)."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from autocontext.knowledge.harness_entries import (
    SCOPE_ORDER,
    HarnessEdit,
    HarnessEntry,
    HarnessEntryStore,
    HarnessRefinement,
    SkillReference,
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


class TestScopeGuardrails:
    def test_run_refinement_cannot_edit_global_entry(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        created = store.apply([HarnessEdit(action="create", kind="policy", title="g", content="c")], scope="global")
        entry_id = created.applied_edits[0].entry_id
        update = store.apply([HarnessEdit(action="update", kind="policy", id=entry_id, content="x")], scope="run")
        assert not update.applied_edits[0].applied and update.applied_edits[0].error == "scope_readonly"
        delete = store.apply([HarnessEdit(action="delete", kind="policy", id=entry_id)], scope="run")
        assert not delete.applied_edits[0].applied and delete.applied_edits[0].error == "scope_readonly"
        assert store.entries()[0].content == "c"

    def test_global_refinement_can_edit_run_entry(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        created = store.apply([HarnessEdit(action="create", kind="fact", title="r", content="c")], scope="run")
        entry_id = created.applied_edits[0].entry_id
        update = store.apply([HarnessEdit(action="update", kind="fact", id=entry_id, content="x")], scope="global")
        assert update.applied_edits[0].applied


class TestRollback:
    def test_rollback_inverts_create_update_delete(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        base = store.apply(
            [
                HarnessEdit(action="create", kind="fact", id="harness_keep", title="keep", content="v1"),
                HarnessEdit(action="create", kind="fact", id="harness_gone", title="gone", content="v1"),
            ],
            scope="run",
        )
        assert all(item.applied for item in base.applied_edits)
        batch = store.apply(
            [
                HarnessEdit(action="create", kind="policy", id="harness_new", title="new", content="c"),
                HarnessEdit(action="update", kind="fact", id="harness_keep", content="v2"),
                HarnessEdit(action="delete", kind="fact", id="harness_gone"),
            ],
            scope="run",
        )
        result = store.rollback(batch.id)
        assert result.rollback_of == batch.id
        by_id = {entry.id: entry for entry in store.entries()}
        assert set(by_id) == {"harness_keep", "harness_gone"}
        assert by_id["harness_keep"].content == "v1"
        assert by_id["harness_gone"].content == "v1"

    def test_rollback_unknown_id_raises(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        with pytest.raises(ValueError, match="unknown refinement"):
            store.rollback("refinement_nope")

    def test_rollback_is_itself_recorded(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        batch = store.apply([HarnessEdit(action="create", kind="fact", title="t", content="c")], scope="run")
        store.rollback(batch.id)
        history = store.load_history()
        assert len(history) == 2 and history[1].rollback_of == batch.id


class TestOutcomeAndRender:
    def test_mark_outcome_updates_entry(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path, now_iso=lambda: "T1")
        created = store.apply([HarnessEdit(action="create", kind="policy", title="t", content="c")], scope="run")
        entry_id = created.applied_edits[0].entry_id
        marked = store.mark_outcome(entry_id, "refuted", scope="run", evidence="score did not improve over 3 gens")
        assert marked.outcome == "refuted" and marked.outcome_evidence.startswith("score did not")
        assert marked.version == 2 and marked.updated_at == "T1"

    def test_mark_outcome_unknown_id_raises(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        with pytest.raises(ValueError, match="unknown harness entry"):
            store.mark_outcome("harness_nope", "confirmed", scope="run")

    def test_render_markdown_groups_by_kind_and_hides_refuted(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply(
            [
                HarnessEdit(
                    action="create",
                    kind="policy",
                    id="harness_p1",
                    title="P",
                    content="line1\nline2",
                    expected_outcome="score rises",
                ),
                HarnessEdit(action="create", kind="fact", id="harness_f1", title="F", content="fact"),
                HarnessEdit(action="create", kind="fact", id="harness_f2", title="Bad", content="wrong"),
            ],
            scope="run",
        )
        store.mark_outcome("harness_f2", "refuted", scope="run")
        text = store.render_markdown()
        assert "## Harness Entries" in text
        assert "### Policies" in text and "### Facts" in text
        assert "line1\n  line2" in text
        assert "(expected: score rises)" in text
        assert "Bad" not in text

    def test_render_markdown_empty_store_is_empty_string(self, tmp_path) -> None:
        assert HarnessEntryStore(tmp_path).render_markdown() == ""


class TestReviewHardening:
    def test_mark_outcome_respects_scope_guardrail(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        created = store.apply([HarnessEdit(action="create", kind="policy", title="g", content="c")], scope="global")
        entry_id = created.applied_edits[0].entry_id
        with pytest.raises(ValueError, match="scope_readonly"):
            store.mark_outcome(entry_id, "refuted", scope="run")
        marked = store.mark_outcome(entry_id, "refuted", scope="global")
        assert marked.outcome == "refuted"

    def test_rollback_of_rollback_restores_original(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", id="harness_a", title="t", content="v1")], scope="run")
        batch = store.apply([HarnessEdit(action="update", kind="fact", id="harness_a", content="v2")], scope="run")
        first = store.rollback(batch.id)
        assert store.entries()[0].content == "v1"
        store.rollback(first.id)
        assert store.entries()[0].content == "v2"

    def test_rollback_preserves_marked_outcome(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="policy", id="harness_p", title="t", content="v1")], scope="run")
        batch = store.apply([HarnessEdit(action="update", kind="policy", id="harness_p", content="v2")], scope="run")
        store.mark_outcome("harness_p", "refuted", scope="run", evidence="did not deliver")
        store.rollback(batch.id)
        entry = store.entries()[0]
        assert entry.content == "v1"
        assert entry.outcome == "refuted" and entry.outcome_evidence == "did not deliver"
        assert "harness_p" not in store.render_markdown()

    def test_rollback_lost_update_semantics_pinned(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", id="harness_a", title="t", content="v1")], scope="run")
        mid = store.apply([HarnessEdit(action="update", kind="fact", id="harness_a", content="v2")], scope="run")
        store.apply([HarnessEdit(action="update", kind="fact", id="harness_a", content="v3")], scope="run")
        store.rollback(mid.id)
        assert store.entries()[0].content == "v1"

    def test_empty_apply_is_a_no_op(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        refinement = store.apply([], scope="run")
        assert refinement.applied_edits == []
        assert not store.state_path.exists()
        assert store.load_history() == []

    def test_partial_batch_failure_does_not_block_others(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        batch = store.apply(
            [
                HarnessEdit(action="update", kind="fact", id="harness_nope", content="x"),
                HarnessEdit(action="create", kind="fact", id="harness_ok", title="t", content="c"),
            ],
            scope="run",
        )
        assert [item.applied for item in batch.applied_edits] == [False, True]
        assert [entry.id for entry in store.entries()] == ["harness_ok"]

    def test_state_entries_rekeyed_by_entry_id(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", id="harness_real", title="t", content="c")], scope="run")
        raw = json.loads(store.state_path.read_text(encoding="utf-8"))
        raw["entries"] = {"stale_key": raw["entries"]["harness_real"]}
        store.state_path.write_text(json.dumps(raw), encoding="utf-8")
        update = store.apply([HarnessEdit(action="update", kind="fact", id="harness_real", content="x")], scope="run")
        assert update.applied_edits[0].applied


class TestSkillReference:
    """AC-899: executable skill payload on procedure entries."""

    def _reference(self) -> SkillReference:
        return SkillReference(
            entrypoint="priority",
            source="def priority(v):\n    return sum(v)",
            call_pattern="priority(vector)",
        )

    def test_reference_validates(self) -> None:
        ref = self._reference()
        assert ref.language == "python"
        with pytest.raises(ValidationError):
            SkillReference(entrypoint="", source="x")
        with pytest.raises(ValidationError):
            SkillReference(entrypoint="f", source="")

    def test_entry_and_edit_default_to_no_reference(self) -> None:
        assert _entry().reference is None
        assert HarnessEdit(action="create", kind="procedure").reference is None

    def test_apply_carries_reference_and_round_trips(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        edit = HarnessEdit(
            action="create",
            kind="procedure",
            id="harness_skill",
            title="Promoted skill: priority",
            content="call priority(vector)",
            reference=self._reference(),
        )
        store.apply([edit], scope="scenario_family")
        entry = HarnessEntryStore(tmp_path).entries(kind="procedure")[0]
        assert entry.reference is not None
        assert entry.reference.entrypoint == "priority"
        assert "def priority" in entry.reference.source

    def test_mis_kinded_reference_update_is_per_edit_error_not_batch_poison(self, tmp_path) -> None:
        """A procedure-declared update aiming a reference at a non-procedure
        entry must fail as a per-edit error; it previously escaped as an
        uncaught ValidationError that lost the whole batch."""
        store = HarnessEntryStore(tmp_path)
        store.apply([HarnessEdit(action="create", kind="fact", id="harness_f", title="t", content="c")], scope="run")
        batch = store.apply(
            [
                HarnessEdit(action="update", kind="procedure", id="harness_f", reference=self._reference()),
                HarnessEdit(action="create", kind="fact", id="harness_ok", title="t2", content="c2"),
            ],
            scope="run",
        )
        assert [item.applied for item in batch.applied_edits] == [False, True]
        assert batch.applied_edits[0].error == "reference_requires_procedure"
        entries = {entry.id: entry for entry in HarnessEntryStore(tmp_path).entries()}
        assert set(entries) == {"harness_f", "harness_ok"}
        assert entries["harness_f"].reference is None and entries["harness_f"].version == 1

    def test_update_replaces_reference_when_provided(self, tmp_path) -> None:
        store = HarnessEntryStore(tmp_path)
        store.apply(
            [HarnessEdit(action="create", kind="procedure", id="harness_s", title="t", content="c", reference=self._reference())],
            scope="run",
        )
        new_ref = SkillReference(entrypoint="priority", source="def priority(v):\n    return max(v)")
        store.apply(
            [HarnessEdit(action="update", kind="procedure", id="harness_s", reference=new_ref)],
            scope="run",
        )
        entry = store.entries()[0]
        assert entry.reference is not None and "max(v)" in entry.reference.source
