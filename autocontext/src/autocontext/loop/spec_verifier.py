"""Pre-flight attack-model spec verifier (AC-772).

Derives *invariants the candidate solution must satisfy* from a task spec
(plus optional fixtures from AC-767) by asking the LLM, parses the result
into structured ``Invariant`` records, caches per scenario keyed by spec
hash, and emits a ``## Solution invariants`` prompt block for injection
into the competitor prompt at gen 1.

Five concerns, each independently testable:

  1. :class:`Invariant` / :class:`InvariantSet` — frozen-slot value types.
  2. :func:`parse_invariants` — extract structured records from an LLM response.
  3. :func:`render_invariants` — emit the prompt block.
  4. :class:`InvariantCache` — persist by ``(scenario, spec_hash)``.
  5. :func:`derive_invariants` — orchestrate LLM call + parse + cache.

Plus AC-769 wiring:
  * :class:`AssertionMismatch` — new ``RemediationHint`` kind.
  * :func:`rule_invariant_violation` — recognise ``InvariantViolation`` in
    ``FailureReport.errors`` and emit one ``AssertionMismatch`` per violation.

Sister to AC-150 ``HarnessSynthesizer``: that synthesizes a *validator
function*; this derives *invariant statements about the solution* that
plug directly into prompts and route through the router on failure.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from autocontext.loop.remediation_router import AssertionMismatch, rule_invariant_violation
from autocontext.providers.base import LLMProvider

__all__ = [
    "AssertionMismatch",
    "Invariant",
    "InvariantCache",
    "InvariantSet",
    "derivation_input_hash",
    "derive_invariants",
    "parse_invariants",
    "render_invariants",
    "rule_invariant_violation",
    "spec_hash",
]

InvariantKind = Literal["roundtrip", "structural", "metric", "literal"]
Confidence = Literal["high", "medium", "low"]

_VALID_KINDS: frozenset[str] = frozenset({"roundtrip", "structural", "metric", "literal"})
_VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "medium", "low"})


# --------------------------------------------------------------------------
# Value types
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Invariant:
    """A single derived solution invariant."""

    kind: InvariantKind
    statement: str
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class InvariantSet:
    """Collection of invariants derived for one spec hash."""

    invariants: list[Invariant]
    spec_hash: str


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


_BLOCK_RE = re.compile(
    r"<invariant(?P<attrs>[^>]*)>(?P<body>.*?)</invariant>",
    re.DOTALL | re.IGNORECASE,
)
_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')


def _parse_attrs(attrs_str: str) -> dict[str, str]:
    return {m.group(1).lower(): m.group(2) for m in _ATTR_RE.finditer(attrs_str)}


def parse_invariants(text: str) -> list[Invariant]:
    """Extract structured ``Invariant`` records from an LLM response.

    Robust to commentary around the tags, missing attributes (kind defaults
    to ``metric``, confidence to ``medium``), and malformed blocks (silently
    skipped). Returns ``[]`` if no parseable blocks are present.
    """
    out: list[Invariant] = []
    for match in _BLOCK_RE.finditer(text):
        attrs = _parse_attrs(match.group("attrs") or "")
        raw_kind = attrs.get("kind", "").lower()
        kind: InvariantKind = raw_kind if raw_kind in _VALID_KINDS else "metric"  # type: ignore[assignment]
        raw_conf = attrs.get("confidence", "").lower()
        confidence: Confidence = raw_conf if raw_conf in _VALID_CONFIDENCES else "medium"  # type: ignore[assignment]
        statement = match.group("body").strip()
        # Normalise interior whitespace: collapse blank lines inside multi-line
        # statements but preserve line breaks for readability.
        statement = "\n".join(line.strip() for line in statement.splitlines() if line.strip())
        if not statement:
            continue
        out.append(Invariant(kind=kind, statement=statement, confidence=confidence))
    return out


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_invariants(invariant_set: InvariantSet) -> str:
    """Emit the ``## Solution invariants`` prompt block, or ``""`` if empty."""
    if not invariant_set.invariants:
        return ""
    lines: list[str] = ["## Solution invariants", ""]
    for inv in invariant_set.invariants:
        lines.append(f"- [{inv.kind}; {inv.confidence}] {inv.statement}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Spec hashing
# --------------------------------------------------------------------------


def spec_hash(text: str) -> str:
    """Stable hex digest of the task spec for cache keying."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def derivation_input_hash(spec: str, fixtures_block: str | None) -> str:
    """Stable hex digest of (spec, fixtures_block) for cache keying.

    PR #977 review (P2): the cache must reflect every input the
    derivation prompt sees. ``None`` and empty fixtures collapse to
    the same key (both mean "no fixtures") so consecutive calls hit
    cache. Otherwise the fixture bytes participate in the digest so
    a fixture refresh (AC-767 re-fetch) invalidates the cache.
    """
    payload = spec + "\x00" + (fixtures_block or "")
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Cache (mirrors AC-767's FixtureCache safe-name discipline)
# --------------------------------------------------------------------------


_SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-]*$")


def _require_safe_name(label: str, value: str) -> None:
    if not isinstance(value, str) or not value or not _SAFE_NAME.match(value) or ".." in value:
        raise ValueError(f"unsafe {label} name: {value!r}")


class InvariantCache:
    """File-backed cache for derived invariant sets.

    Layout: ``<root>/<scenario>/invariants_<spec_hash>.json``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _path(self, scenario: str, spec_hash_: str) -> Path:
        _require_safe_name("scenario", scenario)
        _require_safe_name("spec_hash", spec_hash_)
        return self._root / scenario / f"invariants_{spec_hash_}.json"

    def get(self, scenario: str, spec_hash_: str) -> InvariantSet | None:
        path = self._path(scenario, spec_hash_)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("spec_hash") != spec_hash_:
            return None
        invariants = [
            Invariant(kind=row["kind"], statement=row["statement"], confidence=row["confidence"])
            for row in data.get("invariants", [])
        ]
        return InvariantSet(invariants=invariants, spec_hash=spec_hash_)

    def put(self, scenario: str, invariant_set: InvariantSet) -> None:
        path = self._path(scenario, invariant_set.spec_hash)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "spec_hash": invariant_set.spec_hash,
                    "invariants": [
                        {"kind": i.kind, "statement": i.statement, "confidence": i.confidence} for i in invariant_set.invariants
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )


# --------------------------------------------------------------------------
# Derivation orchestrator
# --------------------------------------------------------------------------


_DERIVATION_SYSTEM_PROMPT = (
    "You are a precise software-engineering assistant. Given a task spec, "
    "you derive concise invariants the candidate solution MUST satisfy. "
    "Emit each invariant as an XML-like tag exactly:\n"
    '<invariant kind="K" confidence="C">STATEMENT</invariant>\n'
    "where K is one of: roundtrip, structural, metric, literal; and "
    "C is one of: high, medium, low. Be specific. No prose outside the tags."
)


def _build_derivation_user_prompt(spec: str, fixtures_block: str | None) -> str:
    parts = [f"Task spec:\n{spec}"]
    if fixtures_block:
        parts.append(f"Available authoritative fixtures:\n{fixtures_block}")
    parts.append(
        "Derive the solution invariants. Cover: roundtrip / structural shape / "
        "metric thresholds / literal expected values, where each applies."
    )
    return "\n\n".join(parts)


def derive_invariants(
    spec: str,
    *,
    scenario: str,
    provider: LLMProvider,
    cache: InvariantCache,
    fixtures_block: str | None = None,
) -> InvariantSet:
    """Derive (or fetch from cache) the invariant set for ``spec``.

    Caches by ``(scenario, spec_hash)``. Returns an empty ``InvariantSet``
    when the provider fails to emit any parseable blocks.
    """
    h = derivation_input_hash(spec, fixtures_block)
    cached = cache.get(scenario, h)
    if cached is not None:
        return cached

    user_prompt = _build_derivation_user_prompt(spec, fixtures_block)
    completion = provider.complete(_DERIVATION_SYSTEM_PROMPT, user_prompt)
    invariants = parse_invariants(completion.text)
    result = InvariantSet(invariants=invariants, spec_hash=h)
    cache.put(scenario, result)
    return result


# --------------------------------------------------------------------------
# AC-769 router rule
# --------------------------------------------------------------------------
#
# ``rule_invariant_violation`` lives in ``remediation_router`` (alongside
# ``DEFAULT_RULES``) so production callers of ``route_remediations``
# always see the rule without having to import this module first (PR
# #977 review P2). It is re-exported from this module so callers that
# imported it from here historically still resolve.
