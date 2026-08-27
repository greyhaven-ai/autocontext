from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, cast

import pytest  # type: ignore[import-not-found]
from pydantic import ValidationError  # type: ignore[import-not-found]

from autocontext.config.settings import AppSettings, load_settings, setting_env_keys  # type: ignore[import-untyped]


def _contract() -> dict[str, Any]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "app-settings-contract.json"
    return cast(dict[str, Any], json.loads(contract_path.read_text(encoding="utf-8")))


def _contract_value(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return value.value
    return value


def _field_env(field: dict[str, Any], runtime: str) -> list[str]:
    runtime_key = f"{runtime}_env"
    if runtime_key in field:
        return list(field[runtime_key])
    return list(field["env"])


def test_python_app_settings_contract_covers_live_shared_fields() -> None:
    contract_names = {field["python"] for field in _contract()["fields"]}

    expected_shared_fields = {
        "browser_allowed_domains",
        "browser_enabled",
        "consultation_enabled",
        "generation_time_budget_seconds",
        "monitor_heartbeat_timeout",
        "simplicity_mode",
    }

    assert expected_shared_fields <= contract_names


def test_python_app_settings_defaults_and_env_aliases_match_shared_contract() -> None:
    settings = AppSettings()

    for field in _contract()["fields"]:
        python_name = field["python"]
        assert _contract_value(getattr(settings, python_name)) == field["default"], python_name
        assert list(setting_env_keys(python_name)) == _field_env(field, "python"), python_name


def test_python_app_settings_ignores_unknown_fields_like_shared_contract() -> None:
    assert _contract()["unknown_field_policy"] == "ignore"

    settings = AppSettings.model_validate({"not_a_portable_setting": "ignored"})

    assert not hasattr(settings, "not_a_portable_setting")


def test_python_load_settings_consumes_contract_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTOCONTEXT_AGENT_PROVIDER", raising=False)
    monkeypatch.setenv("AUTOCONTEXT_PROVIDER", "deterministic")

    assert load_settings().agent_provider == "deterministic"


def test_python_app_settings_rejects_representative_invalid_shared_values() -> None:
    invalid_cases = [
        ("matches_per_generation", 0),
        ("claude_timeout", 0),
        ("browser_profile_mode", "shared"),
        ("monitor_max_conditions", 0),
        ("simplicity_mode", "strict"),
    ]

    for field_name, value in invalid_cases:
        with pytest.raises(ValidationError):
            AppSettings.model_validate({field_name: value})


def test_context_promotion_seed_base_env_is_bounded_to_safe_integers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_key = "AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_SEED_BASE"
    monkeypatch.setenv(env_key, "-1")
    with pytest.raises(ValidationError):
        load_settings()

    monkeypatch.setenv(env_key, "9007199254740992")
    with pytest.raises(ValidationError):
        load_settings()

    monkeypatch.setenv(env_key, "9007199254740991")
    assert load_settings().context_bundle_promotion_seed_base == 9_007_199_254_740_991
