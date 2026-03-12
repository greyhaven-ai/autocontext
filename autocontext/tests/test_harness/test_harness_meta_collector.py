"""Tests for autocontext.harness.meta.collector — MetricsCollector."""

from __future__ import annotations

import threading

from autocontext.harness.meta.collector import MetricsCollector
from autocontext.harness.meta.types import RoleMetric


def _metric(role: str = "competitor", generation: int = 0, cost: float = 0.01, score_delta: float = 0.1) -> RoleMetric:
    return RoleMetric(
        role=role, generation=generation, input_tokens=1000, output_tokens=500,
        latency_ms=2000, cost=cost, gate_decision="advance", score_delta=score_delta,
    )


def test_collector_add_observation() -> None:
    c = MetricsCollector()
    c.add(_metric())
    assert c.generation_count() == 1


def test_collector_observations_for_role() -> None:
    c = MetricsCollector()
    c.add(_metric(role="competitor", generation=0))
    c.add(_metric(role="analyst", generation=0))
    c.add(_metric(role="competitor", generation=1))
    obs = c.for_role("competitor")
    assert len(obs) == 2
    assert all(m.role == "competitor" for m in obs)


def test_collector_all_roles() -> None:
    c = MetricsCollector()
    c.add(_metric(role="competitor"))
    c.add(_metric(role="analyst"))
    c.add(_metric(role="coach"))
    assert c.roles() == {"competitor", "analyst", "coach"}


def test_collector_generation_count() -> None:
    c = MetricsCollector()
    c.add(_metric(generation=0))
    c.add(_metric(generation=0, role="analyst"))
    c.add(_metric(generation=1))
    assert c.generation_count() == 2


def test_collector_latest_generation() -> None:
    c = MetricsCollector()
    assert c.latest_generation() is None
    c.add(_metric(generation=0))
    c.add(_metric(generation=3))
    c.add(_metric(generation=1))
    assert c.latest_generation() == 3


def test_collector_bulk_add() -> None:
    c = MetricsCollector()
    metrics = [
        _metric(role="competitor", generation=0),
        _metric(role="analyst", generation=0),
        _metric(role="coach", generation=0),
    ]
    c.add_generation(metrics)
    assert c.generation_count() == 1
    assert len(c.for_role("competitor")) == 1
    assert len(c.for_role("analyst")) == 1
    assert len(c.for_role("coach")) == 1


def test_collector_observations_ordered() -> None:
    c = MetricsCollector()
    c.add(_metric(generation=3))
    c.add(_metric(generation=1))
    c.add(_metric(generation=0))
    c.add(_metric(generation=2))
    obs = c.for_role("competitor")
    gens = [m.generation for m in obs]
    assert gens == [0, 1, 2, 3]


def test_collector_clear() -> None:
    c = MetricsCollector()
    c.add(_metric())
    c.add(_metric(generation=1))
    c.clear()
    assert c.generation_count() == 0
    assert c.roles() == set()
    assert c.latest_generation() is None


def test_collector_thread_safe() -> None:
    c = MetricsCollector()
    barrier = threading.Barrier(50)

    def worker(gen: int) -> None:
        barrier.wait()
        c.add(_metric(generation=gen))

    threads = [threading.Thread(target=worker, args=(i % 10,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All observations are for "competitor" (default in _metric)
    assert len(c.for_role("competitor")) == 50
    assert c.generation_count() == 10


def test_collector_persist_and_load(tmp_path) -> None:
    c = MetricsCollector()
    c.add(_metric(role="competitor", generation=0, cost=0.01, score_delta=0.1))
    c.add(_metric(role="analyst", generation=1, cost=0.02, score_delta=-0.05))

    path = tmp_path / "metrics.json"
    c.save(path)
    assert path.exists()

    loaded = MetricsCollector.load(path)
    assert loaded.generation_count() == 2
    assert loaded.roles() == {"competitor", "analyst"}
    obs = loaded.for_role("competitor")
    assert len(obs) == 1
    assert obs[0].cost == 0.01
    assert obs[0].score_delta == 0.1
