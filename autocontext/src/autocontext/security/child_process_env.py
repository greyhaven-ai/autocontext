"""Environment filtering for host-side child processes."""

from __future__ import annotations

import os
from collections.abc import Mapping

# Control-plane credentials authenticate the host process itself. Agent CLIs
# are less-trusted children and must not inherit material that lets them call
# back into the server as an operator.
CONTROL_PLANE_SECRET_ENV_KEYS = frozenset(
    {
        "AUTOCONTEXT_SERVER_AUTH_KEYS",
        "AUTOCONTEXT_SERVER_CREDENTIALS_FILE",
        "AUTOCONTEXT_SERVER_TOKEN",
        "AUTOCONTEXT_SERVER_TOKEN_FILE",
    }
)


def child_process_env_without_control_plane_secrets(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy an environment while removing host control-plane credentials."""

    return {
        key: value
        for key, value in (os.environ if source is None else source).items()
        if key.upper() not in CONTROL_PLANE_SECRET_ENV_KEYS
    }


def clear_control_plane_secrets_from_current_process() -> None:
    """Permanently consume server credentials before loading untrusted code."""

    for key in tuple(os.environ):
        if key.upper() in CONTROL_PLANE_SECRET_ENV_KEYS:
            del os.environ[key]
