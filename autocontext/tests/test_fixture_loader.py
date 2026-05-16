"""Tests for AC-767 authoritative ground-truth fixture loader.

Six concerns under test, each isolated:
  1. ``FixtureManifest.from_json`` — parse manifest files.
  2. ``FixtureCache`` — read/write cache files, scenario-scoped paths.
  3. ``load_fixtures`` — orchestrate fetch + cache + checksum.
  4. ``UrlFetcher`` — default urllib fetcher (with patched urlopen).
  5. ``render_fixtures`` — prompt block emission.
  6. ``apply_to_context`` — populate a GenerationContext-shaped fixtures dict.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autocontext.loop.fixture_loader import (
    Fixture,
    FixtureCache,
    FixtureChecksumError,
    FixtureFetchError,
    FixtureManifest,
    FixtureManifestEntry,
    FixtureProvenance,
    UrlFetcher,
    apply_to_context,
    load_fixtures,
    load_scenario_fixtures,
    render_fixtures,
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- Stub fetcher ---------------------------------------------------------


class StubFetcher:
    """In-memory fetcher for tests. Records call counts."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, source: str) -> bytes:
        self.calls.append(source)
        if source not in self.responses:
            raise FixtureFetchError(f"stub has no response for {source}")
        return self.responses[source]


# --- TestFixtureManifest --------------------------------------------------


class TestFixtureManifest:
    def test_load_from_json(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "key": "data_c19",
                            "source": "https://example.com/c19.txt",
                            "expected_sha256": "a" * 64,
                        },
                        {
                            "key": "data_c20",
                            "source": "https://example.com/c20.txt",
                        },
                    ]
                }
            )
        )
        manifest = FixtureManifest.from_json(path)
        assert len(manifest.entries) == 2
        assert manifest.entries[0].key == "data_c19"
        assert manifest.entries[0].expected_sha256 == "a" * 64
        assert manifest.entries[1].expected_sha256 is None

    def test_missing_file_is_empty_manifest(self, tmp_path: Path) -> None:
        # A scenario without a manifest must be a graceful no-op, not an error.
        manifest = FixtureManifest.from_json(tmp_path / "nope.json")
        assert manifest.entries == []

    def test_malformed_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(ValueError):
            FixtureManifest.from_json(path)


# --- TestFixtureCache -----------------------------------------------------


class TestFixtureCache:
    def test_put_and_get_roundtrip(self, tmp_path: Path) -> None:
        cache = FixtureCache(tmp_path)
        prov = FixtureProvenance(source="https://example.com/a", fetched_at="2026-05-15T00:00:00Z", sha256=_sha256(b"hi"))
        fixture = Fixture(key="k", bytes_=b"hi", provenance=prov)
        cache.put("scen", fixture)

        loaded = cache.get("scen", "k")
        assert loaded is not None
        assert loaded.key == "k"
        assert loaded.bytes_ == b"hi"
        assert loaded.provenance.source == "https://example.com/a"

    def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        assert FixtureCache(tmp_path).get("scen", "absent") is None

    def test_scenarios_are_isolated(self, tmp_path: Path) -> None:
        cache = FixtureCache(tmp_path)
        prov = FixtureProvenance(source="x", fetched_at="t", sha256=_sha256(b"v"))
        cache.put("a", Fixture(key="shared", bytes_=b"v", provenance=prov))
        assert cache.get("b", "shared") is None
        assert cache.get("a", "shared") is not None

    def test_traversing_scenario_name_rejected(self, tmp_path: Path) -> None:
        """Reviewer F4: ``../outside`` as scenario must not escape the cache root."""
        cache = FixtureCache(tmp_path)
        prov = FixtureProvenance(source="x", fetched_at="t", sha256=_sha256(b"x"))
        fixture = Fixture(key="k", bytes_=b"x", provenance=prov)
        with pytest.raises(ValueError):
            cache.put("../outside", fixture)
        with pytest.raises(ValueError):
            cache.get("../outside", "k")

    def test_traversing_key_rejected(self, tmp_path: Path) -> None:
        """Reviewer F4: ``../escape`` as key must not escape the scenario dir."""
        cache = FixtureCache(tmp_path)
        prov = FixtureProvenance(source="x", fetched_at="t", sha256=_sha256(b"x"))
        fixture = Fixture(key="../../escape", bytes_=b"x", provenance=prov)
        with pytest.raises(ValueError):
            cache.put("scen", fixture)
        with pytest.raises(ValueError):
            cache.get("scen", "../../escape")

    def test_absolute_path_in_name_rejected(self, tmp_path: Path) -> None:
        cache = FixtureCache(tmp_path)
        prov = FixtureProvenance(source="x", fetched_at="t", sha256=_sha256(b"x"))
        with pytest.raises(ValueError):
            cache.put("/abs", Fixture(key="k", bytes_=b"x", provenance=prov))


