"""Deterministic, pure repair functions for harness surfaces (AC-878).

Each repair here is a PURE function over recorded state: it never calls a
model, never reads answer hints, never touches the filesystem, and is fully
replayable. A repair inspects state it is handed, decides whether a known
failure mode is present, and returns a :class:`RepairResult` describing what it
did (or why it declined). The gate that owns side effects (writing files,
breaking a loop, rejecting a finish) acts on the decision; these functions only
decide.

The repairs are deliberately conservative. They only ever apply *structural*
fixes that cannot change task content:

- ``repair_tool_call_json`` restructures malformed tool-call JSON (strips a code
  fence, drops a trailing comma before a closer, closes a single truncated
  brace/bracket). It never guesses or alters a field value.
- ``repair_artifact_landing`` relocates by *matching existing produced content*
  against the expected contract. It never fabricates artifact content.
- ``finish_guard`` validates a completion claim; it never fabricates completion.
- ``loop_guard`` detects a stuck no-op cycle; the recovery is signalling the
  break, not synthesising an action.
"""

from __future__ import annotations

import json
import re

from autocontext.control_plane.contract_probes._base import (
    ArtifactContractProbeInputs,
    probe_artifact_contract,
)
from autocontext.harness_optimization.contract.models import Parity, RepairResult


def _parity() -> Parity:
    """Fresh cross-language parity stamp for a Python-implemented repair.

    TypeScript parity is pending until the mirror lands; the schema hash is
    empty here because these repairs share the RepairResult schema, whose hash
    is stamped by the sync tooling, not per-call.
    """

    return Parity(python="implemented", typescript="pending", schema_hash="")


# ---------------------------------------------------------------------------
# repair 1: tool-call JSON (structural-only)
# ---------------------------------------------------------------------------


def _strip_code_fence(raw: str) -> str | None:
    """Return the inside of a ```...``` / ```json...``` fence, else None.

    Structural wrapper removal only: the returned text is a verbatim slice of
    the fenced body, so no field value is altered.
    """

    stripped = raw.strip()
    if not stripped.startswith("```"):
        return None
    # Split only on \n / \r\n / \r so a fence body containing a vertical tab or
    # U+2028 stays one line, matching the TypeScript stripCodeFence regex. Plain
    # str.splitlines() would additionally split on the full Unicode line-boundary
    # set and diverge from the TS mirror.
    lines = re.split(r"\r\n|\r|\n", stripped)
    if len(lines) < 2:
        return None
    if not lines[0].startswith("```"):
        return None
    if lines[-1].strip() != "```":
        return None
    return "\n".join(lines[1:-1])


