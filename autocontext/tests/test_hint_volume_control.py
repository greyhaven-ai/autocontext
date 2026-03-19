"""Tests for AC-340: hint volume control with impact ranking and rotation.

Covers: RankedHint, HintVolumePolicy, HintManager, apply_volume_cap.
"""

from __future__ import annotations

# ===========================================================================
# RankedHint
# ===========================================================================


class TestRankedHint:
    def test_construction(self) -> None:
        from autocontext.knowledge.hint_volume import RankedHint

        hint = RankedHint(
            text="Focus on edge control",
            rank=1,
            generation_added=3,
            impact_score=0.8,
        )
        assert hint.rank == 1
        assert hint.impact_score == 0.8

    def test_roundtrip(self) -> None:
        from autocontext.knowledge.hint_volume import RankedHint

        hint = RankedHint(text="test", rank=2, generation_added=5, impact_score=0.5)
        d = hint.to_dict()
        restored = RankedHint.from_dict(d)
        assert restored.rank == 2
        assert restored.generation_added == 5


# ===========================================================================
# HintVolumePolicy
# ===========================================================================


class TestHintVolumePolicy:
    def test_defaults(self) -> None:
        from autocontext.knowledge.hint_volume import HintVolumePolicy

        policy = HintVolumePolicy()
        assert policy.max_hints == 7
        assert policy.archive_rotated is True

    def test_custom(self) -> None:
        from autocontext.knowledge.hint_volume import HintVolumePolicy

        policy = HintVolumePolicy(max_hints=5, archive_rotated=False)
        assert policy.max_hints == 5


# ===========================================================================
# HintManager
# ===========================================================================


class TestHintManager:
    def test_add_within_cap(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=5))
        mgr.add("hint 1", generation=1)
        mgr.add("hint 2", generation=1)
        mgr.add("hint 3", generation=2)

        assert len(mgr.active_hints()) == 3
        assert len(mgr.archived_hints()) == 0

    def test_cap_rotates_lowest_ranked(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=3))
        mgr.add("hint 1", generation=1, impact_score=0.5)
        mgr.add("hint 2", generation=1, impact_score=0.8)
        mgr.add("hint 3", generation=2, impact_score=0.9)
        # This should rotate out the lowest (hint 1, score 0.5)
        mgr.add("hint 4", generation=3, impact_score=0.7)

        active = mgr.active_hints()
        assert len(active) == 3
        active_texts = {h.text for h in active}
        assert "hint 1" not in active_texts  # rotated out
        assert "hint 4" in active_texts

    def test_archived_hints_preserved(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=2, archive_rotated=True))
        mgr.add("a", generation=1, impact_score=0.3)
        mgr.add("b", generation=1, impact_score=0.8)
        mgr.add("c", generation=2, impact_score=0.9)

        archived = mgr.archived_hints()
        assert len(archived) == 1
        assert archived[0].text == "a"

    def test_update_impact_score(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=5))
        mgr.add("hint 1", generation=1, impact_score=0.3)
        mgr.update_impact("hint 1", new_score=0.9)

        active = mgr.active_hints()
        match = [h for h in active if h.text == "hint 1"]
        assert match[0].impact_score == 0.9

    def test_format_for_competitor(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=5))
        mgr.add("Focus on edges", generation=1, impact_score=0.9)
        mgr.add("Avoid center early", generation=2, impact_score=0.6)

        text = mgr.format_for_competitor()
        assert "edges" in text.lower()
        # Highest impact should be first
        edge_pos = text.lower().index("edges")
        center_pos = text.lower().index("center")
        assert edge_pos < center_pos

    def test_roundtrip_preserves_active_and_archived_state(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=2, archive_rotated=True))
        mgr.add("hint 1", generation=1, impact_score=0.2)
        mgr.add("hint 2", generation=2, impact_score=0.8)
        mgr.add("hint 3", generation=3, impact_score=0.9)

        restored = HintManager.from_dict(mgr.to_dict())

        assert [hint.text for hint in restored.active_hints()] == ["hint 3", "hint 2"]
        assert [hint.text for hint in restored.archived_hints()] == ["hint 1"]

    def test_from_hint_text_parses_markdownish_bullets(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager.from_hint_text(
            "- First hint\n2. Second hint\n* Third hint\n",
            policy=HintVolumePolicy(max_hints=5),
            generation=4,
        )

        assert [hint.text for hint in mgr.active_hints()] == [
            "First hint",
            "Second hint",
            "Third hint",
        ]

    def test_add_dedupes_and_resurrects_archived_hint(self) -> None:
        from autocontext.knowledge.hint_volume import HintManager, HintVolumePolicy

        mgr = HintManager(HintVolumePolicy(max_hints=2, archive_rotated=True))
        mgr.add("hint 1", generation=1, impact_score=0.1)
        mgr.add("hint 2", generation=1, impact_score=0.7)
        mgr.add("hint 3", generation=2, impact_score=0.9)

        assert [hint.text for hint in mgr.archived_hints()] == ["hint 1"]

        mgr.add("hint 1", generation=3, impact_score=0.95)

        assert [hint.text for hint in mgr.active_hints()] == ["hint 1", "hint 3"]
        assert [hint.text for hint in mgr.archived_hints()] == ["hint 2"]


# ===========================================================================
# apply_volume_cap
# ===========================================================================


class TestApplyVolumeCap:
    def test_caps_hint_list(self) -> None:
        from autocontext.knowledge.hint_volume import apply_volume_cap

        hints = [
            "hint 1 (high impact)",
            "hint 2 (medium impact)",
            "hint 3 (low impact)",
            "hint 4 (very low)",
        ]
        active, archived = apply_volume_cap(hints, max_hints=2)
        assert len(active) == 2
        assert len(archived) == 2

    def test_no_cap_needed(self) -> None:
        from autocontext.knowledge.hint_volume import apply_volume_cap

        hints = ["hint 1", "hint 2"]
        active, archived = apply_volume_cap(hints, max_hints=5)
        assert len(active) == 2
        assert len(archived) == 0

    def test_empty_hints(self) -> None:
        from autocontext.knowledge.hint_volume import apply_volume_cap

        active, archived = apply_volume_cap([], max_hints=5)
        assert active == []
        assert archived == []
