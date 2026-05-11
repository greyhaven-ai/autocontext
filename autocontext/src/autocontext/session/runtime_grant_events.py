"""Bridge runtime grant lifecycle events into runtime-session logs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from autocontext.runtimes.workspace_grants import RuntimeGrantEvent, RuntimeGrantEventSink
from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventType

RuntimeGrantEventCorrelation = Mapping[str, Any] | Callable[[], Mapping[str, Any]]


def create_runtime_session_grant_event_sink(
    log: RuntimeSessionEventLog,
    correlation: RuntimeGrantEventCorrelation | None = None,
) -> RuntimeGrantEventSink:
    return _RuntimeSessionGrantEventSink(log, correlation or {})


class _RuntimeSessionGrantEventSink:
    def __init__(
        self,
        log: RuntimeSessionEventLog,
        correlation: RuntimeGrantEventCorrelation,
    ) -> None:
        self._log = log
        self._correlation = correlation

    def on_runtime_grant_event(self, event: RuntimeGrantEvent) -> None:
        self._log.append(
            _runtime_session_event_type_for_grant(event),
            {**_runtime_grant_event_payload(event), **_resolve_correlation(self._correlation)},
        )


def _runtime_session_event_type_for_grant(event: RuntimeGrantEvent) -> RuntimeSessionEventType:
    return RuntimeSessionEventType.TOOL_CALL if event.kind == "tool" else RuntimeSessionEventType.SHELL_COMMAND


def _runtime_grant_event_payload(event: RuntimeGrantEvent) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "phase": event.phase,
        "cwd": event.cwd,
        "argsSummary": event.args_summary,
        "redaction": dict(event.redaction),
    }
    if event.kind == "tool":
        payload["tool"] = event.name
        payload["toolName"] = event.name
    else:
        payload["command"] = event.name
        payload["commandName"] = event.name
    if event.exit_code is not None:
        payload["exitCode"] = event.exit_code
    if event.stdout is not None:
        payload["stdout"] = event.stdout
    if event.stderr is not None:
        payload["stderr"] = event.stderr
    if event.error is not None:
        payload["error"] = event.error
    provenance = event.to_dict().get("provenance")
    if provenance:
        payload["provenance"] = provenance
    return payload


def _resolve_correlation(correlation: RuntimeGrantEventCorrelation) -> dict[str, Any]:
    return dict(correlation() if callable(correlation) else correlation)
