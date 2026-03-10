"""Tests for the dead-end registry feature (MTS-102 through MTS-108).

Covers:
- AppSettings fields for dead-end tracking (MTS-102)
- ArtifactStore methods for dead_ends.md (MTS-103)
- DeadEndEntry dataclass and consolidation logic (MTS-106)
- Prompt bundle integration (MTS-105)
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Import mts.agents first to break circular import with mts.prompts.templates.
# See: mts.prompts.templates -> mts.scenarios.base -> mts.scenarios.__init__
#      -> mts.scenarios.custom -> mts.agents -> mts.agents.orchestrator
#      -> mts.prompts.templates (circular).
import mts.agents  # noqa: F401
from mts.config.settings import AppSettings, load_settings
from mts.knowledge.dead_end_manager import DeadEndEntry, consolidate_dead_ends
from mts.prompts.templates import build_prompt_bundle
from mts.scenarios.base import Observation
from mts.storage.artifacts import ArtifactStore

# ---------------------------------------------------------------------------
# MTS-102: Settings fields
# ---------------------------------------------------------------------------


class TestDeadEndSettings:
    def test_dead_end_tracking_enabled_defaults_false(self) -> None:
        settings = AppSettings()
        assert settings.dead_end_tracking_enabled is False

    def test_dead_end_max_entries_defaults_20(self) -> None:
        settings = AppSettings()
        assert settings.dead_end_max_entries == 20

    def test_load_settings_reads_dead_end_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MTS_DEAD_END_TRACKING_ENABLED", "true")
        monkeypatch.setenv("MTS_DEAD_END_MAX_ENTRIES", "50")
        settings = load_settings()
        assert settings.dead_end_tracking_enabled is True
        assert settings.dead_end_max_entries == 50


# ---------------------------------------------------------------------------
# MTS-103: ArtifactStore dead-end methods
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


class TestArtifactStoreDeadEnds:
    def test_read_dead_ends_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.read_dead_ends("grid_ctf")
        assert result == ""

    def test_append_dead_end_creates_file(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.append_dead_end("grid_ctf", "- **Gen 1**: aggressive (score=0.1000) -- rolled back")
        path = tmp_path / "knowledge" / "grid_ctf" / "dead_ends.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "### Dead End" in content
        assert "aggressive" in content

    def test_append_dead_end_appends(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.append_dead_end("grid_ctf", "entry one")
        store.append_dead_end("grid_ctf", "entry two")
        content = store.read_dead_ends("grid_ctf")
        assert content.count("### Dead End") == 2
        assert "entry one" in content
        assert "entry two" in content

    def test_replace_dead_ends(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.append_dead_end("grid_ctf", "old entry")
        store.replace_dead_ends("grid_ctf", "# Dead-End Registry\n\n- new content\n")
        content = store.read_dead_ends("grid_ctf")
        assert "old entry" not in content
        assert "new content" in content


# ---------------------------------------------------------------------------
# MTS-106: DeadEndEntry dataclass
# ---------------------------------------------------------------------------


class TestDeadEndEntry:
    def test_dead_end_entry_to_markdown(self) -> None:
        entry = DeadEndEntry(
            generation=3,
            strategy_summary="aggressive rush",
            score=0.1234,
            reason="Rolled back due to score regression",
        )
        md = entry.to_markdown()
        assert "Gen 3" in md
        assert "aggressive rush" in md
        assert "0.1234" in md
        assert "Rolled back" in md

    def test_dead_end_entry_from_rollback(self) -> None:
        entry = DeadEndEntry.from_rollback(generation=5, strategy="balanced defense", score=0.25)
        assert entry.generation == 5
        assert entry.strategy_summary == "balanced defense"
        assert entry.score == 0.25
        assert "Rolled back" in entry.reason

    def test_dead_end_entry_from_rollback_truncates(self) -> None:
        long_strategy = "x" * 200
        entry = DeadEndEntry.from_rollback(generation=1, strategy=long_strategy, score=0.0)
        assert len(entry.strategy_summary) <= 83  # 80 chars + "..."
        assert entry.strategy_summary.endswith("...")


# ---------------------------------------------------------------------------
# MTS-106: Consolidation
# ---------------------------------------------------------------------------


class TestConsolidateDeadEnds:
    def test_consolidate_dead_ends_under_limit(self) -> None:
        entries = (
            "# Dead-End Registry\n\n"
            "- **Gen 1**: foo (score=0.1000) -- rolled back\n"
            "- **Gen 2**: bar (score=0.2000) -- rolled back\n"
        )
        result = consolidate_dead_ends(entries, max_entries=5)
        assert result == entries  # No change, under limit

    def test_consolidate_dead_ends_over_limit(self) -> None:
        lines = [f"- **Gen {i}**: strat_{i} (score=0.{i:04d}) -- rolled back" for i in range(10)]
        entries = "# Dead-End Registry\n\n" + "\n".join(lines) + "\n"
        result = consolidate_dead_ends(entries, max_entries=3)
        # Should keep only the last 3 entries (most recent)
        assert "strat_7" in result
        assert "strat_8" in result
        assert "strat_9" in result
        assert "strat_0" not in result
        assert "strat_6" not in result

    def test_consolidate_dead_ends_empty(self) -> None:
        result = consolidate_dead_ends("", max_entries=5)
        assert result == ""


# ---------------------------------------------------------------------------
# MTS-105: Prompt bundle integration
# ---------------------------------------------------------------------------


def _minimal_observation() -> Observation:
    return Observation(narrative="test narrative", state={}, constraints=[])


class TestPromptBundleDeadEnds:
    def test_prompt_bundle_includes_dead_ends(self) -> None:
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_minimal_observation(),
            current_playbook="playbook",
            available_tools="tools",
            dead_ends="- **Gen 1**: bad strat (score=0.1) -- rolled back",
        )
        # Dead ends should appear in the competitor prompt
        assert "Known dead ends" in bundle.competitor
        assert "bad strat" in bundle.competitor

    def test_prompt_bundle_empty_dead_ends_omitted(self) -> None:
        bundle = build_prompt_bundle(
            scenario_rules="rules",
            strategy_interface="interface",
            evaluation_criteria="criteria",
            previous_summary="summary",
            observation=_minimal_observation(),
            current_playbook="playbook",
            available_tools="tools",
            dead_ends="",
        )
        # When dead_ends is empty, no dead-end block should appear
        assert "Known dead ends" not in bundle.competitor
