"""Tests for AC-339 + AC-341: novelty exploration and multi-basin playbook exploration.

AC-339: NoveltyConfig, compute_novelty_score, apply_novelty_bonus,
        DivergentCompetitorConfig, should_spawn_divergent
AC-341: MultiBasinConfig, BasinCandidate, generate_basin_candidates, BranchRecord
"""

from __future__ import annotations

# ===========================================================================
# AC-339: NoveltyConfig
# ===========================================================================


class TestNoveltyConfig:
    def test_defaults(self) -> None:
        from autocontext.loop.exploration import NoveltyConfig

        config = NoveltyConfig()
        assert config.weight == 0.1
        assert config.enabled is True

    def test_custom(self) -> None:
        from autocontext.loop.exploration import NoveltyConfig

        config = NoveltyConfig(weight=0.2, enabled=False)
        assert config.weight == 0.2
        assert config.enabled is False


# ===========================================================================
# AC-339: compute_novelty_score
# ===========================================================================


class TestComputeNoveltyScore:
    def test_identical_strategies_zero_novelty(self) -> None:
        from autocontext.loop.exploration import compute_novelty_score

        current = {"aggression": 0.8, "defense": 0.4}
        recent = [{"aggression": 0.8, "defense": 0.4}] * 3
        score = compute_novelty_score(current, recent)
        assert score == 0.0

    def test_different_strategy_high_novelty(self) -> None:
        from autocontext.loop.exploration import compute_novelty_score

        current = {"aggression": 0.1, "defense": 0.9}
        recent = [
            {"aggression": 0.8, "defense": 0.4},
            {"aggression": 0.7, "defense": 0.5},
        ]
        score = compute_novelty_score(current, recent)
        assert score > 0.3

    def test_empty_recent_max_novelty(self) -> None:
        from autocontext.loop.exploration import compute_novelty_score

        score = compute_novelty_score({"x": 0.5}, [])
        assert score == 1.0

    def test_non_numeric_values_ignored(self) -> None:
        from autocontext.loop.exploration import compute_novelty_score

        current = {"aggression": 0.5, "mode": "fast"}
        recent = [{"aggression": 0.5, "mode": "slow"}]
        score = compute_novelty_score(current, recent)
        assert 0.0 <= score <= 1.0


# ===========================================================================
# AC-339: apply_novelty_bonus
# ===========================================================================


class TestApplyNoveltyBonus:
    def test_bonus_applied(self) -> None:
        from autocontext.loop.exploration import NoveltyConfig, apply_novelty_bonus

        config = NoveltyConfig(weight=0.1, enabled=True)
        adjusted = apply_novelty_bonus(
            raw_score=0.70,
            novelty=0.8,
            config=config,
        )
        assert adjusted > 0.70
        assert adjusted == 0.70 + 0.1 * 0.8

    def test_disabled_no_bonus(self) -> None:
        from autocontext.loop.exploration import NoveltyConfig, apply_novelty_bonus

        config = NoveltyConfig(enabled=False)
        adjusted = apply_novelty_bonus(raw_score=0.70, novelty=0.8, config=config)
        assert adjusted == 0.70

    def test_capped_at_one(self) -> None:
        from autocontext.loop.exploration import NoveltyConfig, apply_novelty_bonus

        config = NoveltyConfig(weight=0.5, enabled=True)
        adjusted = apply_novelty_bonus(raw_score=0.95, novelty=1.0, config=config)
        assert adjusted <= 1.0


# ===========================================================================
# AC-339: DivergentCompetitorConfig + should_spawn_divergent
# ===========================================================================


class TestDivergentCompetitor:
    def test_should_spawn_after_threshold(self) -> None:
        from autocontext.loop.exploration import (
            DivergentCompetitorConfig,
            should_spawn_divergent,
        )

        config = DivergentCompetitorConfig(rollback_threshold=3)
        gate_history = ["advance", "rollback", "rollback", "rollback"]
        assert should_spawn_divergent(gate_history, config) is True

    def test_should_not_spawn_below_threshold(self) -> None:
        from autocontext.loop.exploration import (
            DivergentCompetitorConfig,
            should_spawn_divergent,
        )

        config = DivergentCompetitorConfig(rollback_threshold=5)
        gate_history = ["rollback", "rollback", "advance"]
        assert should_spawn_divergent(gate_history, config) is False

    def test_disabled(self) -> None:
        from autocontext.loop.exploration import (
            DivergentCompetitorConfig,
            should_spawn_divergent,
        )

        config = DivergentCompetitorConfig(enabled=False)
        gate_history = ["rollback"] * 10
        assert should_spawn_divergent(gate_history, config) is False


# ===========================================================================
# AC-341: MultiBasinConfig
# ===========================================================================


