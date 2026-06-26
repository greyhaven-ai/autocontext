from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "sync_release_surfaces.py"


def _load_sync_module() -> Any:
    spec = importlib.util.spec_from_file_location("sync_release_surfaces", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_release_manifest_syncs_public_surfaces() -> None:
    sync = _load_sync_module()
    manifest = sync.load_release_manifest()
    issues = sync.check_release_surfaces(manifest)

    assert issues == []


def test_release_manifest_renders_whats_new_asset() -> None:
    sync = _load_sync_module()
    manifest = sync.ReleaseManifest(
        core_version="1.2.3",
        pi_version="0.8.0",
        pi_autoctx_dependency="^0.8.0",
        whats_new=("**One** thing.", "**Two** thing."),
    )

    assert sync.render_whats_new_asset(manifest) == "**One** thing.\n**Two** thing.\n"
    assert sync.render_whats_new_heading(manifest) == "What's New in 1.2.3"


def test_pi_version_syncs_install_snippets() -> None:
    sync = _load_sync_module()
    manifest = sync.ReleaseManifest(
        core_version="0.10.0",
        pi_version="0.9.0",
        pi_autoctx_dependency="^0.9.0",
        whats_new=("**One** thing.",),
    )

    root = sync.sync_root_readme((REPO_ROOT / "README.md").read_text(encoding="utf-8"), manifest)
    pi_readme = sync.sync_pi_readme((REPO_ROOT / "pi" / "README.md").read_text(encoding="utf-8"), manifest)

    assert "pi install npm:pi-autocontext@0.9.0" in root
    assert "pi install npm:pi-autocontext@0.9.0" in pi_readme
    assert '"npm:pi-autocontext@0.9.0"' in pi_readme
    assert "pi-autocontext@0.8.0" not in root
    assert "pi-autocontext@0.8.0" not in pi_readme


def test_release_manifest_checks_package_version_files() -> None:
    sync = _load_sync_module()
    manifest = sync.ReleaseManifest(
        core_version="9.9.9",
        pi_version="0.9.0",
        pi_autoctx_dependency="^0.9.0",
        whats_new=("**One** thing.",),
    )

    issues = sync.check_release_surfaces(manifest)

    assert "autocontext/src/autocontext/__init__.py version 0.10.0 != manifest 9.9.9" in issues
    assert "pi/package.json version 0.8.0 != manifest 0.9.0" in issues
    assert "pi/package.json autoctx dependency ^0.8.0 != manifest ^0.9.0" in issues
