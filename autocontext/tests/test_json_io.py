"""Tests for atomic JSON writes and guarded reads (AC-903)."""

from __future__ import annotations

import json

import pytest

from autocontext.util.json_io import read_json, read_json_guarded, write_json, write_text_atomic


class TestWriteTextAtomic:
    def test_writes_content_and_leaves_no_temp(self, tmp_path) -> None:
        target = tmp_path / "state.json"
        write_text_atomic(target, "hello")
        assert target.read_text(encoding="utf-8") == "hello"
        assert [p.name for p in tmp_path.iterdir()] == ["state.json"]

    def test_replaces_existing_content(self, tmp_path) -> None:
        target = tmp_path / "state.json"
        target.write_text("old", encoding="utf-8")
        write_text_atomic(target, "new")
        assert target.read_text(encoding="utf-8") == "new"

    def test_uses_replace_not_direct_write(self, tmp_path, monkeypatch) -> None:
        import os as os_module

        replaced: list[tuple[str, str]] = []
        real_replace = os_module.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            replaced.append((str(src), str(dst)))
            return real_replace(src, dst)

        monkeypatch.setattr("autocontext.util.json_io.os.replace", spy)
        target = tmp_path / "state.json"
        write_text_atomic(target, "content")
        assert len(replaced) == 1
        src, dst = replaced[0]
        assert src.endswith(".tmp") and dst == str(target)

    def test_cleans_temp_on_failure(self, tmp_path, monkeypatch) -> None:
        target = tmp_path / "state.json"

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise OSError("disk full")

        monkeypatch.setattr("autocontext.util.json_io.os.replace", boom)
        with pytest.raises(OSError):
            write_text_atomic(target, "content")
        assert list(tmp_path.iterdir()) == []


class TestWriteJson:
    def test_round_trips_and_leaves_no_temp(self, tmp_path) -> None:
        target = tmp_path / "data.json"
        write_json(target, {"b": 2, "a": 1})
        assert read_json(target) == {"a": 1, "b": 2}
        assert [p.name for p in tmp_path.iterdir()] == ["data.json"]

    def test_creates_parent_dirs(self, tmp_path) -> None:
        target = tmp_path / "nested" / "deep" / "data.json"
        write_json(target, [1, 2])
        assert read_json(target) == [1, 2]


class TestReadJsonGuarded:
    def test_missing_file_returns_default(self, tmp_path) -> None:
        assert read_json_guarded(tmp_path / "absent.json") is None
        assert read_json_guarded(tmp_path / "absent.json", default={}) == {}

    def test_corrupt_json_returns_default(self, tmp_path) -> None:
        target = tmp_path / "bad.json"
        target.write_text("{not json", encoding="utf-8")
        assert read_json_guarded(target, default=[]) == []

    def test_directory_path_returns_default(self, tmp_path) -> None:
        assert read_json_guarded(tmp_path, default={"d": 1}) == {"d": 1}

    def test_valid_json_is_returned(self, tmp_path) -> None:
        target = tmp_path / "good.json"
        target.write_text(json.dumps({"k": "v"}), encoding="utf-8")
        assert read_json_guarded(target) == {"k": "v"}

    def test_read_json_still_raises(self, tmp_path) -> None:
        with pytest.raises(FileNotFoundError):
            read_json(tmp_path / "absent.json")
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            read_json(bad)
