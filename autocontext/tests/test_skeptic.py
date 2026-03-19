"""Tests for the Skeptic / Red-Team agent (AC-324)."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from autocontext.agents.llm_client import DeterministicDevClient
from autocontext.agents.skeptic import SkepticAgent, SkepticReview, parse_skeptic_review
from autocontext.agents.subagent_runtime import SubagentRuntime
from autocontext.agents.types import AgentOutputs, RoleExecution
from autocontext.config.settings import AppSettings
from autocontext.harness.core.types import RoleUsage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime() -> SubagentRuntime:
    client = DeterministicDevClient()
    return SubagentRuntime(client=client)


def _full_skeptic_output(
    risk: str = "medium",
    concerns: list[str] | None = None,
    recommendation: str = "proceed",
    confidence: int = 7,
) -> str:
    concerns = concerns or ["Overfit to single opponent", "Score jump is suspicious"]
    concern_lines = "\n".join(f"- {c}" for c in concerns)
    return (
        "The proposed strategy shows some concerning patterns.\n\n"
        f"<!-- SKEPTIC_RISK: {risk} -->\n"
        "<!-- SKEPTIC_CONCERNS_START -->\n"
        f"{concern_lines}\n"
        "<!-- SKEPTIC_CONCERNS_END -->\n"
        f"<!-- SKEPTIC_RECOMMENDATION: {recommendation} -->\n"
        f"<!-- SKEPTIC_CONFIDENCE: {confidence} -->\n"
    )


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParseSkepticReview:
    def test_parse_skeptic_review_all_markers(self) -> None:
        content = _full_skeptic_output(
            risk="medium", recommendation="proceed", confidence=7,
            concerns=["Overfit to single opponent", "Score jump is suspicious"],
        )
        review = parse_skeptic_review(content)
        assert review.risk_level == "medium"
        assert review.recommendation == "proceed"
        assert review.confidence == 7
        assert len(review.concerns) == 2
        assert "Overfit to single opponent" in review.concerns[0]
        assert "Score jump is suspicious" in review.concerns[1]
        assert review.parse_success is True
        assert review.reasoning == content

    def test_parse_skeptic_review_high_risk(self) -> None:
        content = _full_skeptic_output(risk="high", recommendation="caution", confidence=9)
        review = parse_skeptic_review(content)
        assert review.risk_level == "high"
        assert review.recommendation == "caution"
        assert review.confidence == 9

    def test_parse_skeptic_review_block(self) -> None:
        content = _full_skeptic_output(recommendation="block", risk="high", confidence=8)
        review = parse_skeptic_review(content)
        assert review.recommendation == "block"
        assert review.risk_level == "high"

    def test_parse_skeptic_review_no_markers(self) -> None:
        content = "This candidate looks fine, no issues found."
        review = parse_skeptic_review(content)
        assert review.risk_level == "low"
        assert review.recommendation == "proceed"
        assert review.confidence == 5
        assert review.concerns == []
        assert review.parse_success is False

    def test_parse_skeptic_review_concerns_extraction(self) -> None:
        concerns = [
            "Pattern overfits to defensive opponents",
            "Score trajectory shows plateau for 3 gens",
            "Contradicts lesson from gen 2",
        ]
        content = _full_skeptic_output(concerns=concerns)
        review = parse_skeptic_review(content)
        assert len(review.concerns) == 3
        assert review.concerns[0] == "Pattern overfits to defensive opponents"
        assert review.concerns[1] == "Score trajectory shows plateau for 3 gens"
        assert review.concerns[2] == "Contradicts lesson from gen 2"

    def test_parse_skeptic_review_confidence_clamping(self) -> None:
        # Confidence > 10 should clamp to 10
        content = (
            "<!-- SKEPTIC_RISK: low -->\n"
            "<!-- SKEPTIC_RECOMMENDATION: proceed -->\n"
            "<!-- SKEPTIC_CONFIDENCE: 15 -->\n"
        )
        review = parse_skeptic_review(content)
        assert review.confidence == 10

        # Confidence < 1 should clamp to 1
        content2 = (
            "<!-- SKEPTIC_RISK: low -->\n"
            "<!-- SKEPTIC_RECOMMENDATION: proceed -->\n"
            "<!-- SKEPTIC_CONFIDENCE: 0 -->\n"
        )
        review2 = parse_skeptic_review(content2)
        assert review2.confidence == 1

    def test_parse_skeptic_review_case_insensitive(self) -> None:
        content = (
            "<!-- SKEPTIC_RISK: HIGH -->\n"
            "<!-- SKEPTIC_RECOMMENDATION: Block -->\n"
            "<!-- SKEPTIC_CONFIDENCE: 6 -->\n"
        )
        review = parse_skeptic_review(content)
        assert review.risk_level == "high"
        assert review.recommendation == "block"
        assert review.parse_success is True


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------

class TestSkepticAgent:
    def test_skeptic_agent_returns_review_and_execution(self) -> None:
        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")
        review, exec_result = agent.review(
            proposed_playbook="## Playbook\n- Strategy A",
            strategy_summary='{"aggression": 0.6}',
            score_trajectory="Gen1: 0.4, Gen2: 0.5",
            recent_analysis="Findings: moderate gains.",
        )
        assert isinstance(review, SkepticReview)
        assert isinstance(exec_result, RoleExecution)
        assert exec_result.role == "skeptic"
        assert exec_result.status == "completed"

    def test_skeptic_agent_constraint_mode(self) -> None:
        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        # Capture the prompt that was sent
        original_run_task = runtime.run_task
        captured_prompts: list[str] = []

        def capture_run_task(task: Any) -> Any:
            captured_prompts.append(task.prompt)
            return original_run_task(task)

        runtime.run_task = capture_run_task  # type: ignore[assignment]

        agent.review(
            proposed_playbook="## Playbook",
            strategy_summary="{}",
            score_trajectory="",
            recent_analysis="",
            constraint_mode=True,
        )
        assert len(captured_prompts) == 1
        assert "Constraints:" in captured_prompts[0]
        assert "Do NOT recommend blocking without citing specific evidence" in captured_prompts[0]

    def test_skeptic_agent_includes_trajectory(self) -> None:
        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        captured_prompts: list[str] = []
        original_run_task = runtime.run_task

        def capture_run_task(task: Any) -> Any:
            captured_prompts.append(task.prompt)
            return original_run_task(task)

        runtime.run_task = capture_run_task  # type: ignore[assignment]

        agent.review(
            proposed_playbook="## PB",
            strategy_summary="{}",
            score_trajectory="Gen1: 0.3, Gen2: 0.5, Gen3: 0.7",
            recent_analysis="Analysis content here.",
        )
        assert "SCORE TRAJECTORY:" in captured_prompts[0]
        assert "Gen1: 0.3, Gen2: 0.5, Gen3: 0.7" in captured_prompts[0]

    def test_skeptic_agent_includes_match_results(self) -> None:
        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        captured_prompts: list[str] = []
        original_run_task = runtime.run_task

        def capture_run_task(task: Any) -> Any:
            captured_prompts.append(task.prompt)
            return original_run_task(task)

        runtime.run_task = capture_run_task  # type: ignore[assignment]

        agent.review(
            proposed_playbook="## PB",
            strategy_summary="{}",
            score_trajectory="",
            recent_analysis="",
            match_results_summary="Match 1: Win, Match 2: Loss",
        )
        assert "MATCH RESULTS SUMMARY:" in captured_prompts[0]
        assert "Match 1: Win, Match 2: Loss" in captured_prompts[0]


# ---------------------------------------------------------------------------
# Stage tests
# ---------------------------------------------------------------------------

def _make_ctx(
    gate_decision: str = "advance",
    coach_playbook: str = "## Playbook\n- Keep defensive anchor.",
    skeptic_can_block: bool = False,
    skeptic_enabled: bool = True,
) -> Any:
    """Build a minimal GenerationContext for stage testing."""
    from autocontext.loop.stage_types import GenerationContext

    settings = AppSettings(
        skeptic_enabled=skeptic_enabled,
        skeptic_can_block=skeptic_can_block,
        curator_enabled=False,
        agent_provider="deterministic",
    )

    outputs = AgentOutputs(
        strategy={"aggression": 0.6},
        analysis_markdown="## Analysis",
        coach_markdown="## Coach",
        coach_playbook=coach_playbook,
        coach_lessons="- lesson 1",
        coach_competitor_hints="- hint 1",
        architect_markdown="## Architect",
        architect_tools=[],
        role_executions=[],
        competitor_output=MagicMock(),
    )

    scenario = MagicMock()
    scenario.name = "grid_ctf"

    ctx = GenerationContext(
        run_id="test-run",
        scenario_name="grid_ctf",
        scenario=scenario,
        generation=2,
        settings=settings,
        previous_best=0.5,
        challenger_elo=1000.0,
        score_history=[0.4, 0.5],
        gate_decision_history=["advance"],
        coach_competitor_hints="",
        replay_narrative="",
        outputs=outputs,
        gate_decision=gate_decision,
    )
    return ctx


def _make_events() -> Any:
    return MagicMock()


def _make_sqlite() -> Any:
    return MagicMock()


def _make_artifacts(tmp_path: Path) -> Any:
    artifacts = MagicMock()
    artifacts.read_latest_advance_analysis.return_value = "Previous analysis content."
    artifacts.knowledge_root = tmp_path
    return artifacts


def _make_trajectory_builder() -> Any:
    builder = MagicMock()
    builder.build_trajectory.return_value = "Gen1: 0.4, Gen2: 0.5"
    return builder


class TestStageSkepticReview:
    def test_stage_skeptic_skips_when_disabled(self) -> None:
        from autocontext.loop.stages import stage_skeptic_review

        ctx = _make_ctx()
        original_playbook = ctx.outputs.coach_playbook
        result = stage_skeptic_review(
            ctx,
            skeptic=None,
            artifacts=MagicMock(),
            trajectory_builder=MagicMock(),
            sqlite=MagicMock(),
            events=MagicMock(),
        )
        assert result.outputs.coach_playbook == original_playbook

    def test_stage_skeptic_skips_non_advance(self, tmp_path: Path) -> None:
        from autocontext.loop.stages import stage_skeptic_review

        ctx = _make_ctx(gate_decision="rollback")
        original_playbook = ctx.outputs.coach_playbook
        skeptic = SkepticAgent(_make_runtime(), model="test-model")
        result = stage_skeptic_review(
            ctx,
            skeptic=skeptic,
            artifacts=_make_artifacts(tmp_path),
            trajectory_builder=_make_trajectory_builder(),
            sqlite=_make_sqlite(),
            events=_make_events(),
        )
        assert result.outputs.coach_playbook == original_playbook

    def test_stage_skeptic_block_clears_playbook(self, tmp_path: Path) -> None:
        from autocontext.loop.stages import stage_skeptic_review

        ctx = _make_ctx(skeptic_can_block=True)

        # Create a skeptic that always returns "block"
        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        def mock_review(**kwargs: Any) -> tuple[SkepticReview, RoleExecution]:
            review = SkepticReview(
                risk_level="high",
                concerns=["Critical overfit detected"],
                recommendation="block",
                confidence=9,
                reasoning="Blocked.",
                parse_success=True,
            )
            exec_result = RoleExecution(
                role="skeptic",
                content="Blocked.",
                usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
                subagent_id="skeptic-test",
                status="completed",
            )
            return review, exec_result

        agent.review = mock_review  # type: ignore[assignment]

        result = stage_skeptic_review(
            ctx,
            skeptic=agent,
            artifacts=_make_artifacts(tmp_path),
            trajectory_builder=_make_trajectory_builder(),
            sqlite=_make_sqlite(),
            events=_make_events(),
        )
        # When block + skeptic_can_block=True, playbook should be cleared
        assert result.outputs.coach_playbook == ""

    def test_stage_skeptic_block_ignored_when_not_enabled(self, tmp_path: Path) -> None:
        from autocontext.loop.stages import stage_skeptic_review

        ctx = _make_ctx(skeptic_can_block=False)
        original_playbook = ctx.outputs.coach_playbook

        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        def mock_review(**kwargs: Any) -> tuple[SkepticReview, RoleExecution]:
            review = SkepticReview(
                risk_level="high",
                concerns=["Critical overfit"],
                recommendation="block",
                confidence=9,
                reasoning="Blocked.",
                parse_success=True,
            )
            exec_result = RoleExecution(
                role="skeptic",
                content="Blocked.",
                usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
                subagent_id="skeptic-test",
                status="completed",
            )
            return review, exec_result

        agent.review = mock_review  # type: ignore[assignment]

        result = stage_skeptic_review(
            ctx,
            skeptic=agent,
            artifacts=_make_artifacts(tmp_path),
            trajectory_builder=_make_trajectory_builder(),
            sqlite=_make_sqlite(),
            events=_make_events(),
        )
        # playbook should NOT be cleared when skeptic_can_block=False
        assert result.outputs.coach_playbook == original_playbook

    def test_stage_skeptic_emits_events(self, tmp_path: Path) -> None:
        from autocontext.loop.stages import stage_skeptic_review

        ctx = _make_ctx()
        events = _make_events()

        runtime = _make_runtime()
        agent = SkepticAgent(runtime, model="test-model")

        def mock_review(**kwargs: Any) -> tuple[SkepticReview, RoleExecution]:
            review = SkepticReview(
                risk_level="medium",
                concerns=["Suspicious gains"],
                recommendation="caution",
                confidence=6,
                reasoning="Caution advised.",
                parse_success=True,
            )
            exec_result = RoleExecution(
                role="skeptic",
                content="Caution advised.",
                usage=RoleUsage(input_tokens=10, output_tokens=5, latency_ms=1, model="test"),
                subagent_id="skeptic-test",
                status="completed",
            )
            return review, exec_result

        agent.review = mock_review  # type: ignore[assignment]

        stage_skeptic_review(
            ctx,
            skeptic=agent,
            artifacts=_make_artifacts(tmp_path),
            trajectory_builder=_make_trajectory_builder(),
            sqlite=_make_sqlite(),
            events=events,
        )

        # Check that both started and completed events were emitted
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "skeptic_started" in event_names
        assert "skeptic_completed" in event_names

        # Check completed event payload
        completed_call = [c for c in events.emit.call_args_list if c.args[0] == "skeptic_completed"][0]
        payload = completed_call.args[1]
        assert payload["risk_level"] == "medium"
        assert payload["recommendation"] == "caution"
        assert payload["concerns_count"] == 1
        assert payload["confidence"] == 6
        assert ctx.skeptic_review is not None
        assert ctx.skeptic_review.recommendation == "caution"


# ---------------------------------------------------------------------------
# Settings tests
# ---------------------------------------------------------------------------

class TestSkepticSettings:
    def test_skeptic_settings_defaults(self) -> None:
        settings = AppSettings()
        assert settings.skeptic_enabled is False
        assert settings.model_skeptic == "claude-opus-4-6"
        assert settings.skeptic_can_block is False

    def test_skeptic_settings_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOCONTEXT_SKEPTIC_ENABLED", "true")
        monkeypatch.setenv("AUTOCONTEXT_MODEL_SKEPTIC", "claude-sonnet-4-5-20250929")
        monkeypatch.setenv("AUTOCONTEXT_SKEPTIC_CAN_BLOCK", "true")
        from autocontext.config.settings import load_settings
        settings = load_settings()
        assert settings.skeptic_enabled is True
        assert settings.model_skeptic == "claude-sonnet-4-5-20250929"
        assert settings.skeptic_can_block is True


# ---------------------------------------------------------------------------
# DeterministicDevClient skeptic branch
# ---------------------------------------------------------------------------

class TestDeterministicSkepticBranch:
    def test_deterministic_client_skeptic_response(self) -> None:
        client = DeterministicDevClient()
        resp = client.generate(
            model="test",
            prompt="You are a skeptic / red-team reviewer. Your job is to argue AGAINST advancing this candidate.",
            max_tokens=2000,
            temperature=0.4,
        )
        assert "SKEPTIC_RISK" in resp.text
        assert "SKEPTIC_RECOMMENDATION" in resp.text
        review = parse_skeptic_review(resp.text)
        assert review.parse_success is True
        assert review.risk_level in ("high", "medium", "low")
        assert review.recommendation in ("proceed", "caution", "block")
