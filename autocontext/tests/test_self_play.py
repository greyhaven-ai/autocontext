"""Tests for AC-334: self-play opponent pool for co-evolutionary pressure.

Covers: SelfPlayOpponent, SelfPlayConfig, SelfPlayPool, build_opponent_pool.
"""

from __future__ import annotations

import json

# ===========================================================================
# SelfPlayOpponent
# ===========================================================================


class TestSelfPlayOpponent:
    def test_construction(self) -> None:
        from autocontext.harness.evaluation.self_play import SelfPlayOpponent

        opp = SelfPlayOpponent(
            strategy={"aggression": 0.8, "defense": 0.4},
            generation=3,
            elo=1150.0,
            score=0.78,
        )
        assert opp.generation == 3
        assert opp.elo == 1150.0
        assert opp.strategy["aggression"] == 0.8

    def test_roundtrip(self) -> None:
        from autocontext.harness.evaluation.self_play import SelfPlayOpponent

        opp = SelfPlayOpponent(
            strategy={"x": 1}, generation=5, elo=1200.0, score=0.85,
        )
        d = opp.to_dict()
        restored = SelfPlayOpponent.from_dict(d)
        assert restored.generation == 5
        assert restored.score == 0.85


# ===========================================================================
# SelfPlayConfig
# ===========================================================================


class TestSelfPlayConfig:
    def test_defaults(self) -> None:
        from autocontext.harness.evaluation.self_play import SelfPlayConfig

        config = SelfPlayConfig()
        assert config.enabled is False
        assert config.pool_size == 3
        assert config.weight == 0.5

    def test_custom(self) -> None:
        from autocontext.harness.evaluation.self_play import SelfPlayConfig

        config = SelfPlayConfig(enabled=True, pool_size=5, weight=0.3)
        assert config.enabled is True
        assert config.pool_size == 5

    def test_roundtrip(self) -> None:
        from autocontext.harness.evaluation.self_play import SelfPlayConfig

        config = SelfPlayConfig(enabled=True, pool_size=4)
        d = config.to_dict()
        restored = SelfPlayConfig.from_dict(d)
        assert restored.enabled is True
        assert restored.pool_size == 4


# ===========================================================================
# SelfPlayPool
# ===========================================================================


class TestSelfPlayPool:
    def test_add_and_get(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
        )

        config = SelfPlayConfig(enabled=True, pool_size=3)
        pool = SelfPlayPool(config)

        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))
        pool.add(SelfPlayOpponent({"a": 2}, generation=2, elo=1050, score=0.6))

        opponents = pool.get_opponents()
        assert len(opponents) == 2

    def test_pool_size_limit(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
        )

        config = SelfPlayConfig(enabled=True, pool_size=2)
        pool = SelfPlayPool(config)

        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))
        pool.add(SelfPlayOpponent({"a": 2}, generation=2, elo=1050, score=0.6))
        pool.add(SelfPlayOpponent({"a": 3}, generation=3, elo=1100, score=0.7))

        opponents = pool.get_opponents()
        assert len(opponents) == 2
        # Should keep the most recent/best
        generations = {o.generation for o in opponents}
        assert 3 in generations

    def test_disabled_pool_returns_empty(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
        )

        config = SelfPlayConfig(enabled=False)
        pool = SelfPlayPool(config)
        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))

        assert pool.get_opponents() == []

    def test_empty_pool(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayPool,
        )

        pool = SelfPlayPool(SelfPlayConfig(enabled=True))
        assert pool.get_opponents() == []


# ===========================================================================
# build_opponent_pool
# ===========================================================================


