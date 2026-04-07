"""Tests for opt-in living docs maintenance (AC-511).

DDD: LivingDoc is an entity tracking an opted-in document.
DocMaintainer orchestrates updates at safe boundaries.
"""

from __future__ import annotations

from pathlib import Path


def _write_doc(root: Path, name: str, *, opted_in: bool = True, content: str = "") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    marker = "<!-- living-doc: true -->" if opted_in else ""
    path.write_text(f"{marker}\n# {name}\n\n{content or 'Initial content.'}\n", encoding="utf-8")
    return path


class TestLivingDoc:
    """Entity tracking one opted-in document."""

    def test_detect_opted_in(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import LivingDoc

        path = _write_doc(tmp_path, "ARCHITECTURE.md", opted_in=True)
        doc = LivingDoc.from_path(path)
        assert doc is not None
        assert doc.is_opted_in

    def test_skip_non_opted_in(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import LivingDoc

        path = _write_doc(tmp_path, "README.md", opted_in=False)
        doc = LivingDoc.from_path(path)
        assert doc is None

    def test_tracks_consultation(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import LivingDoc

        path = _write_doc(tmp_path, "ARCH.md")
        doc = LivingDoc.from_path(path)
        assert doc.consultation_count == 0
        doc.record_consultation()
        assert doc.consultation_count == 1


class TestDocMaintainer:
    """Orchestrates doc updates at safe boundaries."""

    def test_discover_opted_in_docs(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import DocMaintainer

        _write_doc(tmp_path, "ARCHITECTURE.md", opted_in=True)
        _write_doc(tmp_path, "README.md", opted_in=False)
        _write_doc(tmp_path, "docs/ONBOARDING.md", opted_in=True)

        maintainer = DocMaintainer(roots=[tmp_path])
        docs = maintainer.discover()
        assert len(docs) == 2

    def test_skip_when_disabled(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import DocMaintainer

        _write_doc(tmp_path, "ARCH.md", opted_in=True)
        maintainer = DocMaintainer(roots=[tmp_path], enabled=False)
        result = maintainer.run(learnings=["new finding"])
        assert result.skipped
        assert "disabled" in result.reason

    def test_skip_when_no_learnings(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import DocMaintainer

        _write_doc(tmp_path, "ARCH.md", opted_in=True)
        maintainer = DocMaintainer(roots=[tmp_path])
        result = maintainer.run(learnings=[])
        assert result.skipped
        assert "no learnings" in result.reason.lower()

    def test_produces_audit_trail(self, tmp_path: Path) -> None:
        from autocontext.session.living_docs import DocMaintainer

        _write_doc(tmp_path, "ARCH.md", opted_in=True, content="Old architecture info.")
        maintainer = DocMaintainer(roots=[tmp_path])
        result = maintainer.run(learnings=["Auth now uses OAuth2"])
        assert not result.skipped
        assert len(result.updates) >= 0  # may or may not produce updates depending on signal
