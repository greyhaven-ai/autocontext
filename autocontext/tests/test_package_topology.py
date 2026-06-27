from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import cast

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"
PACKAGE_README = PACKAGES_DIR / "README.md"
SPLIT_MANIFESTS = [
    PACKAGES_DIR / "package-topology.json",
    PACKAGES_DIR / "package-boundaries.json",
]
SPLIT_PACKAGE_DIRS = [
    PACKAGES_DIR / "python" / "core",
    PACKAGES_DIR / "python" / "control",
    PACKAGES_DIR / "ts" / "core",
    PACKAGES_DIR / "ts" / "control-plane",
]


def _json(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return cast(dict[str, object], data)


def test_package_split_manifests_are_deferred() -> None:
    for path in SPLIT_MANIFESTS:
        assert not path.exists(), path


def test_placeholder_split_packages_are_absent() -> None:
    for path in SPLIT_PACKAGE_DIRS:
        assert not path.exists(), path


def test_packages_directory_points_to_deferred_policy() -> None:
    readme = PACKAGE_README.read_text(encoding="utf-8")

    assert "Core/control split packages are deferred" in readme
    assert "Do not add `packages/python/*`, `packages/ts/*`" in readme
    assert "autocontext" in readme
    assert "autoctx" in readme
    assert "pi-autocontext" in readme


def test_deferred_split_doc_keeps_active_package_surfaces() -> None:
    doc = (REPO_ROOT / "docs" / "core-control-package-split.md").read_text(encoding="utf-8")

    assert "Status: **deferred**" in doc
    assert "## Agent App Build Targets" in doc
    assert "`autoctx/agent-runtime`" in doc
    assert "future packages uncreated" in doc


def test_shipping_package_names_stay_unchanged() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "autocontext" / "pyproject.toml").read_text(encoding="utf-8"))
    ts_package = _json(REPO_ROOT / "ts" / "package.json")
    pi_package = _json(REPO_ROOT / "pi" / "package.json")

    project = pyproject["project"]
    assert isinstance(project, dict)
    assert project["name"] == "autocontext"
    assert ts_package["name"] == "autoctx"
    assert pi_package["name"] == "pi-autocontext"
