"""AC-728 contract-probe suite runner (Python parity, slice 3).

Mirrors ``ts/src/control-plane/contract-probes/runner.ts`` (PR #990).
Library-level entry point that dispatches a JSON-defined probe suite
across all seven AC-728 probes and aggregates results. Pure function:
callers do the IO to populate observations; the runner verifies.

The suite schema is intentionally separate from the probe Inputs
models (mirroring the TS Zod / interface split):

- ``ContractProbeSuiteSchema`` validates the JSON wire format with
  every nested object set to ``extra="forbid"``, so a typo like
  ``requiredStdoutPattern`` (missing ``s``) fails validation rather
  than silently being dropped.
- RegExp values can arrive as either a bare pattern string or
  ``{"source": ..., "flags": ...}``; invalid regexes surface as
  ``ValidationError``, not raw ``re.error``.
- ISO-8601 strings parse to ``datetime`` automatically.
- ``run_contract_probe_suite(suite)`` dispatches via ``kind`` and
  returns a discriminated-union list of per-probe results, each
  preserving the probe's typed failure shape.
- ``load_contract_probe_suite(path)`` is the JSON file loader.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from ._advanced import (
    CleanupContractFailure,
    CleanupContractProbeInputs,
    CleanupFileEntry,
    DistributedContractFailure,
    DistributedContractProbeInputs,
    DistributedRankReport,
    MediaContractFailure,
    MediaContractProbeInputs,
    probe_cleanup_contract,
    probe_distributed_contract,
    probe_media_contract,
)
from ._base import (
    ArtifactContractFailure,
    ArtifactContractProbeInputs,
    DirectoryContractFailure,
    DirectoryContractProbeInputs,
    ServiceContractFailure,
    ServiceContractProbeInputs,
    ServiceEndpointObservation,
    TerminalContractFailure,
    TerminalContractProbeInputs,
    probe_artifact_contract,
    probe_directory_contract,
    probe_service_contract,
    probe_terminal_contract,
)

__all__ = [
    "ContractProbeInvocation",
    "ContractProbeKind",
    "ContractProbeRunResult",
    "ContractProbeSuite",
    "ContractProbeSuiteResult",
    "load_contract_probe_suite",
    "run_contract_probe_suite",
]


# ---------------------------------------------------------------------------
# JSON-side helpers: RegExp + Date coercion
# ---------------------------------------------------------------------------

ContractProbeKind = Literal[
    "directory",
    "terminal",
    "service",
    "artifact",
    "cleanup",
    "media",
    "distributed",
]


def _coerce_pattern(value: object) -> re.Pattern[str]:
    """Accept a string, a dict ``{"source": ..., "flags": ...}``, or
    a pre-compiled ``re.Pattern[str]``. Invalid patterns raise
    ``ValueError`` so Pydantic surfaces them as ``ValidationError``
    rather than letting raw ``re.error`` escape.
    """
    if isinstance(value, re.Pattern):
        return value
    if isinstance(value, str):
        try:
            return re.compile(value)
        except re.error as err:
            raise ValueError(f"invalid regular expression {value!r}: {err}") from None
    if isinstance(value, dict):
        source = value.get("source")
        if not isinstance(source, str):
            raise ValueError("RegExp object missing 'source' string")
        flags_str = value.get("flags", "")
        if not isinstance(flags_str, str):
            raise ValueError("RegExp object 'flags' must be a string")
        flags = 0
        for ch in flags_str:
            mapping = {
                "i": re.IGNORECASE,
                "m": re.MULTILINE,
                "s": re.DOTALL,
                "x": re.VERBOSE,
                "u": re.UNICODE,
            }
            if ch not in mapping:
                raise ValueError(f"unknown RegExp flag {ch!r}")
            flags |= mapping[ch]
        try:
            return re.compile(source, flags)
        except re.error as err:
            raise ValueError(f"invalid regular expression {source!r}: {err}") from None
    raise ValueError(
        f"RegExp value must be a string, a {{source, flags?}} object, or a compiled pattern; got {type(value).__name__}"
    )


def _coerce_patterns(value: object) -> tuple[re.Pattern[str], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("expected a list of RegExp values")
    return tuple(_coerce_pattern(item) for item in value)


_PatternList = Annotated[tuple[re.Pattern[str], ...], BeforeValidator(_coerce_patterns)]


# ---------------------------------------------------------------------------
# Per-probe JSON-shape schemas
# ---------------------------------------------------------------------------
#
# Every model is `extra="forbid"`: unknown keys fail validation rather than
# being silently stripped. This catches typos like `requiredStdoutPattern`
# (missing `s`) at parse time -- otherwise the field would disappear and
# the runner would silently skip the expectation, returning passed=True
# for what was supposed to be a stronger contract.


class _Strict(BaseModel):
    """Base for every JSON-shape model.

    - ``extra="forbid"``: unknown keys reject. Mirrors TS `.strict()`.
    - ``arbitrary_types_allowed=True``: lets compiled ``re.Pattern[str]``
      ride through after the ``_coerce_patterns`` BeforeValidator.

    Primitive coercion is blocked per-field via ``StrictInt`` /
    ``StrictBool`` (PR #1006 review P3). String -> string is a no-op
    so plain ``str`` is fine; we want lists -> tuples to keep
    working, so the model is not blanket-strict.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null(cls, data: object) -> object:
        # PR #1006 review P2: TS `.optional()` accepts omission but
        # not `null`. Python should reject explicit nulls so a
        # broken extractor cannot weaken declared expectations by
        # writing `requiredStdoutPatterns: null`.
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None and k in cls.model_fields]
            if nulls:
                fields = ", ".join(sorted(nulls))
                raise ValueError(
                    f"explicit null not allowed for optional field(s): {fields}; omit the key to disable the expectation"
                )
        return data


