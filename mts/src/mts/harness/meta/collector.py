"""Metrics collector — ingests per-generation role observations."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from mts.harness.meta.types import RoleMetric


class MetricsCollector:
    """Collects per-generation role metrics for profiling."""

    def __init__(self) -> None:
        self._observations: list[RoleMetric] = []
        self._lock = threading.Lock()

    def add(self, metric: RoleMetric) -> None:
        with self._lock:
            self._observations.append(metric)

    def add_generation(self, metrics: list[RoleMetric]) -> None:
        with self._lock:
            self._observations.extend(metrics)

    def for_role(self, role: str) -> list[RoleMetric]:
        with self._lock:
            return sorted(
                [m for m in self._observations if m.role == role],
                key=lambda m: m.generation,
            )

    def roles(self) -> set[str]:
        with self._lock:
            return {m.role for m in self._observations}

    def generation_count(self) -> int:
        with self._lock:
            return len({m.generation for m in self._observations})

    def latest_generation(self) -> int | None:
        with self._lock:
            if not self._observations:
                return None
            return max(m.generation for m in self._observations)

    def clear(self) -> None:
        with self._lock:
            self._observations.clear()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            data = [
                {
                    "role": m.role,
                    "generation": m.generation,
                    "input_tokens": m.input_tokens,
                    "output_tokens": m.output_tokens,
                    "latency_ms": m.latency_ms,
                    "cost": m.cost,
                    "gate_decision": m.gate_decision,
                    "score_delta": m.score_delta,
                }
                for m in self._observations
            ]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> MetricsCollector:
        collector = cls()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            collector._observations = [RoleMetric(**d) for d in data]
        return collector
