from __future__ import annotations

import importlib.util
import json
import re
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
        python_version="1.2.3",
        npm_version="4.5.6",
        whats_new_version="7.8.9",
        pi_version="0.8.0",
        pi_autoctx_dependency="^0.8.0",
        whats_new=("**One** thing.", "**Two** thing."),
    )

    assert sync.render_whats_new_asset(manifest) == "**One** thing.\n**Two** thing.\n"
    assert sync.render_whats_new_heading(manifest) == "What's New in 7.8.9"


def test_core_package_versions_can_advance_independently() -> None:
    sync = _load_sync_module()
    manifest = sync.ReleaseManifest(
        python_version="1.2.3",
        npm_version="4.5.6",
        whats_new_version="7.8.9",
        pi_version="0.8.0",
        pi_autoctx_dependency="^4.0.0",
        whats_new=("**One** thing.",),
    )

    root = sync.sync_root_readme((REPO_ROOT / "README.md").read_text(encoding="utf-8"), manifest)

    assert "autocontext==1.2.3" in root
    assert "autoctx@4.5.6" in root
    assert "What's New in 7.8.9" in root


def test_pi_version_syncs_install_snippets() -> None:
    sync = _load_sync_module()
    manifest = sync.ReleaseManifest(
        python_version="0.14.0",
        npm_version="0.14.0",
        whats_new_version="0.14.0",
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
        python_version="9.9.9",
        npm_version="8.8.8",
        whats_new_version="9.9.9",
        pi_version="0.8.0",
        pi_autoctx_dependency="^0.9.0",
        whats_new=("**One** thing.",),
    )

    issues = sync.check_release_surfaces(manifest)

    # Read the shipped version independently of the implementation rather than
    # hardcoding it: the point is that the message names the REAL version, and a
    # literal here silently becomes a chore that fails on every release bump
    # (it did, on 0.15.0). A different reader keeps the assertion honest -- using
    # sync's own would make it tautological.
    shipped = re.search(
        r'__version__ = "([^"]+)"',
        (Path(__file__).resolve().parents[1] / "src" / "autocontext" / "__init__.py").read_text(encoding="utf-8"),
    )
    assert shipped is not None
    assert (
        "autocontext/src/autocontext/__init__.py version "
        f"{shipped.group(1)} != manifest python_version 9.9.9"
    ) in issues
    npm_package = json.loads((REPO_ROOT / "ts" / "package.json").read_text(encoding="utf-8"))
    assert (
        f"ts/package.json version {npm_package['version']} != manifest npm_version 8.8.8"
    ) in issues
    # Read from the file for the same reason as the version above: hardcoding
    # the shipped pi values turned every pi bump into a test edit, which is
    # exactly what happened bumping the autoctx pin to ^0.15.0.
    pi_package = json.loads((Path(__file__).resolve().parents[2] / "pi" / "package.json").read_text(encoding="utf-8"))
    assert f"pi/package.json version {pi_package['version']} != manifest 0.8.0" in issues
    assert (
        f"pi/package.json autoctx dependency {pi_package['dependencies']['autoctx']} != manifest ^0.9.0" in issues
    )
