"""Tests for HarnessSynthesizer — iterative LLM refinement loop for harness code."""
from __future__ import annotations

import textwrap
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from autocontext.execution.harness_synthesizer import HarnessSynthesizer, SynthesisResult
from autocontext.execution.sample_states import SampleState
from autocontext.providers.base import CompletionResult, LLMProvider
from autocontext.scenarios.base import Observation, Result, ScenarioInterface

# ── Helpers ───────────────────────────────────────────────────────────────────


GOOD_HARNESS_CODE = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return [{"action": "up"}, {"action": "down"}]

    def is_legal_action(state, action):
        return action.get("action") in ("up", "down")
""")

BAD_HARNESS_CODE = textwrap.dedent("""\
    def validate_strategy(strategy, scenario):
        return True, []

    def enumerate_legal_actions(state):
        return [{"action": "left"}]

    def is_legal_action(state, action):
        return action.get("action") == "left"
""")


class FakeScenario(ScenarioInterface):
    """Minimal scenario for testing."""

    name = "fake"

    def describe_rules(self) -> str:
        return "Fake rules."

    def describe_strategy_interface(self) -> str:
        return "JSON with 'value'."

    def describe_evaluation_criteria(self) -> str:
        return "Maximize value."

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "turn": 0, "terminal": False, "max_turns": 5}

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        return Observation(narrative="test", state=dict(state))

    def validate_actions(
        self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]
    ) -> tuple[bool, str]:
        return True, "ok"

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        turn = int(state["turn"]) + 1
        return {**dict(state), "turn": turn, "terminal": turn >= int(state["max_turns"])}

    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        return bool(state.get("terminal", False))

    def get_result(self, state: Mapping[str, Any]) -> Result:
        return Result(score=0.5, summary="done")

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        return "replay"

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {}

    def enumerate_legal_actions(self, state: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        if self.is_terminal(state):
            return []
        return [{"action": "up", "description": "Move up"}, {"action": "down", "description": "Move down"}]


class MockProvider(LLMProvider):
    """Mock provider that returns canned responses."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else []
        self._call_count = 0

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int = 4096,
    ) -> CompletionResult:
        idx = min(self._call_count, len(self._responses) - 1) if self._responses else 0
        text = self._responses[idx] if self._responses else ""
        self._call_count += 1
        return CompletionResult(text=text, model=model or "mock")

    def default_model(self) -> str:
        return "mock-model"

    @property
    def call_count(self) -> int:
        return self._call_count


def _make_states() -> list[SampleState]:
    phases = ["early", "mid", "late"]
    return [
        SampleState(
            state={"turn": i, "terminal": False},
            description=f"Turn {i}",
            expected_legal_actions=[{"action": "up"}, {"action": "down"}],
            difficulty=phases[i % 3],
        )
        for i in range(10)
    ]


# ── SynthesisResult dataclass ────────────────────────────────────────────────


class TestSynthesisResult:
    def test_fields(self) -> None:
        r = SynthesisResult(
            harness_source="code",
            iterations=5,
            accuracy=0.95,
            converged=False,
            failure_log=["iter 1: 0.5"],
        )
        assert r.harness_source == "code"
        assert r.iterations == 5
        assert r.accuracy == 0.95
        assert not r.converged
        assert len(r.failure_log) == 1

    def test_frozen(self) -> None:
        r = SynthesisResult(
            harness_source="", iterations=0, accuracy=0.0,
            converged=False, failure_log=[],
        )
        with pytest.raises(AttributeError):
            r.accuracy = 1.0  # type: ignore[misc]


# ── HarnessSynthesizer converges immediately ──────────────────────────────────


