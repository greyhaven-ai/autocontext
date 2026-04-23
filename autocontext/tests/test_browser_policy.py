from __future__ import annotations

from autocontext.integrations.browser.policy import (
    build_default_browser_session_config,
    evaluate_browser_action_policy,
)
from autocontext.integrations.browser.validate import validate_browser_session_config_dict


def test_navigation_requires_allowlisted_domain() -> None:
    config = build_default_browser_session_config(allowed_domains=["example.com"])
    action = {
        "schemaVersion": "1.0",
        "actionId": "act_nav_1",
        "sessionId": "session_1",
        "timestamp": "2026-04-22T12:00:00Z",
        "type": "navigate",
        "params": {"url": "https://blocked.example.net/dashboard"},
    }

    decision = evaluate_browser_action_policy(config, action)

    assert decision.allowed is False
    assert decision.reason == "domain_not_allowed"


def test_navigation_accepts_exact_and_wildcard_domains() -> None:
    config = build_default_browser_session_config(allowed_domains=["example.com", "*.example.org"])

    exact = {
        "schemaVersion": "1.0",
        "actionId": "act_nav_exact",
        "sessionId": "session_1",
        "timestamp": "2026-04-22T12:00:00Z",
        "type": "navigate",
        "params": {"url": "https://example.com/path"},
    }
    wildcard = {
        "schemaVersion": "1.0",
        "actionId": "act_nav_wild",
        "sessionId": "session_1",
        "timestamp": "2026-04-22T12:00:01Z",
        "type": "navigate",
        "params": {"url": "https://app.example.org/path"},
    }

    assert evaluate_browser_action_policy(config, exact).allowed is True
    assert evaluate_browser_action_policy(config, wildcard).allowed is True


def test_password_fill_requires_auth_opt_in() -> None:
    config = build_default_browser_session_config()
    action = {
        "schemaVersion": "1.0",
        "actionId": "act_fill_pw",
        "sessionId": "session_1",
        "timestamp": "2026-04-22T12:00:00Z",
        "type": "fill",
        "params": {
            "ref": "@e1",
            "text": "super-secret",
            "fieldKind": "password",
        },
    }

    decision = evaluate_browser_action_policy(config, action)

    assert decision.allowed is False
    assert decision.reason == "auth_blocked"


def test_session_config_dict_validator_applies_cross_field_policy() -> None:
    ok, errors = validate_browser_session_config_dict({
        "schemaVersion": "1.0",
        "profileMode": "ephemeral",
        "allowedDomains": [],
        "allowAuth": False,
        "allowUploads": False,
        "allowDownloads": True,
        "captureScreenshots": True,
        "headless": True,
        "downloadsRoot": None,
        "uploadsRoot": None,
    })

    assert ok is False
    assert any("downloadsRoot is required" in error for error in errors)


def test_session_config_dict_validator_rejects_user_profile_without_auth() -> None:
    ok, errors = validate_browser_session_config_dict({
        "schemaVersion": "1.0",
        "profileMode": "user-profile",
        "allowedDomains": [],
        "allowAuth": False,
        "allowUploads": False,
        "allowDownloads": False,
        "captureScreenshots": True,
        "headless": True,
        "downloadsRoot": None,
        "uploadsRoot": None,
    })

    assert ok is False
    assert any("allowAuth must be true" in error for error in errors)