def _remove_trailing_commas(raw: str) -> str:
    """Drop structural commas that sit immediately before a ``}`` or ``]``.

    String-aware: a comma inside a JSON string literal is copied verbatim, so
    a value like ``"a,]"`` is never mutated. Only a comma that the grammar
    forbids (right before a closer) is removed.
    """

    out: list[str] = []
    i = 0
    n = len(raw)
    in_string = False
    escape = False
    while i < n:
        ch = raw[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and raw[j] in " \t\r\n":
                j += 1
            if j < n and raw[j] in "}]":
                # trailing comma before a closer: drop it, keep the closer.
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _close_single_unclosed(raw: str) -> str | None:
    """Append one closer iff exactly one brace/bracket is left open.

    Returns None when the truncation is ambiguous: zero unclosed openers,
    more than one unclosed opener (multiple plausible closings), or a
    truncation that lands inside a string literal.
    """

    stack: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_string:
        return None
    if len(stack) != 1:
        return None
    return raw + ("}" if stack[0] == "{" else "]")


def _structural_attempts(raw: str) -> list[tuple[str, str]]:
    """Ordered (reason, candidate) structural repairs to try, most-local first.

    Every candidate is derived from ``raw`` by structural transforms only
    (fence strip, trailing-comma drop, single-closer append), so none can
    introduce or change a field value.
    """

    attempts: list[tuple[str, str]] = []

    bases: list[tuple[str, str]] = [("", raw)]
    fenced = _strip_code_fence(raw)
    if fenced is not None and fenced != raw:
        bases.append(("stripped markdown code fence", fenced))

    for base_reason, base in bases:
        if base_reason:
            attempts.append((base_reason, base))
        no_comma = _remove_trailing_commas(base)
        if no_comma != base:
            attempts.append((_join(base_reason, "removed trailing comma before closer"), no_comma))
        closed = _close_single_unclosed(base)
        if closed is not None and closed != base:
            attempts.append((_join(base_reason, "closed a single truncated brace/bracket"), closed))
        if no_comma != base:
            both = _close_single_unclosed(no_comma)
            if both is not None and both != no_comma:
                attempts.append((_join(base_reason, "removed trailing comma and closed a truncated brace/bracket"), both))
    return attempts


def _join(prefix: str, suffix: str) -> str:
    return f"{prefix}; {suffix}" if prefix else suffix


def repair_tool_call_json(raw: str) -> tuple[str | None, RepairResult]:
    """Structurally repair malformed tool-call JSON without touching values.

    Returns ``(json_string, result)`` when already valid or repaired, and
    ``(None, result)`` when the input is ambiguous or unrecoverable by
    structural means. Field values are never guessed or altered.
    """

    try:
        json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        return raw, RepairResult(
            schema_version=1,
            repair_name="tool_call_json",
            status="not_applicable",
            reason="already valid json",
            target="",
            before={"valid": True},
            after={"valid": True},
            parity=_parity(),
        )

    for reason, candidate in _structural_attempts(raw):
        try:
            json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return candidate, RepairResult(
            schema_version=1,
            repair_name="tool_call_json",
            status="applied",
            reason=f"structural repair: {reason}",
            target="",
            before={"valid": False},
            after={"valid": True},
            parity=_parity(),
        )

    return None, RepairResult(
        schema_version=1,
        repair_name="tool_call_json",
        status="skipped",
        reason="ambiguous or unrecoverable tool json",
        target="",
        before={"valid": False},
        after={"valid": False},
        parity=_parity(),
    )


# ---------------------------------------------------------------------------
# repair 2: artifact landing (relocate by matching existing content)
# ---------------------------------------------------------------------------


def _contract_for(path: str, content: str, expected: ArtifactContractProbeInputs) -> ArtifactContractProbeInputs:
    """Rebuild the expected contract against a candidate path+content, in memory."""

    return ArtifactContractProbeInputs(
        path=path,
        content=content,
        expected_line_ending=expected.expected_line_ending,
        required_substrings=expected.required_substrings,
        forbidden_substrings=expected.forbidden_substrings,
        required_json_fields=expected.required_json_fields,
    )


def repair_artifact_landing(
    *,
    expected: ArtifactContractProbeInputs,
    produced: dict[str, str],
) -> tuple[str | None, RepairResult]:
    """Detect the "right content, wrong path" landing mistake, purely.

    ``produced`` maps produced_path -> file content (cached in memory). If the
    expected contract already passes, this is ``not_applicable``. Otherwise it
    searches ``produced`` for a path whose content satisfies the contract; when
    found at a different path, it returns that path as the relocation target.
    It performs no filesystem writes: the gate relocates, this function decides.
    """

    if probe_artifact_contract(expected).passed:
        return None, RepairResult(
            schema_version=1,
            repair_name="artifact_landing",
            status="not_applicable",
            reason="expected artifact already satisfies the contract",
            target="",
            before={"landed": True},
            after={"landed": True},
            parity=_parity(),
        )

    for produced_path, content in produced.items():
        if produced_path == expected.path:
            continue
        if probe_artifact_contract(_contract_for(produced_path, content, expected)).passed:
            return produced_path, RepairResult(
                schema_version=1,
                repair_name="artifact_landing",
                status="applied",
                reason="expected content found at a different path; relocate",
                target=produced_path,
                before={"landed": False},
                after={"landed": True, "source_path": produced_path},
                parity=_parity(),
            )

    return None, RepairResult(
        schema_version=1,
        repair_name="artifact_landing",
        status="skipped",
        reason="no produced artifact matches the expected contract",
        target="",
        before={"landed": False},
        after={"landed": False},
        parity=_parity(),
    )


# ---------------------------------------------------------------------------
# repair 3: finish guard (validate completion before accepting done)
# ---------------------------------------------------------------------------


def finish_guard(*, claimed_done: bool, completion_ok: bool, reason_if_not: str) -> RepairResult:
    """Reject a done claim when completion conditions are not met.

    This validates completion; it never fabricates completion. When the run
    claims done but ``completion_ok`` is False, the finish is rejected.
    """

    if claimed_done and not completion_ok:
        return RepairResult(
            schema_version=1,
            repair_name="finish_guard",
            status="applied",
            reason=f"finish rejected: {reason_if_not}",
            target="",
            before={"claimed_done": True},
            after={"accepted_done": False},
            parity=_parity(),
        )

    return RepairResult(
        schema_version=1,
        repair_name="finish_guard",
        status="not_applicable",
        reason="no unmet completion claim to reject",
        target="",
        before={"claimed_done": claimed_done},
        after={"accepted_done": claimed_done},
        parity=_parity(),
    )


# ---------------------------------------------------------------------------
# repair 4: loop guard (detect a stuck identical-action cycle)
# ---------------------------------------------------------------------------


def loop_guard(*, recent_actions: list[str], max_repeat: int) -> RepairResult:
    """Detect ``>= max_repeat`` consecutive identical trailing actions.

    Deterministic detection only. When a stuck cycle is present the result
    signals the break; acting on it (breaking the loop) is the gate's job.
    """

    trailing = _trailing_repeat_count(recent_actions)
    if trailing >= max_repeat >= 1:
        return RepairResult(
            schema_version=1,
            repair_name="loop_guard",
            status="applied",
            reason=f"loop detected: {max_repeat} identical actions",
            target=recent_actions[-1],
            before={"repeat_count": trailing},
            after={"loop_break": True},
            parity=_parity(),
        )

    return RepairResult(
        schema_version=1,
        repair_name="loop_guard",
        status="not_applicable",
        reason="no identical-action loop at the tail",
        target="",
        before={"repeat_count": trailing},
        after={"loop_break": False},
        parity=_parity(),
    )


def _trailing_repeat_count(actions: list[str]) -> int:
    if not actions:
        return 0
    last = actions[-1]
    count = 0
    for action in reversed(actions):
        if action == last:
            count += 1
        else:
            break
    return count
