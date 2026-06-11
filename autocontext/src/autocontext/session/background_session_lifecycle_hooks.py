from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, TypeAlias, cast

from autocontext.session.background_session_events import NormalizedSessionEvent, build_lifecycle_session_event

LifecycleHookName: TypeAlias = Literal["setup", "start"]
LifecycleHookFailurePolicy: TypeAlias = Literal["continue", "fail_session"]
LifecycleHookPhase: TypeAlias = Literal["skipped", "started", "completed", "failed", "timeout"]
LifecycleHookContext: TypeAlias = Mapping[str, str]
LifecycleHookDefinition: TypeAlias = Mapping[str, Any]
LifecycleHookInvocation: TypeAlias = dict[str, Any]
LifecycleHookRunnerResult: TypeAlias = Mapping[str, Any]
LifecycleHookRunner: TypeAlias = Callable[[LifecycleHookInvocation], LifecycleHookRunnerResult]
LifecycleHookOutcome: TypeAlias = dict[str, Any]
LifecycleHookExecutionResult: TypeAlias = dict[str, Any]
BackgroundSessionLifecycleHooksResult: TypeAlias = dict[str, Any]

_LIFECYCLE_HOOK_ORDER: tuple[LifecycleHookName, ...] = ("setup", "start")


def build_lifecycle_hook_env(
    context: LifecycleHookContext,
    hook: LifecycleHookName,
    extra_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build deterministic adapter env without copying ambient process secrets."""

    env: dict[str, str] = {
        "AUTOCTX_BACKGROUND_SESSION_ID": context["session_id"],
        "AUTOCTX_SESSION_ID": context["session_id"],
        "AUTOCTX_HOOK_NAME": hook,
    }
    _add_if_present(env, "AUTOCTX_RUN_ID", context.get("run_id"))
    _add_if_present(env, "AUTOCTX_TASK_ID", context.get("task_id"))
    _add_if_present(env, "AUTOCTX_WORKER_ID", context.get("worker_id"))
    if extra_env:
        env.update({key: value for key, value in extra_env.items() if isinstance(key, str) and isinstance(value, str)})
    return env


def execute_background_session_lifecycle_hooks(
    *,
    hooks: Mapping[str, LifecycleHookDefinition | None],
    context: LifecycleHookContext,
    sequence: int,
    runner: LifecycleHookRunner,
    timestamp: str | None = None,
) -> BackgroundSessionLifecycleHooksResult:
    outcomes: list[LifecycleHookOutcome] = []
    events: list[NormalizedSessionEvent] = []
    next_sequence = sequence
    terminal = False

    for hook in _LIFECYCLE_HOOK_ORDER:
        if hook not in hooks:
            continue
        result = execute_lifecycle_hook(
            hook=hook,
            definition=hooks.get(hook),
            context=context,
            sequence=next_sequence,
            timestamp=timestamp,
            runner=runner,
        )
        outcomes.append(cast(LifecycleHookOutcome, result["outcome"]))
        events.extend(cast(list[NormalizedSessionEvent], result["events"]))
        next_sequence = cast(int, result["next_sequence"])
        terminal = bool(result["outcome"].get("terminal"))
        if terminal:
            break

    return {"outcomes": outcomes, "events": events, "terminal": terminal, "next_sequence": next_sequence}


def execute_lifecycle_hook(
    *,
    hook: LifecycleHookName,
    context: LifecycleHookContext,
    sequence: int,
    runner: LifecycleHookRunner,
    definition: LifecycleHookDefinition | None = None,
    timestamp: str | None = None,
) -> LifecycleHookExecutionResult:
    _assert_lifecycle_hook_name(hook)
    failure_policy = _failure_policy(hook, definition)
    ts = timestamp or datetime.now(UTC).isoformat().replace("+00:00", "Z")
    command = _command(definition)

    if not command:
        event = build_lifecycle_session_event(
            session_id=context["session_id"],
            sequence=sequence,
            timestamp=ts,
            hook=hook,
            phase="skipped",
        )
        return {
            "outcome": {
                "hook": hook,
                "phase": "skipped",
                "ok": True,
                "terminal": False,
                "failure_policy": failure_policy,
            },
            "events": [event],
            "next_sequence": sequence + 1,
        }

    started = build_lifecycle_session_event(
        session_id=context["session_id"],
        sequence=sequence,
        timestamp=ts,
        hook=hook,
        phase="started",
    )
    invocation: LifecycleHookInvocation = {
        "hook": hook,
        "command": command,
        "env": build_lifecycle_hook_env(context, hook, _env(definition)),
        "context": dict(context),
    }
    cwd = _optional_str(definition, "cwd")
    if cwd:
        invocation["cwd"] = cwd
    timeout_ms = _optional_int(definition, "timeout_ms")
    if timeout_ms is not None:
        invocation["timeout_ms"] = timeout_ms

    try:
        runner_result = runner(invocation)
    except Exception as exc:  # pragma: no cover - exercised by contract behavior, not exception class
        runner_result = {"error": str(exc)}

    phase = _phase_for_runner_result(runner_result)
    ok = phase == "completed"
    terminal = not ok and failure_policy == "fail_session"
    finished = build_lifecycle_session_event(
        session_id=context["session_id"],
        sequence=sequence + 1,
        timestamp=ts,
        hook=hook,
        phase=phase,
    )

    outcome: LifecycleHookOutcome = {
        "hook": hook,
        "phase": phase,
        "ok": ok,
        "terminal": terminal,
        "failure_policy": failure_policy,
    }
    exit_code = _exit_code(runner_result)
    if exit_code is not None:
        outcome["exit_code"] = exit_code
    error = _error_for_runner_result(runner_result, phase)
    if error:
        outcome["error"] = error
    if runner_result.get("timed_out") is True:
        outcome["timed_out"] = True

    return {"outcome": outcome, "events": [started, finished], "next_sequence": sequence + 2}


def _failure_policy(hook: LifecycleHookName, definition: LifecycleHookDefinition | None) -> LifecycleHookFailurePolicy:
    value = definition.get("failure_policy") if definition else None
    if value == "continue":
        return "continue"
    if value == "fail_session":
        return "fail_session"
    return "fail_session" if hook == "start" else "continue"


def _command(definition: LifecycleHookDefinition | None) -> list[str]:
    raw = definition.get("command") if definition else None
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes):
        return []
    return [part for part in raw if isinstance(part, str) and part.strip()]


def _env(definition: LifecycleHookDefinition | None) -> dict[str, str]:
    raw = definition.get("env") if definition else None
    if not isinstance(raw, Mapping):
        return {}
    return {key: value for key, value in raw.items() if isinstance(key, str) and isinstance(value, str)}


def _phase_for_runner_result(result: LifecycleHookRunnerResult) -> Literal["completed", "failed", "timeout"]:
    if result.get("timed_out") is True:
        return "timeout"
    exit_code = _exit_code(result)
    if exit_code is not None and exit_code != 0:
        return "failed"
    error = result.get("error")
    if isinstance(error, str) and error.strip():
        return "failed"
    return "completed"


def _error_for_runner_result(result: LifecycleHookRunnerResult, phase: str) -> str:
    if phase in {"completed", "skipped"}:
        return ""
    error = result.get("error")
    if isinstance(error, str) and error.strip():
        return error
    stderr = result.get("stderr")
    if isinstance(stderr, str) and stderr.strip():
        return stderr
    if phase == "timeout":
        return "Lifecycle hook timed out"
    exit_code = _exit_code(result)
    return f"Lifecycle hook exited with code {exit_code}" if exit_code is not None else "Lifecycle hook failed"


def _exit_code(result: LifecycleHookRunnerResult) -> int | None:
    value = result.get("exit_code")
    return value if isinstance(value, int) else None


def _optional_str(definition: LifecycleHookDefinition | None, key: str) -> str:
    value = definition.get(key) if definition else None
    return value if isinstance(value, str) else ""


def _optional_int(definition: LifecycleHookDefinition | None, key: str) -> int | None:
    value = definition.get(key) if definition else None
    return value if isinstance(value, int) else None


def _add_if_present(target: dict[str, str], key: str, value: str | None) -> None:
    if value and value.strip():
        target[key] = value


def _assert_lifecycle_hook_name(hook: str) -> None:
    if hook not in {"setup", "start"}:
        raise ValueError(f"Unsupported lifecycle hook: {hook}")
