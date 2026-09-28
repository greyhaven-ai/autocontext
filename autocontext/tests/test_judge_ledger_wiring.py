from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.execution.improvement_loop import ImprovementResult
from autocontext.prescreen.ledger import JudgeLedger
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


class _StubAgentTask:
    def initial_state(self) -> dict[str, Any]:
        return {}

    def prepare_context(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def validate_context(self, state: dict[str, Any]) -> list[str]:
        return []

    def get_task_prompt(self, state: dict[str, Any]) -> str:
        return "do the task"


@contextmanager
def _noop_hook_bus(_hook_bus: Any):  # type: ignore[no-untyped-def]
    yield


def _result() -> ImprovementResult:
    return ImprovementResult(
        rounds=[],
        best_output="final",
        best_score=0.95,
        best_round=1,
        total_rounds=1,
        met_threshold=True,
        termination_reason="threshold_met",
        duration_ms=10,
        evaluator_epoch="e-1",
    )


def _loop_kwargs(tmp_path: Path, enabled: object) -> dict[str, Any]:
    from autocontext import cli

    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)
    fake_loop = MagicMock()
    fake_loop.run.return_value = _result()
    settings = MagicMock()
    settings.extensions = None
    settings.simplicity_mode = "off"
    settings.agent_provider = "anthropic"
    settings.knowledge_root = tmp_path / "knowledge"
    settings.judge_ledger_enabled = enabled
    provider = SimpleNamespace(complete=lambda **_: SimpleNamespace(text="initial"))
    with (
        patch.dict(cli.SCENARIO_REGISTRY, {"stub_task": _StubAgentTask}, clear=False),
        patch("autocontext.cli._sqlite_from_settings", return_value=store),
        patch("autocontext.cli.initialize_hook_bus", return_value=(MagicMock(), [])),
        patch("autocontext.cli.active_hook_bus", _noop_hook_bus),
        patch("autocontext.cli._resolve_agent_task_runtime", return_value=(provider, "m")),
        patch("autocontext.cli.ImprovementLoop", return_value=fake_loop) as loop_cls,
        patch("autocontext.cli.build_evaluator_guardrail_payload", return_value=None),
    ):
        cli._run_agent_task("stub_task", settings, max_rounds=1, run_id="run-ledger")
    return dict(loop_cls.call_args.kwargs)


def test_run_agent_task_passes_a_ledger_when_enabled(tmp_path: Path) -> None:
    ledger = _loop_kwargs(tmp_path, True)["judge_ledger"]
    assert isinstance(ledger, JudgeLedger)
    assert (ledger.run_id, ledger.scenario_name, ledger.scenario_family) == ("run-ledger", "stub_task", "agent_task")


def test_run_agent_task_passes_no_ledger_when_disabled(tmp_path: Path) -> None:
    assert _loop_kwargs(tmp_path, False)["judge_ledger"] is None