class TestFixtureCacheBytesIntegrity:
    """Reviewer F3: cache reads must verify the actual bytes, not trust provenance."""

    def test_corrupted_bin_with_intact_provenance_is_rejected(self, tmp_path: Path) -> None:
        cache = FixtureCache(tmp_path)
        # Put a clean fixture, then tamper with the .bin while leaving provenance.
        good = b"clean payload"
        prov = FixtureProvenance(source="https://x", fetched_at="t", sha256=_sha256(good))
        cache.put("scen", Fixture(key="k", bytes_=good, provenance=prov))
        # Find the cached .bin and overwrite.
        bin_path = tmp_path / "scen" / "k.bin"
        assert bin_path.is_file()
        bin_path.write_bytes(b"CORRUPTED")
        # get() must NOT silently return corrupted bytes claiming the old sha.
        assert cache.get("scen", "k") is None


class TestLoadFixturesSourceChange:
    """Reviewer F5: cache-hit must invalidate when the manifest source changes,
    even when no expected_sha256 is given."""

    def test_source_change_triggers_refetch(self, tmp_path: Path) -> None:
        old_body = b"old"
        new_body = b"new"
        cache = FixtureCache(tmp_path)
        # Seed with body from source=old.
        prov = FixtureProvenance(source="https://example.com/old", fetched_at="t", sha256=_sha256(old_body))
        cache.put("scen", Fixture(key="k", bytes_=old_body, provenance=prov))

        # Manifest now points at source=new; no expected_sha256.
        manifest = FixtureManifest(entries=[FixtureManifestEntry(key="k", source="https://example.com/new")])
        fetcher = StubFetcher({"https://example.com/new": new_body})
        result = load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        assert result[0].bytes_ == new_body
        assert fetcher.calls == ["https://example.com/new"]


# --- TestLoadFixtures -----------------------------------------------------


