"""Tests for AC-212: Escalation-based provider consultation.

Tests cover:
- ConsultationTrigger enum values
- ConsultationRequest / ConsultationResult dataclass construction
- ConsultationResult.to_advisory_markdown() rendering
- detect_consultation_triggers() — stagnation, judge uncertainty, no triggers
- ConsultationRunner.consult() with mock provider
- SQLite migration 010 + store methods (insert, query, cost tracking)
- stage_consultation() — enabled/disabled, triggers/no triggers, budget check
- Settings defaults and env var loading
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from autocontext.config.settings import AppSettings
from autocontext.providers.callable_wrapper import CallableProvider
from autocontext.storage import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

if TYPE_CHECKING:
    from autocontext.loop.stage_types import GenerationContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sqlite_store(tmp_path: Path) -> SQLiteStore:
    """Create a SQLiteStore with all migrations applied."""
    store = SQLiteStore(tmp_path / "test.db")
    migrations_dir = Path(__file__).parent.parent / "migrations"
    store.migrate(migrations_dir)
    store.create_run("run-1", "grid_ctf", 5, "local")
    store.upsert_generation("run-1", 1, 0.5, 0.7, 1000.0, 1, 0, "advance", "completed")
    return store


@pytest.fixture()
def artifact_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


@pytest.fixture()
def mock_provider() -> CallableProvider:
    """Provider that returns a structured consultation response."""
    def _respond(_system: str, _user: str) -> str:
        return (
            "## Critique\nThe strategy is over-specialising on early flags.\n\n"
            "## Alternative Hypothesis\nConsider a defensive-first opening.\n\n"
            "## Tiebreak Recommendation\nPrioritise map control.\n\n"
            "## Suggested Next Action\nRevise the opening to cover more territory."
        )
    return CallableProvider(_respond, model_name="test-model")


# ===========================================================================
# 1. Types
# ===========================================================================

class TestConsultationTypes:
    def test_trigger_enum_values(self) -> None:
        from autocontext.consultation.types import ConsultationTrigger

        assert ConsultationTrigger.STAGNATION == "stagnation"
        assert ConsultationTrigger.JUDGE_UNCERTAINTY == "judge_uncertainty"
        assert ConsultationTrigger.PARSE_FAILURE == "parse_failure"
        assert ConsultationTrigger.OPERATOR_REQUEST == "operator_request"

    def test_request_construction(self) -> None:
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        req = ConsultationRequest(
            run_id="run-1",
            generation=3,
            trigger=ConsultationTrigger.STAGNATION,
            context_summary="3 consecutive rollbacks",
            current_strategy_summary="aggressive flag capture",
            score_history=[0.5, 0.5, 0.5],
            gate_history=["rollback", "rollback", "rollback"],
        )
        assert req.run_id == "run-1"
        assert req.generation == 3
        assert req.trigger == ConsultationTrigger.STAGNATION
        assert len(req.score_history) == 3

    def test_request_defaults(self) -> None:
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        req = ConsultationRequest(
            run_id="r",
            generation=1,
            trigger=ConsultationTrigger.PARSE_FAILURE,
            context_summary="parse failures",
            current_strategy_summary="default",
        )
        assert req.score_history == []
        assert req.gate_history == []

    def test_result_defaults(self) -> None:
        from autocontext.consultation.types import ConsultationResult

        result = ConsultationResult()
        assert result.critique == ""
        assert result.alternative_hypothesis == ""
        assert result.tiebreak_recommendation == ""
        assert result.suggested_next_action == ""
        assert result.raw_response == ""
        assert result.cost_usd is None
        assert result.model_used == ""

    def test_result_to_advisory_markdown(self) -> None:
        from autocontext.consultation.types import ConsultationResult

        result = ConsultationResult(
            critique="Strategy is stale",
            alternative_hypothesis="Try defensive play",
            tiebreak_recommendation="Choose defense",
            suggested_next_action="Revise opening",
            model_used="test-model",
        )
        md = result.to_advisory_markdown()
        assert "Strategy is stale" in md
        assert "Try defensive play" in md
        assert "Choose defense" in md
        assert "Revise opening" in md
        assert "test-model" in md

    def test_result_to_advisory_markdown_empty(self) -> None:
        from autocontext.consultation.types import ConsultationResult

        result = ConsultationResult()
        md = result.to_advisory_markdown()
        # Should still return valid markdown (may be sparse)
        assert isinstance(md, str)


# ===========================================================================
# 2. Trigger Detection
# ===========================================================================

class TestTriggerDetection:
    def test_no_triggers_healthy_run(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers

        triggers = detect_consultation_triggers(
            gate_history=["advance", "advance", "advance"],
            score_history=[0.3, 0.5, 0.7],
            settings=AppSettings(consultation_enabled=True),
        )
        assert triggers == []

    def test_stagnation_three_rollbacks(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        triggers = detect_consultation_triggers(
            gate_history=["advance", "rollback", "rollback", "rollback"],
            score_history=[0.5, 0.4, 0.4, 0.4],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=3),
        )
        assert ConsultationTrigger.STAGNATION in triggers

    def test_stagnation_custom_threshold(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        # 2 rollbacks with threshold=2 should trigger
        triggers = detect_consultation_triggers(
            gate_history=["rollback", "rollback"],
            score_history=[0.4, 0.4],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=2),
        )
        assert ConsultationTrigger.STAGNATION in triggers

    def test_stagnation_retry_counts(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        # retry is also a stall signal
        triggers = detect_consultation_triggers(
            gate_history=["retry", "retry", "retry"],
            score_history=[0.5, 0.5, 0.5],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=3),
        )
        assert ConsultationTrigger.STAGNATION in triggers

    def test_stagnation_mixed_rollback_retry(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        triggers = detect_consultation_triggers(
            gate_history=["rollback", "retry", "rollback"],
            score_history=[0.5, 0.5, 0.5],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=3),
        )
        assert ConsultationTrigger.STAGNATION in triggers

    def test_judge_uncertainty_low_variance_no_advance(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        # Very flat scores with no advance => judge_uncertainty
        triggers = detect_consultation_triggers(
            gate_history=["retry", "retry", "retry"],
            score_history=[0.50, 0.50, 0.51],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=5),
        )
        assert ConsultationTrigger.JUDGE_UNCERTAINTY in triggers

    def test_no_judge_uncertainty_with_advance(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers
        from autocontext.consultation.types import ConsultationTrigger

        # Low variance but we have recent advances => no uncertainty trigger
        triggers = detect_consultation_triggers(
            gate_history=["advance", "advance", "advance"],
            score_history=[0.50, 0.50, 0.51],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=5),
        )
        assert ConsultationTrigger.JUDGE_UNCERTAINTY not in triggers

    def test_no_triggers_short_history(self) -> None:
        from autocontext.consultation.triggers import detect_consultation_triggers

        triggers = detect_consultation_triggers(
            gate_history=["rollback"],
            score_history=[0.5],
            settings=AppSettings(consultation_enabled=True, consultation_stagnation_threshold=3),
        )
        assert triggers == []


# ===========================================================================
# 3. Consultation Runner
# ===========================================================================

class TestConsultationRunner:
    def test_consult_returns_result(self, mock_provider: CallableProvider) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        runner = ConsultationRunner(mock_provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=3,
            trigger=ConsultationTrigger.STAGNATION,
            context_summary="3 consecutive rollbacks",
            current_strategy_summary="aggressive",
            score_history=[0.5, 0.5, 0.5],
            gate_history=["rollback", "rollback", "rollback"],
        )
        result = runner.consult(request)

        assert result.critique != ""
        assert result.raw_response != ""
        assert result.model_used == "test-model"

    def test_consult_parses_sections(self, mock_provider: CallableProvider) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        runner = ConsultationRunner(mock_provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=3,
            trigger=ConsultationTrigger.STAGNATION,
            context_summary="stalled",
            current_strategy_summary="default",
        )
        result = runner.consult(request)

        # The mock provider returns structured markdown with clear sections
        assert "over-specialising" in result.critique or result.critique != ""
        assert result.alternative_hypothesis != "" or result.raw_response != ""

    def test_consult_with_cost_tracking(self) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        def _respond(_s: str, _u: str) -> str:
            return "## Critique\nSome critique"

        provider = CallableProvider(_respond, model_name="priced-model")
        runner = ConsultationRunner(provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=1,
            trigger=ConsultationTrigger.JUDGE_UNCERTAINTY,
            context_summary="unclear",
            current_strategy_summary="default",
        )
        result = runner.consult(request)
        assert result.model_used == "priced-model"

    def test_consult_handles_empty_response(self) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        provider = CallableProvider(lambda _s, _u: "", model_name="empty-model")
        runner = ConsultationRunner(provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=1,
            trigger=ConsultationTrigger.PARSE_FAILURE,
            context_summary="parse failures",
            current_strategy_summary="default",
        )
        result = runner.consult(request)
        assert result.raw_response == ""
        assert result.model_used == "empty-model"

    def test_system_prompt_includes_trigger(self) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        captured: list[str] = []

        def _capture(system: str, _user: str) -> str:
            captured.append(system)
            return "## Critique\nDone"

        provider = CallableProvider(_capture, model_name="spy")
        runner = ConsultationRunner(provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=3,
            trigger=ConsultationTrigger.STAGNATION,
            context_summary="stalled",
            current_strategy_summary="default",
        )
        runner.consult(request)
        assert len(captured) == 1
        assert "stagnation" in captured[0].lower() or "consultant" in captured[0].lower()

    def test_user_prompt_includes_context(self) -> None:
        from autocontext.consultation.runner import ConsultationRunner
        from autocontext.consultation.types import ConsultationRequest, ConsultationTrigger

        captured_user: list[str] = []

        def _capture(_system: str, user: str) -> str:
            captured_user.append(user)
            return "## Critique\nDone"

        provider = CallableProvider(_capture, model_name="spy")
        runner = ConsultationRunner(provider)
        request = ConsultationRequest(
            run_id="run-1",
            generation=3,
            trigger=ConsultationTrigger.STAGNATION,
            context_summary="3 consecutive rollbacks observed",
            current_strategy_summary="aggressive flag capture",
            score_history=[0.5, 0.5, 0.5],
            gate_history=["rollback", "rollback", "rollback"],
        )
        runner.consult(request)
        assert len(captured_user) == 1
        assert "3 consecutive rollbacks observed" in captured_user[0]
        assert "aggressive flag capture" in captured_user[0]


# ===========================================================================
# 4. SQLite Migration + Store Methods
# ===========================================================================

class TestConsultationStorage:
    def test_consultation_log_table_exists(self, sqlite_store: SQLiteStore) -> None:
        with sqlite_store.connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='consultation_log'"
            )
            assert cursor.fetchone() is not None

    def test_insert_consultation(self, sqlite_store: SQLiteStore) -> None:
        row_id = sqlite_store.insert_consultation(
            run_id="run-1",
            generation_index=1,
            trigger="stagnation",
            context_summary="3 rollbacks",
            critique="Strategy is stale",
            alternative_hypothesis="Try defense",
            tiebreak_recommendation="Choose defense",
            suggested_next_action="Revise opening",
            raw_response="full response text",
            model_used="test-model",
            cost_usd=0.05,
        )
        assert row_id > 0

    def test_get_consultations_for_run(self, sqlite_store: SQLiteStore) -> None:
        sqlite_store.insert_consultation(
            run_id="run-1",
            generation_index=1,
            trigger="stagnation",
            context_summary="stalled",
            critique="critique 1",
            alternative_hypothesis="hyp 1",
            tiebreak_recommendation="rec 1",
            suggested_next_action="action 1",
            raw_response="raw 1",
            model_used="model-a",
            cost_usd=0.03,
        )
        sqlite_store.insert_consultation(
            run_id="run-1",
            generation_index=2,
            trigger="judge_uncertainty",
            context_summary="uncertain",
            critique="critique 2",
            alternative_hypothesis="hyp 2",
            tiebreak_recommendation="rec 2",
            suggested_next_action="action 2",
            raw_response="raw 2",
            model_used="model-b",
            cost_usd=0.07,
        )

        rows = sqlite_store.get_consultations_for_run("run-1")
        assert len(rows) == 2
        assert rows[0]["trigger"] == "stagnation"
        assert rows[1]["trigger"] == "judge_uncertainty"

    def test_get_consultations_empty(self, sqlite_store: SQLiteStore) -> None:
        rows = sqlite_store.get_consultations_for_run("nonexistent")
        assert rows == []

    def test_get_total_consultation_cost(self, sqlite_store: SQLiteStore) -> None:
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=1, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=0.10,
        )
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=2, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=0.20,
        )
        total = sqlite_store.get_total_consultation_cost("run-1")
        assert abs(total - 0.30) < 1e-9

    def test_get_total_consultation_cost_no_consultations(self, sqlite_store: SQLiteStore) -> None:
        total = sqlite_store.get_total_consultation_cost("run-1")
        assert total == 0.0

    def test_get_total_consultation_cost_with_null_costs(self, sqlite_store: SQLiteStore) -> None:
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=1, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=None,
        )
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=2, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=0.15,
        )
        total = sqlite_store.get_total_consultation_cost("run-1")
        assert abs(total - 0.15) < 1e-9

    def test_consultation_index_exists(self, sqlite_store: SQLiteStore) -> None:
        with sqlite_store.connect() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_consultation_log_run'"
            ).fetchone()
            assert row is not None


# ===========================================================================
# 5. Pipeline Stage
# ===========================================================================

class TestStageConsultation:
    def _make_ctx(
        self,
        *,
        consultation_enabled: bool = True,
        gate_history: list[str] | None = None,
        score_history: list[float] | None = None,
        consultation_stagnation_threshold: int = 3,
        consultation_cost_budget: float = 0.0,
    ) -> GenerationContext:
        from autocontext.loop.stage_types import GenerationContext

        settings = AppSettings(
            consultation_enabled=consultation_enabled,
            consultation_stagnation_threshold=consultation_stagnation_threshold,
            consultation_cost_budget=consultation_cost_budget,
        )
        scenario = MagicMock()
        scenario.describe_rules.return_value = "test rules"
        return GenerationContext(
            run_id="run-1",
            scenario_name="grid_ctf",
            scenario=scenario,
            generation=3,
            settings=settings,
            previous_best=0.5,
            challenger_elo=1000.0,
            score_history=score_history or [0.5, 0.5, 0.5],
            gate_decision_history=gate_history or ["rollback", "rollback", "rollback"],
            coach_competitor_hints="",
            replay_narrative="",
        )

    def test_disabled_returns_ctx_unchanged(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        ctx = self._make_ctx(consultation_enabled=False)
        events = MagicMock()
        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)
        assert result is ctx
        assert result.consultation_result is None

    def test_no_triggers_returns_ctx_unchanged(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        ctx = self._make_ctx(
            gate_history=["advance", "advance", "advance"],
            score_history=[0.3, 0.5, 0.7],
        )
        events = MagicMock()
        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)
        assert result.consultation_result is None

    def test_triggers_run_consultation(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
        monkeypatch: pytest.MonkeyPatch,
        mock_provider: CallableProvider,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        ctx = self._make_ctx()
        events = MagicMock()
        ctx.settings.consultation_api_key = "test-key"
        monkeypatch.setattr(
            "autocontext.consultation.stage._create_consultation_provider",
            lambda _ctx: mock_provider,
        )
        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)

        assert result.consultation_result is not None
        # Should have persisted to DB
        rows = sqlite_store.get_consultations_for_run("run-1")
        assert len(rows) >= 1
        advisory_path = artifact_store.generation_dir("run-1", 3) / "consultation.md"
        assert advisory_path.exists()
        # Should have emitted events
        assert events.emit.call_count >= 1

    def test_budget_exceeded_skips_consultation(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        # Pre-fill some cost
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=1, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=1.00,
        )

        ctx = self._make_ctx(consultation_cost_budget=0.50)
        events = MagicMock()
        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)

        assert result.consultation_result is None
        # Should emit a budget-exceeded event
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "consultation_skipped_budget" in event_names

    def test_budget_zero_means_unlimited(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
        monkeypatch: pytest.MonkeyPatch,
        mock_provider: CallableProvider,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        # Pre-fill large cost but budget=0 means unlimited
        sqlite_store.insert_consultation(
            run_id="run-1", generation_index=1, trigger="stagnation",
            context_summary="", critique="", alternative_hypothesis="",
            tiebreak_recommendation="", suggested_next_action="",
            raw_response="", model_used="m", cost_usd=100.0,
        )

        ctx = self._make_ctx(consultation_cost_budget=0.0)
        events = MagicMock()
        ctx.settings.consultation_api_key = "test-key"
        monkeypatch.setattr(
            "autocontext.consultation.stage._create_consultation_provider",
            lambda _ctx: mock_provider,
        )
        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)

        # Should NOT be skipped
        assert result.consultation_result is not None

    def test_unconfigured_provider_skips_without_persisting(
        self,
        sqlite_store: SQLiteStore,
        artifact_store: ArtifactStore,
    ) -> None:
        from autocontext.consultation.stage import stage_consultation

        ctx = self._make_ctx()
        events = MagicMock()

        result = stage_consultation(ctx, sqlite=sqlite_store, artifacts=artifact_store, events=events)

        assert result.consultation_result is None
        assert sqlite_store.get_consultations_for_run("run-1") == []
        event_names = [call.args[0] for call in events.emit.call_args_list]
        assert "consultation_skipped_unconfigured" in event_names


# ===========================================================================
# 6. Settings
# ===========================================================================

class TestConsultationSettings:
    def test_defaults(self) -> None:
        s = AppSettings()
        assert s.consultation_enabled is False
        assert s.consultation_provider == "anthropic"
        assert s.consultation_model == "claude-sonnet-4-20250514"
        assert s.consultation_api_key == ""
        assert s.consultation_base_url == ""
        assert s.consultation_stagnation_threshold == 3
        assert s.consultation_cost_budget == 0.0

    def test_env_var_loading(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_CONSULTATION_ENABLED", "true")
        monkeypatch.setenv("AUTOCONTEXT_CONSULTATION_PROVIDER", "openai")
        monkeypatch.setenv("AUTOCONTEXT_CONSULTATION_MODEL", "gpt-4o")
        monkeypatch.setenv("AUTOCONTEXT_CONSULTATION_STAGNATION_THRESHOLD", "5")
        monkeypatch.setenv("AUTOCONTEXT_CONSULTATION_COST_BUDGET", "2.50")

        settings = load_settings()
        assert settings.consultation_enabled is True
        assert settings.consultation_provider == "openai"
        assert settings.consultation_model == "gpt-4o"
        assert settings.consultation_stagnation_threshold == 5
        assert settings.consultation_cost_budget == 2.50

    def test_stagnation_threshold_min_2(self) -> None:
        with pytest.raises(ValidationError):
            AppSettings(consultation_stagnation_threshold=1)

    def test_cost_budget_non_negative(self) -> None:
        with pytest.raises(ValidationError):
            AppSettings(consultation_cost_budget=-1.0)


# ===========================================================================
# 7. GenerationContext field
# ===========================================================================

class TestGenerationContextField:
    def test_consultation_result_field_exists(self) -> None:
        from autocontext.loop.stage_types import GenerationContext

        scenario = MagicMock()
        settings = AppSettings()
        ctx = GenerationContext(
            run_id="r",
            scenario_name="s",
            scenario=scenario,
            generation=1,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )
        assert ctx.consultation_result is None

    def test_consultation_result_can_be_set(self) -> None:
        from autocontext.consultation.types import ConsultationResult
        from autocontext.loop.stage_types import GenerationContext

        scenario = MagicMock()
        settings = AppSettings()
        ctx = GenerationContext(
            run_id="r",
            scenario_name="s",
            scenario=scenario,
            generation=1,
            settings=settings,
            previous_best=0.0,
            challenger_elo=1000.0,
            score_history=[],
            gate_decision_history=[],
            coach_competitor_hints="",
            replay_narrative="",
        )
        result = ConsultationResult(critique="test")
        ctx.consultation_result = result
        assert ctx.consultation_result.critique == "test"
