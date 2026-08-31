from __future__ import annotations

import os

import pytest

from autocontext.security.child_process_env import (
    CONTROL_PLANE_SECRET_ENV_KEYS,
    child_process_env_without_control_plane_secrets,
    clear_control_plane_secrets_from_current_process,
)


def test_child_process_environment_removes_only_control_plane_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "provider-secret",
        "AutoContext_Server_Token": "mixed-case-secret",
        **{key: f"secret-for-{key}" for key in CONTROL_PLANE_SECRET_ENV_KEYS},
    }

    child = child_process_env_without_control_plane_secrets(source)

    assert child == {"PATH": "/usr/bin", "OPENAI_API_KEY": "provider-secret"}
    assert source.keys() >= CONTROL_PLANE_SECRET_ENV_KEYS


def test_current_process_clear_consumes_mixed_case_control_plane_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AUTOCONTEXT_SERVER_TOKEN", "uppercase-secret")
    monkeypatch.setenv("AutoContext_Server_Credentials_File", "mixed-case-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-secret")

    clear_control_plane_secrets_from_current_process()

    assert all(key.upper() not in CONTROL_PLANE_SECRET_ENV_KEYS for key in os.environ)
    assert os.environ["OPENAI_API_KEY"] == "provider-secret"
