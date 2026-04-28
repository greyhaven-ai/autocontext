from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOPOLOGY_PATH = REPO_ROOT / "packages" / "package-topology.json"


@dataclass(frozen=True, slots=True)
class PythonPackageShell:
    role: str
    name: str
    path: Path
    module: str


def _load_topology() -> dict[str, object]:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def _load_pyproject(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _python_shells() -> list[PythonPackageShell]:
    topology = _load_topology()
    python_topology = topology["python"]
    assert isinstance(python_topology, dict)
    shells: list[PythonPackageShell] = []
    for role in ("core", "control"):
        entry = python_topology[role]
        assert isinstance(entry, dict)
        shells.append(
            PythonPackageShell(
                role=role,
                name=str(entry["name"]),
                path=REPO_ROOT / str(entry["path"]),
                module=str(entry["module"]),
            )
        )
    return shells


def test_package_topology_manifest_exists() -> None:
    assert TOPOLOGY_PATH.exists()


def test_package_topology_declares_expected_domain_terms() -> None:
    topology = _load_topology()
    terms = topology["terms"]
    assert isinstance(terms, dict)
    assert set(terms) == {
        "umbrellaPackage",
        "corePackage",
        "controlPackage",
        "compatibilityShell",
        "packageTopology",
    }


def test_package_topology_declares_apache_boundary_wrap_up_guardrails() -> None:
    topology = _load_topology()
    assert topology["status"] == "apache-boundary-wrap-up"
    guardrails = topology["guardrails"]
    assert isinstance(guardrails, dict)

    assert guardrails["repoWideLicenseFlip"] == (
        "out-of-scope-existing-code-remains-apache-2.0"
    )
    assert guardrails["dualLicenseMetadata"] == "do-not-publish-for-existing-repo"
    assert guardrails["historicalRelicensing"] == "out-of-scope"
    assert guardrails["futureProprietaryWork"] == "separate-repository"
    assert guardrails["defaultInstallCompatibility"] == (
        "preserve-autocontext-autoctx-and-autoctx-cli"
    )


def test_python_package_shells_exist() -> None:
    for shell in _python_shells():
        assert shell.path.exists(), shell.path
        assert (shell.path / "pyproject.toml").exists(), shell.path / "pyproject.toml"
        assert (shell.path / "src" / shell.module / "__init__.py").exists()


def test_python_package_shell_metadata_matches_topology() -> None:
    for shell in _python_shells():
        pyproject = _load_pyproject(shell.path / "pyproject.toml")
        project = pyproject["project"]
        assert isinstance(project, dict)
        assert project["name"] == shell.name
        assert project["version"] == "0.0.0"
        assert project["requires-python"] == ">=3.11"


def test_python_umbrella_package_keeps_existing_cli_entrypoint() -> None:
    topology = _load_topology()
    python_topology = topology["python"]
    assert isinstance(python_topology, dict)
    umbrella = python_topology["umbrella"]
    assert isinstance(umbrella, dict)
    assert umbrella["name"] == "autocontext"
    assert umbrella["path"] == "autocontext"
    assert umbrella["entrypoint"] == "autocontext.cli:app"
