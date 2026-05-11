"""Runtime workspace grant event vocabulary and redaction helpers."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES = 4096
DEFAULT_RUNTIME_COMMAND_ARG_LIMIT = 12
DEFAULT_RUNTIME_COMMAND_ARG_BYTES = 160

RuntimeGrantKind = Literal["command", "tool"]
RuntimeGrantEventPhase = Literal["start", "end", "error"]
RuntimeGrantInheritanceMode = Literal["scope", "child_task"]


@dataclass(frozen=True, slots=True)
class RuntimeGrantProvenance:
    source: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, str]:
        payload: dict[str, str] = {}
        if self.source:
            payload["source"] = self.source
        if self.description:
            payload["description"] = self.description
        return payload


@dataclass(frozen=True, slots=True)
class RuntimeGrantScopePolicy:
    inherit_to_child_tasks: bool | None = None


@dataclass(frozen=True, slots=True)
class RuntimeGrantOutputRedactionMetadata:
    redacted: bool
    truncated: bool
    original_bytes: int
    emitted_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "redacted": self.redacted,
            "truncated": self.truncated,
            "originalBytes": self.original_bytes,
            "emittedBytes": self.emitted_bytes,
        }


@dataclass(frozen=True, slots=True)
class RuntimeGrantEvent:
    kind: RuntimeGrantKind
    phase: RuntimeGrantEventPhase
    name: str
    cwd: str
    args_summary: list[str]
    redaction: Mapping[str, Any]
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    provenance: Mapping[str, str] | RuntimeGrantProvenance | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "phase": self.phase,
            "name": self.name,
            "cwd": self.cwd,
            "argsSummary": self.args_summary,
            "redaction": dict(self.redaction),
        }
        if self.exit_code is not None:
            payload["exitCode"] = self.exit_code
        if self.stdout is not None:
            payload["stdout"] = self.stdout
        if self.stderr is not None:
            payload["stderr"] = self.stderr
        if self.error is not None:
            payload["error"] = self.error
        provenance = provenance_to_dict(self.provenance)
        if provenance:
            payload["provenance"] = provenance
        return payload


class RuntimeGrantEventSink(Protocol):
    def on_runtime_grant_event(self, event: RuntimeGrantEvent) -> None:
        """Receive a scoped runtime grant event."""


RuntimeGrantEventSinkLike = RuntimeGrantEventSink | Callable[[RuntimeGrantEvent], None]


@dataclass(frozen=True, slots=True)
class GrantArgsSummary:
    summary: list[str]
    redacted: bool
    truncated: bool


@dataclass(frozen=True, slots=True)
class PreviewText:
    text: str
    metadata: RuntimeGrantOutputRedactionMetadata


def normalize_output_limit(value: int | None) -> int:
    if value is None:
        return DEFAULT_RUNTIME_COMMAND_OUTPUT_LIMIT_BYTES
    if isinstance(value, bool) or value < 0:
        raise ValueError("Runtime command output_limit_bytes must be a non-negative integer")
    return int(value)


def secret_values(env: Mapping[str, str]) -> list[str]:
    return sorted({value for value in env.values() if value})


def base_grant_redaction(
    env: Mapping[str, str],
    args: GrantArgsSummary,
) -> dict[str, Any]:
    return {
        "envKeys": sorted(env.keys()),
        "args": {
            "redacted": args.redacted,
            "truncated": args.truncated,
        },
    }


def summarize_args(args: Sequence[str], secrets: Sequence[str]) -> GrantArgsSummary:
    redacted = False
    truncated = len(args) > DEFAULT_RUNTIME_COMMAND_ARG_LIMIT
    summary: list[str] = []
    for arg in args[:DEFAULT_RUNTIME_COMMAND_ARG_LIMIT]:
        preview = preview_text(arg, secrets, DEFAULT_RUNTIME_COMMAND_ARG_BYTES)
        summary.append(preview.text)
        redacted = redacted or preview.metadata.redacted
        truncated = truncated or preview.metadata.truncated
    if len(args) > DEFAULT_RUNTIME_COMMAND_ARG_LIMIT:
        summary.append(f"[{len(args) - DEFAULT_RUNTIME_COMMAND_ARG_LIMIT} more args]")
    return GrantArgsSummary(summary=summary, redacted=redacted, truncated=truncated)


def preview_text(
    value: str,
    secrets: Sequence[str],
    limit_bytes: int,
) -> PreviewText:
    original_bytes = len(value.encode())
    redacted_text, was_redacted = redact_secrets(value, secrets)
    text, was_truncated = truncate_utf8(redacted_text, limit_bytes)
    return PreviewText(
        text=text,
        metadata=RuntimeGrantOutputRedactionMetadata(
            redacted=was_redacted,
            truncated=was_truncated,
            original_bytes=original_bytes,
            emitted_bytes=len(text.encode()),
        ),
    )


def emit_runtime_grant_event(
    sink: RuntimeGrantEventSinkLike | None,
    event: RuntimeGrantEvent,
) -> None:
    try:
        if sink is None:
            return
        if callable(sink):
            sink(event)
            return
        sink.on_runtime_grant_event(event)
    except Exception:
        pass


def inherits_to_child_tasks(scope: RuntimeGrantScopePolicy | Mapping[str, Any] | None) -> bool:
    if scope is None:
        return True
    if isinstance(scope, RuntimeGrantScopePolicy):
        return scope.inherit_to_child_tasks is not False
    value = scope.get("inheritToChildTasks", scope.get("inherit_to_child_tasks", True))
    return value is not False


def provenance_to_dict(provenance: Mapping[str, str] | RuntimeGrantProvenance | None) -> dict[str, str]:
    if provenance is None:
        return {}
    if isinstance(provenance, RuntimeGrantProvenance):
        return provenance.to_dict()
    return {str(key): str(value) for key, value in provenance.items() if value}


def pick_process_env(keys: Sequence[str]) -> dict[str, str]:
    return {key: os.environ[key] for key in keys if key in os.environ}


def combine_timeout_ms(configured: int | None, call_site: int | None) -> int | None:
    if configured is None:
        return call_site
    if call_site is None:
        return configured
    return min(configured, call_site)


def redact_secrets(value: str, secrets: Sequence[str]) -> tuple[str, bool]:
    text = value
    redacted = False
    for secret in secrets:
        if not secret or secret not in text:
            continue
        text = text.replace(secret, "[redacted]")
        redacted = True
    return text, redacted


def truncate_utf8(value: str, limit_bytes: int) -> tuple[str, bool]:
    encoded = value.encode()
    if len(encoded) <= limit_bytes:
        return value, False
    return encoded[:limit_bytes].decode(errors="ignore"), True
