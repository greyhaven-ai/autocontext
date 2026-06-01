"""AC-728 `autoctx probes extract` library surface (Python parity, slices 5 + 6).

Mirrors ``ts/src/control-plane/contract-probes/cli/extract.ts`` (TS
PRs #992 + #993): reads a harness-trace JSON envelope and
synthesises a runnable ``ContractProbeSuite`` covering all seven
AC-728 probe kinds. Slice 5 shipped the four base kinds (terminal /
directory / service / artifact); slice 6 extends coverage to
cleanup / media / distributed.

A harness trace bundles two halves:

- ``observations``: what actually happened in a recorded run.
- ``expectations``: what the operator declared should have happened.

The extractor joins them. Per the slice-1 audit invariant, every
declared expectation must have a matching observation; orphan
expectations fail validation at parse time rather than silently
producing a vacuously-passing suite. Per the slice-2 P2 fix,
rank-scoped distributed expectations (``expectedSteps``,
``mustMatchAcrossRanks``) reject when the trace records zero ranks;
the non-rank-scoped ``expectedWorldSize`` is fine to declare even
against an empty rank list.

Module split lives next to ``runner.py`` so both load the same
``ContractProbeSuite`` and ``_PatternList`` helpers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import (
    StrictBool,
    StrictInt,
    model_validator,
)

from .runner import (
    _PatternList,
    _Strict,
    _StrictDate,
)

__all__ = [
    "HarnessTrace",
    "HarnessTraceSchema",
    "extract_contract_probe_suite",
    "load_harness_trace",
    "serialize_suite",
]


# ---------------------------------------------------------------------------
# Per-kind observation + expectation schemas
# ---------------------------------------------------------------------------


class _TerminalObservation(_Strict):
    exitCode: StrictInt
    stdout: str = ""
    stderr: str = ""


class _TerminalExpectations(_Strict):
    expectedExitCode: StrictInt | None = None
    requiredStdoutPatterns: _PatternList | None = None
    forbiddenStdoutPatterns: _PatternList | None = None
    requiredStderrPatterns: _PatternList | None = None
    forbiddenStderrPatterns: _PatternList | None = None


class _WorkdirObservation(_Strict):
    presentFiles: tuple[str, ...]


class _DirectoryExpectations(_Strict):
    requiredFiles: tuple[str, ...] = ()
    allowedFiles: tuple[str, ...] = ()
    ignoredPatterns: _PatternList | None = None


class _ServiceEndpointSchema(_Strict):
    host: str
    port: StrictInt
    protocol: Literal["tcp", "udp"] | None = None


class _ServiceExpectations(_Strict):
    required: tuple[_ServiceEndpointSchema, ...] = ()
    allowed: tuple[_ServiceEndpointSchema, ...] | None = None


class _ArtifactObservation(_Strict):
    path: str
    content: str


class _ArtifactExpectations(_Strict):
    path: str
    label: str | None = None
    expectedLineEnding: Literal["lf", "crlf"] | None = None
    requiredSubstrings: tuple[str, ...] | None = None
    forbiddenSubstrings: tuple[str, ...] | None = None
    requiredJsonFields: tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# slice 6: cleanup / media / distributed observation + expectation schemas
# ---------------------------------------------------------------------------


class _CleanupEntrySchema(_Strict):
    path: str
    isSymlink: StrictBool | None = None
    symlinkTarget: str | None = None
    symlinkBroken: StrictBool | None = None
    # PR #1011 review (P2): plain `datetime` coerced JSON ints
    # (Unix timestamps); reject non-string / non-datetime inputs so
    # the trace JSON wire format matches TS `DateJson`.
    mtime: _StrictDate | None = None


class _CleanupObservation(_Strict):
    entries: tuple[_CleanupEntrySchema, ...]


class _CleanupExpectations(_Strict):
    # PR #1011 review (P2): same strict-date guard as `mtime` above.
    now: _StrictDate | None = None
    maxLockfileAgeMs: StrictInt | None = None
    lockfilePatterns: _PatternList | None = None
    sidecarPatterns: _PatternList | None = None
    backupPatterns: _PatternList | None = None
    forbidSymlinks: StrictBool | None = None
    allowedSymlinkTargets: tuple[str, ...] | None = None
    ignoredPatterns: _PatternList | None = None


class _MediaObservation(_Strict):
    path: str
    headerBytes: tuple[StrictInt, ...] | None = None
    width: StrictInt | None = None
    height: StrictInt | None = None
    byteSize: StrictInt | None = None
    columnCount: StrictInt | None = None
    columnNames: tuple[str, ...] | None = None
    lineCount: StrictInt | None = None


class _MediaExpectations(_Strict):
    path: str
    label: str | None = None
    expectedMagicBytes: tuple[StrictInt, ...] | None = None
    expectedWidth: StrictInt | None = None
    expectedHeight: StrictInt | None = None
    minByteSize: StrictInt | None = None
    maxByteSize: StrictInt | None = None
    expectedColumnCount: StrictInt | None = None
    requiredColumnNames: tuple[str, ...] | None = None
    expectedLineCount: StrictInt | None = None


class _DistributedRankSchema(_Strict):
    rank: StrictInt
    steps: StrictInt | None = None
    observations: dict[str, str] | None = None


class _DistributedObservation(_Strict):
    worldSize: StrictInt | None = None
    ranks: tuple[_DistributedRankSchema, ...]


class _DistributedExpectations(_Strict):
    expectedWorldSize: StrictInt | None = None
    expectedSteps: StrictInt | None = None
    mustMatchAcrossRanks: tuple[str, ...] | None = None


# ---------------------------------------------------------------------------
# Trace envelope
# ---------------------------------------------------------------------------


class _HarnessObservations(_Strict):
    terminal: _TerminalObservation | None = None
    workdir: _WorkdirObservation | None = None
    services: tuple[_ServiceEndpointSchema, ...] | None = None
    artifacts: tuple[_ArtifactObservation, ...] | None = None
    cleanup: _CleanupObservation | None = None
    media: tuple[_MediaObservation, ...] | None = None
    distributed: _DistributedObservation | None = None


class _HarnessExpectations(_Strict):
    terminal: _TerminalExpectations | None = None
    directory: _DirectoryExpectations | None = None
    services: _ServiceExpectations | None = None
    artifacts: tuple[_ArtifactExpectations, ...] | None = None
    cleanup: _CleanupExpectations | None = None
    media: tuple[_MediaExpectations, ...] | None = None
    distributed: _DistributedExpectations | None = None


class HarnessTrace(_Strict):
    """Slice-5 harness-trace envelope: 4 base probe kinds only.

    Slice 6 will extend ``observations`` / ``expectations`` with
    cleanup / media / distributed sections. The envelope itself
    forbids unknown keys today, so adding sections in slice 6 is a
    visible source change.
    """

    schema_version: Literal[1]
    label: str | None = None
    observations: _HarnessObservations
    expectations: _HarnessExpectations | None = None

    @model_validator(mode="after")
    def _check_orphan_expectations(self) -> HarnessTrace:
        """Every declared expectation must have its matching observation.

        Mirrors the TS ``superRefine`` (PR #992 review P2): without
        this guard an expectation-only section would be silently
        dropped at extraction time and the resulting suite would
        pass vacuously. The slice-1 audit invariant says: corrupted
        traces fail loudly, not silently.
        """
        if self.expectations is None:
            return self

        violations: list[str] = []
        obs = self.observations
        exp = self.expectations

        if exp.terminal is not None and obs.terminal is None:
            violations.append(
                "expectations.terminal: declared without observations.terminal; add terminal exit code / stdout / stderr"
            )
        if exp.directory is not None and obs.workdir is None:
            violations.append("expectations.directory: declared without observations.workdir; add the present-files list")
        if exp.services is not None and obs.services is None:
            violations.append("expectations.services: declared without observations.services; add the observed endpoints list")

        if exp.artifacts is not None:
            if obs.artifacts is None:
                violations.append(
                    "expectations.artifacts: per-artifact expectations declared "
                    "without observations.artifacts; add the matching observations"
                )
            else:
                observed_paths = {a.path for a in obs.artifacts}
                seen_paths: set[str] = set()
                for index, art_exp in enumerate(exp.artifacts):
                    if art_exp.path not in observed_paths:
                        violations.append(
                            f"expectations.artifacts.{index}.path: expectation "
                            f"references {art_exp.path!r} but no observation "
                            "with that path was recorded"
                        )
                    if art_exp.path in seen_paths:
                        violations.append(
                            f"expectations.artifacts.{index}.path: duplicate "
                            f"per-artifact expectation for {art_exp.path!r}; "
                            "merge into a single entry"
                        )
                    seen_paths.add(art_exp.path)

        # ---- slice 6: cleanup / media / distributed ----------------------

        if exp.cleanup is not None and obs.cleanup is None:
            violations.append("expectations.cleanup: declared without observations.cleanup.entries; add the directory listing")

        if exp.media is not None:
            if obs.media is None:
                violations.append(
                    "expectations.media: per-media expectations declared "
                    "without observations.media; add the matching observations"
                )
            else:
                observed_media_paths = {m.path for m in obs.media}
                seen_media_paths: set[str] = set()
                for index, m_exp in enumerate(exp.media):
                    if m_exp.path not in observed_media_paths:
                        violations.append(
                            f"expectations.media.{index}.path: expectation "
                            f"references {m_exp.path!r} but no observation "
                            "with that path was recorded"
                        )
                    if m_exp.path in seen_media_paths:
                        # PR #993 review (P2) parity: duplicate per-media
                        # expectations silently lost assertions because the
                        # extractor stored them in a path-keyed dict. Reject
                        # at parse time rather than overwrite.
                        violations.append(
                            f"expectations.media.{index}.path: duplicate "
                            f"per-media expectation for {m_exp.path!r}; "
                            "merge into a single entry"
                        )
                    seen_media_paths.add(m_exp.path)

        if exp.distributed is not None:
            if obs.distributed is None:
                violations.append(
                    "expectations.distributed: declared without observations.distributed.ranks; add the rank reports"
                )
            elif len(obs.distributed.ranks) == 0:
                # PR #1005 review (P2) parity: rank-scoped expectations
                # (`expectedSteps`, `mustMatchAcrossRanks`) silently pass
                # when there are zero rank reports because the slice-2
                # distributed probe's per-rank loop is vacuous. The
                # non-rank-scoped `expectedWorldSize` is fine to declare
                # against an empty rank list, so we reject only the two
                # rank-scoped expectations here.
                if exp.distributed.expectedSteps is not None:
                    violations.append(
                        "expectations.distributed.expectedSteps: rank-scoped "
                        "expectation requires observations.distributed.ranks "
                        "to contain at least one report"
                    )
                if exp.distributed.mustMatchAcrossRanks is not None:
                    violations.append(
                        "expectations.distributed.mustMatchAcrossRanks: "
                        "rank-scoped expectation requires "
                        "observations.distributed.ranks to contain at least "
                        "one report"
                    )

        if violations:
            raise ValueError("\n".join(violations))
        return self


HarnessTraceSchema = HarnessTrace


# ---------------------------------------------------------------------------
# Extractor
# ---------------------------------------------------------------------------


def _service_endpoint_dict(endpoint: _ServiceEndpointSchema) -> dict:
    payload = {"host": endpoint.host, "port": endpoint.port}
    if endpoint.protocol is not None:
        payload["protocol"] = endpoint.protocol
    return payload


def _patterns_dict_list(patterns: tuple) -> list[dict]:
    """Serialise a ``_PatternList`` as a list of ``{source, flags}`` dicts.

    The runner schema accepts either bare strings or ``{source, flags}``
    objects. Emitting the dict form preserves any flags set on the
    compiled pattern.
    """
    out: list[dict] = []
    flag_chars = {
        2: "i",  # re.IGNORECASE
        8: "m",  # re.MULTILINE
        16: "s",  # re.DOTALL
        64: "x",  # re.VERBOSE
        32: "u",  # re.UNICODE
    }
    for pat in patterns:
        flags = ""
        for bit, ch in flag_chars.items():
            if pat.flags & bit:
                flags += ch
        # re.UNICODE is set by default in Python 3 for str patterns; drop
        # it from the serialised flag string so round-trips do not gain
        # a spurious `u`.
        flags = flags.replace("u", "")
        entry: dict = {"source": pat.pattern}
        if flags:
            entry["flags"] = flags
        out.append(entry)
    return out


def extract_contract_probe_suite(trace: HarnessTrace) -> dict:
    """Build a ``ContractProbeSuite`` dict from a harness trace.

    Returns a plain dict matching the slice-3 suite wire format so
    callers can serialise it directly via ``json.dumps`` without an
    intermediate model dump. Pure function; no IO.
    """
    probes: list[dict] = []
    label = trace.label
    expectations = trace.expectations

    if trace.observations.terminal is not None:
        term_exp = expectations.terminal if expectations else None
        inputs: dict = {
            "exitCode": trace.observations.terminal.exitCode,
            "stdout": trace.observations.terminal.stdout,
            "stderr": trace.observations.terminal.stderr,
        }
        if term_exp is not None:
            if term_exp.expectedExitCode is not None:
                inputs["expectedExitCode"] = term_exp.expectedExitCode
            if term_exp.requiredStdoutPatterns is not None:
                inputs["requiredStdoutPatterns"] = _patterns_dict_list(term_exp.requiredStdoutPatterns)
            if term_exp.forbiddenStdoutPatterns is not None:
                inputs["forbiddenStdoutPatterns"] = _patterns_dict_list(term_exp.forbiddenStdoutPatterns)
            if term_exp.requiredStderrPatterns is not None:
                inputs["requiredStderrPatterns"] = _patterns_dict_list(term_exp.requiredStderrPatterns)
            if term_exp.forbiddenStderrPatterns is not None:
                inputs["forbiddenStderrPatterns"] = _patterns_dict_list(term_exp.forbiddenStderrPatterns)
        probe: dict = {"kind": "terminal", "inputs": inputs}
        if label is not None:
            probe["label"] = label
        probes.append(probe)

    if trace.observations.workdir is not None:
        dir_exp = expectations.directory if expectations else None
        inputs = {
            "presentFiles": list(trace.observations.workdir.presentFiles),
            "requiredFiles": list(dir_exp.requiredFiles) if dir_exp else [],
            "allowedFiles": list(dir_exp.allowedFiles) if dir_exp else [],
        }
        if dir_exp is not None and dir_exp.ignoredPatterns is not None:
            inputs["ignoredPatterns"] = _patterns_dict_list(dir_exp.ignoredPatterns)
        probe = {"kind": "directory", "inputs": inputs}
        if label is not None:
            probe["label"] = label
        probes.append(probe)

    if trace.observations.services is not None:
        svc_exp = expectations.services if expectations else None
        inputs = {
            "observed": [_service_endpoint_dict(e) for e in trace.observations.services],
            "required": [_service_endpoint_dict(e) for e in svc_exp.required] if svc_exp is not None else [],
        }
        if svc_exp is not None and svc_exp.allowed is not None:
            inputs["allowed"] = [_service_endpoint_dict(e) for e in svc_exp.allowed]
        probe = {"kind": "service", "inputs": inputs}
        if label is not None:
            probe["label"] = label
        probes.append(probe)

    if trace.observations.artifacts is not None:
        artifact_exp_by_path: dict[str, _ArtifactExpectations] = {}
        if expectations is not None and expectations.artifacts is not None:
            for declared_exp in expectations.artifacts:
                artifact_exp_by_path[declared_exp.path] = declared_exp
        for artifact in trace.observations.artifacts:
            art_exp = artifact_exp_by_path.get(artifact.path)
            inputs = {"path": artifact.path, "content": artifact.content}
            if art_exp is not None:
                if art_exp.expectedLineEnding is not None:
                    inputs["expectedLineEnding"] = art_exp.expectedLineEnding
                if art_exp.requiredSubstrings is not None:
                    inputs["requiredSubstrings"] = list(art_exp.requiredSubstrings)
                if art_exp.forbiddenSubstrings is not None:
                    inputs["forbiddenSubstrings"] = list(art_exp.forbiddenSubstrings)
                if art_exp.requiredJsonFields is not None:
                    inputs["requiredJsonFields"] = list(art_exp.requiredJsonFields)
            probe = {"kind": "artifact", "inputs": inputs}
            probe_label = art_exp.label if art_exp is not None and art_exp.label is not None else label
            if probe_label is not None:
                probe["label"] = probe_label
            probes.append(probe)

    # ---- slice 6: cleanup / media / distributed emission --------------------

    if trace.observations.cleanup is not None:
        cln_exp = expectations.cleanup if expectations else None
        inputs = {
            "entries": [_cleanup_entry_dict(e) for e in trace.observations.cleanup.entries],
        }
        if cln_exp is not None:
            if cln_exp.now is not None:
                inputs["now"] = cln_exp.now.isoformat()
            if cln_exp.maxLockfileAgeMs is not None:
                inputs["maxLockfileAgeMs"] = cln_exp.maxLockfileAgeMs
            if cln_exp.lockfilePatterns is not None:
                inputs["lockfilePatterns"] = _patterns_dict_list(cln_exp.lockfilePatterns)
            if cln_exp.sidecarPatterns is not None:
                inputs["sidecarPatterns"] = _patterns_dict_list(cln_exp.sidecarPatterns)
            if cln_exp.backupPatterns is not None:
                inputs["backupPatterns"] = _patterns_dict_list(cln_exp.backupPatterns)
            if cln_exp.forbidSymlinks is not None:
                inputs["forbidSymlinks"] = cln_exp.forbidSymlinks
            if cln_exp.allowedSymlinkTargets is not None:
                inputs["allowedSymlinkTargets"] = list(cln_exp.allowedSymlinkTargets)
            if cln_exp.ignoredPatterns is not None:
                inputs["ignoredPatterns"] = _patterns_dict_list(cln_exp.ignoredPatterns)
        probe = {"kind": "cleanup", "inputs": inputs}
        if label is not None:
            probe["label"] = label
        probes.append(probe)

    if trace.observations.media is not None:
        media_exp_by_path: dict[str, _MediaExpectations] = {}
        if expectations is not None and expectations.media is not None:
            for declared_m_exp in expectations.media:
                media_exp_by_path[declared_m_exp.path] = declared_m_exp
        for observation in trace.observations.media:
            m_exp = media_exp_by_path.get(observation.path)
            inputs = {"path": observation.path}
            if observation.headerBytes is not None:
                inputs["headerBytes"] = list(observation.headerBytes)
            if observation.width is not None:
                inputs["width"] = observation.width
            if observation.height is not None:
                inputs["height"] = observation.height
            if observation.byteSize is not None:
                inputs["byteSize"] = observation.byteSize
            if observation.columnCount is not None:
                inputs["columnCount"] = observation.columnCount
            if observation.columnNames is not None:
                inputs["columnNames"] = list(observation.columnNames)
            if observation.lineCount is not None:
                inputs["lineCount"] = observation.lineCount
            if m_exp is not None:
                if m_exp.expectedMagicBytes is not None:
                    inputs["expectedMagicBytes"] = list(m_exp.expectedMagicBytes)
                if m_exp.expectedWidth is not None:
                    inputs["expectedWidth"] = m_exp.expectedWidth
                if m_exp.expectedHeight is not None:
                    inputs["expectedHeight"] = m_exp.expectedHeight
                if m_exp.minByteSize is not None:
                    inputs["minByteSize"] = m_exp.minByteSize
                if m_exp.maxByteSize is not None:
                    inputs["maxByteSize"] = m_exp.maxByteSize
                if m_exp.expectedColumnCount is not None:
                    inputs["expectedColumnCount"] = m_exp.expectedColumnCount
                if m_exp.requiredColumnNames is not None:
                    inputs["requiredColumnNames"] = list(m_exp.requiredColumnNames)
                if m_exp.expectedLineCount is not None:
                    inputs["expectedLineCount"] = m_exp.expectedLineCount
            probe = {"kind": "media", "inputs": inputs}
            probe_label = m_exp.label if m_exp is not None and m_exp.label is not None else label
            if probe_label is not None:
                probe["label"] = probe_label
            probes.append(probe)

    if trace.observations.distributed is not None:
        dist_exp = expectations.distributed if expectations else None
        inputs = {
            "ranks": [_distributed_rank_dict(r) for r in trace.observations.distributed.ranks],
        }
        if trace.observations.distributed.worldSize is not None:
            inputs["worldSize"] = trace.observations.distributed.worldSize
        if dist_exp is not None:
            if dist_exp.expectedWorldSize is not None:
                inputs["expectedWorldSize"] = dist_exp.expectedWorldSize
            if dist_exp.expectedSteps is not None:
                inputs["expectedSteps"] = dist_exp.expectedSteps
            if dist_exp.mustMatchAcrossRanks is not None:
                inputs["mustMatchAcrossRanks"] = list(dist_exp.mustMatchAcrossRanks)
        probe = {"kind": "distributed", "inputs": inputs}
        if label is not None:
            probe["label"] = label
        probes.append(probe)

    return {"schema_version": 1, "probes": probes}


def _cleanup_entry_dict(entry: _CleanupEntrySchema) -> dict:
    payload: dict = {"path": entry.path}
    if entry.isSymlink is not None:
        payload["isSymlink"] = entry.isSymlink
    if entry.symlinkTarget is not None:
        payload["symlinkTarget"] = entry.symlinkTarget
    if entry.symlinkBroken is not None:
        payload["symlinkBroken"] = entry.symlinkBroken
    if entry.mtime is not None:
        payload["mtime"] = entry.mtime.isoformat()
    return payload


def _distributed_rank_dict(rank: _DistributedRankSchema) -> dict:
    payload: dict = {"rank": rank.rank}
    if rank.steps is not None:
        payload["steps"] = rank.steps
    if rank.observations is not None:
        payload["observations"] = dict(rank.observations)
    return payload


def serialize_suite(suite: dict) -> str:
    """Serialise an extracted suite to JSON suitable for the suite runner."""
    return json.dumps(suite, indent=2)


def load_harness_trace(path: str | Path) -> HarnessTrace:
    """Load a harness trace JSON file and validate it."""
    raw = Path(path).read_text(encoding="utf-8")
    parsed = json.loads(raw)
    return HarnessTraceSchema.model_validate(parsed)
