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


@dataclass(frozen=True, slots=True)
class AssertionMismatch:
    """A runtime invariant from AC-772 fired against the candidate solution."""

    invariant: str
    observed: str
    reason: str


@dataclass(frozen=True, slots=True)
class BudgetIncrease:
    """Increase a trial / sample budget (AC-770).

    Emitted when an assertion error suggests the failure is a
    signal-to-noise ratio problem (0 or near-zero hits at the
    available budget) rather than a code bug.
    """

    parameter: str
    current: int
    suggested_factor: int
    reason: str


@dataclass(frozen=True, slots=True)
class IndexingCheck:
    """Try the alternate 0-vs-1-indexed offset (AC-771).

    Emitted when a 0/N failure smells like a literature-vs-code
    indexing-base mismatch (e.g., `Z_16` referenced at
    `position = 16` when the canonical 0-indexed byte is 15).
    """

    reason: str


RemediationHint = (
    RefreshFixture
    | SurfaceSignatures
    | SmallCaseVerify
    | AssertionMismatch
    | BudgetIncrease
    | IndexingCheck
)


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


# --- AC-772 rule: invariant violation detector -----------------------------

_INVARIANT_VIOLATION_RE = re.compile(
    r"InvariantViolation:\s*(?P<inv>.+?)\s+failed;\s*observed:\s*(?P<obs>.+?)(?:$|\n)",
    re.IGNORECASE | re.DOTALL,
)


def rule_invariant_violation(report: FailureReport, **_: Any) -> list[RemediationHint]:
    """Emit one :class:`AssertionMismatch` per ``InvariantViolation:`` error
    string in the report (AC-772).

    Lives alongside the other AC-769 rules and :data:`DEFAULT_RULES`
    rather than in :mod:`spec_verifier` so production callers of
    :func:`route_remediations` always see the rule without having to
    import :mod:`spec_verifier` first (PR #977 review P2).
    """
    out: list[RemediationHint] = []
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            for match in _INVARIANT_VIOLATION_RE.finditer(error):
                inv = match.group("inv").strip().strip('"')
                obs = match.group("obs").strip().strip('"')
                out.append(
                    AssertionMismatch(
                        invariant=inv,
                        observed=obs,
                        reason=f"match {diagnosis.match_index}",
                    )
                )
    return out


# --- AC-770: threshold-vs-actual budget mismatch ---------------------------

# Two patterns for the two observed shapes:
#  - "k/total at N trials" — fraction first (loose).
#  - "at N trials: k/total" — trials first (c32 marker, AC-770).
_FRACTION_THEN_TRIALS = re.compile(
    r"(?P<k>\d+)\s*/\s*(?P<total>\d+)(?:\s+\w+){0,4}\s+at\s+(?P<trials>\d+)\s+trials",
    re.IGNORECASE,
)
_TRIALS_THEN_FRACTION = re.compile(
    r"at\s+(?P<trials>\d+)\s+trials[^\d]*?(?P<k>\d+)\s*/\s*(?P<total>\d+)",
    re.IGNORECASE,
)
_INSUFFICIENT_SAMPLES = re.compile(
    r"insufficient\s+samples|convergence\s+not\s+reached",
    re.IGNORECASE,
)


def _budget_factor_for(k: int, total: int) -> int:
    """k == 0 → 16x; otherwise 4x. Pinned by AC-770 acceptance tests."""
    if k == 0:
        return 16
    return 4


def rule_threshold_budget(report: FailureReport, **_: Any) -> list[RemediationHint]:
    """Emit :class:`BudgetIncrease` when an assertion error suggests the
    trial budget was too small to clear the signal-to-noise threshold."""
    hints: list[RemediationHint] = []
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            match = _TRIALS_THEN_FRACTION.search(error) or _FRACTION_THEN_TRIALS.search(error)
            if match is not None:
                k = int(match.group("k"))
                total = int(match.group("total"))
                if total == 0:
                    continue
                # Only fire when the observed pass rate is well below
                # 25% of the maximum (the ticket's heuristic). 15/16
                # → 93% pass rate → no hint.
                if k > total * 0.25:
                    continue
                trials = int(match.group("trials"))
                hints.append(
                    BudgetIncrease(
                        parameter="trials",
                        current=trials,
                        suggested_factor=_budget_factor_for(k, total),
                        reason=f"match {diagnosis.match_index}: {k}/{total} at {trials} trials",
                    )
                )
                continue
            if _INSUFFICIENT_SAMPLES.search(error):
                hints.append(
                    BudgetIncrease(
                        parameter="trials",
                        current=0,
                        suggested_factor=4,
                        reason=f"match {diagnosis.match_index}: insufficient samples / no convergence",
                    )
                )
    return hints


# --- AC-771: 0-vs-1 indexing-base detector --------------------------------

# Capture `k/total` shapes specifically for the "zero hits" indexing case;
# scoped tightly so 15/16 (high pass rate) does not fire.
_ZERO_OR_NEAR_ZERO_HITS = re.compile(
    r"(?P<k>\d+)\s*/\s*(?P<total>\d+)\s+(?:bytes\s+)?(?:recovered|hits|correct)",
    re.IGNORECASE,
)
# Source-code patterns: capture three shapes in one pass —
#   a) literature-style identifier:  `Z_16`, `index_32`, `idx_first` …
#   b) assignment to a generic name:  `position = 16`, `index = 32` …
#   c) assignment whose lvalue is itself the literature name:
#      `index_32 = 32`, `Z_16 = 15` …  (PR #979 review P2)
# Each match yields either a literature_value (group `n`) or a
# code_value (group `value`) or both (case c).
_INDEX_NAMED_CONSTANT = re.compile(
    r"\b(?P<name>(?:position|index|Z|X|idx)_(?P<n>\d+))(?:\s*=\s*(?P<value_named>\d+))?\b|"
    r"\b(?P<assign>position|index|idx)\s*=\s*(?P<value>\d+)",
    re.IGNORECASE,
)