class _DirectoryInputsSchema(_Strict):
    # PR #1006 review P2: TS marks these fields required (not optional).
    # A broken extractor that omits observations should fail validation,
    # not silently turn into an empty-passing suite.
    presentFiles: tuple[str, ...]
    requiredFiles: tuple[str, ...]
    allowedFiles: tuple[str, ...]
    ignoredPatterns: _PatternList | None = None

    def to_inputs(self) -> DirectoryContractProbeInputs:
        return DirectoryContractProbeInputs(
            present_files=self.presentFiles,
            required_files=self.requiredFiles,
            allowed_files=self.allowedFiles,
            ignored_patterns=self.ignoredPatterns or (),
        )


class _TerminalInputsSchema(_Strict):
    # PR #1006 review P3: use Strict* so the JSON schema rejects
    # `"exitCode": "0"` etc rather than coercing them silently.
    exitCode: StrictInt
    stdout: str
    stderr: str
    expectedExitCode: StrictInt | None = None
    requiredStdoutPatterns: _PatternList | None = None
    forbiddenStdoutPatterns: _PatternList | None = None
    requiredStderrPatterns: _PatternList | None = None
    forbiddenStderrPatterns: _PatternList | None = None

    def to_inputs(self) -> TerminalContractProbeInputs:
        return TerminalContractProbeInputs(
            exit_code=self.exitCode,
            stdout=self.stdout,
            stderr=self.stderr,
            expected_exit_code=self.expectedExitCode,
            required_stdout_patterns=self.requiredStdoutPatterns or (),
            forbidden_stdout_patterns=self.forbiddenStdoutPatterns or (),
            required_stderr_patterns=self.requiredStderrPatterns or (),
            forbidden_stderr_patterns=self.forbiddenStderrPatterns or (),
        )


class _ServiceEndpointSchema(_Strict):
    host: str
    port: StrictInt
    protocol: Literal["tcp", "udp"] | None = None

    def to_inputs(self) -> ServiceEndpointObservation:
        return ServiceEndpointObservation(host=self.host, port=self.port, protocol=self.protocol)


class _ServiceInputsSchema(_Strict):
    # PR #1006 review P2: TS marks `observed` + `required` required.
    observed: tuple[_ServiceEndpointSchema, ...]
    required: tuple[_ServiceEndpointSchema, ...]
    allowed: tuple[_ServiceEndpointSchema, ...] | None = None

    def to_inputs(self) -> ServiceContractProbeInputs:
        return ServiceContractProbeInputs(
            observed=tuple(e.to_inputs() for e in self.observed),
            required=tuple(e.to_inputs() for e in self.required),
            allowed=None if self.allowed is None else tuple(e.to_inputs() for e in self.allowed),
        )


class _ArtifactInputsSchema(_Strict):
    path: str
    content: str
    expectedLineEnding: Literal["lf", "crlf"] | None = None
    requiredSubstrings: tuple[str, ...] | None = None
    forbiddenSubstrings: tuple[str, ...] | None = None
    requiredJsonFields: tuple[str, ...] | None = None

    def to_inputs(self) -> ArtifactContractProbeInputs:
        return ArtifactContractProbeInputs(
            path=self.path,
            content=self.content,
            expected_line_ending=self.expectedLineEnding,
            required_substrings=self.requiredSubstrings or (),
            forbidden_substrings=self.forbiddenSubstrings or (),
            required_json_fields=self.requiredJsonFields or (),
        )


class _CleanupEntrySchema(_Strict):
    path: str
    isSymlink: StrictBool | None = None
    symlinkTarget: str | None = None
    symlinkBroken: StrictBool | None = None
    mtime: datetime | None = None

    def to_inputs(self) -> CleanupFileEntry:
        return CleanupFileEntry(
            path=self.path,
            is_symlink=bool(self.isSymlink),
            symlink_target=self.symlinkTarget,
            symlink_broken=bool(self.symlinkBroken),
            mtime=self.mtime,
        )


