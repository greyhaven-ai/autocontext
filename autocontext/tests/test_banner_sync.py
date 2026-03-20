from __future__ import annotations

from pathlib import Path

from autocontext.banner import (
    SYNC_BLOCK_END,
    SYNC_BLOCK_START,
    render_dashboard_banner_block,
    render_readme_banner_block,
)


def _extract_synced_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(SYNC_BLOCK_START)
    end = text.index(SYNC_BLOCK_END) + len(SYNC_BLOCK_END)
    return text[start:end]


def test_root_readme_banner_stays_synced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    assert _extract_synced_block(repo_root / "README.md") == render_readme_banner_block()


def test_dashboard_banner_stays_synced() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    dashboard = repo_root / "autocontext" / "dashboard" / "index.html"
    assert _extract_synced_block(dashboard) == render_dashboard_banner_block()