def _detect_index_mismatch(source_code: str) -> tuple[int, int] | None:
    """Look for a literature-style identifier (e.g. ``Z_16``, ``index_32``)
    paired with a code-side numeric constant in the same source. Return
    ``(literature_value, code_value)`` when both candidates are present
    AND they differ by exactly 1 OR equal each other (the 0-vs-1-indexed
    mismatch shape; see c56 in the Cryptopals validation set).
    """
    literature_values: list[int] = []
    code_values: list[int] = []
    for match in _INDEX_NAMED_CONSTANT.finditer(source_code):
        if match.group("name") is not None:
            literature_values.append(int(match.group("n")))
            # PR #979 review P2: a `<name>_N = N` match also yields the
            # code-side value on the same line. Record both.
            value_named = match.group("value_named")
            if value_named is not None:
                code_values.append(int(value_named))
        elif match.group("value") is not None:
            code_values.append(int(match.group("value")))
    for lit in literature_values:
        for code in code_values:
            if abs(lit - code) == 1 or lit == code:
                return (lit, code)
    return None


def rule_indexing_base(
    report: FailureReport,
    *,
    source_code: str | None = None,
    **_: Any,
) -> list[RemediationHint]:
    """Emit :class:`IndexingCheck` when a near-zero hit rate AND source
    code together suggest a 0-vs-1-indexed mismatch between literature
    naming and code.

    PR #979 review (P2): the rule fires ONLY when source-code evidence
    is available. A bare 0/N failure is ambiguous (could be an AC-770
    budget problem) so the indexing rule stays quiet without source
    evidence rather than sending refinement down the wrong path. The
    rule is also NOT in :data:`DEFAULT_RULES`; callers with source
    code opt in explicitly.
    """
    if not source_code:
        return []
    hints: list[RemediationHint] = []
    for diagnosis in report.match_diagnoses:
        for error in diagnosis.errors:
            match = _ZERO_OR_NEAR_ZERO_HITS.search(error)
            if match is None:
                continue
            k = int(match.group("k"))
            total = int(match.group("total"))
            if total == 0 or k > total * 0.25:
                # 15/16 isn't an indexing issue; skip.
                continue
            mismatch = _detect_index_mismatch(source_code)
            if mismatch is None:
                continue
            lit, code = mismatch
            zero_indexed = lit - 1 if lit == code else min(lit, code)
            one_indexed = lit if lit == code else max(lit, code)
            hints.append(
                IndexingCheck(
                    reason=(
                        f"match {diagnosis.match_index}: {k}/{total} hits with "
                        f"indexing-name vs constant — try {zero_indexed} (0-indexed) "
                        f"and {one_indexed} (1-indexed)"
                    ),
                )
            )
    return hints


DEFAULT_RULES: list[Rule] = [
    rule_off_by_one,
    rule_positional_typerror,
    rule_stale_fixture,
    rule_threshold_budget,
    rule_invariant_violation,
    # PR #979 review (P2): rule_indexing_base is intentionally NOT in
    # the default set. It fires on 0/N failures, which equally describe
    # AC-770 budget problems; mixing both in the default path sends
    # refinement down the wrong remediation. Callers with source code
    # opt in via `rules=[*DEFAULT_RULES, rule_indexing_base]`.
]


# --- Router ----------------------------------------------------------------


def route_remediations(
    report: FailureReport,
    *,
    fixtures: dict[str, Fixture] | None = None,
    stale_after_days: int = 7,
    source_code: str | None = None,
    rules: Sequence[Rule] = (),
) -> list[RemediationHint]:
    """Run each rule against ``report``, return the concatenated hints.

    If ``rules`` is empty, the default ruleset is used. Pass an explicit
    list (including the defaults if desired) to extend or replace.
    ``source_code`` is forwarded to rules that consume it (AC-771).
    """
    chosen_rules = rules if rules else DEFAULT_RULES
    out: list[RemediationHint] = []
    for rule in chosen_rules:
        out.extend(
            rule(
                report,
                fixtures=fixtures,
                stale_after_days=stale_after_days,
                source_code=source_code,
            )
        )
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
    if isinstance(hint, AssertionMismatch):
        return f"invariant `{hint.invariant}` failed; observed `{hint.observed}` ({hint.reason})"
    if isinstance(hint, BudgetIncrease):
        scope = f"`{hint.parameter}`"
        return (
            f"increase {scope} budget by {hint.suggested_factor}x "
            f"(currently {hint.current}; {hint.reason})"
        )
    if isinstance(hint, IndexingCheck):
        return f"check 0-vs-1 indexing-base ({hint.reason})"
    return repr(hint)  # unreachable


def render_hints(hints: Sequence[RemediationHint]) -> str:
    """Emit a compact prompt block listing the suggested next moves."""
    if not hints:
        return ""
    lines = ["## Suggested next moves", ""]
    for hint in hints:
        lines.append(f"- {_describe(hint)}")
    return "\n".join(lines)