class TestLoadFixtures:
    def test_empty_manifest_returns_empty(self, tmp_path: Path) -> None:
        result = load_fixtures(
            FixtureManifest(entries=[]),
            fetcher=StubFetcher({}),
            cache=FixtureCache(tmp_path),
            scenario="scen",
        )
        assert result == []

    def test_fetches_and_caches_on_miss(self, tmp_path: Path) -> None:
        body = b"hello world"
        manifest = FixtureManifest(
            entries=[
                FixtureManifestEntry(key="k1", source="https://example.com/x", expected_sha256=_sha256(body)),
            ]
        )
        fetcher = StubFetcher({"https://example.com/x": body})
        cache = FixtureCache(tmp_path)

        result = load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        assert len(result) == 1
        assert result[0].key == "k1"
        assert result[0].bytes_ == body
        assert result[0].provenance.sha256 == _sha256(body)
        assert fetcher.calls == ["https://example.com/x"]

        # Cache populated.
        assert cache.get("scen", "k1") is not None

    def test_cache_hit_skips_fetch(self, tmp_path: Path) -> None:
        body = b"hello world"
        manifest = FixtureManifest(
            entries=[
                FixtureManifestEntry(key="k1", source="https://example.com/x", expected_sha256=_sha256(body)),
            ]
        )
        cache = FixtureCache(tmp_path)
        # Pre-seed cache.
        prov = FixtureProvenance(source="https://example.com/x", fetched_at="t", sha256=_sha256(body))
        cache.put("scen", Fixture(key="k1", bytes_=body, provenance=prov))

        fetcher = StubFetcher({})  # would raise if invoked
        result = load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        assert len(result) == 1
        assert result[0].bytes_ == body
        assert fetcher.calls == []  # zero network calls on cache hit

    def test_stale_cache_sha_mismatch_refetches(self, tmp_path: Path) -> None:
        body = b"fresh"
        stale = b"stale"
        manifest = FixtureManifest(
            entries=[
                FixtureManifestEntry(key="k1", source="https://example.com/x", expected_sha256=_sha256(body)),
            ]
        )
        cache = FixtureCache(tmp_path)
        # Seed with stale content (wrong sha).
        prov = FixtureProvenance(source="https://example.com/x", fetched_at="t", sha256=_sha256(stale))
        cache.put("scen", Fixture(key="k1", bytes_=stale, provenance=prov))

        fetcher = StubFetcher({"https://example.com/x": body})
        result = load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        assert result[0].bytes_ == body
        assert fetcher.calls == ["https://example.com/x"]

    def test_fetcher_returns_wrong_sha_raises(self, tmp_path: Path) -> None:
        expected = _sha256(b"want")
        manifest = FixtureManifest(
            entries=[
                FixtureManifestEntry(key="k1", source="https://example.com/x", expected_sha256=expected),
            ]
        )
        fetcher = StubFetcher({"https://example.com/x": b"got_something_else"})
        cache = FixtureCache(tmp_path)

        with pytest.raises(FixtureChecksumError) as exc_info:
            load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        assert "k1" in str(exc_info.value)

    def test_fetcher_failure_raises(self, tmp_path: Path) -> None:
        manifest = FixtureManifest(
            entries=[
                FixtureManifestEntry(key="k1", source="https://example.com/missing"),
            ]
        )
        with pytest.raises(FixtureFetchError):
            load_fixtures(
                manifest,
                fetcher=StubFetcher({}),
                cache=FixtureCache(tmp_path),
                scenario="scen",
            )

    def test_no_expected_sha_skips_checksum_verify(self, tmp_path: Path) -> None:
        body = b"unverified content"
        manifest = FixtureManifest(entries=[FixtureManifestEntry(key="k1", source="https://example.com/x")])
        fetcher = StubFetcher({"https://example.com/x": body})
        cache = FixtureCache(tmp_path)
        result = load_fixtures(manifest, fetcher=fetcher, cache=cache, scenario="scen")
        # No expected sha → accept whatever, record the actual hash in provenance.
        assert result[0].bytes_ == body
        assert result[0].provenance.sha256 == _sha256(body)


# --- TestLoadScenarioFixtures ---------------------------------------------


class TestLoadScenarioFixtures:
    def test_no_manifest_means_empty_no_op(self, tmp_path: Path) -> None:
        knowledge_root = tmp_path / "knowledge"
        knowledge_root.mkdir()
        cache_root = tmp_path / "cache"
        # No knowledge/<scenario>/fixtures.json → graceful empty list.
        result = load_scenario_fixtures(
            "scen",
            knowledge_root=knowledge_root,
            cache_root=cache_root,
            fetcher=StubFetcher({}),
        )
        assert result == []

    def test_reads_manifest_at_knowledge_path(self, tmp_path: Path) -> None:
        knowledge_root = tmp_path / "knowledge"
        scen_dir = knowledge_root / "scen"
        scen_dir.mkdir(parents=True)
        body = b"payload"
        (scen_dir / "fixtures.json").write_text(
            json.dumps(
                {
                    "entries": [
                        {
                            "key": "data_c19",
                            "source": "https://x/c19",
                            "expected_sha256": _sha256(body),
                        }
                    ]
                }
            )
        )
        cache_root = tmp_path / "cache"
        fetcher = StubFetcher({"https://x/c19": body})

        result = load_scenario_fixtures(
            "scen",
            knowledge_root=knowledge_root,
            cache_root=cache_root,
            fetcher=fetcher,
        )
        assert len(result) == 1
        assert result[0].key == "data_c19"
        assert result[0].bytes_ == body


