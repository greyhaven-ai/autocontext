"""Authoritative ground-truth fixture loader (AC-767).

Pre-fetches external reference data (canonical test vectors, known-good
challenge files, published outputs) for a scenario before generation begins.
Different from ``bootstrap/`` (captures local env) and
``analytics/regression_fixtures.py`` (synthesizes from friction) — this
seeds the run with authoritative ground truth.

Six concerns, each independently testable:
  1. :class:`FixtureManifest` — parse manifest files (``from_json``).
  2. :class:`FixtureCache` — read/write cache files, scenario-scoped paths.
  3. :func:`load_fixtures` — orchestrate fetch + cache + checksum.
  4. :class:`UrlFetcher` — default fetcher (http(s) via urllib; ``file://``
     URIs and bare local paths read directly from disk).
  5. :func:`render_fixtures` — prompt-block emission.
  6. :func:`apply_to_context` — attach fixtures to a ``GenerationContext``.

Targets the wrong-reference-data bug class observed in the Cryptopals 1-7
campaign (c18, c19, c43, c44): the right answer existed externally, the
model just didn't have it.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse
from urllib.request import urlopen

# --- Errors ----------------------------------------------------------------


class FixtureChecksumError(Exception):
    """Fetched bytes did not match the manifest's expected_sha256."""


class FixtureFetchError(Exception):
    """The fetcher could not retrieve a fixture."""


# --- Value types -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FixtureProvenance:
    """Where a fixture came from and when."""

    source: str
    fetched_at: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Fixture:
    """A single resolved fixture."""

    key: str
    bytes_: bytes
    provenance: FixtureProvenance


@dataclass(frozen=True, slots=True)
class FixtureManifestEntry:
    """One row of a scenario fixture manifest."""

    key: str
    source: str
    expected_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    """Parsed scenario fixture manifest."""

    entries: list[FixtureManifestEntry] = field(default_factory=list)

    @classmethod
    def from_json(cls, path: Path) -> FixtureManifest:
        """Load a manifest JSON file. Missing file → empty manifest."""
        if not path.is_file():
            return cls(entries=[])
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"malformed fixture manifest at {path}: {e}") from e
        entries = [
            FixtureManifestEntry(
                key=row["key"],
                source=row["source"],
                expected_sha256=row.get("expected_sha256"),
            )
            for row in raw.get("entries", [])
        ]
        return cls(entries=entries)


# --- Fetcher ---------------------------------------------------------------


class Fetcher(Protocol):
    """Anything that can turn a source string (URL or path) into bytes."""

    def fetch(self, source: str) -> bytes: ...


class UrlFetcher:
    """Default fetcher: urllib for http(s), direct disk read for local paths.

    Local-path support (PR #968 review P3): ``file:///abs/path`` URIs and
    bare absolute/relative paths read from disk via :class:`Path`. http(s)
    URLs read via ``urllib.request.urlopen``.
    """

    def fetch(self, source: str) -> bytes:
        scheme = urlparse(source).scheme
        if scheme in ("", "file"):
            local_path = _local_path_for(source)
            try:
                return local_path.read_bytes()
            except OSError as e:
                raise FixtureFetchError(f"could not read {source}: {e}") from e
        try:
            with urlopen(source) as response:
                body: bytes = response.read()
                return body
        except OSError as e:
            raise FixtureFetchError(f"could not fetch {source}: {e}") from e


def _local_path_for(source: str) -> Path:
    """Resolve a ``file://`` URI or bare path to a :class:`Path`."""
    parsed = urlparse(source)
    if parsed.scheme == "file":
        return Path(parsed.path)
    return Path(source)


# --- Cache -----------------------------------------------------------------

# PR #968 review (P2): cache path segments must be single-segment names —
# no separators, no `..`, no absolute components — so a malicious manifest
# or scenario name cannot write outside the cache root.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._\-]+$")


def _validate_segment(name: str, *, kind: str) -> str:
    """Reject a path segment that would let a write escape the cache root."""
    if not name or name in {".", ".."}:
        raise ValueError(f"invalid {kind} {name!r}: must be a non-empty single path segment")
    if not _SAFE_SEGMENT.match(name):
        raise ValueError(f"invalid {kind} {name!r}: only alphanumerics, dot, underscore, and hyphen are allowed")
    return name


