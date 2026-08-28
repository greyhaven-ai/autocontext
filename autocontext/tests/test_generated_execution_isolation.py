"""Regression tests for generated Python execution trust boundaries."""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.config.settings import AppSettings
from autocontext.execution.isolated_python import local_isolation_available
from autocontext.harness.repl.types import ReplCommand
from autocontext.harness.repl.worker import ReplWorker
from autocontext.investigation.engine import _execute_generated_investigation
from autocontext.scenarios.custom.agent_task_validator import validate_execution
from autocontext.simulation.engine import SimulationEngine

pytestmark = pytest.mark.skipif(
    not local_isolation_available(),
    reason="generated Python execution fails closed without POSIX child isolation",
)


def test_unknown_repl_backend_does_not_fall_back_to_exec() -> None:
    with pytest.raises(ValidationError):
        AppSettings(rlm_backend="typo")  # type: ignore[arg-type]


def _engine(tmp_path: Path, *, timeout_seconds: float = 0.2) -> SimulationEngine:
    return SimulationEngine(
        lambda _system, _user: "{}",
        tmp_path,
        execution_timeout_seconds=timeout_seconds,
    )


def test_simulation_infinite_loop_is_killed(tmp_path: Path) -> None:
    result = _engine(tmp_path)._execute_single("while True:\n    pass", "loop", 1)

    assert result == {
        "score": 0,
        "reasoning": "generated simulation execution timed out",
        "dimension_scores": {},
    }


def test_repl_candidate_cannot_mutate_parent_state() -> None:
    parent_state: list[str] = []
    worker = ReplWorker(namespace={"parent_state": parent_state})

    result = worker.run_code(ReplCommand("parent_state.append('child')"))

    assert result.error is None
    assert parent_state == []
    assert worker.namespace["parent_state"] == ["child"]


@pytest.mark.parametrize(
    "escape_expression",
    [
        "json.__builtins__['open']",
        "json.dumps.__globals__['__builtins__']['open']",
        "getattr(json.dumps, '__globals__')['__builtins__']['open']",
    ],
)
def test_repl_denies_module_builtins_file_escape(
    tmp_path: Path,
    escape_expression: str,
) -> None:
    target = tmp_path / "escaped.txt"
    worker = ReplWorker()

    result = worker.run_code(
        ReplCommand(f"{escape_expression}({str(target)!r}, 'w').write('owned')"),
    )

    assert result.error is not None and "AstSafetyError" in result.error
    assert not target.exists()


def test_repl_module_facades_are_immutable_and_fresh() -> None:
    worker = ReplWorker()

    mutation = worker.run_code(ReplCommand("json.dumps = 1"))
    after = worker.run_code(ReplCommand("json.dumps({'safe': True})"))

    assert mutation.error is not None and "read-only" in mutation.error
    assert '"safe": true' in after.stdout


def test_repl_builtins_are_an_explicit_capability_allowlist() -> None:
    worker = ReplWorker()
    builtins_namespace = worker.namespace["__builtins__"]

    assert "open" not in builtins_namespace
    assert "__import__" not in builtins_namespace
    assert "getattr" not in builtins_namespace
    assert "globals" not in builtins_namespace
    assert "type" not in builtins_namespace


def test_repl_denies_context_manager_traceback_frame_escape(tmp_path: Path) -> None:
    target = tmp_path / "traceback-escape.txt"
    worker = ReplWorker()
    payload = (
        "def enter(self):\n"
        "    return self\n"
        "def exit(self, exc_type, exc, tb):\n"
        f"    tb.tb_frame.f_back.f_globals['__builtins__']['open']({str(target)!r}, 'w').write('owned')\n"
        "    return True\n"
        "Trap = type('Trap', (), {'__enter__': enter, '__exit__': exit})\n"
        "with Trap():\n"
        "    1 / 0\n"
    )

    result = worker.run_code(ReplCommand(payload))

    assert result.error is not None and "AstSafetyError" in result.error
    assert not target.exists()


def test_simulation_cannot_mutate_parent_builtins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "_autocontext_simulation_child_probe"
    monkeypatch.delattr(builtins, marker, raising=False)

    _engine(tmp_path)._execute_single(
        f"import builtins\nbuiltins.{marker} = 'child'",
        "mutation",
        1,
    )

    assert not hasattr(builtins, marker)


def test_investigation_infinite_loop_is_killed() -> None:
    with pytest.raises(RuntimeError, match="execution timed out"):
        _execute_generated_investigation(
            source="while True:\n    pass",
            name="loop",
            max_steps=1,
            timeout_seconds=0.2,
        )


def test_investigation_cannot_mutate_parent_builtins(monkeypatch: pytest.MonkeyPatch) -> None:
    marker = "_autocontext_investigation_child_probe"
    monkeypatch.delattr(builtins, marker, raising=False)

    with pytest.raises(RuntimeError, match="failed in isolation"):
        _execute_generated_investigation(
            source=f"import builtins\nbuiltins.{marker} = 'child'",
            name="mutation",
            max_steps=1,
        )

    assert not hasattr(builtins, marker)


def test_agent_task_validation_infinite_loop_is_killed() -> None:
    errors = validate_execution("while True:\n    pass", timeout_seconds=0.2)

    assert errors == ["execution validation timed out"]


def test_agent_task_validation_cannot_mutate_parent_builtins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = "_autocontext_agent_task_child_probe"
    monkeypatch.delattr(builtins, marker, raising=False)

    errors = validate_execution(
        f"import builtins\nbuiltins.{marker} = 'child'",
    )

    assert any("no AgentTaskInterface subclass" in error for error in errors)
    assert not hasattr(builtins, marker)