class _CleanupInputsSchema(_Strict):
    # PR #1006 review P2: TS marks `entries` required.
    entries: tuple[_CleanupEntrySchema, ...]
    now: datetime | None = None
    maxLockfileAgeMs: StrictInt | None = None
    lockfilePatterns: _PatternList | None = None
    sidecarPatterns: _PatternList | None = None
    backupPatterns: _PatternList | None = None
    forbidSymlinks: StrictBool | None = None
    allowedSymlinkTargets: tuple[str, ...] | None = None
    ignoredPatterns: _PatternList | None = None

    def to_inputs(self) -> CleanupContractProbeInputs:
        return CleanupContractProbeInputs(
            entries=tuple(e.to_inputs() for e in self.entries),
            now=self.now,
            max_lockfile_age_ms=self.maxLockfileAgeMs,
            lockfile_patterns=self.lockfilePatterns,
            sidecar_patterns=self.sidecarPatterns,
            backup_patterns=self.backupPatterns,
            forbid_symlinks=bool(self.forbidSymlinks),
            allowed_symlink_targets=self.allowedSymlinkTargets,
            ignored_patterns=self.ignoredPatterns or (),
        )


class _MediaInputsSchema(_Strict):
    path: str
    headerBytes: tuple[StrictInt, ...] | None = None
    expectedMagicBytes: tuple[StrictInt, ...] | None = None
    width: StrictInt | None = None
    height: StrictInt | None = None
    expectedWidth: StrictInt | None = None
    expectedHeight: StrictInt | None = None
    byteSize: StrictInt | None = None
    minByteSize: StrictInt | None = None
    maxByteSize: StrictInt | None = None
    columnCount: StrictInt | None = None
    expectedColumnCount: StrictInt | None = None
    columnNames: tuple[str, ...] | None = None
    requiredColumnNames: tuple[str, ...] | None = None
    lineCount: StrictInt | None = None
    expectedLineCount: StrictInt | None = None

    def to_inputs(self) -> MediaContractProbeInputs:
        return MediaContractProbeInputs(
            path=self.path,
            header_bytes=self.headerBytes,
            expected_magic_bytes=self.expectedMagicBytes,
            width=self.width,
            height=self.height,
            expected_width=self.expectedWidth,
            expected_height=self.expectedHeight,
            byte_size=self.byteSize,
            min_byte_size=self.minByteSize,
            max_byte_size=self.maxByteSize,
            column_count=self.columnCount,
            expected_column_count=self.expectedColumnCount,
            column_names=self.columnNames,
            required_column_names=self.requiredColumnNames,
            line_count=self.lineCount,
            expected_line_count=self.expectedLineCount,
        )


class _DistributedRankSchema(_Strict):
    rank: StrictInt
    steps: StrictInt | None = None
    observations: dict[str, str] | None = None

    def to_inputs(self) -> DistributedRankReport:
        return DistributedRankReport(rank=self.rank, steps=self.steps, observations=self.observations)


class _DistributedInputsSchema(_Strict):
    # PR #1006 review P2: TS marks `ranks` required.
    ranks: tuple[_DistributedRankSchema, ...]
    worldSize: StrictInt | None = None
    expectedWorldSize: StrictInt | None = None
    expectedSteps: StrictInt | None = None
    mustMatchAcrossRanks: tuple[str, ...] | None = None

    def to_inputs(self) -> DistributedContractProbeInputs:
        return DistributedContractProbeInputs(
            ranks=tuple(r.to_inputs() for r in self.ranks),
            world_size=self.worldSize,
            expected_world_size=self.expectedWorldSize,
            expected_steps=self.expectedSteps,
            must_match_across_ranks=self.mustMatchAcrossRanks,
        )


# ---------------------------------------------------------------------------
# Discriminated invocation envelope
# ---------------------------------------------------------------------------


class _DirectoryInvocation(_Strict):
    kind: Literal["directory"]
    label: str | None = None
    inputs: _DirectoryInputsSchema


class _TerminalInvocation(_Strict):
    kind: Literal["terminal"]
    label: str | None = None
    inputs: _TerminalInputsSchema


class _ServiceInvocation(_Strict):
    kind: Literal["service"]
    label: str | None = None
    inputs: _ServiceInputsSchema


class _ArtifactInvocation(_Strict):
    kind: Literal["artifact"]
    label: str | None = None
    inputs: _ArtifactInputsSchema


class _CleanupInvocation(_Strict):
    kind: Literal["cleanup"]
    label: str | None = None
    inputs: _CleanupInputsSchema


class _MediaInvocation(_Strict):
    kind: Literal["media"]
    label: str | None = None
    inputs: _MediaInputsSchema


