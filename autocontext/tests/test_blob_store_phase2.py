"""AC-518 Phase 2: Blob store integration — settings, cache, mirror, CLI.

Tests hydration cache, artifact store mirror mixin, and settings fields.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from autocontext.blobstore.cache import HydrationCache
from autocontext.blobstore.local import LocalBlobStore
from autocontext.blobstore.mirror import BlobMirror
from autocontext.blobstore.registry import BlobRegistry
from autocontext.blobstore.sync import SyncManager
from autocontext.config.settings import AppSettings

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestBlobStoreSettings:
    def test_settings_have_blob_store_fields(self) -> None:
        settings = AppSettings()
        assert hasattr(settings, "blob_store_enabled")
        assert settings.blob_store_enabled is False
        assert hasattr(settings, "blob_store_backend")
        assert settings.blob_store_backend == "local"
        assert hasattr(settings, "blob_store_root")
        assert hasattr(settings, "blob_store_repo")
        assert hasattr(settings, "blob_store_cache_max_mb")


# ---------------------------------------------------------------------------
# HydrationCache
# ---------------------------------------------------------------------------


class TestHydrationCache:
    """Lazy hydration with digest verification and bounded eviction."""

    def test_put_and_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            data = b"cached payload"
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            cache.put("run_001/events.ndjson", data, digest)

            result = cache.get("run_001/events.ndjson")
            assert result == data

    def test_get_returns_none_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            assert cache.get("missing") is None

    def test_verify_digest_on_get(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            data = b"original"
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            cache.put("test.txt", data, digest)

            # Corrupt the cached file
            (Path(tmp) / "test.txt").write_bytes(b"corrupted")
            # Should return None because digest doesn't match
            result = cache.get("test.txt", expected_digest=digest)
            assert result is None

    def test_eviction_when_over_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # 1KB budget
            cache = HydrationCache(root=Path(tmp), max_mb=0.001)
            big = b"x" * 600
            d1 = "sha256:" + hashlib.sha256(big).hexdigest()
            cache.put("first.bin", big, d1)

            big2 = b"y" * 600
            d2 = "sha256:" + hashlib.sha256(big2).hexdigest()
            cache.put("second.bin", big2, d2)

            # After eviction, at least second should exist
            assert cache.get("second.bin") is not None

    def test_total_size(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            cache.put("a.txt", b"aaa", "sha256:a")
            cache.put("b.txt", b"bbb", "sha256:b")
            assert cache.total_size_bytes() == 6

    def test_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            cache.put("a.txt", b"data", "sha256:x")
            cache.clear()
            assert cache.get("a.txt") is None
            assert cache.total_size_bytes() == 0

    def test_put_rejects_escaping_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = HydrationCache(root=Path(tmp), max_mb=100)
            digest = "sha256:" + hashlib.sha256(b"data").hexdigest()
            escaped_name = f"escape-{Path(tmp).name}.txt"

            try:
                cache.put(f"../{escaped_name}", b"data", digest)
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError for escaping cache key")

            assert not (Path(tmp).parent / escaped_name).exists()


# ---------------------------------------------------------------------------
# BlobMirror — hooks into ArtifactStore writes
# ---------------------------------------------------------------------------


class TestBlobMirror:
    """Mirrors large artifacts from ArtifactStore to a BlobStore backend."""

    def test_mirror_write_sends_to_blob_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            mirror = BlobMirror(store=store, min_size_bytes=0)

            data = b'{"event":"gen_complete"}'
            ref = mirror.mirror_artifact(
                key="runs/run_001/events.ndjson",
                data=data,
                kind="trace",
            )
            assert ref is not None
            assert ref.kind == "trace"
            assert ref.digest.startswith("sha256:")
            assert ref.size_bytes == len(data)

            # Verify it's in the store
            retrieved = store.get("runs/run_001/events.ndjson")
            assert retrieved == data

    def test_mirror_skips_small_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            mirror = BlobMirror(store=store, min_size_bytes=1000)

            ref = mirror.mirror_artifact(
                key="small.txt",
                data=b"tiny",
                kind="report",
            )
            assert ref is None  # Too small to mirror

    def test_mirror_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            mirror = BlobMirror(store=store, min_size_bytes=0)

            src = Path(tmp) / "source.bin"
            src.write_bytes(b"file payload")
            ref = mirror.mirror_file(
                key="runs/r1/checkpoint.bin",
                path=src,
                kind="checkpoint",
            )
            assert ref is not None
            assert ref.kind == "checkpoint"
            assert store.get("runs/r1/checkpoint.bin") == b"file payload"

    def test_mirror_registers_in_registry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            registry = BlobRegistry()
            mirror = BlobMirror(store=store, min_size_bytes=0, registry=registry)

            mirror.mirror_artifact(
                key="runs/run_001/events.ndjson",
                data=b"data",
                kind="trace",
                run_id="run_001",
                artifact_name="events.ndjson",
            )
            ref = registry.lookup("run_001", "events.ndjson")
            assert ref is not None
            assert ref.kind == "trace"


# ---------------------------------------------------------------------------
# SyncManager — bulk sync local runs to blob store
# ---------------------------------------------------------------------------


class TestSyncManager:
    def test_sync_run_copies_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create a run directory
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")
            gen_dir = run_dir / "generations" / "gen_1"
            gen_dir.mkdir(parents=True)
            (gen_dir / "output.json").write_text('{"score":0.85}', encoding="utf-8")

            store = LocalBlobStore(root=root / "blobs")
            mgr = SyncManager(store=store, runs_root=root / "runs")
            result = mgr.sync_run("run_001")
            assert result.synced_count >= 2
            assert result.total_bytes > 0
            assert store.get("runs/run_001/events.ndjson") is not None

    def test_sync_run_returns_zero_for_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            mgr = SyncManager(store=store, runs_root=Path(tmp) / "runs")
            result = mgr.sync_run("nonexistent")
            assert result.synced_count == 0

    def test_sync_run_updates_changed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            artifact = run_dir / "events.ndjson"
            artifact.write_bytes(b"v1")

            store = LocalBlobStore(root=root / "blobs")
            mgr = SyncManager(store=store, runs_root=root / "runs")
            first = mgr.sync_run("run_001")
            artifact.write_bytes(b"v2")
            second = mgr.sync_run("run_001")

            assert first.synced_count == 1
            assert second.synced_count == 1
            assert second.skipped_count == 0
            assert store.get("runs/run_001/events.ndjson") == b"v2"

    def test_status_shows_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_bytes(b"data")

            store = LocalBlobStore(root=root / "blobs")
            mgr = SyncManager(store=store, runs_root=root / "runs")
            mgr.sync_run("run_001")

            status = mgr.status()
            assert status["total_blobs"] >= 1
            assert status["total_bytes"] > 0
