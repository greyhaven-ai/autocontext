"""AC-518: Blob store abstraction tests.

Tests the BlobStore ABC, BlobRef model, LocalBlobStore (content-addressed
filesystem backend), HfBucketStore (mocked), BlobRegistry, and factory.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# BlobRef model
# ---------------------------------------------------------------------------


class TestBlobRef:
    def test_create_and_serialize(self) -> None:
        from autocontext.blobstore.ref import BlobRef

        ref = BlobRef(
            kind="trace",
            local_path="/tmp/run_001/events.ndjson",
            remote_uri="hf://org/repo/blobs/abc123",
            digest="sha256:abc123def456",
            size_bytes=4096,
            content_type="application/x-ndjson",
        )
        d = ref.to_dict()
        assert d["kind"] == "trace"
        assert d["digest"] == "sha256:abc123def456"
        assert d["size_bytes"] == 4096

    def test_roundtrip(self) -> None:
        from autocontext.blobstore.ref import BlobRef

        ref = BlobRef(kind="checkpoint", local_path="/tmp/ckpt.bin", digest="sha256:aaa", size_bytes=100)
        restored = BlobRef.from_dict(ref.to_dict())
        assert restored.kind == ref.kind
        assert restored.digest == ref.digest
        assert restored.size_bytes == ref.size_bytes

    def test_is_hydrated(self) -> None:
        from autocontext.blobstore.ref import BlobRef

        ref = BlobRef(kind="trace", local_path="/tmp/exists.json", digest="sha256:x", size_bytes=10)
        # is_hydrated checks if local_path exists — use tmpfile for positive
        with tempfile.NamedTemporaryFile() as f:
            ref_with_file = BlobRef(kind="trace", local_path=f.name, digest="sha256:x", size_bytes=10)
            assert ref_with_file.is_hydrated

    def test_not_hydrated_when_no_local_path(self) -> None:
        from autocontext.blobstore.ref import BlobRef

        ref = BlobRef(kind="trace", remote_uri="hf://org/repo/blobs/x", digest="sha256:x", size_bytes=10)
        assert not ref.is_hydrated


# ---------------------------------------------------------------------------
# LocalBlobStore
# ---------------------------------------------------------------------------


class TestLocalBlobStore:
    def test_put_and_get(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            key = "runs/run_001/events.ndjson"
            data = b'{"event":"start"}\n'
            store.put(key, data)

            retrieved = store.get(key)
            assert retrieved == data

    def test_put_returns_digest(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            data = b"hello world"
            digest = store.put("test/hello.txt", data)
            expected = "sha256:" + hashlib.sha256(data).hexdigest()
            assert digest == expected

    def test_get_returns_none_for_missing(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            assert store.get("nonexistent/key") is None

    def test_head(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            store.put("test/file.txt", b"content")
            meta = store.head("test/file.txt")
            assert meta is not None
            assert meta["size_bytes"] == 7
            assert meta["digest"].startswith("sha256:")

    def test_head_returns_none_for_missing(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            assert store.head("missing") is None

    def test_list_prefix(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            store.put("runs/r1/a.txt", b"a")
            store.put("runs/r1/b.txt", b"b")
            store.put("runs/r2/c.txt", b"c")
            keys = store.list_prefix("runs/r1/")
            assert sorted(keys) == ["runs/r1/a.txt", "runs/r1/b.txt"]

    def test_delete(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            store.put("test/del.txt", b"delete me")
            assert store.get("test/del.txt") is not None
            store.delete("test/del.txt")
            assert store.get("test/del.txt") is None

    def test_put_file_and_get_file(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            src = Path(tmp) / "source.bin"
            src.write_bytes(b"binary content here")
            store.put_file("test/binary.bin", src)

            dest = Path(tmp) / "dest.bin"
            store.get_file("test/binary.bin", dest)
            assert dest.read_bytes() == b"binary content here"

    def test_content_addressed_dedup(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            data = b"same content"
            d1 = store.put("key1", data)
            d2 = store.put("key2", data)
            assert d1 == d2  # same content = same digest


# ---------------------------------------------------------------------------
# HfBucketStore (mocked)
# ---------------------------------------------------------------------------


class TestHfBucketStore:
    def test_put_calls_hf_upload(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp))
            with patch.object(store, "_run_hf_command") as mock_hf:
                mock_hf.return_value = ""
                store.put("test/file.txt", b"hello")
                mock_hf.assert_called_once()

    def test_get_calls_hf_download(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp))
            # Pre-populate cache so get doesn't need real download
            cache_path = Path(tmp) / "test" / "file.txt"
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(b"cached content")
            result = store.get("test/file.txt")
            assert result == b"cached content"

    def test_list_prefix_returns_empty_on_error(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp))
            with patch.object(store, "_run_hf_command", side_effect=RuntimeError("no auth")):
                keys = store.list_prefix("runs/")
                assert keys == []


# ---------------------------------------------------------------------------
# BlobRegistry
# ---------------------------------------------------------------------------


class TestBlobRegistry:
    def test_register_and_lookup(self) -> None:
        from autocontext.blobstore.ref import BlobRef
        from autocontext.blobstore.registry import BlobRegistry

        registry = BlobRegistry()
        ref = BlobRef(kind="trace", local_path="/tmp/events.ndjson", digest="sha256:abc", size_bytes=100)
        registry.register("run_001", "events.ndjson", ref)
        found = registry.lookup("run_001", "events.ndjson")
        assert found is not None
        assert found.digest == "sha256:abc"

    def test_lookup_missing_returns_none(self) -> None:
        from autocontext.blobstore.registry import BlobRegistry

        registry = BlobRegistry()
        assert registry.lookup("run_001", "missing") is None

    def test_list_for_run(self) -> None:
        from autocontext.blobstore.ref import BlobRef
        from autocontext.blobstore.registry import BlobRegistry

        registry = BlobRegistry()
        registry.register("run_001", "a.txt", BlobRef(kind="trace", digest="sha256:a", size_bytes=10))
        registry.register("run_001", "b.txt", BlobRef(kind="report", digest="sha256:b", size_bytes=20))
        registry.register("run_002", "c.txt", BlobRef(kind="trace", digest="sha256:c", size_bytes=30))
        refs = registry.list_for_run("run_001")
        assert len(refs) == 2

    def test_save_and_load(self) -> None:
        from autocontext.blobstore.ref import BlobRef
        from autocontext.blobstore.registry import BlobRegistry

        with tempfile.TemporaryDirectory() as tmp:
            registry = BlobRegistry()
            registry.register("r1", "f.txt", BlobRef(kind="trace", digest="sha256:x", size_bytes=50))
            path = Path(tmp) / "registry.json"
            registry.save(path)

            loaded = BlobRegistry.load(path)
            assert loaded.lookup("r1", "f.txt") is not None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestFactory:
    def test_creates_local_backend(self) -> None:
        from autocontext.blobstore.factory import create_blob_store
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = create_blob_store(backend="local", root=tmp)
            assert isinstance(store, LocalBlobStore)

    def test_creates_hf_backend(self) -> None:
        from autocontext.blobstore.factory import create_blob_store
        from autocontext.blobstore.hf_bucket import HfBucketStore

        with tempfile.TemporaryDirectory() as tmp:
            store = create_blob_store(backend="hf_bucket", repo_id="org/repo", cache_dir=tmp)
            assert isinstance(store, HfBucketStore)

    def test_raises_for_unknown_backend(self) -> None:
        from autocontext.blobstore.factory import create_blob_store

        with pytest.raises(ValueError, match="Unknown"):
            create_blob_store(backend="s3")
