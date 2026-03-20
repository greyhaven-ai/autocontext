#!/usr/bin/env python3
"""Sync the banner and What's New surfaces from canonical assets."""

from __future__ import annotations

import re
import sys
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_banner_module():
    banner_path = REPO_ROOT / "autocontext" / "src" / "autocontext" / "banner.py"
    spec = spec_from_file_location("autocontext_banner_sync", banner_path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"unable to load banner module from {banner_path}")
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_banner = load_banner_module()
SYNC_BLOCK_END = _banner.SYNC_BLOCK_END
SYNC_BLOCK_START = _banner.SYNC_BLOCK_START
WHATS_NEW_BLOCK_END = _banner.WHATS_NEW_BLOCK_END
WHATS_NEW_BLOCK_START = _banner.WHATS_NEW_BLOCK_START
get_banner_svg_path = _banner.get_banner_svg_path
render_banner_svg = _banner.render_banner_svg
render_dashboard_banner_block = _banner.render_dashboard_banner_block
render_readme_banner_block = _banner.render_readme_banner_block
render_readme_whats_new_block = _banner.render_readme_whats_new_block


def replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> str:
    pattern = re.compile(
        rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(f"sync markers not found for {start_marker}")
    return pattern.sub(replacement, text, count=1)


def write_if_changed(path: Path, updated: str) -> None:
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if updated != current:
        path.write_text(updated, encoding="utf-8")


def main() -> int:
    readme = REPO_ROOT / "README.md"
    readme_text = readme.read_text(encoding="utf-8")
    readme_text = replace_block(
        readme_text,
        SYNC_BLOCK_START,
        SYNC_BLOCK_END,
        render_readme_banner_block(),
    )
    readme_text = replace_block(
        readme_text,
        WHATS_NEW_BLOCK_START,
        WHATS_NEW_BLOCK_END,
        render_readme_whats_new_block(),
    )
    write_if_changed(readme, readme_text)

    dashboard = REPO_ROOT / "autocontext" / "dashboard" / "index.html"
    dashboard_text = replace_block(
        dashboard.read_text(encoding="utf-8"),
        SYNC_BLOCK_START,
        SYNC_BLOCK_END,
        render_dashboard_banner_block(),
    )
    write_if_changed(dashboard, dashboard_text)
    write_if_changed(get_banner_svg_path(), render_banner_svg())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
