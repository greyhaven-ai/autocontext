from __future__ import annotations

import pytest


def test_start_run_protocol_accepts_playbook_approval_flag() -> None:
    from autocontext.server.protocol import StartRunCmd, parse_client_message

    cmd = parse_client_message({"type": "start_run", "scenario": "grid_ctf", "generations": 1, "require_playbook_approval": True})

    assert isinstance(cmd, StartRunCmd)
    assert cmd.require_playbook_approval is True


def test_start_run_protocol_defaults_playbook_approval_off() -> None:
    from autocontext.server.protocol import StartRunCmd, parse_client_message

    cmd = parse_client_message({"type": "start_run", "scenario": "grid_ctf", "generations": 1})

    assert isinstance(cmd, StartRunCmd)
    assert cmd.require_playbook_approval is False


def test_start_run_protocol_rejects_removed_lesson_approval_alias() -> None:
    """The deprecated require_lesson_approval alias was removed; extra=forbid rejects it."""
    from autocontext.server.protocol import parse_client_message

    with pytest.raises(ValueError):
        parse_client_message({"type": "start_run", "scenario": "grid_ctf", "generations": 1, "require_lesson_approval": True})
