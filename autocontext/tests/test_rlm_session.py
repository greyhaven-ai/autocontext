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
        client = _ChangingNeverReadyClient()
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

    def test_soft_finalizes_explicit_final_answer_marker(self) -> None:
        client = _StaticMultiturnClient("<final_answer>Y</final_answer>")
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

        assert result.status == "soft_finalized"
        assert result.content == "Y"
        assert result.metadata["finalize_reason"] == "final_answer_marker"
        assert len(session.execution_history) == 0

    def test_soft_finalizes_natural_language_closure_without_ready_mutation(self) -> None:
        client = _StaticMultiturnClient("I'm confident the answer is X")
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

        assert result.status == "soft_finalized"
        assert "X" in result.content
        assert result.metadata["finalize_reason"] == "natural_language_closure"

    def test_soft_finalizes_after_repeated_no_progress_turns(self) -> None:
        client = _SilentNoProgressClient()
        worker = ReplWorker()
        session = RlmSession(
            client=client,
            worker=worker,
            role="analyst",
            model="test-model",
            system_prompt="You are a test agent.",
            max_turns=25,
        )

        result = session.run()

        assert result.status == "soft_finalized"
        assert result.metadata["finalize_reason"] == "no_progress"
        assert len(session.execution_history) == 3
        assert result.content

    def test_distinct_read_only_inspection_turns_are_not_no_progress(self) -> None:
        client = _DistinctInspectionClient()
        worker = ReplWorker(namespace={"values": [1, 2, 3]})
        session = RlmSession(
            client=client,
            worker=worker,
            role="analyst",
            model="test-model",
            system_prompt="You are a test agent.",
            max_turns=5,
        )

        result = session.run()

        assert result.status == "truncated"
        assert result.metadata.get("finalize_reason") != "no_progress"
        assert len(session.execution_history) == 5

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


class _ChangingNeverReadyClient(LanguageModelClient):
    """Returns code that mutates state but never sets answer['ready']."""

    def __init__(self) -> None:
        self._turn = 0

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        del system, messages, max_tokens, temperature, role
        self._turn += 1
        return ModelResponse(
            text=f'<code>\nstate_{self._turn} = {self._turn}\nprint("turn {self._turn}")\n</code>',
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )


class _SilentNoProgressClient(LanguageModelClient):
    """Always returns identical silent no-op code."""

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        return ModelResponse(
            text="<code>\npass\n</code>",
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )


class _DistinctInspectionClient(LanguageModelClient):
    """Returns read-only inspection code that makes observable progress."""

    def __init__(self) -> None:
        self._turn = 0

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        del system, messages, max_tokens, temperature, role
        self._turn += 1
        return ModelResponse(
            text=f'<code>\nprint("inspection {self._turn}", values[{(self._turn - 1) % 3}])\n</code>',
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )


class _StaticMultiturnClient(LanguageModelClient):
    def __init__(self, text: str) -> None:
        self._text = text

    def generate_multiturn(
        self, *, model: str, system: str, messages: list[dict[str, str]],
        max_tokens: int, temperature: float, role: str = "",
    ) -> ModelResponse:
        del system, messages, max_tokens, temperature, role
        return ModelResponse(
            text=self._text,
            usage=RoleUsage(input_tokens=10, output_tokens=10, latency_ms=1, model=model),
        )
