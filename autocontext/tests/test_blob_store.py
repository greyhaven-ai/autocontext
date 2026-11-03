"""AC-518: Blob store abstraction tests.

Tests the BlobStore ABC, BlobRef model, LocalBlobStore (content-addressed
filesystem backend), HfBucketStore (mocked), BlobRegistry, and factory.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
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

    def test_append_extends_existing_key(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp))
            store.put("runs/run_001/compactions.jsonl", b"first\n")
            digest = store.append("runs/run_001/compactions.jsonl", b"second\n")

            expected = b"first\nsecond\n"
            assert store.get("runs/run_001/compactions.jsonl") == expected
            assert digest == "sha256:" + hashlib.sha256(expected).hexdigest()

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

    def test_put_rejects_directory_escape(self) -> None:
        from autocontext.blobstore.local import LocalBlobStore

        with tempfile.TemporaryDirectory() as tmp:
            store = LocalBlobStore(root=Path(tmp) / "root")
            with pytest.raises(ValueError, match="invalid blob key"):
                store.put("../escape.txt", b"x")


# ---------------------------------------------------------------------------
# HfBucketStore (mocked)
# ---------------------------------------------------------------------------


class TestHfBucketStore:
    @staticmethod
    def _fake_hf(remote: dict[str, bytes]):
        from autocontext.blobstore.hf_bucket import _HfEntryNotFoundError

        def runner(cmd: list[str]) -> str:
            if cmd[1] == "upload":
                local_path = Path(cmd[3])
                key = cmd[4]
                remote[key] = local_path.read_bytes()
                return ""
            if cmd[1] == "download":
                key = cmd[3]
                local_dir = Path(cmd[cmd.index("--local-dir") + 1])
                if key not in remote:
                    raise _HfEntryNotFoundError("missing remote key")
                # The real CLI preserves the repository directory structure.
                local_path = local_dir / key
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(remote[key])
                return ""
            if cmd[1:3] == ["repo-files", "delete"]:
                key = cmd[4]
                remote.pop(key, None)
                return ""
            raise AssertionError(f"unexpected command: {cmd}")

        return runner

    def test_put_calls_hf_upload(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        with tempfile.TemporaryDirectory() as tmp:
            store = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp))
            with patch.object(store, "_run_hf_command") as mock_hf:
                mock_hf.return_value = ""
                store.put("test/file.txt", b"hello")
                assert mock_hf.call_count >= 2

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

    def test_head_uses_remote_index_for_fresh_instance(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        with tempfile.TemporaryDirectory() as tmp:
            first = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp) / "cache1")
            second = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp) / "cache2")
            runner = self._fake_hf(remote)
            with patch.object(first, "_run_hf_command", side_effect=runner):
                first.put("nested/remote.txt", b"remote data")
            with patch.object(second, "_run_hf_command", side_effect=runner):
                meta = second.head("nested/remote.txt")
                assert meta is not None
                assert meta["size_bytes"] == len(b"remote data")
                assert second.list_prefix("nested/") == ["nested/remote.txt"]

    def test_delete_uses_remote_index_for_uncached_key(self) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        with tempfile.TemporaryDirectory() as tmp:
            first = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp) / "cache1")
            second = HfBucketStore(repo_id="org/repo", cache_dir=Path(tmp) / "cache2")
            runner = self._fake_hf(remote)
            with patch.object(first, "_run_hf_command", side_effect=runner):
                first.put("nested/remote.txt", b"remote data")
            with patch.object(second, "_run_hf_command", side_effect=runner):
                assert second.delete("nested/remote.txt") is True
                assert second.head("nested/remote.txt") is None
                assert second.list_prefix("nested/") == []

    def test_get_downloads_nested_key_into_cache_root(self, tmp_path: Path) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote = {"nested/remote.txt": b"remote data"}
        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        with patch.object(store, "_run_hf_command", side_effect=self._fake_hf(remote)):
            assert store.get("nested/remote.txt") == b"remote data"
        assert (tmp_path / "nested/remote.txt").read_bytes() == b"remote data"

    @pytest.mark.parametrize("failed_key", [".autocontext/blob_index.json", "runs/run_001/compactions.jsonl"])
    @pytest.mark.parametrize("cached", [False, True])
    def test_append_does_not_replace_existing_blob_when_remote_read_fails(
        self, tmp_path: Path, failed_key: str, cached: bool
    ) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        key = "runs/run_001/compactions.jsonl"
        writer = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path / "writer")
        reader = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path / "reader")
        runner = self._fake_hf(remote)
        with patch.object(writer, "_run_hf_command", side_effect=runner):
            writer.put(key, b"existing entry\n")
        if cached:
            with patch.object(reader, "_run_hf_command", side_effect=runner):
                assert reader.get(key) == b"existing entry\n"
        before = dict(remote)

        def fail_download(cmd: list[str]) -> str:
            if cmd[1] == "download" and cmd[3] == failed_key:
                raise RuntimeError("temporary remote read failure")
            return runner(cmd)

        with patch.object(reader, "_run_hf_command", side_effect=fail_download) as command:
            with pytest.raises(RuntimeError, match="temporary remote read failure"):
                reader.append(key, b"new entry\n")
            assert all(call.args[0][1] != "upload" for call in command.call_args_list)
        assert remote == before

    @pytest.mark.parametrize("seed_index", [False, True])
    def test_append_creates_missing_key_in_new_or_initialized_repository(
        self, tmp_path: Path, seed_index: bool
    ) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        with patch.object(store, "_run_hf_command", side_effect=self._fake_hf(remote)):
            if seed_index:
                store.put("runs/seed.jsonl", b"seed\n")
            digest = store.append("runs/new.jsonl", b"new entry\n")
            store.append("runs/new.jsonl", b"second entry\n")
        assert remote["runs/new.jsonl"] == b"new entry\nsecond entry\n"
        assert digest == "sha256:" + hashlib.sha256(b"new entry\n").hexdigest()
        index = json.loads(remote[".autocontext/blob_index.json"])
        if seed_index:
            assert "runs/seed.jsonl" in index
        assert index["runs/new.jsonl"]["size_bytes"] == len(remote["runs/new.jsonl"])

    def test_append_refreshes_stale_cache_and_preserves_other_index_entries(self, tmp_path: Path) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        first = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path / "first")
        second = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path / "second")
        runner = self._fake_hf(remote)
        with patch.object(first, "_run_hf_command", side_effect=runner):
            first.put("nested/ledger.jsonl", b"first\n")
        with patch.object(second, "_run_hf_command", side_effect=runner):
            second.append("nested/ledger.jsonl", b"second\n")
            second.put("other.txt", b"other data")
        with patch.object(first, "_run_hf_command", side_effect=runner):
            first.append("nested/ledger.jsonl", b"third\n")
        assert remote["nested/ledger.jsonl"] == b"first\nsecond\nthird\n"
        assert "other.txt" in json.loads(remote[".autocontext/blob_index.json"])

    @pytest.mark.parametrize("index_present", [False, True])
    def test_append_preserves_unindexed_blob(self, tmp_path: Path, index_present: bool) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote = {"nested/ledger.jsonl": b"original\n"}
        if index_present:
            remote[".autocontext/blob_index.json"] = b"{}"
        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        with patch.object(store, "_run_hf_command", side_effect=self._fake_hf(remote)):
            store.append("nested/ledger.jsonl", b"new\n")
        assert remote["nested/ledger.jsonl"] == b"original\nnew\n"

    @pytest.mark.parametrize("corruption", ["invalid_json", "invalid_index", "missing_blob", "wrong_digest"])
    def test_append_rejects_corrupt_remote_state_without_upload(self, tmp_path: Path, corruption: str) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        runner = self._fake_hf(remote)
        with patch.object(store, "_run_hf_command", side_effect=runner):
            store.put("nested/ledger.jsonl", b"original\n")
        if corruption == "invalid_json":
            remote[".autocontext/blob_index.json"] = b"{"
        elif corruption == "invalid_index":
            remote[".autocontext/blob_index.json"] = b'{"nested/ledger.jsonl": null}'
        elif corruption == "missing_blob":
            del remote["nested/ledger.jsonl"]
        else:
            remote["nested/ledger.jsonl"] = b"truncated"
        before = dict(remote)
        with patch.object(store, "_run_hf_command", side_effect=runner) as command:
            with pytest.raises(RuntimeError):
                store.append("nested/ledger.jsonl", b"new\n")
            assert all(call.args[0][1] != "upload" for call in command.call_args_list)
        assert remote == before

    @pytest.mark.parametrize("missing_key", [".autocontext/blob_index.json", "nested/ledger.jsonl"])
    def test_append_rejects_successful_download_without_file(self, tmp_path: Path, missing_key: str) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore

        remote: dict[str, bytes] = {}
        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        runner = self._fake_hf(remote)

        def no_output(cmd: list[str]) -> str:
            if cmd[1] == "download" and cmd[3] == missing_key:
                return ""
            return runner(cmd)

        with patch.object(store, "_run_hf_command", side_effect=no_output) as command:
            with pytest.raises(RuntimeError, match="without producing"):
                store.append("nested/ledger.jsonl", b"new\n")
            assert all(call.args[0][1] != "upload" for call in command.call_args_list)
        assert remote == {}

    @pytest.mark.parametrize(
        ("stderr", "missing"),
        [
            ("huggingface_hub.errors.EntryNotFoundError: 404 Client Error", True),
            ("huggingface_hub.utils._errors.EntryNotFoundError: 404 Client Error", True),
            ("huggingface_hub.errors.RemoteEntryNotFoundError: 404 Client Error", True),
            ("huggingface_hub.errors.RepositoryNotFoundError: 404 Client Error", False),
            ("huggingface_hub.errors.GatedRepoError: 403 Client Error", False),
            ("HTTP 404 Not Found", False),
            ("huggingface_hub.errors.EntryNotFoundError: missing\nRuntimeError: later failure", False),
            ("huggingface_hub.errors.EntryNotFoundError: missing\nSystemExit: later failure", False),
        ],
    )
    def test_download_only_classifies_explicit_missing_entry(self, tmp_path: Path, stderr: str, missing: bool) -> None:
        from autocontext.blobstore.hf_bucket import HfBucketStore, _HfEntryNotFoundError

        store = HfBucketStore(repo_id="org/repo", cache_dir=tmp_path)
        result = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)
        with patch("autocontext.blobstore.hf_bucket.subprocess.run", return_value=result):
            with pytest.raises(RuntimeError) as raised:
                store._run_hf_command(["huggingface-cli", "download", "org/repo", "file.txt"])
        assert isinstance(raised.value, _HfEntryNotFoundError) is missing


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
