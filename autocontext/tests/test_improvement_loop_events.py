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


class TestImprovementLoopEventFieldOrder:
    """AC-753 PR #925 review: keep positional construction backwards-compatible.

    The dataclass is not keyword-only, so the order of fields matters for any
    external code constructing events positionally. `event, round, score, ...`
    was the contract before `output` was added; `output` must come after the
    existing fields so positional construction keeps working.
    """

    def test_positional_score_argument_still_lands_on_score(self) -> None:
        e = ImprovementLoopEvent("judge_done", 1, 0.95)
        # Before the field-order fix, 0.95 silently landed on `output`.
        assert e.score == 0.95
        assert e.output is None


class TestImprovementLoopEventStream:
    def test_loop_emits_minimum_event_sequence_for_single_round(self) -> None:
        # Single round, no verifier. Expected sequence:
        #   round_start, revision_done, judge_done, round_summary, final
        # (AC-753: revision_done carries the output being evaluated)
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("initial", {})

        event_types = [e.event for e in events]
        assert event_types == [
            "round_start",
            "revision_done",
            "judge_done",
            "round_summary",
            "final",
        ]

    def test_loop_emits_verifier_event_when_verifier_configured(self) -> None:
        # With a passing verifier: round_start, revision_done, judge_done,
        # verifier_done, round_summary, final.
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
            "revision_done",
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


# -- AC-753: revision_done events carry per-round output content --


class _TwoRoundUpgradingTask(AgentTaskInterface):
    """Round 1 returns a low score (forces a revision), round 2 returns high.
    The revision appends a marker so tests can distinguish round-1 output
    (the seed) from round-2 output (the revision)."""

    def get_task_prompt(self, state):
        return "."

    def evaluate_output(self, output, state, **kwargs):
        score = 0.95 if "REVISED" in output else 0.1
        return AgentTaskResult(score=score, reasoning="x", dimension_scores={})

    def revise_output(self, current_output, judge_result, state):
        return current_output + "\nREVISED"

    def get_rubric(self):
        return "."

    def initial_state(self, seed=None):
        return {}

    def describe_task(self):
        return "."


