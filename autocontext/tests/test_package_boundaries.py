from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES_DIR = REPO_ROOT / "packages"
FORBIDDEN_SPLIT_PATHS = [
    PACKAGES_DIR / "package-topology.json",
    PACKAGES_DIR / "package-boundaries.json",
    PACKAGES_DIR / "python" / "core",
    PACKAGES_DIR / "python" / "control",
    PACKAGES_DIR / "ts" / "core",
    PACKAGES_DIR / "ts" / "control-plane",
]
FORBIDDEN_LICENSE_METADATA = [
    REPO_ROOT / "LICENSING.md",
    PACKAGES_DIR / "python" / "core" / "LICENSE",
    PACKAGES_DIR / "python" / "control" / "LICENSE",
    PACKAGES_DIR / "ts" / "core" / "LICENSE",
    PACKAGES_DIR / "ts" / "control-plane" / "LICENSE",
]


def test_split_package_scaffolding_is_not_present() -> None:
    for path in FORBIDDEN_SPLIT_PATHS:
        assert not path.exists(), path


def test_no_dual_license_metadata_was_added_for_deferred_split() -> None:
    assert (REPO_ROOT / "LICENSE").exists()
    for path in FORBIDDEN_LICENSE_METADATA:
        assert not path.exists(), path


def test_rights_audit_is_historical_context_only() -> None:
    doc = (REPO_ROOT / "docs" / "contributor-rights-audit.md").read_text(encoding="utf-8")

    assert "historical snapshot" in doc
    assert "existing public repo code remains Apache-2.0" in doc
    assert "packages/package-boundaries.json" not in doc


def test_knowledge_trace_boundary_map_is_deferred_not_a_manifest_plan() -> None:
    doc = (REPO_ROOT / "docs" / "knowledge-production-trace-boundary-map.md").read_text(encoding="utf-8")

    assert "Status: **deferred**" in doc
    assert "future package hygiene" in doc
    assert "package-boundaries.json" not in doc
    assert "package-topology.json" not in doc