class FixtureCache:
    """File-backed cache, scenario-scoped.

    Layout: ``<root>/<scenario>/<key>.bin`` and ``<key>.provenance.json``.

    Both ``scenario`` and ``key`` must be single safe path segments
    (alphanumerics + ``.`` ``_`` ``-``); anything else raises
    :class:`ValueError` at the boundary so a manifest cannot write
    outside the cache root.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _paths(self, scenario: str, key: str) -> tuple[Path, Path]:
        safe_scen = _validate_segment(scenario, kind="scenario")
        safe_key = _validate_segment(key, kind="key")
        scen_dir = self._root / safe_scen
        return scen_dir / f"{safe_key}.bin", scen_dir / f"{safe_key}.provenance.json"

    def get(self, scenario: str, key: str) -> Fixture | None:
        bin_path, prov_path = self._paths(scenario, key)
        if not (bin_path.is_file() and prov_path.is_file()):
            return None
        body = bin_path.read_bytes()
        prov_data = json.loads(prov_path.read_text(encoding="utf-8"))
        provenance = FixtureProvenance(
            source=prov_data["source"],
            fetched_at=prov_data["fetched_at"],
            sha256=prov_data["sha256"],
        )
        return Fixture(key=key, bytes_=body, provenance=provenance)

    def put(self, scenario: str, fixture: Fixture) -> None:
        bin_path, prov_path = self._paths(scenario, fixture.key)
        bin_path.parent.mkdir(parents=True, exist_ok=True)
        bin_path.write_bytes(fixture.bytes_)
        prov_path.write_text(
            json.dumps(
                {
                    "source": fixture.provenance.source,
                    "fetched_at": fixture.provenance.fetched_at,
                    "sha256": fixture.provenance.sha256,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


# --- Orchestration ---------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_fixtures(
    manifest: FixtureManifest,
    *,
    fetcher: Fetcher,
    cache: FixtureCache,
    scenario: str,
) -> list[Fixture]:
    """Resolve every manifest entry to a :class:`Fixture`.

    For each entry:
      - If cache hit AND the on-disk bytes still hash to the manifest's
        expected sha (or, when no expected sha is set, the cached
        provenance's recorded sha): return cached.
      - Else: fetch, verify checksum (if expected), cache, return.

    Raises :class:`FixtureChecksumError` if a fetched body fails its expected sha.
    Raises :class:`FixtureFetchError` if the fetcher cannot retrieve.
    """
    out: list[Fixture] = []
    for entry in manifest.entries:
        cached = cache.get(scenario, entry.key)
        if cached is not None and _cache_is_fresh(cached, entry):
            out.append(cached)
            continue

        body = fetcher.fetch(entry.source)
        actual_sha = _sha256(body)
        if entry.expected_sha256 is not None and actual_sha != entry.expected_sha256:
            raise FixtureChecksumError(f"checksum mismatch for {entry.key}: expected {entry.expected_sha256}, got {actual_sha}")

        fixture = Fixture(
            key=entry.key,
            bytes_=body,
            provenance=FixtureProvenance(source=entry.source, fetched_at=_now_iso(), sha256=actual_sha),
        )
        cache.put(scenario, fixture)
        out.append(fixture)
    return out


def _cache_is_fresh(cached: Fixture, entry: FixtureManifestEntry) -> bool:
    """A cache entry is fresh iff its on-disk bytes still hash to the
    expected sha.

    PR #968 review (P2): the prior implementation trusted the
    provenance JSON's recorded sha. That let a corrupted ``.bin``
    alongside an intact provenance silently return tampered bytes.
    Rehash the actual cached payload so cache freshness is decided by
    what will be served, not by what the side-car claims.
    """
    if entry.expected_sha256 is None:
        return _sha256(cached.bytes_) == cached.provenance.sha256
    return _sha256(cached.bytes_) == entry.expected_sha256


# --- Scenario-level convenience --------------------------------------------


def load_scenario_fixtures(
    scenario: str,
    *,
    knowledge_root: Path,
    cache_root: Path,
    fetcher: Fetcher | None = None,
) -> list[Fixture]:
    """Load fixtures for ``scenario`` from ``<knowledge_root>/<scenario>/fixtures.json``.

    Missing manifest is a graceful no-op (returns ``[]``). The default fetcher
    is :class:`UrlFetcher`.
    """
    manifest_path = knowledge_root / scenario / "fixtures.json"
    manifest = FixtureManifest.from_json(manifest_path)
    if not manifest.entries:
        return []
    return load_fixtures(
        manifest,
        fetcher=fetcher if fetcher is not None else UrlFetcher(),
        cache=FixtureCache(cache_root),
        scenario=scenario,
    )


# --- Rendering -------------------------------------------------------------


def render_fixtures(fixtures: Sequence[Fixture]) -> str:
    """Emit a compact prompt block listing fixture keys + provenance."""
    if not fixtures:
        return ""
    lines: list[str] = ["## Available fixtures", ""]
    for fx in fixtures:
        sha_short = fx.provenance.sha256[:8]
        lines.append(f"- `{fx.key}` ({len(fx.bytes_)} bytes, sha {sha_short}) — {fx.provenance.source}")
    return "\n".join(lines)


# --- Context wiring --------------------------------------------------------


def apply_to_context(ctx: Any, fixtures: Sequence[Fixture]) -> None:
    """Attach fixtures to a ``GenerationContext``-shaped object.

    Sets ``ctx.fixtures`` to a dict mapping ``key -> Fixture``. If
    ``ctx.fixtures`` already exists, merge (incoming wins on conflict).
    """
    existing: dict[str, Fixture] = getattr(ctx, "fixtures", {}) or {}
    merged = {**existing, **{fx.key: fx for fx in fixtures}}
    ctx.fixtures = merged
