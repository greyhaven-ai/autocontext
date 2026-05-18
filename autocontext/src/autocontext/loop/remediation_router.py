"""Failure-type → remediation routing (AC-769).

Pattern-match a :class:`FailureReport` (plus optional context from AC-767
fixtures and AC-768 signature surfacing) to typed :class:`RemediationHint`
instances. Each rule is a pure function: pluggable, independently testable.

Targets the observation from the Cryptopals 1-7 campaign that different
failure classes want different remediation strategies:
  * Stale fixture → re-fetch (AC-767 RefreshFixture).
  * Wrong arg order → surface signatures (AC-768 SurfaceSignatures).
  * Off-by-one → small-case symbolic verification (SmallCaseVerify).

Rules consume the report (and optional kwargs ``fixtures``,
``stale_after_days``) and emit hints. The router runs every rule, collects
their output in order, and the renderer emits a "Suggested next moves"
prompt block.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from autocontext.harness.evaluation.failure_report import FailureReport
from autocontext.loop.fixture_loader import Fixture

# --- Hint value types ------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RefreshFixture:
    """Re-fetch a fixture whose cached payload looks stale."""

    key: str
    reason: str


@dataclass(frozen=True, slots=True)
class SurfaceSignatures:
    """Inject signatures from the named modules into the next prompt."""

    modules: tuple[str, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SmallCaseVerify:
    """Run a small-case symbolic verification for the named function."""

    function: str | None
    reason: str


RemediationHint = RefreshFixture | SurfaceSignatures | SmallCaseVerify


# --- Rule Protocol ---------------------------------------------------------


class Rule(Protocol):
    def __call__(self, report: FailureReport, **kwargs: Any) -> list[RemediationHint]: ...


# --- Built-in rules --------------------------------------------------------


_EXPECTED_GOT = re.compile(r"expected\s+(?P<exp>-?\d+)[\w\s,/]*?got\s+(?P<got>-?\d+)", re.IGNORECASE)
_OFF_BY_KEYWORDS = re.compile(r"off[\s-]by[\s-](?P<n>\d+)", re.IGNORECASE)
_BLOCK_SIZES = {1, 8, 16, 32, 64, 128, 256}


def _is_off_by_one_diff(expected: int, got: int) -> bool:
    """A diff is considered off-by-one if it equals ±1, ±BLOCK, or ±BLOCK²
    for some common BLOCK size."""
    diff = abs(expected - got)
    if diff == 0:
        return False
    candidates = _BLOCK_SIZES | {b * b for b in _BLOCK_SIZES}
    return diff in candidates


def rule_off_by_one(report: FailureReport, **_: Any) -> list[RemediationHint]:
    """Emit :class:`SmallCaseVerify` when a numerical diff smells like
    an off-by-one or block-multiple error."""
    hints: list[RemediationHint] = []
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            match = _EXPECTED_GOT.search(error)
            if match is not None:
                expected = int(match.group("exp"))
                got = int(match.group("got"))
                if _is_off_by_one_diff(expected, got):
                    hints.append(
                        SmallCaseVerify(
                            function=None,
                            reason=f"match {diagnosis.match_index}: expected {expected}, got {got}",
                        )
                    )
                    continue
            if _OFF_BY_KEYWORDS.search(error):
                hints.append(
                    SmallCaseVerify(
                        function=None,
                        reason=f"match {diagnosis.match_index}: explicit off-by-N error",
                    )
                )
    return hints


_POSITIONAL_TYPEERROR = re.compile(
    r"TypeError:\s+(?P<func>\w+)\(\)\s+takes\s+\d+\s+positional\s+arguments?",
    re.IGNORECASE,
)
_TRACEBACK_FILE = re.compile(r'File\s+"(?P<path>[^"]+\.py)"')


def _modules_from_traceback(error: str) -> tuple[str, ...]:
    """Extract module stems from ``File "..."`` lines in a traceback."""
    modules: list[str] = []
    for match in _TRACEBACK_FILE.finditer(error):
        path = match.group("path")
        stem = path.rsplit("/", 1)[-1].removesuffix(".py")
        if stem and stem not in modules:
            modules.append(stem)
    return tuple(modules)


def rule_positional_typerror(report: FailureReport, **_: Any) -> list[RemediationHint]:
    """Emit :class:`SurfaceSignatures` for the modules in a positional-args
    ``TypeError`` traceback."""
    hints: list[RemediationHint] = []
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            if not _POSITIONAL_TYPEERROR.search(error):
                continue
            modules = _modules_from_traceback(error)
            if not modules:
                continue
            hints.append(
                SurfaceSignatures(
                    modules=modules,
                    reason=f"match {diagnosis.match_index}: positional TypeError",
                )
            )
    return hints


_MISSING_SUBSTRING = re.compile(r"missing[\s-]substring\b", re.IGNORECASE)


def _fixture_is_stale(fixture: Fixture, stale_after_days: int) -> bool:
    fetched = fixture.provenance.fetched_at
    try:
        ts = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    age = datetime.now(tz=UTC) - ts
    return age.days >= stale_after_days


def rule_stale_fixture(
    report: FailureReport,
    *,
    fixtures: dict[str, Fixture] | None = None,
    stale_after_days: int = 7,
    **_: Any,
) -> list[RemediationHint]:
    """Emit :class:`RefreshFixture` when a missing-substring failure
    references a fixture key whose cached payload is older than the
    staleness threshold."""
    if not fixtures:
        return []
    hints: list[RemediationHint] = []
    seen_keys: set[str] = set()
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            if not _MISSING_SUBSTRING.search(error):
                continue
            for key, fixture in fixtures.items():
                if key in seen_keys:
                    continue
                if key in error and _fixture_is_stale(fixture, stale_after_days):
                    hints.append(
                        RefreshFixture(
                            key=key,
                            reason=f"match {diagnosis.match_index}: cache aged >= {stale_after_days}d",
                        )
                    )
                    seen_keys.add(key)
    return hints


DEFAULT_RULES: list[Rule] = [rule_off_by_one, rule_positional_typerror, rule_stale_fixture]


# --- Router ----------------------------------------------------------------


def route_remediations(
    report: FailureReport,
    *,
    fixtures: dict[str, Fixture] | None = None,
    stale_after_days: int = 7,
    rules: Sequence[Rule] = (),
) -> list[RemediationHint]:
    """Run each rule against ``report``, return the concatenated hints.

    If ``rules`` is empty, the default ruleset is used. Pass an explicit
    list (including the defaults if desired) to extend or replace.
    """
    chosen_rules = rules if rules else DEFAULT_RULES
    out: list[RemediationHint] = []
    for rule in chosen_rules:
        out.extend(rule(report, fixtures=fixtures, stale_after_days=stale_after_days))
    return out


# --- Rendering -------------------------------------------------------------


def _describe(hint: RemediationHint) -> str:
    if isinstance(hint, RefreshFixture):
        return f"refresh fixture `{hint.key}` ({hint.reason})"
    if isinstance(hint, SurfaceSignatures):
        modules = ", ".join(f"`{m}`" for m in hint.modules)
        return f"surface signatures from {modules} ({hint.reason})"
    if isinstance(hint, SmallCaseVerify):
        target = f"`{hint.function}`" if hint.function else "the failing function"
        return f"small-case verify {target} ({hint.reason})"
    return repr(hint)  # unreachable


def render_hints(hints: Sequence[RemediationHint]) -> str:
    """Emit a compact prompt block listing the suggested next moves."""
    if not hints:
        return ""
    lines = ["## Suggested next moves", ""]
    for hint in hints:
        lines.append(f"- {_describe(hint)}")
    return "\n".join(lines)
