"""Settings-backed output-token budgets + model-aware clamp (AC-905)."""

from __future__ import annotations

from autocontext.providers.token_caps import clamp_output_tokens

from autocontext.config.settings import AppSettings


class TestBudgetSettings:
    def test_defaults_match_previous_literals(self) -> None:
        settings = AppSettings()
        assert settings.competitor_max_tokens == 800
        assert settings.translator_max_tokens == 1024  # deliberate floor raise from 400/200
        assert settings.analyst_max_tokens == 1200
        assert settings.coach_max_tokens == 2000
        assert settings.architect_max_tokens == 1600
        assert settings.curator_max_tokens == 3000
        assert settings.curator_rating_max_tokens == 1200
        assert settings.curator_consolidation_max_tokens == 4000
        assert settings.skeptic_max_tokens == 2000
        assert settings.scenario_designer_max_tokens == 3000
        assert settings.train_codegen_max_tokens == 8000

    def test_env_override(self, monkeypatch) -> None:
        from autocontext.config.settings import load_settings

        monkeypatch.setenv("AUTOCONTEXT_COACH_MAX_TOKENS", "4096")
        assert load_settings().coach_max_tokens == 4096


class TestClampOutputTokens:
    def test_known_capped_model_clamps(self) -> None:
        assert clamp_output_tokens(100_000, "claude-3-haiku-20240307") == 4096

    def test_requested_below_cap_passes(self) -> None:
        assert clamp_output_tokens(2000, "claude-3-haiku-20240307") == 2000

    def test_unknown_model_passes_through(self) -> None:
        assert clamp_output_tokens(100_000, "future-model-9000") == 100_000

    def test_none_model_passes_through(self) -> None:
        assert clamp_output_tokens(5000, None) == 5000
