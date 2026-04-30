from __future__ import annotations

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from autocontext.banner import (
    SYNC_BLOCK_END,
    SYNC_BLOCK_START,
    WHATS_NEW_BLOCK_END,
    WHATS_NEW_BLOCK_START,
    banner_plain,
    get_banner_svg_path,
    load_banner_art,
    load_whats_new,
    render_banner_svg,
    render_readme_banner_block,
    render_readme_whats_new_block,
)
from autocontext.cli import app


def _extract_synced_block(path: Path, start_marker: str, end_marker: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker) + len(end_marker)
    return text[start:end]


def test_root_readme_banner_stays_synced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert (
        _extract_synced_block(repo_root / "README.md", SYNC_BLOCK_START, SYNC_BLOCK_END)
        == render_readme_banner_block()
    )


def test_root_readme_whats_new_stays_synced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert (
        _extract_synced_block(
            repo_root / "README.md",
            WHATS_NEW_BLOCK_START,
            WHATS_NEW_BLOCK_END,
        )
        == render_readme_whats_new_block()
    )

def test_banner_svg_stays_synced() -> None:
    assert get_banner_svg_path().read_text(encoding="utf-8") == render_banner_svg()


def test_banner_falls_back_when_assets_are_missing(monkeypatch, tmp_path: Path) -> None:
    import autocontext.banner as banner

    monkeypatch.setattr(banner, "_assets_dir", lambda: tmp_path / "missing-assets")
    load_banner_art.cache_clear()
    load_whats_new.cache_clear()
    try:
        assert load_banner_art() == "autocontext"
        assert load_whats_new() == ()
        assert "autocontext" in banner_plain()
    finally:
        load_banner_art.cache_clear()
        load_whats_new.cache_clear()


def test_no_args_cli_does_not_traceback_when_banner_assets_are_missing(monkeypatch, tmp_path: Path) -> None:
    import autocontext.banner as banner

    monkeypatch.setattr(banner, "_assets_dir", lambda: tmp_path / "missing-assets")
    load_banner_art.cache_clear()
    load_whats_new.cache_clear()
    try:
        result = CliRunner().invoke(app, [])
    finally:
        load_banner_art.cache_clear()
        load_whats_new.cache_clear()

    assert result.exit_code == 0, result.output
    assert "Traceback" not in result.output


def test_wheel_packages_banner_assets() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    force_include = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"]
    assert force_include["assets"] == "autocontext/assets"
