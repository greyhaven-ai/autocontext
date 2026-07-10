"""AC-885 Slice C1, Task 3: the agent-task write site must stamp ``quarantined``
on the persisted generation row from the evaluator-epoch registry.

Contract: when an agent-task run scores under an epoch that is NOT the scenario's
active epoch, the persisted generation row is quarantined (truthy/1); when it
scores under the active (or bootstrap) epoch, quarantined is null/false.

This drives the real ``_run_agent_task`` code path (as
``test_agent_task_generation_epoch.py`` does) against a stub scenario and a
stubbed ``ImprovementLoop.run`` returning a *real* ``ImprovementResult`` carrying
a chosen ``evaluator_epoch``. The registry root is pointed at ``tmp_path`` via
``settings.knowledge_root``. The first run bootstraps epoch ``e-1`` to active
(not quarantined); a second run of the same scenario under a different epoch
``e-2`` mints a candidate (quarantined). Both persisted rows are asserted.
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


def _real_result(epoch: str) -> ImprovementResult:
    return ImprovementResult(
        rounds=[],
        best_output="final output",
        best_score=0.75,
        best_round=1,
        total_rounds=1,
        met_threshold=True,
        termination_reason="threshold_met",
        duration_ms=1234,
        evaluator_epoch=epoch,
    )


def _run_once(store: SQLiteStore, settings: Any, *, epoch: str, run_id: str) -> None:
    from autocontext import cli

    fake_provider = SimpleNamespace(complete=lambda **_: SimpleNamespace(text="initial"))
    fake_loop = MagicMock()
    fake_loop.run.return_value = _real_result(epoch)

    with (
        patch.object(store, "upsert_generation", store.upsert_generation),
        patch.dict(cli.SCENARIO_REGISTRY, {"stub_task": _StubAgentTask}, clear=False),
        patch("autocontext.cli._sqlite_from_settings", return_value=store),
        patch("autocontext.cli.initialize_hook_bus", return_value=(MagicMock(), [])),
        patch("autocontext.cli.active_hook_bus", _noop_hook_bus),
        patch("autocontext.cli._resolve_agent_task_runtime", return_value=(fake_provider, "m")),
        patch("autocontext.cli.ImprovementLoop", return_value=fake_loop),
        patch("autocontext.cli.build_evaluator_guardrail_payload", return_value=None),
    ):
        cli._run_agent_task("stub_task", settings, max_rounds=1, run_id=run_id)


def test_active_epoch_not_quarantined_new_epoch_quarantined(tmp_path) -> None:
    """First epoch bootstraps active (not quarantined); a second, different epoch
    on the same scenario is a candidate (quarantined)."""
    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)

    settings = MagicMock()
    settings.extensions = None
    settings.simplicity_mode = "off"
    settings.agent_provider = "anthropic"
    settings.knowledge_root = tmp_path / "knowledge"

    # First run: epoch e-1 bootstraps the scenario's active epoch.
    _run_once(store, settings, epoch="e-1", run_id="run-e1")
    row1 = store.get_generation("run-e1", 1)
    assert row1 is not None
    assert row1["evaluator_epoch"] == "e-1"
    assert not row1["quarantined"], "bootstrap/active epoch must not be quarantined"

    # Second run: epoch e-2 is a candidate for the same scenario -> quarantined.
    _run_once(store, settings, epoch="e-2", run_id="run-e2")
    row2 = store.get_generation("run-e2", 1)
    assert row2 is not None
    assert row2["evaluator_epoch"] == "e-2"
    assert row2["quarantined"], "a non-active (candidate) epoch's score must be quarantined"
