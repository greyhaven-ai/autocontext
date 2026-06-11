from __future__ import annotations

from collections.abc import Mapping

from autocontext.session.background_session_lifecycle_hooks import (
    LifecycleHookInvocation,
    build_lifecycle_hook_env,
    execute_background_session_lifecycle_hooks,
    execute_lifecycle_hook,
)

_CONTEXT = {
    "session_id": "run:run-123:runtime",
    "run_id": "run-123",
    "task_id": "task-123",
    "worker_id": "worker-1",
}
_TIMESTAMP = "2026-06-01T00:05:00.000Z"


def test_absent_lifecycle_hooks_are_skipped_without_invoking_adapter() -> None:
    invocations: list[LifecycleHookInvocation] = []

    def runner(invocation: LifecycleHookInvocation) -> Mapping[str, object]:
        invocations.append(invocation)
        return {"exit_code": 0}

    result = execute_lifecycle_hook(
        hook="setup",
        context=_CONTEXT,
        sequence=20,
        timestamp=_TIMESTAMP,
        runner=runner,
    )

    assert invocations == []
    assert result["outcome"] | {"events": []} == {
        "hook": "setup",
        "phase": "skipped",
        "ok": True,
        "terminal": False,
        "failure_policy": "continue",
        "events": [],
    }
    assert result["events"] == [
        {
            "event_id": "lifecycle:run:run-123:runtime:setup:skipped:20",
            "session_id": "run:run-123:runtime",
            "sequence": 20,
            "ts": _TIMESTAMP,
            "event": "session_status",
            "source_event_type": "lifecycle_hook",
            "status": "skipped",
            "title": "Lifecycle hook setup skipped",
            "payload_summary": {"hook": "setup", "phase": "skipped"},
        }
    ]
    assert result["next_sequence"] == 21


def test_successful_setup_and_start_hooks_match_typescript_contract() -> None:
    invocations: list[LifecycleHookInvocation] = []

    def runner(invocation: LifecycleHookInvocation) -> Mapping[str, object]:
        invocations.append(invocation)
        return {"exit_code": 0, "stdout": "ok", "stderr": ""}

    result = execute_background_session_lifecycle_hooks(
        hooks={
            "setup": {
                "command": ["npm", "install"],
                "timeout_ms": 30_000,
                "env": {"AUTOCTX_HOOK_MODE": "bootstrap", "SECRET_TOKEN": "explicit-secret"},
            },
            "start": {"command": ["autoctx", "run"], "cwd": "/workspace"},
        },
        context=_CONTEXT,
        sequence=30,
        timestamp=_TIMESTAMP,
        runner=runner,
    )

    assert [(outcome["hook"], outcome["phase"], outcome["ok"]) for outcome in result["outcomes"]] == [
        ("setup", "completed", True),
        ("start", "completed", True),
    ]
    assert [invocation["hook"] for invocation in invocations] == ["setup", "start"]
    assert invocations[0]["env"] == {
        "AUTOCTX_BACKGROUND_SESSION_ID": "run:run-123:runtime",
        "AUTOCTX_SESSION_ID": "run:run-123:runtime",
        "AUTOCTX_RUN_ID": "run-123",
        "AUTOCTX_TASK_ID": "task-123",
        "AUTOCTX_WORKER_ID": "worker-1",
        "AUTOCTX_HOOK_NAME": "setup",
        "AUTOCTX_HOOK_MODE": "bootstrap",
        "SECRET_TOKEN": "explicit-secret",
    }
    assert invocations[1]["cwd"] == "/workspace"
    assert result["terminal"] is False
    assert [(event["payload_summary"]["hook"], event["payload_summary"]["phase"]) for event in result["events"]] == [
        ("setup", "started"),
        ("setup", "completed"),
        ("start", "started"),
        ("start", "completed"),
    ]
    assert "explicit-secret" not in str(result["events"])


def test_setup_timeouts_can_continue_to_start_hook() -> None:
    result = execute_background_session_lifecycle_hooks(
        hooks={
            "setup": {"command": ["./bootstrap"], "timeout_ms": 10, "failure_policy": "continue"},
            "start": {"command": ["autoctx", "run"]},
        },
        context=_CONTEXT,
        sequence=40,
        timestamp=_TIMESTAMP,
        runner=lambda invocation: (
            {"timed_out": True, "error": "deadline exceeded"} if invocation["hook"] == "setup" else {"exit_code": 0}
        ),
    )

    assert [(outcome["hook"], outcome["phase"], outcome["terminal"]) for outcome in result["outcomes"]] == [
        ("setup", "timeout", False),
        ("start", "completed", False),
    ]
    assert result["terminal"] is False
    assert [event["payload_summary"]["phase"] for event in result["events"]] == [
        "started",
        "timeout",
        "started",
        "completed",
    ]


def test_non_fatal_setup_failures_continue_but_strict_start_failures_stop_session() -> None:
    setup_result = execute_background_session_lifecycle_hooks(
        hooks={
            "setup": {"command": ["./bootstrap"], "failure_policy": "continue"},
            "start": {"command": ["autoctx", "run"]},
        },
        context=_CONTEXT,
        sequence=50,
        timestamp=_TIMESTAMP,
        runner=lambda invocation: (
            {"exit_code": 17, "stderr": "setup failed"} if invocation["hook"] == "setup" else {"exit_code": 0}
        ),
    )

    assert [(outcome["hook"], outcome["phase"], outcome["terminal"]) for outcome in setup_result["outcomes"]] == [
        ("setup", "failed", False),
        ("start", "completed", False),
    ]
    assert setup_result["terminal"] is False

    start_result = execute_background_session_lifecycle_hooks(
        hooks={"start": {"command": ["autoctx", "run"]}},
        context=_CONTEXT,
        sequence=60,
        timestamp=_TIMESTAMP,
        runner=lambda invocation: {"exit_code": 2, "stderr": "missing runtime"},
    )

    assert len(start_result["outcomes"]) == 1
    assert start_result["outcomes"][0] == {
        "hook": "start",
        "phase": "failed",
        "ok": False,
        "terminal": True,
        "failure_policy": "fail_session",
        "exit_code": 2,
        "error": "missing runtime",
    }
    assert start_result["terminal"] is True


def test_lifecycle_hook_env_is_deterministic_without_ambient_secret_leakage() -> None:
    assert build_lifecycle_hook_env(
        {
            "session_id": "run:run-123:runtime",
            "run_id": "run-123",
            "task_id": "task-123",
            "worker_id": "worker-1",
        },
        "start",
        {"AUTOCTX_TRIGGER": "manual"},
    ) == {
        "AUTOCTX_BACKGROUND_SESSION_ID": "run:run-123:runtime",
        "AUTOCTX_SESSION_ID": "run:run-123:runtime",
        "AUTOCTX_RUN_ID": "run-123",
        "AUTOCTX_TASK_ID": "task-123",
        "AUTOCTX_WORKER_ID": "worker-1",
        "AUTOCTX_HOOK_NAME": "start",
        "AUTOCTX_TRIGGER": "manual",
    }
