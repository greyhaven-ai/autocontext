from __future__ import annotations

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.rlm.repl_worker import ReplWorker
from autocontext.rlm.session import RlmSession, make_llm_batch


class TestRlmSession:
    def test_runs_to_completion(self) -> None:
        client = DeterministicDevClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client,
            worker=worker,
            role="analyst",
            model="test-model",
            system_prompt="You are a test agent.",
            max_turns=5,
        )
        result = session.run()
        assert result.status == "completed"
        assert result.role == "analyst"
        assert "Findings" in result.content
        assert result.usage.input_tokens > 0
        assert result.usage.output_tokens > 0

    def test_respects_max_turns(self) -> None:
        """When the model never sets ready=True, session should truncate."""
        client = _NeverReadyClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client,
            worker=worker,
            role="analyst",
            model="test-model",
            system_prompt="You are a test agent.",
            max_turns=3,
        )
        result = session.run()
        assert result.status == "truncated"

    def test_usage_aggregated_across_turns(self) -> None:
        client = DeterministicDevClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client,
            worker=worker,
            role="analyst",
            model="test-model",
            system_prompt="test",
            max_turns=5,
        )
        result = session.run()
        # DeterministicDevClient returns 100 input + 50 output per turn, runs 2 turns
        assert result.usage.input_tokens == 200
        assert result.usage.output_tokens == 100

    def test_deterministic_client_resets(self) -> None:
        """Verify that reset_rlm_turns allows re-running sessions."""
        client = DeterministicDevClient()
        worker1 = ReplWorker()
        session1 = RlmSession(
            client=client, worker=worker1, role="analyst",
            model="m", system_prompt="s", max_turns=5,
        )
        r1 = session1.run()
        assert r1.status == "completed"

        client.reset_rlm_turns()
        worker2 = ReplWorker()
        session2 = RlmSession(
            client=client, worker=worker2, role="architect",
            model="m", system_prompt="s", max_turns=5,
        )
        r2 = session2.run()
        assert r2.status == "completed"


class TestMakeLlmBatch:
    def test_returns_correct_count(self) -> None:
        client = DeterministicDevClient()
        batch = make_llm_batch(client, model="test-model")
        results = batch(["prompt one", "prompt two"])
        assert len(results) == 2
        assert all(isinstance(r, str) for r in results)

    def test_empty_prompts(self) -> None:
        client = DeterministicDevClient()
        batch = make_llm_batch(client, model="test-model")
        assert batch([]) == []

    def test_injected_in_worker(self) -> None:
        """llm_batch is usable inside the REPL namespace."""
        client = DeterministicDevClient()
        batch = make_llm_batch(client, model="test-model")
        worker = ReplWorker(namespace={"llm_batch": batch})
        result = worker.run_code(
            __import__("autocontext.rlm.types", fromlist=["ReplCommand"]).ReplCommand(
                'results = llm_batch(["hello"])\nprint(len(results))'
            )
        )
        assert "1" in result.stdout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from autocontext.agents.llm_client import LanguageModelClient, ModelResponse  # noqa: E402
from autocontext.agents.types import RoleUsage  # noqa: E402


class TestRlmSessionExecutionHistory:
    def test_session_tracks_execution_history(self) -> None:
        client = DeterministicDevClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client, worker=worker, role="analyst",
            model="m", system_prompt="s", max_turns=5,
        )
        session.run()
        assert len(session.execution_history) == 2
        assert session.execution_history[0].turn == 1
        assert session.execution_history[1].turn == 2
        assert session.execution_history[1].answer_ready is True

    def test_session_history_includes_errors(self) -> None:
        client = _ErrorThenReadyClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client, worker=worker, role="analyst",
            model="m", system_prompt="s", max_turns=5,
        )
        session.run()
        assert len(session.execution_history) == 2
        assert session.execution_history[0].error is not None
        assert "ZeroDivisionError" in session.execution_history[0].error
        assert session.execution_history[1].error is None

    def test_session_history_available_after_run(self) -> None:
        client = DeterministicDevClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client, worker=worker, role="analyst",
            model="m", system_prompt="s", max_turns=5,
        )
        result = session.run()
        assert result.status == "completed"
        # History should be accessible after run completes
        for rec in session.execution_history:
            assert isinstance(rec.code, str)
            assert isinstance(rec.stdout, str)

    def test_session_history_count_matches_turns(self) -> None:
        client = _NeverReadyClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client, worker=worker, role="analyst",
            model="m", system_prompt="s", max_turns=3,
        )
        session.run()
        assert len(session.execution_history) == 3


class _ErrorThenReadyClient(LanguageModelClient):
    """First turn errors, second sets ready."""

    def __init__(self) -> None:
        self._turn = 0

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        self._turn += 1
        if self._turn == 1:
            text = '<code>\n1 / 0\n</code>'
        else:
            text = '<code>\nanswer["content"] = "recovered"\nanswer["ready"] = True\n</code>'
        return ModelResponse(
            text=text,
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )


class _NeverReadyClient(LanguageModelClient):
    """Always returns code that prints but never sets answer['ready']."""

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        return ModelResponse(
            text='<code>\nprint("still working")\n</code>',
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )
