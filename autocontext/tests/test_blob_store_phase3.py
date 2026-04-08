"""AC-518 Phase 3: ArtifactStore integration + CLI commands.

Tests transparent blob mirroring on ArtifactStore writes and
the autoctx blob CLI subcommands.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# ArtifactStore blob integration
# ---------------------------------------------------------------------------


class TestArtifactStoreBlobIntegration:
    """When blob_store_enabled, ArtifactStore writes mirror to blob store."""

    def test_write_json_mirrors_when_enabled(self) -> None:
        from autocontext.storage.blob_integration import BlobAwareWriter

        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            blob_store = LocalBlobStore(root=Path(tmp) / "blobs")
            writer = BlobAwareWriter(blob_store=blob_store, min_size_bytes=0)

            data = {"score": 0.85, "reasoning": "Good strategy"}
            content = json.dumps(data, indent=2).encode("utf-8")
            ref = writer.mirror_write(
                key="runs/run_001/gen_1/metrics.json",
                data=content,
                kind="trace",
            )
            assert ref is not None
            assert ref.digest.startswith("sha256:")
            assert blob_store.get("runs/run_001/gen_1/metrics.json") == content

    def test_write_skips_small_artifacts(self) -> None:
        from autocontext.storage.blob_integration import BlobAwareWriter

        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            blob_store = LocalBlobStore(root=Path(tmp) / "blobs")
            writer = BlobAwareWriter(blob_store=blob_store, min_size_bytes=1000)

            ref = writer.mirror_write(key="small.txt", data=b"tiny", kind="report")
            assert ref is None

    def test_writer_disabled_when_no_store(self) -> None:
        from autocontext.storage.blob_integration import BlobAwareWriter

        writer = BlobAwareWriter(blob_store=None, min_size_bytes=0)
        ref = writer.mirror_write(key="test.txt", data=b"data", kind="trace")
        assert ref is None

    def test_classify_artifact_kind(self) -> None:
        from autocontext.storage.blob_integration import classify_artifact_kind

        assert classify_artifact_kind(Path("runs/r1/gen_1/metrics.json")) == "trace"
        assert classify_artifact_kind(Path("runs/r1/gen_1/replays/grid_ctf_1.json")) == "trace"
        assert classify_artifact_kind(Path("knowledge/grid_ctf/playbook.md")) == "report"
        assert classify_artifact_kind(Path("knowledge/grid_ctf/tools/validator.py")) == "tool"
        assert classify_artifact_kind(Path("runs/r1/gen_1/analysis/gen_1.md")) == "report"
        assert classify_artifact_kind(Path("other/file.bin")) == "artifact"


# ---------------------------------------------------------------------------
# CLI blob commands
# ---------------------------------------------------------------------------


class TestBlobCli:
    """Tests for autoctx blob sync/status/hydrate commands."""

    def test_sync_command_syncs_run(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore
        from autocontext.blobstore.sync import SyncManager

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"e":"start"}\n', encoding="utf-8")

            store = LocalBlobStore(root=root / "blobs")
            mgr = SyncManager(store=store, runs_root=root / "runs")
            result = mgr.sync_run("run_001")
            assert result.synced_count >= 1

            # Status should show the synced run
            status = mgr.status()
            assert status["run_count"] >= 1
            assert "run_001" in status["synced_runs"]

    def test_hydrate_retrieves_from_store(self) -> None:
        from autocontext.blobstore.cache import HydrationCache
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = LocalBlobStore(root=root / "blobs")
            cache = HydrationCache(root=root / "cache", max_mb=100)

            # Put something in the store
            data = b"important artifact content"
            digest = store.put("runs/r1/events.ndjson", data)

            # Hydrate it into cache
            cached = store.get("runs/r1/events.ndjson")
            assert cached == data
            cache.put("runs/r1/events.ndjson", cached, digest)

            # Verify cache has it
            result = cache.get("runs/r1/events.ndjson", expected_digest=digest)
            assert result == data

    def test_status_reports_backend_info(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore
        from autocontext.blobstore.sync import SyncManager

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "blobs")
            mgr = SyncManager(store=store, runs_root=Path(tmp) / "runs")
            status = mgr.status()
            assert "total_blobs" in status
            assert "total_bytes" in status
            assert "run_count" in status


# ---------------------------------------------------------------------------
# TS parity — blob store module exists
# ---------------------------------------------------------------------------


class TestTsParity:
    """TS blobstore package should mirror Python's structure."""

    def test_ts_blobstore_modules_exist(self) -> None:
        ts_root = Path(__file__).resolve().parents[2] / "ts" / "src" / "blobstore"
        expected = ["index.ts", "store.ts", "ref.ts", "local.ts", "registry.ts", "factory.ts", "cache.ts", "mirror.ts", "sync.ts"]
        for name in expected:
            assert (ts_root / name).exists(), f"Missing TS module: blobstore/{name}"