class TestBuildOpponentPool:
    def test_baselines_only_when_disabled(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayPool,
            build_opponent_pool,
        )

        baselines = [{"strategy": "baseline_1"}, {"strategy": "baseline_2"}]
        pool = SelfPlayPool(SelfPlayConfig(enabled=False))

        result = build_opponent_pool(baselines, pool)
        assert len(result) == 2

    def test_includes_self_play_opponents(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
            build_opponent_pool,
        )

        baselines = [{"strategy": "baseline"}]
        config = SelfPlayConfig(enabled=True, pool_size=3, weight=0.5)
        pool = SelfPlayPool(config)
        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))
        pool.add(SelfPlayOpponent({"a": 2}, generation=2, elo=1050, score=0.6))

        result = build_opponent_pool(baselines, pool)
        # Should have baselines + self-play opponents
        assert len(result) > len(baselines)

    def test_weight_shapes_live_schedule_when_trials_provided(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
            build_opponent_pool,
        )

        baselines = [{"strategy": "baseline"}]
        pool = SelfPlayPool(SelfPlayConfig(enabled=True, pool_size=3, weight=0.25))
        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))

        result = build_opponent_pool(baselines, pool, trials=4)

        self_play_entries = [entry for entry in result if entry.get("source") == "self_play"]
        assert len(result) == 4
        assert len(self_play_entries) == 1

    def test_self_play_tagged(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
            build_opponent_pool,
        )

        baselines = [{"strategy": "baseline"}]
        pool = SelfPlayPool(SelfPlayConfig(enabled=True))
        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))

        result = build_opponent_pool(baselines, pool)
        self_play_entries = [e for e in result if e.get("source") == "self_play"]
        assert len(self_play_entries) >= 1

    def test_empty_baselines_with_self_play(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            SelfPlayOpponent,
            SelfPlayPool,
            build_opponent_pool,
        )

        pool = SelfPlayPool(SelfPlayConfig(enabled=True))
        pool.add(SelfPlayOpponent({"a": 1}, generation=1, elo=1000, score=0.5))

        result = build_opponent_pool([], pool)
        assert len(result) >= 1


class TestLoadSelfPlayPool:
    def test_loads_prior_advanced_strategies_only(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            load_self_play_pool,
        )

        history = [
            {
                "generation_index": 1,
                "content": json.dumps({"aggression": 0.9}),
                "best_score": 0.8,
                "gate_decision": "advance",
                "elo": 1110.0,
            },
            {
                "generation_index": 2,
                "content": json.dumps({"aggression": 0.1}),
                "best_score": 0.2,
                "gate_decision": "rollback",
                "elo": 980.0,
            },
        ]

        pool = load_self_play_pool(
            history,
            SelfPlayConfig(enabled=True, pool_size=3, weight=0.5),
            current_generation=3,
        )

        opponents = pool.get_opponents()
        assert len(opponents) == 1
        assert opponents[0].generation == 1
        assert opponents[0].elo == 1110.0

    def test_accepts_tuple_sequence_history(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            load_self_play_pool,
        )

        history = (
            {
                "generation_index": 1,
                "content": json.dumps({"aggression": 0.9}),
                "best_score": 0.8,
                "gate_decision": "advance",
                "elo": 1110.0,
            },
        )

        pool = load_self_play_pool(
            history,
            SelfPlayConfig(enabled=True, pool_size=3, weight=0.5),
            current_generation=3,
        )

        opponents = pool.get_opponents()
        assert len(opponents) == 1
        assert opponents[0].generation == 1

    def test_ignores_future_and_invalid_rows(self) -> None:
        from autocontext.harness.evaluation.self_play import (
            SelfPlayConfig,
            load_self_play_pool,
        )

        history = [
            {
                "generation_index": 4,
                "content": json.dumps({"aggression": 0.9}),
                "best_score": 0.8,
                "gate_decision": "advance",
            },
            {
                "generation_index": 2,
                "content": "{bad json",
                "best_score": 0.5,
                "gate_decision": "advance",
            },
        ]

        pool = load_self_play_pool(
            history,
            SelfPlayConfig(enabled=True, pool_size=3, weight=0.5),
            current_generation=3,
        )

        assert pool.get_opponents() == []