class TestHarnessSynthesizerConverges:
    def test_converges_on_first_try(self) -> None:
        """If the LLM returns perfect harness code on the first try, converge in 1 iteration."""
        provider = MockProvider([GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert result.converged
        assert result.accuracy == 1.0
        assert result.iterations == 1

    def test_uses_provider(self) -> None:
        """Verify the provider is actually called."""
        provider = MockProvider([GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        synth.synthesize(states)
        assert provider.call_count >= 1


# ── HarnessSynthesizer iterates to fix ────────────────────────────────────────


class TestHarnessSynthesizerIterates:
    def test_iterates_until_good(self) -> None:
        """First attempt bad, second good — should converge on iteration 2."""
        provider = MockProvider([BAD_HARNESS_CODE, GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert result.converged
        assert result.iterations == 2

    def test_max_iterations_respected(self) -> None:
        """If never converges, stop at max_iterations."""
        provider = MockProvider([BAD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=3)
        states = _make_states()
        result = synth.synthesize(states)
        assert not result.converged
        assert result.iterations == 3
        assert result.accuracy < 1.0

    def test_failure_log_populated(self) -> None:
        provider = MockProvider([BAD_HARNESS_CODE, GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert len(result.failure_log) >= 1


# ── HarnessSynthesizer accuracy target ────────────────────────────────────────


class TestHarnessSynthesizerAccuracyTarget:
    def test_custom_accuracy_target(self) -> None:
        """With a lower target, a partially-correct harness can converge."""
        # BAD_HARNESS_CODE has wrong actions but won't crash,
        # so it will have some accuracy (0.0 for action mismatch though)
        provider = MockProvider([BAD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=1, accuracy_target=0.0)
        states = _make_states()
        result = synth.synthesize(states)
        # With accuracy_target=0.0, any result is acceptable
        assert result.converged


# ── HarnessSynthesizer harness output ─────────────────────────────────────────


class TestHarnessSynthesizerOutput:
    def test_harness_source_contains_code(self) -> None:
        provider = MockProvider([GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert "def validate_strategy" in result.harness_source
        assert "def enumerate_legal_actions" in result.harness_source

    def test_writes_to_output_dir(self, tmp_path: Path) -> None:
        provider = MockProvider([GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        synth.synthesize(states, output_dir=tmp_path)
        # Should write a .py file in output_dir
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) >= 1
        content = py_files[0].read_text(encoding="utf-8")
        assert "def validate_strategy" in content


# ── HarnessSynthesizer AST safety ─────────────────────────────────────────────


class TestHarnessSynthesizerSafety:
    def test_rejects_unsafe_code_and_retries(self) -> None:
        """If LLM returns code with imports, it should be rejected and retried."""
        unsafe_code = textwrap.dedent("""\
            import os

            def validate_strategy(strategy, scenario):
                return True, []
        """)
        provider = MockProvider([unsafe_code, GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert result.converged
        assert result.iterations == 2

    def test_syntax_error_retried(self) -> None:
        """Syntax errors should be caught and retried."""
        bad_syntax = "def validate_strategy(:\n"
        provider = MockProvider([bad_syntax, GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert result.converged
        assert result.iterations == 2


# ── HarnessSynthesizer target_functions ───────────────────────────────────────


class TestHarnessSynthesizerTargetFunctions:
    def test_default_target_functions(self) -> None:
        provider = MockProvider([GOOD_HARNESS_CODE])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states)
        assert result.converged

    def test_default_target_functions_reject_missing_callables(self) -> None:
        harness_validate_only = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        provider = MockProvider([harness_validate_only])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=1)
        states = _make_states()
        result = synth.synthesize(states)
        assert not result.converged
        assert result.accuracy == 0.0

    def test_custom_target_functions(self) -> None:
        """Can request just validate_strategy."""
        harness_validate_only = textwrap.dedent("""\
            def validate_strategy(strategy, scenario):
                return True, []
        """)
        provider = MockProvider([harness_validate_only])
        scenario = FakeScenario()
        synth = HarnessSynthesizer(scenario, provider, max_iterations=5)
        states = _make_states()
        result = synth.synthesize(states, target_functions=["validate_strategy"])
        assert result.converged
