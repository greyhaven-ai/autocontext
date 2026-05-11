"""Tests for AC-752: per-round event streaming from ImprovementLoop.

Long-running improvement loops can run silently for many minutes when
`--json` buffers everything until completion. The loop should emit
structured per-round events through an optional `on_event` callback so
callers (e.g. `autoctx improve --ndjson`) can stream progress.
"""

from __future__ import annotations

import sys
import textwrap

from autocontext.execution.improvement_events import ImprovementLoopEvent
from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.output_verifier import OutputVerifier
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class _OneShotPerfectTask(AgentTaskInterface):
    """Judge returns 1.0 on first round, terminating the loop after one
    round when threshold=0.9. Useful for shape-of-event-stream tests."""

    def get_task_prompt(self, state):
        return "."

    def evaluate_output(self, output, state, **kwargs):
        return AgentTaskResult(score=1.0, reasoning="ok", dimension_scores={})

    def revise_output(self, current_output, judge_result, state):
        return current_output + "\nrev"

    def get_rubric(self):
        return "."

    def initial_state(self, seed=None):
        return {}

    def describe_task(self):
        return "."


def _passing_verifier() -> OutputVerifier:
    return OutputVerifier(command=[sys.executable, "-c", "pass"])


def _failing_verifier() -> OutputVerifier:
    script = textwrap.dedent(
        """
        import sys
        print('boom', file=sys.stderr)
        sys.exit(2)
        """
    ).strip()
    return OutputVerifier(command=[sys.executable, "-c", script])


class TestImprovementLoopEventStream:
    def test_loop_emits_minimum_event_sequence_for_single_round(self) -> None:
        # Single round, no verifier. Expected sequence:
        #   round_start, judge_done, round_summary, final
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("initial", {})

        event_types = [e.event for e in events]
        assert event_types == ["round_start", "judge_done", "round_summary", "final"]

    def test_loop_emits_verifier_event_when_verifier_configured(self) -> None:
        # With a passing verifier: round_start, judge_done, verifier_done,
        # round_summary, final.
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=_passing_verifier(),
            on_event=events.append,
        )
        loop.run("initial", {})

        event_types = [e.event for e in events]
        assert event_types == [
            "round_start",
            "judge_done",
            "verifier_done",
            "round_summary",
            "final",
        ]
        verifier_event = next(e for e in events if e.event == "verifier_done")
        assert verifier_event.verifier_ok is True

    def test_verifier_event_records_veto_when_verifier_rejects(self) -> None:
        # A failing verifier should emit verifier_done with verifier_ok=False
        # and a non-zero exit code.
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            output_verifier=_failing_verifier(),
            on_event=events.append,
        )
        loop.run("initial", {})

        verifier_event = next(e for e in events if e.event == "verifier_done")
        assert verifier_event.verifier_ok is False
        assert verifier_event.verifier_exit_code == 2

    def test_judge_event_carries_round_and_score(self) -> None:
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("initial", {})

        judge_event = next(e for e in events if e.event == "judge_done")
        assert judge_event.round == 1
        assert judge_event.score == 1.0

    def test_final_event_carries_summary_fields(self) -> None:
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            on_event=events.append,
        )
        result = loop.run("initial", {})

        final = next(e for e in events if e.event == "final")
        assert final.best_score == result.best_score
        assert final.best_round == result.best_round
        assert final.total_rounds == result.total_rounds
        assert final.met_threshold == result.met_threshold

    def test_no_event_callback_means_no_emission_or_breakage(self) -> None:
        # Backward compatibility: omitting on_event must not break anything.
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
        )
        result = loop.run("initial", {})
        assert result.best_score == 1.0


# -- AC-752: CLI `--ndjson` streams events as JSON lines --


class TestImproveNdjsonFlag:
    """End-to-end check that `autoctx improve --ndjson` writes one JSON line
    per event from the loop (round_start, judge_done, round_summary, final).
    """

    def test_ndjson_emits_one_json_line_per_event(self, tmp_path) -> None:
        import json as _json
        from types import SimpleNamespace
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings
        from autocontext.providers.base import CompletionResult

        runner = CliRunner()

        class _Provider:
            def complete(self, system_prompt, user_prompt, model=None, **_):
                return CompletionResult(text="x", model=model)

            def default_model(self):
                return "m"

        settings = AppSettings(
            db_path=tmp_path / "runs" / "autocontext.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            judge_provider="anthropic",
        )

        # Build a fake ImprovementLoop that records the on_event callback and
        # exercises it with the expected event sequence, then returns a stub
        # result. This isolates the CLI-side --ndjson wiring from the real
        # loop logic (which already has dedicated tests above).
        captured: dict[str, object] = {"on_event_truthy": None}

        class _FakeLoop:
            def __init__(self, **kwargs):
                captured["init_called"] = True
                captured["init_kwargs_keys"] = sorted(kwargs.keys())
                self._on_event = kwargs.get("on_event")
                captured["on_event_truthy"] = self._on_event is not None

            def run(self, **_kwargs):
                captured["run_called"] = True
                if self._on_event is not None:
                    self._on_event(ImprovementLoopEvent(event="round_start", round=1))
                    self._on_event(ImprovementLoopEvent(event="judge_done", round=1, score=0.95))
                    self._on_event(ImprovementLoopEvent(event="round_summary", round=1, effective_score=0.95))
                    self._on_event(
                        ImprovementLoopEvent(
                            event="final",
                            best_score=0.95,
                            best_round=1,
                            total_rounds=1,
                            met_threshold=True,
                        )
                    )
                return SimpleNamespace(
                    best_score=0.95,
                    best_round=1,
                    total_rounds=1,
                    met_threshold=True,
                    best_output="x",
                )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.providers.registry.get_provider", return_value=_Provider()),
            patch("autocontext.execution.improvement_loop.ImprovementLoop", _FakeLoop),
        ):
            result = runner.invoke(
                app,
                [
                    "improve",
                    "-p",
                    "x",
                    "-r",
                    "y",
                    "--ndjson",
                ],
            )

        assert result.exit_code == 0, result.output
        # Sanity: the patched loop was used and received the on_event callback.
        assert captured.get("init_called")
        assert captured.get("on_event_truthy")
        # Every non-empty stdout line is a JSON event (Rich summary is suppressed
        # under --ndjson so stdout is pure newline-delimited JSON).
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        events = [_json.loads(line) for line in lines]
        event_types = [e["event"] for e in events]
        assert event_types == ["round_start", "judge_done", "round_summary", "final"]
        final = events[-1]
        assert final["best_score"] == 0.95
        assert final["best_round"] == 1
        assert final["total_rounds"] == 1
        assert final["met_threshold"] is True
