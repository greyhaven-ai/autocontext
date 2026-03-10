"""Tests for HarnessMode enum and validation (MTS-83)."""
from __future__ import annotations

from unittest.mock import patch

from mts.config.settings import AppSettings, HarnessMode, load_settings, validate_harness_mode

# ---------------------------------------------------------------------------
# HarnessMode enum
# ---------------------------------------------------------------------------


class TestHarnessMode:
    def test_enum_values(self) -> None:
        assert HarnessMode.NONE == "none"
        assert HarnessMode.FILTER == "filter"
        assert HarnessMode.VERIFY == "verify"
        assert HarnessMode.POLICY == "policy"

    def test_enum_from_string(self) -> None:
        assert HarnessMode("none") is HarnessMode.NONE
        assert HarnessMode("filter") is HarnessMode.FILTER
        assert HarnessMode("verify") is HarnessMode.VERIFY
        assert HarnessMode("policy") is HarnessMode.POLICY


# ---------------------------------------------------------------------------
# AppSettings defaults
# ---------------------------------------------------------------------------


class TestHarnessModeSettings:
    def test_default_is_none(self) -> None:
        settings = AppSettings()
        assert settings.harness_mode is HarnessMode.NONE

    def test_explicit_mode(self) -> None:
        settings = AppSettings(harness_mode=HarnessMode.FILTER)
        assert settings.harness_mode is HarnessMode.FILTER

    def test_env_var_parsing(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "verify", "MTS_HARNESS_VALIDATORS_ENABLED": "true"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.VERIFY

    def test_env_var_filter(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "filter", "MTS_HARNESS_VALIDATORS_ENABLED": "true"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.FILTER

    def test_env_var_policy(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "policy"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.POLICY

    def test_env_var_none(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "none"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.NONE

    def test_load_settings_applies_mode_fallback(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "verify", "MTS_HARNESS_VALIDATORS_ENABLED": "false"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.NONE

    def test_load_settings_policy_enables_code_strategies(self) -> None:
        with patch.dict("os.environ", {"MTS_HARNESS_MODE": "policy", "MTS_CODE_STRATEGIES_ENABLED": "false"}):
            settings = load_settings()
            assert settings.harness_mode is HarnessMode.POLICY
            assert settings.code_strategies_enabled is True


# ---------------------------------------------------------------------------
# validate_harness_mode
# ---------------------------------------------------------------------------


class TestValidateHarnessMode:
    def test_none_passes_through(self) -> None:
        settings = AppSettings(harness_mode=HarnessMode.NONE)
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.NONE

    def test_filter_without_validators_falls_back(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.FILTER,
            harness_validators_enabled=False,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.NONE

    def test_filter_with_validators_ok(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.FILTER,
            harness_validators_enabled=True,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.FILTER

    def test_verify_without_validators_falls_back(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.VERIFY,
            harness_validators_enabled=False,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.NONE

    def test_verify_with_validators_ok(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.VERIFY,
            harness_validators_enabled=True,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.VERIFY

    def test_policy_enables_code_strategies(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.POLICY,
            code_strategies_enabled=False,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.POLICY
        assert result.code_strategies_enabled is True

    def test_policy_with_code_strategies_already_on(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.POLICY,
            code_strategies_enabled=True,
        )
        result = validate_harness_mode(settings)
        assert result.harness_mode is HarnessMode.POLICY
        assert result.code_strategies_enabled is True

    def test_validate_does_not_mutate_original(self) -> None:
        settings = AppSettings(
            harness_mode=HarnessMode.FILTER,
            harness_validators_enabled=False,
        )
        result = validate_harness_mode(settings)
        assert settings.harness_mode is HarnessMode.FILTER  # original unchanged
        assert result.harness_mode is HarnessMode.NONE  # new copy modified
