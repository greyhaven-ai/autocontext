"""AC-885 Slice B, Task 2: the agent-task generation write sites must stamp
``evaluator_epoch`` from the ``ImprovementResult`` onto the persisted row.

The positive test drives the real ``_run_agent_task`` code path against a stub
scenario and a stubbed ``ImprovementLoop.run`` that returns a *real*
``ImprovementResult`` carrying ``evaluator_epoch="e-1"``. Everything from that
result through to the real ``SQLiteStore.upsert_generation`` call and back out of
the persisted row is exercised (no mock stands in for the call site). The
negative test pins the contract boundary: the tournament write site
(``loop/stages.py``, deliberately untouched by this task) omits the kwarg, so a
generation upserted the way that site upserts stays ``evaluator_epoch = NULL``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.execution.improvement_loop import ImprovementResult
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class _StubAgentTask:
    """Minimal AgentTaskInterface stub for driving ``_run_agent_task``."""

    def initial_state(self) -> dict[str, Any]:
        return {}

    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def validate_context(self, state: dict[str, Any]) -> list[str]:
        return []

    def get_task_prompt(self, state: dict[str, Any]) -> str:
        return "do the task"


@contextmanager
def _noop_hook_bus(_hook_bus: Any):
    yield


def _real_result() -> ImprovementResult:
    return ImprovementResult(
        rounds=[],
        best_output="final output",
        best_score=0.75,
        best_round=1,
        total_rounds=1,
        met_threshold=True,
        termination_reason="threshold_met",
        duration_ms=1234,
        evaluator_epoch="e-1",
    )


def test_agent_task_run_persists_generation_epoch(tmp_path) -> None:
    """The agent-task write site forwards ImprovementResult.evaluator_epoch."""
    from autocontext import cli

    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)

    calls: list[dict[str, Any]] = []
    orig = store.upsert_generation

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return orig(*args, **kwargs)

    fake_provider = SimpleNamespace(complete=lambda **_: SimpleNamespace(text="initial"))
    fake_loop = MagicMock()
    fake_loop.run.return_value = _real_result()

    settings = MagicMock()
    settings.extensions = None
    settings.simplicity_mode = "off"
    settings.agent_provider = "anthropic"
    # real path so the evaluator-epoch registry (observe wiring) writes under tmp, not a MagicMock dir
    settings.knowledge_root = tmp_path / "knowledge"

    with (
        patch.object(store, "upsert_generation", _spy),
        patch.dict(cli.SCENARIO_REGISTRY, {"stub_task": _StubAgentTask}, clear=False),
        patch("autocontext.cli._sqlite_from_settings", return_value=store),
        patch("autocontext.cli.initialize_hook_bus", return_value=(MagicMock(), [])),
        patch("autocontext.cli.active_hook_bus", _noop_hook_bus),
        patch("autocontext.cli._resolve_agent_task_runtime", return_value=(fake_provider, "m")),
        patch("autocontext.cli.ImprovementLoop", return_value=fake_loop),
        patch("autocontext.cli.build_evaluator_guardrail_payload", return_value=None),
    ):
        summary = cli._run_agent_task("stub_task", settings, max_rounds=1, run_id="run-epoch")

    # The persisted "completed" upsert forwarded the result's epoch...
    completed = [c for c in calls if c.get("status") == "completed"]
    assert completed, "expected a completed upsert_generation call"
    assert completed[-1].get("evaluator_epoch") == "e-1"

    # ...and it round-trips through the real row.
    row = store.get_generation("run-epoch", 1)
    assert row is not None
    assert row["evaluator_epoch"] == "e-1"
    assert summary.run_id == "run-epoch"


def test_tournament_generation_epoch_stays_null(tmp_path) -> None:
    """The tournament write site (loop/stages.py) omits the kwarg, so its rows
    stay null. Mirrors the exact kwargs of stages.py::upsert_generation."""
    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)
    store.create_run("run-tourney", "some_scenario", 1, "tournament")

    # Faithful mirror of loop/stages.py:1092 (no evaluator_epoch kwarg).
    store.upsert_generation(
        "run-tourney",
        1,
        mean_score=0.5,
        best_score=0.6,
        elo=1500.0,
        wins=1,
        losses=0,
        gate_decision="promoted",
        status="completed",
    )

    row = store.get_generation("run-tourney", 1)
    assert row is not None
    assert row["evaluator_epoch"] is None
