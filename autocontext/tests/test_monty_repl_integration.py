"""Integration tests: MontyReplWorker with RlmSession."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.harness.core.types import RoleUsage
from autocontext.harness.repl.monty_worker import MontyReplWorker
from autocontext.harness.repl.session import RlmSession
from autocontext.harness.repl.types import ReplWorkerProtocol

# ---------------------------------------------------------------------------
# Fake LLM client for session tests
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text
        self.usage = RoleUsage(input_tokens=10, output_tokens=20, latency_ms=5, model="test")


class _FakeClient:
    """Returns pre-set responses in order, then the finalize response."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    def generate_multiturn(self, **kwargs: Any) -> _FakeResponse:
        if self._idx < len(self._responses):
            text = self._responses[self._idx]
            self._idx += 1
            return _FakeResponse(text)
        return _FakeResponse("No more responses.")


# ---------------------------------------------------------------------------
# Mock Monty helpers
# ---------------------------------------------------------------------------


def _make_complete(output: Any) -> MagicMock:
    c = MagicMock(spec=[])
    c.output = output
    return c


def _make_snapshot(fn_name: str, args: tuple[Any, ...]) -> MagicMock:
    s = MagicMock()
    s.function_name = fn_name
    s.args = args
    return s


def _build_monty_for_output(
    prints: list[str],
    answer: dict[str, Any],
    state: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a mock Monty that prints then completes."""
    st = state or {}
    complete = _make_complete({"answer": answer, "state": st})

    if not prints:
        monty = MagicMock()
        monty.start.return_value = complete
        return monty

    snapshots = [_make_snapshot("_print", (text,)) for text in prints]
    for i, snap in enumerate(snapshots):
        snap.resume.return_value = snapshots[i + 1] if i + 1 < len(snapshots) else complete

    monty = MagicMock()
    monty.start.return_value = snapshots[0]
    return monty


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestMontyReplWorkerWithSession:
    def test_session_single_turn_finalize(self) -> None:
        """Session should complete in one turn when answer["ready"]=True."""
        mock = _build_monty_for_output(
            prints=["analysis complete"],
            answer={"content": "## Findings\nDone.", "ready": True},
        )

        client = _FakeClient([
            '<code>\nanswer["content"] = "## Findings\\nDone."\nanswer["ready"] = True\n</code>',
        ])

        worker = MontyReplWorker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            session = RlmSession(
                client=client,
                worker=worker,
                role="analyst",
                model="test",
                system_prompt="Test system.",
                max_turns=5,
            )
            result = session.run()

        assert result.status == "completed"
        assert result.content == "## Findings\nDone."

    def test_session_multi_turn_with_state(self) -> None:
        """Session should handle multiple turns with state persistence."""
        mock1 = _build_monty_for_output(
            prints=["3 replays loaded"],
            answer={"content": "", "ready": False},
            state={"count": 3},
        )
        mock2 = _build_monty_for_output(
            prints=["analysis done"],
            answer={"content": "## Results\nFound 3 items.", "ready": True},
            state={"count": 3},
        )

        mock_iter = iter([mock1, mock2])

        client = _FakeClient([
            '<code>\nstate["count"] = len(replays)\nprint(f"{state[\'count\']} replays loaded")\n</code>',
            '<code>\nanswer["content"] = f"## Results\\nFound {state[\'count\']} items."\nanswer["ready"] = True\n</code>',
        ])

        worker = MontyReplWorker(namespace={"replays": [1, 2, 3]})
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", side_effect=lambda **kw: next(mock_iter)):
            session = RlmSession(
                client=client,
                worker=worker,
                role="analyst",
                model="test",
                system_prompt="Test system.",
                max_turns=5,
            )
            result = session.run()

        assert result.status == "completed"
        assert "Found 3 items" in result.content

    def test_session_get_history_works(self) -> None:
        """RlmSession injects get_history into worker.namespace; it should be callable."""
        mock = _build_monty_for_output(
            prints=[],
            answer={"content": "done", "ready": True},
        )

        client = _FakeClient([
            '<code>\nanswer["content"] = "done"\nanswer["ready"] = True\n</code>',
        ])

        worker = MontyReplWorker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", return_value=mock):
            session = RlmSession(
                client=client,
                worker=worker,
                role="analyst",
                model="test",
                system_prompt="Test.",
                max_turns=3,
            )
            result = session.run()

        # get_history should have been injected
        assert "get_history" in worker.namespace
        assert callable(worker.namespace["get_history"])
        assert result.status == "completed"

    def test_session_handles_monty_error(self) -> None:
        """Runtime errors in Monty should be fed back to the LLM as error messages."""
        error_monty = MagicMock()
        error_monty.start.side_effect = RuntimeError("test error")

        recovery_monty = _build_monty_for_output(
            prints=[],
            answer={"content": "recovered", "ready": True},
        )

        mock_iter = iter([error_monty, recovery_monty])

        client = _FakeClient([
            '<code>\nundefined_var\n</code>',
            '<code>\nanswer["content"] = "recovered"\nanswer["ready"] = True\n</code>',
        ])

        worker = MontyReplWorker()
        with patch("autocontext.harness.repl.monty_worker._create_repl_monty", side_effect=lambda **kw: next(mock_iter)):
            session = RlmSession(
                client=client,
                worker=worker,
                role="analyst",
                model="test",
                system_prompt="Test.",
                max_turns=5,
            )
            result = session.run()

        assert result.content == "recovered"
        # First turn should have recorded an error
        assert session.execution_history[0].error is not None


class TestMontyReplWorkerProtocolCompatibility:
    def test_monty_worker_drop_in_replacement(self) -> None:
        """MontyReplWorker should satisfy ReplWorkerProtocol."""
        worker = MontyReplWorker()
        assert isinstance(worker, ReplWorkerProtocol)
        assert hasattr(worker, "run_code")
        assert hasattr(worker, "namespace")