class TestMultiBasinConfig:
    def test_defaults(self) -> None:
        from autocontext.loop.exploration import MultiBasinConfig

        config = MultiBasinConfig()
        assert config.enabled is False
        assert config.trigger_rollbacks == 3
        assert config.candidates == 3

    def test_custom(self) -> None:
        from autocontext.loop.exploration import MultiBasinConfig

        config = MultiBasinConfig(enabled=True, candidates=5, periodic_every_n=10)
        assert config.candidates == 5
        assert config.periodic_every_n == 10


class TestShouldTriggerMultiBasin:
    def test_triggers_after_consecutive_non_advances(self) -> None:
        from autocontext.loop.exploration import MultiBasinConfig, should_trigger_multi_basin

        config = MultiBasinConfig(enabled=True, trigger_rollbacks=3)
        gate_history = ["advance", "retry", "rollback", "rollback"]
        assert should_trigger_multi_basin(gate_history, generation=4, config=config) is True

    def test_triggers_periodically(self) -> None:
        from autocontext.loop.exploration import MultiBasinConfig, should_trigger_multi_basin

        config = MultiBasinConfig(enabled=True, trigger_rollbacks=99, periodic_every_n=5)
        assert should_trigger_multi_basin(["advance"], generation=5, config=config) is True

    def test_disabled_returns_false(self) -> None:
        from autocontext.loop.exploration import MultiBasinConfig, should_trigger_multi_basin

        config = MultiBasinConfig(enabled=False, trigger_rollbacks=1, periodic_every_n=1)
        assert should_trigger_multi_basin(["rollback"] * 10, generation=10, config=config) is False


# ===========================================================================
# AC-341: BasinCandidate + generate_basin_candidates
# ===========================================================================


class TestBasinCandidates:
    def test_generate_candidates(self) -> None:
        from autocontext.loop.exploration import (
            MultiBasinConfig,
            generate_basin_candidates,
        )

        config = MultiBasinConfig(enabled=True, candidates=3)
        candidates = generate_basin_candidates(
            playbook="Current playbook content",
            lessons="Lesson 1\nLesson 2",
            config=config,
        )
        assert len(candidates) == 3
        types = {c.branch_type for c in candidates}
        assert "conservative" in types
        assert "experimental" in types
        assert "divergent" in types

    def test_conservative_has_full_playbook(self) -> None:
        from autocontext.loop.exploration import (
            MultiBasinConfig,
            generate_basin_candidates,
        )

        config = MultiBasinConfig(enabled=True)
        candidates = generate_basin_candidates(
            playbook="Full playbook", lessons="Lessons", config=config,
        )
        conservative = next(c for c in candidates if c.branch_type == "conservative")
        assert "Full playbook" in conservative.playbook

    def test_divergent_has_no_playbook(self) -> None:
        from autocontext.loop.exploration import (
            MultiBasinConfig,
            generate_basin_candidates,
        )

        config = MultiBasinConfig(enabled=True)
        candidates = generate_basin_candidates(
            playbook="Full playbook", lessons="Lessons", config=config,
        )
        divergent = next(c for c in candidates if c.branch_type == "divergent")
        assert divergent.playbook == ""
        assert "Lessons" in divergent.lessons

    def test_experimental_retains_high_level_playbook_context(self) -> None:
        from autocontext.loop.exploration import (
            MultiBasinConfig,
            generate_basin_candidates,
        )

        config = MultiBasinConfig(enabled=True)
        candidates = generate_basin_candidates(
            playbook="Overview\n- precise tactic 1\n- precise tactic 2",
            lessons="Lessons",
            config=config,
        )
        experimental = next(c for c in candidates if c.branch_type == "experimental")
        divergent = next(c for c in candidates if c.branch_type == "divergent")
        assert experimental.playbook != ""
        assert experimental.playbook != divergent.playbook

    def test_disabled_returns_empty(self) -> None:
        from autocontext.loop.exploration import (
            MultiBasinConfig,
            generate_basin_candidates,
        )

        config = MultiBasinConfig(enabled=False)
        assert generate_basin_candidates("pb", "l", config=config) == []


# ===========================================================================
# AC-341: BranchRecord
# ===========================================================================


class TestBranchRecord:
    def test_construction(self) -> None:
        from autocontext.loop.exploration import BranchRecord

        rec = BranchRecord(
            generation=5,
            branch_type="experimental",
            score=0.78,
            advanced=True,
        )
        assert rec.branch_type == "experimental"
        assert rec.advanced is True

    def test_roundtrip(self) -> None:
        from autocontext.loop.exploration import BranchRecord

        rec = BranchRecord(generation=3, branch_type="divergent", score=0.65, advanced=False)
        d = rec.to_dict()
        restored = BranchRecord.from_dict(d)
        assert restored.branch_type == "divergent"
        assert restored.score == 0.65
