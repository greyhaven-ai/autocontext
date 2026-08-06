"""Atomic-write behavior for the shared artifact write path (AC-903)."""

from __future__ import annotations

from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.buffered_writer import BufferedWriter


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def _no_tmp_files(root: Path) -> bool:
    return not [p for p in root.rglob("*.tmp")]


class TestArtifactWriteAtomicity:
    def test_write_json_leaves_no_temp(self, tmp_path) -> None:
        store = _store(tmp_path)
        target = tmp_path / "knowledge" / "scenario" / "state.json"
        store.write_json(target, {"a": 1})
        assert target.exists()
        assert _no_tmp_files(tmp_path)

    def test_write_markdown_leaves_no_temp(self, tmp_path) -> None:
        store = _store(tmp_path)
        target = tmp_path / "knowledge" / "scenario" / "notes.md"
        store.write_markdown(target, "# hi")
        assert target.read_text(encoding="utf-8") == "# hi\n"
        assert _no_tmp_files(tmp_path)

    def test_write_json_goes_through_atomic_replace(self, tmp_path, monkeypatch) -> None:
        replaced: list[str] = []
        import os as os_module

        real_replace = os_module.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            replaced.append(str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr("autocontext.util.json_io.os.replace", spy)
        store = _store(tmp_path)
        target = tmp_path / "knowledge" / "scenario" / "state.json"
        store.write_json(target, {"a": 1})
        assert str(target) in replaced


class TestBufferedWriterAtomicity:
    def test_write_mode_leaves_no_temp(self, tmp_path) -> None:
        writer = BufferedWriter()
        target = tmp_path / "out.txt"
        writer.write_text(target, "content")
        assert target.read_text(encoding="utf-8") == "content"
        assert _no_tmp_files(tmp_path)

    def test_append_mode_still_appends(self, tmp_path) -> None:
        writer = BufferedWriter()
        target = tmp_path / "log.txt"
        writer.write_text(target, "one\n")
        writer.append_text(target, "two\n")
        assert target.read_text(encoding="utf-8") == "one\ntwo\n"