class TestRevisionDoneEvent:
    """AC-753: each round emits a revision_done event carrying the output
    being evaluated, so consumers can salvage verifier-vetoed near-misses
    without rerunning."""

    def test_revision_done_carries_seed_on_round_one(self) -> None:
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_OneShotPerfectTask(),
            max_rounds=1,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("initial-seed", {})

        rev_events = [e for e in events if e.event == "revision_done"]
        assert len(rev_events) == 1
        assert rev_events[0].round == 1
        assert rev_events[0].output == "initial-seed"

    def test_revision_done_carries_revised_output_on_subsequent_rounds(self) -> None:
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_TwoRoundUpgradingTask(),
            max_rounds=2,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("seed", {})

        rev_events = [e for e in events if e.event == "revision_done"]
        assert [e.round for e in rev_events] == [1, 2]
        assert rev_events[0].output == "seed"
        # Round 2's revision_done carries the revised content produced by
        # task.revise_output() at the end of round 1.
        assert rev_events[1].output is not None
        assert "REVISED" in rev_events[1].output

    def test_revision_done_fires_immediately_after_round_start(self) -> None:
        # Strict ordering: revision_done must come right after round_start
        # for the same round, before judge_done. This lets consumers see
        # the input the judge is about to evaluate.
        events: list[ImprovementLoopEvent] = []
        loop = ImprovementLoop(
            task=_TwoRoundUpgradingTask(),
            max_rounds=2,
            quality_threshold=0.9,
            on_event=events.append,
        )
        loop.run("seed", {})

        # Look at the first three events of each round:
        types = [e.event for e in events]
        first_round = types[: types.index("round_summary") + 1]
        assert first_round[:3] == ["round_start", "revision_done", "judge_done"]


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

    def test_ndjson_keeps_stdout_parseable_on_provider_error(self, tmp_path) -> None:
        # AC-752 (P2 follow-up): when --ndjson is set and a provider raises, the
        # CLI must not write Rich/plain text to stdout (would poison the ndjson
        # stream). Either an error event line on stdout, or write to stderr.
        import json as _json
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings
        from autocontext.providers.base import ProviderError

        runner = CliRunner()

        class _BoomProvider:
            def complete(self, *_args, **_kwargs):
                raise ProviderError("ClaudeCLIRuntime failed: timeout")

            def default_model(self):
                return "claude-cli"

        settings = AppSettings(
            db_path=tmp_path / "runs" / "autocontext.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            judge_provider="claude-cli",
        )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.providers.registry.get_provider", return_value=_BoomProvider()),
        ):
            result = runner.invoke(
                app,
                ["improve", "-p", "x", "-r", "y", "--provider", "claude-cli", "--ndjson"],
            )

        assert result.exit_code == 1
        # Every non-empty stdout line must be valid JSON, so ndjson consumers
        # can parse uniformly. An error event is fine; raw text is not.
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            _json.loads(line)  # raises if any stdout line is non-JSON

    def test_json_and_ndjson_combination_is_rejected(self, tmp_path) -> None:
        # AC-752 (P3 follow-up): --json (final-blob) and --ndjson (streaming) are
        # mutually exclusive output modes. Passing both produces a mixed,
        # un-parseable stream. The CLI should reject the combination up front.
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings

        runner = CliRunner()

        settings = AppSettings(
            db_path=tmp_path / "runs" / "autocontext.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
            judge_provider="anthropic",
        )

        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(app, ["improve", "-p", "x", "-r", "y", "--json", "--ndjson"])

        assert result.exit_code != 0
        # The error message should mention both flags so the user knows why.
        combined = (result.stdout + (result.stderr or "")).lower()
        assert "--json" in combined and "--ndjson" in combined

    def test_ndjson_includes_revision_done_with_output_by_default(self, tmp_path) -> None:
        # AC-753: by default, --ndjson emits revision_done events carrying
        # the per-round output content so consumers can salvage near-misses.
        import json as _json
        from types import SimpleNamespace
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings
        from autocontext.providers.base import CompletionResult

        runner = CliRunner()

        class _Provider:
            def complete(self, *args, **kwargs):
                return CompletionResult(text="x", model=None)

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

        class _FakeLoop:
            def __init__(self, **kwargs):
                self._on_event = kwargs.get("on_event") or (lambda _e: None)

            def run(self, **_kwargs):
                self._on_event(ImprovementLoopEvent(event="round_start", round=1))
                self._on_event(ImprovementLoopEvent(event="revision_done", round=1, output="lean code v1"))
                self._on_event(ImprovementLoopEvent(event="judge_done", round=1, score=0.9))
                self._on_event(ImprovementLoopEvent(event="round_summary", round=1, effective_score=0.9))
                self._on_event(
                    ImprovementLoopEvent(
                        event="final",
                        best_score=0.9,
                        best_round=1,
                        total_rounds=1,
                        met_threshold=True,
                    )
                )
                return SimpleNamespace(
                    best_score=0.9,
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
            result = runner.invoke(app, ["improve", "-p", "x", "-r", "y", "--ndjson"])

        assert result.exit_code == 0, result.output
        events = [_json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        rev = next(e for e in events if e["event"] == "revision_done")
        assert rev["round"] == 1
        assert rev["output"] == "lean code v1"

    def test_no_ndjson_include_output_suppresses_revision_done(self, tmp_path) -> None:
        # AC-753: --no-ndjson-include-output drops revision_done events
        # entirely (their only payload is the output content). Other events
        # are still emitted unchanged.
        import json as _json
        from types import SimpleNamespace
        from unittest.mock import patch

        from typer.testing import CliRunner

        from autocontext.cli import app
        from autocontext.config.settings import AppSettings
        from autocontext.providers.base import CompletionResult

        runner = CliRunner()

        class _Provider:
            def complete(self, *args, **kwargs):
                return CompletionResult(text="x", model=None)

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

        class _FakeLoop:
            def __init__(self, **kwargs):
                self._on_event = kwargs.get("on_event") or (lambda _e: None)

            def run(self, **_kwargs):
                self._on_event(ImprovementLoopEvent(event="round_start", round=1))
                self._on_event(ImprovementLoopEvent(event="revision_done", round=1, output="bulky-lean-code"))
                self._on_event(ImprovementLoopEvent(event="judge_done", round=1, score=0.9))
                self._on_event(ImprovementLoopEvent(event="round_summary", round=1, effective_score=0.9))
                self._on_event(
                    ImprovementLoopEvent(
                        event="final",
                        best_score=0.9,
                        best_round=1,
                        total_rounds=1,
                        met_threshold=True,
                    )
                )
                return SimpleNamespace(
                    best_score=0.9,
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
                ["improve", "-p", "x", "-r", "y", "--ndjson", "--no-ndjson-include-output"],
            )

        assert result.exit_code == 0, result.output
        events = [_json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        types = [e["event"] for e in events]
        assert "revision_done" not in types
        # Other events still present.
        assert "round_start" in types
        assert "judge_done" in types
        assert "round_summary" in types
        assert "final" in types
        # Defense-in-depth: the suppressed-output mode must not leak the
        # bulk-output payload anywhere in stdout.
        assert "bulky-lean-code" not in result.stdout
