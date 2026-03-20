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
render_dashboard_banner_block = _banner.render_dashboard_banner_block
render_readme_banner_block = _banner.render_readme_banner_block


def replace_block(text: str, replacement: str) -> str:
    pattern = re.compile(
        rf"{re.escape(SYNC_BLOCK_START)}.*?{re.escape(SYNC_BLOCK_END)}",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit("sync markers not found")
    return pattern.sub(replacement, text, count=1)


def write_if_changed(path: Path, replacement: str) -> None:
    updated = replace_block(path.read_text(encoding="utf-8"), replacement)
    if updated != path.read_text(encoding="utf-8"):
        path.write_text(updated, encoding="utf-8")


def main() -> int:
    write_if_changed(REPO_ROOT / "README.md", render_readme_banner_block())
    write_if_changed(
        REPO_ROOT / "autocontext" / "dashboard" / "index.html",
        render_dashboard_banner_block(),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
