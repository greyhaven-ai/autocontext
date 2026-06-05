"""Teacher reasoning-trace collection (provider-agnostic).

A teacher model (any ``LLMProvider``: hosted, OpenAI-compatible/local, or a callable
for tests) is prompted to reason and then emit a construction. The construction is
verified in-scenario for a real score, and high-scoring traces become training
records carrying the rationale. Nothing here is tied to a specific model or vendor.
"""

from __future__ import annotations

from autocontext.providers.callable_wrapper import CallableProvider
from autocontext.scenarios.agent_task import AgentTaskResult


class _FakeScenario:
    """Minimal agent-task scenario: score = (number of points) / 10, capped at 1.0."""

    name = "toy"
    description = "toy construction task"

    def initial_state(self, seed=None):
        return {}

    def get_task_prompt(self, state=None):
        return "Build a set of integers."

    def evaluate_output(self, output, state=None, **kwargs):
        import json

        try:
            pts = json.loads(output).get("points", [])
        except Exception:
            pts = []
        return AgentTaskResult(score=min(1.0, len(pts) / 10.0), reasoning="")


def test_parse_teacher_output_fenced_json() -> None:
    from autocontext.training.autoresearch.trace_collector import parse_teacher_output

    text = 'We use a coset because it avoids 3-term sums.\n```json\n{"points": [1, 2]}\n```'
    parsed = parse_teacher_output(text)
    assert parsed is not None
    reasoning, strategy = parsed
    assert reasoning == "We use a coset because it avoids 3-term sums."
    assert strategy == {"points": [1, 2]}


def test_parse_teacher_output_bare_trailing_json() -> None:
    from autocontext.training.autoresearch.trace_collector import parse_teacher_output

    reasoning, strategy = parse_teacher_output('reason first {"points": [3]}')
    assert reasoning == "reason first"
    assert strategy == {"points": [3]}


def test_parse_teacher_output_returns_none_without_json() -> None:
    from autocontext.training.autoresearch.trace_collector import parse_teacher_output

    assert parse_teacher_output("just prose, no construction") is None


def test_build_record_carries_reasoning_and_score() -> None:
    from autocontext.training.autoresearch.trace_collector import build_record

    rec = build_record(scenario="toy", context="ctx", reasoning="because", strategy={"points": [1]}, score=0.5, run_id="t0")
    assert rec["scenario"] == "toy"
    assert rec["reasoning"] == "because"
    assert rec["strategy"] == {"points": [1]}
    assert rec["score"] == 0.5
    assert rec["run_id"] == "t0"


def test_collect_is_provider_agnostic_and_verifies_in_scenario() -> None:
    """Any LLMProvider works; the construction is scored by the scenario, not trusted."""
    from autocontext.training.autoresearch.trace_collector import collect

    # teacher always returns a 5-point construction -> scenario scores it 0.5
    provider = CallableProvider(lambda system, user: 'use a wide spread\n{"points": [1,2,3,4,5]}')
    records = collect(_FakeScenario(), provider, n_traces=3, score_threshold=0.0)

    assert len(records) == 3
    assert all(r["reasoning"] == "use a wide spread" for r in records)
    assert all(r["score"] == 0.5 for r in records)  # verified in-scenario, not from the teacher
    assert all(r["scenario"] == "toy" for r in records)


def test_collect_gates_by_verified_score_threshold() -> None:
    """Low-scoring teacher constructions are excluded from the positive set."""
    from autocontext.training.autoresearch.trace_collector import collect

    provider = CallableProvider(lambda system, user: 'thin\n{"points": [1]}')  # scenario scores 0.1
    records = collect(_FakeScenario(), provider, n_traces=4, score_threshold=0.5)
    assert records == []


def test_collect_skips_unparseable_teacher_output() -> None:
    from autocontext.training.autoresearch.trace_collector import collect

    provider = CallableProvider(lambda system, user: "I refuse to produce JSON")
    records = collect(_FakeScenario(), provider, n_traces=3, score_threshold=0.0)
    assert records == []


class _FakeGameScenario:
    """Game scenario (execute_match), like the built-in grid_ctf / othello."""

    name = "toygame"
    description = "toy game"

    def initial_state(self, seed=None):
        return {}

    def get_task_prompt(self, state=None):
        return "Play the game."

    def execute_match(self, strategy, seed=0):
        from autocontext.scenarios.base import Result

        pts = strategy.get("points", []) if isinstance(strategy, dict) else []
        return Result(score=min(1.0, len(pts) / 10.0), summary="match")


def test_collect_supports_game_scenarios_via_execute_match() -> None:
    """Reviewer F3: built-in scenarios are execute_match games; collect must score
    them via execute_match, not silently drop them by assuming evaluate_output."""
    from autocontext.training.autoresearch.trace_collector import collect

    provider = CallableProvider(lambda system, user: 'spread out\n{"points": [1,2,3,4,5]}')
    records = collect(_FakeGameScenario(), provider, n_traces=2, score_threshold=0.0)
    assert len(records) == 2  # not dropped
    assert all(r["score"] == 0.5 for r in records)  # scored via execute_match
    assert all(r["reasoning"] == "spread out" for r in records)
