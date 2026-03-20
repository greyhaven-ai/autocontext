from __future__ import annotations

from pathlib import Path

from autocontext.banner import (
    SYNC_BLOCK_END,
    SYNC_BLOCK_START,
    WHATS_NEW_BLOCK_END,
    WHATS_NEW_BLOCK_START,
    get_banner_svg_path,
    render_banner_svg,
    render_dashboard_banner_block,
    render_readme_banner_block,
    render_readme_whats_new_block,
)


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


def test_dashboard_banner_stays_synced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dashboard = repo_root / "autocontext" / "dashboard" / "index.html"
    assert (
        _extract_synced_block(dashboard, SYNC_BLOCK_START, SYNC_BLOCK_END)
        == render_dashboard_banner_block()
    )


def test_banner_svg_stays_synced() -> None:
    assert get_banner_svg_path().read_text(encoding="utf-8") == render_banner_svg()