# --- TestUrlFetcher -------------------------------------------------------


class TestUrlFetcher:
    def test_fetches_via_urlopen(self) -> None:
        fetcher = UrlFetcher()
        body = b"the body"
        fake_response = type("R", (), {"read": lambda self: body, "__enter__": lambda self: self, "__exit__": lambda *a: None})()
        with patch("autocontext.loop.fixture_loader.urlopen", return_value=fake_response):
            assert fetcher.fetch("https://example.com/x") == body

    def test_urlopen_failure_raises_fixture_fetch_error(self) -> None:
        fetcher = UrlFetcher()
        with patch("autocontext.loop.fixture_loader.urlopen", side_effect=OSError("boom")):
            with pytest.raises(FixtureFetchError):
                fetcher.fetch("https://example.com/x")

    def test_local_file_path_is_supported(self, tmp_path: Path) -> None:
        """Reviewer F6: docstring claims local paths supported. Verify it actually works."""
        target = tmp_path / "fixture.dat"
        target.write_bytes(b"local payload")
        fetcher = UrlFetcher()
        assert fetcher.fetch(str(target)) == b"local payload"

    def test_missing_local_file_raises_fixture_fetch_error(self, tmp_path: Path) -> None:
        fetcher = UrlFetcher()
        with pytest.raises(FixtureFetchError):
            fetcher.fetch(str(tmp_path / "does-not-exist.dat"))

    def test_unknown_url_scheme_wrapped_as_fixture_fetch_error(self) -> None:
        """If urlopen raises ValueError ('unknown url type'), wrap it so callers
        get a single exception type to handle."""
        fetcher = UrlFetcher()
        with pytest.raises(FixtureFetchError):
            fetcher.fetch("gopher://example.com/x")


# --- TestRender -----------------------------------------------------------


class TestRender:
    def test_empty_list(self) -> None:
        assert render_fixtures([]) == ""

    def test_renders_compact_block(self) -> None:
        prov = FixtureProvenance(
            source="https://cryptopals.com/sets/3/challenges/19",
            fetched_at="2026-05-15T00:00:00Z",
            sha256="a" * 64,
        )
        f = Fixture(key="data_c19", bytes_=b"...", provenance=prov)
        block = render_fixtures([f])
        assert "## Available fixtures" in block
        assert "data_c19" in block
        assert "https://cryptopals.com/sets/3/challenges/19" in block
        assert "a" * 8 in block  # first 8 chars of sha shown


# --- TestApplyToContext ---------------------------------------------------


class TestApplyToContext:
    def test_writes_fixtures_dict_onto_context(self) -> None:
        # Use a stand-in for GenerationContext: anything with a settable attr.
        class Ctx:
            pass

        ctx = Ctx()
        prov = FixtureProvenance(source="s", fetched_at="t", sha256="x")
        fx = Fixture(key="data_c19", bytes_=b"payload", provenance=prov)
        apply_to_context(ctx, [fx])
        assert ctx.fixtures["data_c19"].bytes_ == b"payload"

    def test_idempotent_merge_preserves_existing(self) -> None:
        class Ctx:
            pass

        ctx = Ctx()
        prov = FixtureProvenance(source="s", fetched_at="t", sha256="x")
        ctx.fixtures = {"existing": Fixture(key="existing", bytes_=b"o", provenance=prov)}  # type: ignore[attr-defined]
        new_fx = Fixture(key="data_c19", bytes_=b"payload", provenance=prov)
        apply_to_context(ctx, [new_fx])
        assert set(ctx.fixtures) == {"existing", "data_c19"}  # type: ignore[attr-defined]
