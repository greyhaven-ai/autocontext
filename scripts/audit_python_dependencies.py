"""Audit every Python dependency, including the verified maintained Accelerate build."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from autocontext.accelerate_patch import verified_bundle

ROOT = Path(__file__).resolve().parents[1]


def normalize_requirements(requirements: str) -> str:
    """Verify the fork artifact, then query all advisories for its upstream version.

    PyPI's advisory service cannot identify an unpublished local version. No
    vulnerability is ignored or accepted in this normalization.
    """
    wheel, manifest = verified_bundle()
    lock = tomllib.loads((ROOT / "autocontext/uv.lock").read_text())
    packages = [p for p in lock["package"] if p["name"] == "accelerate"]
    if len(packages) != 1 or packages[0]["version"] != manifest["version"]:
        raise ValueError("Full audit requires the maintained Accelerate build in uv.lock")
    source = packages[0]["source"].get("path")
    if not source or (ROOT / "autocontext" / source).resolve() != wheel.resolve():
        raise ValueError("Locked Accelerate source differs from the verified bundle")
    entries = requirements.replace("\\\n", "").splitlines()
    replaced = 0
    normalized = []
    for entry in entries:
        if not entry.strip() or entry.lstrip().startswith("#"):
            normalized.append(entry)
            continue
        if ".whl" in entry:
            fields = entry.split()
            if Path(fields[0]).resolve() != wheel.resolve() or fields[1:] != [
                "--hash=sha256:" + manifest["wheel_sha256"],
            ]:
                raise ValueError("Unverified local wheel in full dependency export")
            normalized.append(
                f"accelerate=={manifest['upstream_version']} --hash=sha256:{manifest['upstream_sha256']}"
            )
            replaced += 1
        else:
            normalized.append(entry)
    if replaced != 1:
        raise ValueError("Full export must contain exactly one verified Accelerate wheel")
    return "\n".join(normalized) + "\n"


def evaluate_report(report: dict[str, Any]) -> list[str]:
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Audit returned no dependency inventory")
    failures = []
    observed = {}
    for dependency in dependencies:
        name = dependency["name"]
        if "skip_reason" in dependency:
            failures.append(f"Unaudited dependency: {name}: {dependency['skip_reason']}")
            continue
        if name in observed:
            raise ValueError(f"Duplicate audited dependency: {name}")
        observed[name] = dependency["version"]
        for vulnerability in dependency["vulns"]:
            failures.append(f"{name}=={dependency['version']}: {vulnerability['id']}")
    _, manifest = verified_bundle()
    if observed.get("accelerate") != manifest["upstream_version"]:
        failures.append("Full upstream advisory inventory missing or changed: accelerate")
    return failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_python_dependencies.py EXPORTED_REQUIREMENTS", file=sys.stderr)
        return 2
    try:
        requirements = normalize_requirements(Path(sys.argv[1]).read_text())
        with tempfile.TemporaryDirectory(prefix="autocontext-audit-") as temporary:
            audited = Path(temporary) / "requirements.lock"
            audited.write_text(requirements)
            result = subprocess.run(
                [sys.executable, "-m", "pip_audit", "-r", str(audited),
                 "--disable-pip", "--strict", "--format", "json"],
                stdout=subprocess.PIPE, text=True, timeout=180, check=False,
            )
        if result.returncode not in (0, 1):
            raise ValueError(f"Audit tool failed with exit code {result.returncode}")
        failures = evaluate_report(json.loads(result.stdout))
        if result.returncode == 1 and not failures:
            raise ValueError("Audit failed without a vulnerability result")
        for message in failures:
            print(f"BLOCKED: {message}", file=sys.stderr)
        if not failures:
            print("Full Python audit passed; maintained Accelerate build verified; no accepted findings.")
        return int(bool(failures))
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Audit blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
