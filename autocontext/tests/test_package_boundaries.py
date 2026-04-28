from __future__ import annotations

import ast
import json
import subprocess
import tomllib
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

REPO_ROOT = Path(__file__).resolve().parents[2]
BOUNDARIES_PATH = REPO_ROOT / "packages" / "package-boundaries.json"
TOPOLOGY_PATH = REPO_ROOT / "packages" / "package-topology.json"
CORE_INIT_PATH = REPO_ROOT / "packages" / "python" / "core" / "src" / "autocontext_core" / "__init__.py"
CONTROL_INIT_PATH = REPO_ROOT / "packages" / "python" / "control" / "src" / "autocontext_control" / "__init__.py"


def _load_boundaries() -> dict[str, object]:
    return json.loads(BOUNDARIES_PATH.read_text(encoding="utf-8"))


def _load_topology() -> dict[str, object]:
    return json.loads(TOPOLOGY_PATH.read_text(encoding="utf-8"))


def _load_pyproject(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _python_import_targets(path: Path) -> list[str]:
    targets: list[str] = []
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    class ImportModuleVisitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                targets.append(node.args[0].value)
            self.generic_visit(node)

    ImportModuleVisitor().visit(module)
    return targets


def _python_core_import_targets() -> list[str]:
    return _python_import_targets(CORE_INIT_PATH)


def _python_control_import_targets() -> list[str]:
    return _python_import_targets(CONTROL_INIT_PATH)


def _dependency_name(requirement: object) -> str:
    return (
        str(requirement)
        .split(";", 1)[0]
        .split("[", 1)[0]
        .split("<", 1)[0]
        .split(">", 1)[0]
        .split("=", 1)[0]
        .split("~", 1)[0]
        .split("!", 1)[0]
        .strip()
    )


def _licensing_guardrails() -> dict[str, object]:
    boundaries = _load_boundaries()
    licensing = boundaries["licensing"]
    assert isinstance(licensing, dict)
    return licensing


def test_package_boundaries_manifest_exists() -> None:
    assert BOUNDARIES_PATH.exists()


def test_license_metadata_publication_is_deferred_to_linear_guardrails() -> None:
    licensing = _licensing_guardrails()

    assert licensing["status"] == "deferred"
    assert licensing["licenseMetadataIssue"] == "AC-645"
    assert licensing["rightsAuditIssue"] == "AC-646"


def test_deferred_license_publication_files_are_absent() -> None:
    licensing = _licensing_guardrails()
    forbidden_paths = licensing["forbiddenPathsUntilAC645"]
    assert isinstance(forbidden_paths, list)
    assert forbidden_paths == [
        "LICENSING.md",
        "packages/python/core/LICENSE",
        "packages/python/control/LICENSE",
        "packages/ts/core/LICENSE",
        "packages/ts/control-plane/LICENSE",
    ]

    for relative_path in forbidden_paths:
        assert isinstance(relative_path, str)
        assert not (REPO_ROOT / relative_path).exists()


def test_rights_audit_blocks_unclear_paths_from_non_apache_relicensing() -> None:
    licensing = _licensing_guardrails()
    rights_audit = licensing["rightsAudit"]
    assert isinstance(rights_audit, dict)

    assert rights_audit["status"] == "in-progress"
    assert rights_audit["auditDoc"] == "docs/contributor-rights-audit.md"
    assert (REPO_ROOT / str(rights_audit["auditDoc"])).exists()
    blocked_paths = rights_audit["blockedRelicensingPathsUntilConfirmed"]
    assert isinstance(blocked_paths, list)
    assert blocked_paths == [
        "autocontext/src/autocontext/mcp/server.py",
        "autocontext/src/autocontext/mcp/tools.py",
        "autocontext/src/autocontext/knowledge/export.py",
        "autocontext/src/autocontext/knowledge/search.py",
        "ts/src/knowledge/skill-package.ts",
    ]

    for relative_path in blocked_paths:
        assert isinstance(relative_path, str)
        assert (REPO_ROOT / relative_path).exists()


def test_python_license_metadata_stays_deferred_for_new_package_artifacts() -> None:
    licensing = _licensing_guardrails()
    python_metadata = licensing["pythonProjectMetadata"]
    assert isinstance(python_metadata, dict)
    pyproject_paths = python_metadata["paths"]
    forbidden_project_keys = python_metadata["forbiddenProjectKeys"]
    forbidden_classifier_prefixes = python_metadata["forbiddenClassifierPrefixes"]
    assert isinstance(pyproject_paths, list)
    assert isinstance(forbidden_project_keys, list)
    assert isinstance(forbidden_classifier_prefixes, list)
    assert forbidden_project_keys == ["license", "license-files"]
    assert forbidden_classifier_prefixes == ["License ::"]

    for relative_path in pyproject_paths:
        assert isinstance(relative_path, str)
        pyproject = _load_pyproject(REPO_ROOT / relative_path)
        project = pyproject["project"]
        assert isinstance(project, dict)
        for key in forbidden_project_keys:
            assert isinstance(key, str)
            assert key not in project
        classifiers = project.get("classifiers", [])
        assert isinstance(classifiers, list)
        for classifier in classifiers:
            assert isinstance(classifier, str)
            for prefix in forbidden_classifier_prefixes:
                assert isinstance(prefix, str)
                assert not classifier.startswith(prefix)


def test_python_boundary_contract_reuses_topology_core_module() -> None:
    boundaries = _load_boundaries()
    topology = _load_topology()
    python_boundaries = boundaries["python"]
    python_topology = topology["python"]
    assert isinstance(python_boundaries, dict)
    assert isinstance(python_topology, dict)
    core_boundary = python_boundaries["core"]
    core_topology = python_topology["core"]
    assert isinstance(core_boundary, dict)
    assert isinstance(core_topology, dict)

    assert core_boundary["module"] == core_topology["module"]


def test_python_core_facade_imports_match_boundary_contract() -> None:
    boundaries = _load_boundaries()
    python_boundaries = boundaries["python"]
    assert isinstance(python_boundaries, dict)
    core = python_boundaries["core"]
    assert isinstance(core, dict)
    allowed_imports = core["allowedImports"]
    assert isinstance(allowed_imports, list)

    assert _python_core_import_targets() == allowed_imports


def test_python_core_facade_excludes_control_plane_imports() -> None:
    boundaries = _load_boundaries()
    python_boundaries = boundaries["python"]
    assert isinstance(python_boundaries, dict)
    core = python_boundaries["core"]
    assert isinstance(core, dict)
    blocked_prefixes = core["blockedImportPrefixes"]
    assert isinstance(blocked_prefixes, list)

    import_targets = _python_core_import_targets()
    for target in import_targets:
        for prefix in blocked_prefixes:
            assert isinstance(prefix, str)
            assert target != prefix
            assert not target.startswith(f"{prefix}.")


def test_python_core_package_dependencies_point_away_from_control_and_umbrella_packages() -> None:
    boundaries = _load_boundaries()
    python_boundaries = boundaries["python"]
    assert isinstance(python_boundaries, dict)
    core = python_boundaries["core"]
    assert isinstance(core, dict)
    blocked_dependencies = core["blockedDependencies"]
    assert blocked_dependencies == ["autocontext", "autocontext-control"]

    pyproject = _load_pyproject(REPO_ROOT / "packages" / "python" / "core" / "pyproject.toml")
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependency_names = {_dependency_name(dependency) for dependency in project.get("dependencies", [])}
    optional_dependencies = project.get("optional-dependencies", {})
    assert isinstance(optional_dependencies, dict)
    for group_dependencies in optional_dependencies.values():
        assert isinstance(group_dependencies, list)
        dependency_names.update(_dependency_name(dependency) for dependency in group_dependencies)

    for dependency in blocked_dependencies:
        assert dependency not in dependency_names


def test_python_control_boundary_contract_reuses_topology_control_module() -> None:
    boundaries = _load_boundaries()
    topology = _load_topology()
    python_boundaries = boundaries["python"]
    python_topology = topology["python"]
    assert isinstance(python_boundaries, dict)
    assert isinstance(python_topology, dict)
    control_boundary = python_boundaries["control"]
    control_topology = python_topology["control"]
    assert isinstance(control_boundary, dict)
    assert isinstance(control_topology, dict)

    assert control_boundary["module"] == control_topology["module"]


def test_python_control_facade_imports_match_boundary_contract() -> None:
    boundaries = _load_boundaries()
    python_boundaries = boundaries["python"]
    assert isinstance(python_boundaries, dict)
    control = python_boundaries["control"]
    assert isinstance(control, dict)
    allowed_imports = control["allowedImports"]
    assert isinstance(allowed_imports, list)

    assert _python_control_import_targets() == allowed_imports


def test_python_control_package_dependencies_point_away_from_umbrella_package() -> None:
    boundaries = _load_boundaries()
    python_boundaries = boundaries["python"]
    assert isinstance(python_boundaries, dict)
    control = python_boundaries["control"]
    assert isinstance(control, dict)
    blocked_dependencies = control["blockedDependencies"]
    assert blocked_dependencies == ["autocontext"]

    pyproject = _load_pyproject(REPO_ROOT / "packages" / "python" / "control" / "pyproject.toml")
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependency_names = {_dependency_name(dependency) for dependency in project.get("dependencies", [])}
    optional_dependencies = project.get("optional-dependencies", {})
    assert isinstance(optional_dependencies, dict)
    for group_dependencies in optional_dependencies.values():
        assert isinstance(group_dependencies, list)
        dependency_names.update(_dependency_name(dependency) for dependency in group_dependencies)

    for dependency in blocked_dependencies:
        assert dependency not in dependency_names


def test_python_package_builds_emit_wheel_and_sdist() -> None:
    packages = [
        REPO_ROOT / "packages" / "python" / "core",
        REPO_ROOT / "packages" / "python" / "control",
    ]

    for package_dir in packages:
        pyproject = _load_pyproject(package_dir / "pyproject.toml")
        project = pyproject["project"]
        assert isinstance(project, dict)
        project_name = str(project["name"])
        normalized_name = project_name.replace("-", "_")

        with TemporaryDirectory(prefix=f"{normalized_name}-dist-") as tmpdir:
            out_dir = Path(tmpdir)
            subprocess.run(
                ["uv", "build", str(package_dir), "-o", str(out_dir)],
                check=True,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
            wheel = next(out_dir.glob(f"{normalized_name}-*.whl"), None)
            sdist = next(out_dir.glob(f"{normalized_name}-*.tar.gz"), None)
            assert wheel is not None
            assert sdist is not None

            module_dir = package_dir / "src"
            package_modules = [path.name for path in module_dir.iterdir() if path.is_dir()]
            with zipfile.ZipFile(wheel) as wheel_zip:
                wheel_names = set(wheel_zip.namelist())
            for module in package_modules:
                assert f"{module}/__init__.py" in wheel_names
