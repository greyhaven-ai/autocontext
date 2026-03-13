# tests/test_settings_library.py
from autocontext.config.settings import AppSettings, load_settings


def test_library_defaults():
    s = AppSettings()
    assert s.library_root == "knowledge/_library"
    assert s.library_books == []
    assert s.librarian_enabled is True
    assert s.model_librarian == "claude-sonnet-4-5-20250929"
    assert s.librarian_provider == ""
    assert s.library_max_consults_per_role == 3
    assert s.model_archivist == "claude-opus-4-6"
    assert s.archivist_provider == ""
    assert s.ingestion_model == "claude-opus-4-6"


def test_library_books_from_env(monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_BOOKS", "clean-arch,ddd")
    s = load_settings()
    assert s.library_books == ["clean-arch", "ddd"]


def test_library_books_empty_string(monkeypatch):
    monkeypatch.setenv("AUTOCONTEXT_LIBRARY_BOOKS", "")
    s = load_settings()
    assert s.library_books == []
