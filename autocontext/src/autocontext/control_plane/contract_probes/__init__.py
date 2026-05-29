"""AC-728 contract probes, Python parity (slice 1).

Mirrors ``ts/src/control-plane/contract-probes/index.ts`` at the
PR #957 shape: four base probes (directory, terminal, service,
artifact). Each probe is a pure function returning a Pydantic
result model so it composes with the same trace-replay surfaces
the TS probes use.

Missing-observation invariant
-----------------------------
Every observation field on these four probes is non-optional, so the
silent-pass shape simply does not arise here. The follow-up slice-2
cleanup / media / distributed probes (mirroring TS PRs #983, #985,
#987) emit explicit ``missing-observation`` failure kinds; this
slice does not need that surface.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ArtifactContractFailure",
    "ArtifactContractFailureKind",
    "ArtifactContractProbeInputs",
    "ArtifactContractProbeResult",
    "DirectoryContractFailure",
    "DirectoryContractFailureKind",
    "DirectoryContractProbeInputs",
    "DirectoryContractProbeResult",
    "ServiceContractFailure",
    "ServiceContractFailureKind",
    "ServiceContractProbeInputs",
    "ServiceContractProbeResult",
    "ServiceEndpointObservation",
    "ServiceEndpointProtocol",
    "TerminalContractFailure",
    "TerminalContractFailureKind",
    "TerminalContractProbeInputs",
    "TerminalContractProbeResult",
    "probe_artifact_contract",
    "probe_directory_contract",
    "probe_service_contract",
    "probe_terminal_contract",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


# ---------------------------------------------------------------------------
# directory contract probe
# ---------------------------------------------------------------------------

DirectoryContractFailureKind = Literal["unexpected-file", "missing-file"]


class DirectoryContractFailure(_Frozen):
    kind: DirectoryContractFailureKind
    path: str
    message: str


class DirectoryContractProbeInputs(_Frozen):
    present_files: tuple[str, ...] = Field(default=())
    required_files: tuple[str, ...] = Field(default=())
    allowed_files: tuple[str, ...] = Field(default=())
    ignored_patterns: tuple[re.Pattern[str], ...] = Field(default=())


class DirectoryContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[DirectoryContractFailure, ...]


def _is_ignored(path: str, ignored_patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(pattern.search(path) is not None for pattern in ignored_patterns)


def probe_directory_contract(
    inputs: DirectoryContractProbeInputs,
) -> DirectoryContractProbeResult:
    present_files = tuple(path for path in inputs.present_files if not _is_ignored(path, inputs.ignored_patterns))
    present = set(present_files)
    allowed = set(inputs.allowed_files)
    failures: list[DirectoryContractFailure] = []

    for path in present_files:
        if path not in allowed:
            failures.append(
                DirectoryContractFailure(
                    kind="unexpected-file",
                    path=path,
                    message=f"unexpected file {path}",
                )
            )

    for path in inputs.required_files:
        if path not in present:
            failures.append(
                DirectoryContractFailure(
                    kind="missing-file",
                    path=path,
                    message=f"required file {path} is missing",
                )
            )

    return DirectoryContractProbeResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# terminal contract probe
# ---------------------------------------------------------------------------

TerminalContractFailureKind = Literal[
    "unexpected-exit-code",
    "missing-stdout-pattern",
    "forbidden-stdout-pattern",
    "missing-stderr-pattern",
    "forbidden-stderr-pattern",
]


class TerminalContractFailure(_Frozen):
    kind: TerminalContractFailureKind
    message: str


class TerminalContractProbeInputs(_Frozen):
    exit_code: int
    stdout: str
    stderr: str
    expected_exit_code: int | None = None
    required_stdout_patterns: tuple[re.Pattern[str], ...] = Field(default=())
    forbidden_stdout_patterns: tuple[re.Pattern[str], ...] = Field(default=())
    required_stderr_patterns: tuple[re.Pattern[str], ...] = Field(default=())
    forbidden_stderr_patterns: tuple[re.Pattern[str], ...] = Field(default=())


class TerminalContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[TerminalContractFailure, ...]


def probe_terminal_contract(
    inputs: TerminalContractProbeInputs,
) -> TerminalContractProbeResult:
    failures: list[TerminalContractFailure] = []
    expected_exit_code = inputs.expected_exit_code if inputs.expected_exit_code is not None else 0
    if inputs.exit_code != expected_exit_code:
        failures.append(
            TerminalContractFailure(
                kind="unexpected-exit-code",
                message=f"expected exit code {expected_exit_code}, got {inputs.exit_code}",
            )
        )
    for pattern in inputs.required_stdout_patterns:
        if pattern.search(inputs.stdout) is None:
            failures.append(
                TerminalContractFailure(
                    kind="missing-stdout-pattern",
                    message=f"stdout did not match {pattern.pattern}",
                )
            )
    for pattern in inputs.forbidden_stdout_patterns:
        if pattern.search(inputs.stdout) is not None:
            failures.append(
                TerminalContractFailure(
                    kind="forbidden-stdout-pattern",
                    message=f"stdout matched forbidden {pattern.pattern}",
                )
            )
    for pattern in inputs.required_stderr_patterns:
        if pattern.search(inputs.stderr) is None:
            failures.append(
                TerminalContractFailure(
                    kind="missing-stderr-pattern",
                    message=f"stderr did not match {pattern.pattern}",
                )
            )
    for pattern in inputs.forbidden_stderr_patterns:
        if pattern.search(inputs.stderr) is not None:
            failures.append(
                TerminalContractFailure(
                    kind="forbidden-stderr-pattern",
                    message=f"stderr matched forbidden {pattern.pattern}",
                )
            )
    return TerminalContractProbeResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# service contract probe
# ---------------------------------------------------------------------------

ServiceEndpointProtocol = Literal["tcp", "udp"]
ServiceContractFailureKind = Literal[
    "missing-endpoint",
    "unexpected-endpoint",
    "wrong-interface",
]


class ServiceEndpointObservation(_Frozen):
    host: str
    port: int
    protocol: ServiceEndpointProtocol | None = None


class ServiceContractFailure(_Frozen):
    kind: ServiceContractFailureKind
    endpoint: ServiceEndpointObservation
    message: str


class ServiceContractProbeInputs(_Frozen):
    observed: tuple[ServiceEndpointObservation, ...] = Field(default=())
    required: tuple[ServiceEndpointObservation, ...] = Field(default=())
    allowed: tuple[ServiceEndpointObservation, ...] | None = None


class ServiceContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[ServiceContractFailure, ...]


def _normalize_protocol(endpoint: ServiceEndpointObservation) -> ServiceEndpointProtocol:
    return endpoint.protocol if endpoint.protocol is not None else "tcp"


def _endpoint_key(endpoint: ServiceEndpointObservation) -> str:
    return f"{_normalize_protocol(endpoint)}://{endpoint.host}:{endpoint.port}"


def _endpoint_matches_any_host(
    required: ServiceEndpointObservation,
    observed: tuple[ServiceEndpointObservation, ...],
) -> ServiceEndpointObservation | None:
    required_protocol = _normalize_protocol(required)
    for candidate in observed:
        if candidate.port == required.port and _normalize_protocol(candidate) == required_protocol:
            return candidate
    return None


def probe_service_contract(
    inputs: ServiceContractProbeInputs,
) -> ServiceContractProbeResult:
    failures: list[ServiceContractFailure] = []
    observed_keys = {_endpoint_key(endpoint) for endpoint in inputs.observed}

    for required in inputs.required:
        required_key = _endpoint_key(required)
        if required_key in observed_keys:
            continue
        port_match = _endpoint_matches_any_host(required, inputs.observed)
        if port_match is not None:
            failures.append(
                ServiceContractFailure(
                    kind="wrong-interface",
                    endpoint=required,
                    message=(f"required {required_key} but observed {_endpoint_key(port_match)}"),
                )
            )
        else:
            failures.append(
                ServiceContractFailure(
                    kind="missing-endpoint",
                    endpoint=required,
                    message=f"required endpoint {required_key} not observed",
                )
            )

    if inputs.allowed is not None:
        allowed_keys = {_endpoint_key(endpoint) for endpoint in inputs.allowed}
        for observed in inputs.observed:
            if _endpoint_key(observed) not in allowed_keys:
                failures.append(
                    ServiceContractFailure(
                        kind="unexpected-endpoint",
                        endpoint=observed,
                        message=(f"observed endpoint {_endpoint_key(observed)} not in allowed list"),
                    )
                )

    return ServiceContractProbeResult(passed=not failures, failures=tuple(failures))


# ---------------------------------------------------------------------------
# artifact contract probe
# ---------------------------------------------------------------------------

ArtifactContractFailureKind = Literal[
    "missing-substring",
    "forbidden-substring",
    "wrong-line-ending",
    "invalid-json",
    "missing-json-field",
]


class ArtifactContractFailure(_Frozen):
    kind: ArtifactContractFailureKind
    path: str
    message: str


class ArtifactContractProbeInputs(_Frozen):
    path: str
    content: str
    expected_line_ending: Literal["lf", "crlf"] | None = None
    required_substrings: tuple[str, ...] = Field(default=())
    forbidden_substrings: tuple[str, ...] = Field(default=())
    required_json_fields: tuple[str, ...] = Field(default=())


class ArtifactContractProbeResult(_Frozen):
    passed: bool
    failures: tuple[ArtifactContractFailure, ...]


_BARE_LF_RE = re.compile(r"(?<!\r)\n")

# Sentinel distinguishing "JSON key absent" from "JSON key present with
# null value". The TS probe treats `undefined` as missing but accepts
# `null` as a present-but-null value; mirroring that here requires a
# sentinel because Python `json.loads` decodes JSON `null` to `None`.
_MISSING = object()


def _read_json_dot_path(value: object, path: str) -> object:
    cursor: object = value
    for segment in path.split("."):
        if not isinstance(cursor, dict):
            return _MISSING
        if segment not in cursor:
            return _MISSING
        cursor = cursor[segment]
    return cursor


def probe_artifact_contract(
    inputs: ArtifactContractProbeInputs,
) -> ArtifactContractProbeResult:
    failures: list[ArtifactContractFailure] = []

    for required in inputs.required_substrings:
        if required not in inputs.content:
            failures.append(
                ArtifactContractFailure(
                    kind="missing-substring",
                    path=inputs.path,
                    message=(f"{inputs.path} is missing required substring {json.dumps(required)}"),
                )
            )

    for forbidden in inputs.forbidden_substrings:
        if forbidden in inputs.content:
            failures.append(
                ArtifactContractFailure(
                    kind="forbidden-substring",
                    path=inputs.path,
                    message=(f"{inputs.path} contains forbidden substring {json.dumps(forbidden)}"),
                )
            )

    if inputs.expected_line_ending == "lf":
        if "\r\n" in inputs.content:
            failures.append(
                ArtifactContractFailure(
                    kind="wrong-line-ending",
                    path=inputs.path,
                    message=f"{inputs.path} contains CRLF but LF was required",
                )
            )
    elif inputs.expected_line_ending == "crlf":
        if _BARE_LF_RE.search(inputs.content) is not None:
            failures.append(
                ArtifactContractFailure(
                    kind="wrong-line-ending",
                    path=inputs.path,
                    message=f"{inputs.path} contains bare LF but CRLF was required",
                )
            )

    if inputs.required_json_fields:
        try:
            parsed = json.loads(inputs.content)
        except json.JSONDecodeError as err:
            failures.append(
                ArtifactContractFailure(
                    kind="invalid-json",
                    path=inputs.path,
                    message=f"{inputs.path} is not valid JSON: {err.msg}",
                )
            )
            return ArtifactContractProbeResult(passed=False, failures=tuple(failures))
        for field in inputs.required_json_fields:
            if _read_json_dot_path(parsed, field) is _MISSING:
                failures.append(
                    ArtifactContractFailure(
                        kind="missing-json-field",
                        path=field,
                        message=f"{inputs.path} is missing required JSON field {field}",
                    )
                )

    return ArtifactContractProbeResult(passed=not failures, failures=tuple(failures))
