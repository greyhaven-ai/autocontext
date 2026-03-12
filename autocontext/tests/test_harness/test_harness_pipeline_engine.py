"""Tests for autocontext.harness.orchestration.engine — DAG-ordered pipeline execution."""

from __future__ import annotations

import threading
import time

import pytest

from autocontext.harness.core.types import RoleExecution, RoleUsage
from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.engine import PipelineEngine
from autocontext.harness.orchestration.types import RoleSpec


def _make_usage() -> RoleUsage:
    return RoleUsage(input_tokens=10, output_tokens=20, latency_ms=100, model="test")


def _simple_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
    return RoleExecution(role=name, content=f"{name}:{prompt}", usage=_make_usage(), subagent_id="sa", status="ok")


class TestPipelineEngine:
    def test_engine_executes_single_role(self) -> None:
        dag = RoleDAG([RoleSpec(name="solo")])
        engine = PipelineEngine(dag, _simple_handler)
        results = engine.execute({"solo": "do it"})
        assert "solo" in results
        assert results["solo"].content == "solo:do it"

    def test_engine_executes_linear_chain(self) -> None:
        roles = [
            RoleSpec(name="A"),
            RoleSpec(name="B", depends_on=("A",)),
            RoleSpec(name="C", depends_on=("B",)),
        ]
        dag = RoleDAG(roles)
        order: list[str] = []

        def tracking_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            order.append(name)
            return _simple_handler(name, prompt, completed)

        engine = PipelineEngine(dag, tracking_handler)
        engine.execute({"A": "p1", "B": "p2", "C": "p3"})
        assert order == ["A", "B", "C"]

    def test_engine_executes_parallel_batch(self) -> None:
        roles = [RoleSpec(name="A"), RoleSpec(name="B")]
        dag = RoleDAG(roles)
        threads: list[str] = []
        lock = threading.Lock()

        def parallel_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            with lock:
                threads.append(threading.current_thread().name)
            time.sleep(0.05)
            return _simple_handler(name, prompt, completed)

        engine = PipelineEngine(dag, parallel_handler, max_workers=2)
        results = engine.execute({"A": "", "B": ""})
        assert "A" in results
        assert "B" in results

    def test_engine_passes_completed_results_to_handler(self) -> None:
        roles = [
            RoleSpec(name="first"),
            RoleSpec(name="second", depends_on=("first",)),
        ]
        dag = RoleDAG(roles)
        captured: dict[str, dict[str, RoleExecution]] = {}

        def capturing_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            captured[name] = dict(completed)
            return _simple_handler(name, prompt, completed)

        engine = PipelineEngine(dag, capturing_handler)
        engine.execute({"first": "p1", "second": "p2"})
        assert "first" not in captured["first"]  # first has no deps
        assert "first" in captured["second"]  # second sees first

    def test_engine_on_role_event_callback(self) -> None:
        dag = RoleDAG([RoleSpec(name="solo")])
        events: list[tuple[str, str]] = []

        def on_event(role: str, event: str) -> None:
            events.append((role, event))

        engine = PipelineEngine(dag, _simple_handler)
        engine.execute({"solo": ""}, on_role_event=on_event)
        assert ("solo", "started") in events
        assert ("solo", "completed") in events

    def test_engine_handler_error_propagates(self) -> None:
        dag = RoleDAG([RoleSpec(name="broken")])

        def error_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            raise RuntimeError("handler failed")

        engine = PipelineEngine(dag, error_handler)
        with pytest.raises(RuntimeError, match="handler failed"):
            engine.execute({"broken": ""})

    def test_engine_returns_all_executions(self) -> None:
        roles = [RoleSpec(name="A"), RoleSpec(name="B"), RoleSpec(name="C")]
        dag = RoleDAG(roles)
        engine = PipelineEngine(dag, _simple_handler)
        results = engine.execute({"A": "", "B": "", "C": ""})
        assert set(results.keys()) == {"A", "B", "C"}

    def test_engine_diamond_dag(self) -> None:
        roles = [
            RoleSpec(name="A"),
            RoleSpec(name="B", depends_on=("A",)),
            RoleSpec(name="C", depends_on=("A",)),
            RoleSpec(name="D", depends_on=("B", "C")),
        ]
        dag = RoleDAG(roles)
        order: list[str] = []
        lock = threading.Lock()

        def tracking_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            with lock:
                order.append(name)
            return _simple_handler(name, prompt, completed)

        engine = PipelineEngine(dag, tracking_handler, max_workers=2)
        results = engine.execute({"A": "", "B": "", "C": "", "D": ""})
        # A must be before B and C; D must be last
        assert order.index("A") < order.index("B")
        assert order.index("A") < order.index("C")
        assert order.index("D") == 3
        assert set(results.keys()) == {"A", "B", "C", "D"}

    def test_engine_respects_dependency_order(self) -> None:
        roles = [
            RoleSpec(name="A"),
            RoleSpec(name="B", depends_on=("A",)),
        ]
        dag = RoleDAG(roles)
        seen_by_b: list[str] = []

        def checking_handler(name: str, prompt: str, completed: dict[str, RoleExecution]) -> RoleExecution:
            if name == "B":
                seen_by_b.extend(completed.keys())
            return _simple_handler(name, prompt, completed)

        engine = PipelineEngine(dag, checking_handler)
        engine.execute({"A": "", "B": ""})
        assert "A" in seen_by_b

    def test_engine_max_workers_param(self) -> None:
        roles = [RoleSpec(name="A"), RoleSpec(name="B"), RoleSpec(name="C"), RoleSpec(name="D")]
        dag = RoleDAG(roles)
        engine = PipelineEngine(dag, _simple_handler, max_workers=2)
        results = engine.execute({"A": "", "B": "", "C": "", "D": ""})
        assert len(results) == 4