class _DistributedInvocation(_Strict):
    kind: Literal["distributed"]
    label: str | None = None
    inputs: _DistributedInputsSchema


_InvocationUnion = (
    _DirectoryInvocation
    | _TerminalInvocation
    | _ServiceInvocation
    | _ArtifactInvocation
    | _CleanupInvocation
    | _MediaInvocation
    | _DistributedInvocation
)
ContractProbeInvocation = Annotated[_InvocationUnion, Field(discriminator="kind")]


class ContractProbeSuite(_Strict):
    """JSON wire-format suite envelope.

    Both fields are required. PR #1006 review P2: ``probes: null``
    used to coerce to an empty passing suite; the explicit-null
    rejection in ``_Strict._reject_explicit_null`` plus the lack of
    a default here makes ``null`` and omission both fail validation
    (TS rejects ``null`` the same way).
    """

    schema_version: Literal[1]
    probes: tuple[ContractProbeInvocation, ...]


ContractProbeSuiteSchema = ContractProbeSuite


# ---------------------------------------------------------------------------
# Run-result discriminated union
# ---------------------------------------------------------------------------


class _ResultBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    label: str | None = None
    passed: bool


class DirectoryRunResult(_ResultBase):
    kind: Literal["directory"] = "directory"
    failures: tuple[DirectoryContractFailure, ...]


class TerminalRunResult(_ResultBase):
    kind: Literal["terminal"] = "terminal"
    failures: tuple[TerminalContractFailure, ...]


class ServiceRunResult(_ResultBase):
    kind: Literal["service"] = "service"
    failures: tuple[ServiceContractFailure, ...]


class ArtifactRunResult(_ResultBase):
    kind: Literal["artifact"] = "artifact"
    failures: tuple[ArtifactContractFailure, ...]


class CleanupRunResult(_ResultBase):
    kind: Literal["cleanup"] = "cleanup"
    failures: tuple[CleanupContractFailure, ...]


class MediaRunResult(_ResultBase):
    kind: Literal["media"] = "media"
    failures: tuple[MediaContractFailure, ...]


class DistributedRunResult(_ResultBase):
    kind: Literal["distributed"] = "distributed"
    failures: tuple[DistributedContractFailure, ...]


ContractProbeRunResult = Annotated[
    DirectoryRunResult
    | TerminalRunResult
    | ServiceRunResult
    | ArtifactRunResult
    | CleanupRunResult
    | MediaRunResult
    | DistributedRunResult,
    Field(discriminator="kind"),
]


class ContractProbeSuiteResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)
    passed: bool
    results: tuple[ContractProbeRunResult, ...]


# ---------------------------------------------------------------------------
# Runner + loader
# ---------------------------------------------------------------------------


def run_contract_probe_suite(suite: ContractProbeSuite) -> ContractProbeSuiteResult:
    """Dispatch each invocation through the matching probe and aggregate."""
    results: list[ContractProbeRunResult] = []
    for invocation in suite.probes:
        match invocation:
            case _DirectoryInvocation():
                r = probe_directory_contract(invocation.inputs.to_inputs())
                results.append(DirectoryRunResult(label=invocation.label, passed=r.passed, failures=r.failures))
            case _TerminalInvocation():
                tr = probe_terminal_contract(invocation.inputs.to_inputs())
                results.append(TerminalRunResult(label=invocation.label, passed=tr.passed, failures=tr.failures))
            case _ServiceInvocation():
                sr = probe_service_contract(invocation.inputs.to_inputs())
                results.append(ServiceRunResult(label=invocation.label, passed=sr.passed, failures=sr.failures))
            case _ArtifactInvocation():
                ar = probe_artifact_contract(invocation.inputs.to_inputs())
                results.append(ArtifactRunResult(label=invocation.label, passed=ar.passed, failures=ar.failures))
            case _CleanupInvocation():
                cr = probe_cleanup_contract(invocation.inputs.to_inputs())
                results.append(CleanupRunResult(label=invocation.label, passed=cr.passed, failures=cr.failures))
            case _MediaInvocation():
                mr = probe_media_contract(invocation.inputs.to_inputs())
                results.append(MediaRunResult(label=invocation.label, passed=mr.passed, failures=mr.failures))
            case _DistributedInvocation():
                dr = probe_distributed_contract(invocation.inputs.to_inputs())
                results.append(DistributedRunResult(label=invocation.label, passed=dr.passed, failures=dr.failures))
    return ContractProbeSuiteResult(
        passed=all(r.passed for r in results),
        results=tuple(results),
    )


def load_contract_probe_suite(path: str | Path) -> ContractProbeSuite:
    """Load a JSON file and validate it against the suite schema."""
    raw = Path(path).read_text(encoding="utf-8")
    parsed = json.loads(raw)
    return ContractProbeSuite.model_validate(parsed)
