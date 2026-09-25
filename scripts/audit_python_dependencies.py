"""Run the full strict audit with one expiring, graph-bound risk acceptance."""
from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_APIS = ("load_checkpoint_in_model", "load_checkpoint_and_dispatch", "load_and_quantize_model")


def validate_policy(root: Path, today: date) -> dict[str, Any]:
    policy = json.loads((root / "scripts/python_audit_exception.json").read_text())
    if today >= date.fromisoformat(policy["expires"]):
        raise ValueError("Accelerate risk acceptance expired; review or remove it")
    if not (root / policy["review"]).is_file():
        raise ValueError("Missing risk assessment")
    package_root = root / "autocontext"
    lock = tomllib.loads((package_root / "uv.lock").read_text())
    manifest = tomllib.loads((package_root / "pyproject.toml").read_text())
    cuda = manifest["project"]["optional-dependencies"]["cuda"]
    for name, expected in policy["reviewed_packages"].items():
        packages = [p for p in lock["package"] if p["name"] == name]
        if len(packages) != 1:
            raise ValueError(f"Review required: ambiguous or missing {name}")
        package = packages[0]
        if package["version"] != expected["version"] or {
            w["hash"] for w in package.get("wheels", [])
        } != {expected["wheel_hash"]}:
            raise ValueError(f"Review required: {name} version or wheel changed")
        if f'{name}=={expected["version"]}' not in cuda:
            raise ValueError(f"Review required: {name} CUDA version is not pinned")
    # A review tripwire, not a sandbox: extensions and direct third-party API
    # calls are outside this assessment. New harness use needs a fresh review.
    for source in (package_root / "src/autocontext").rglob("*.py"):
        if any(api in source.read_text(encoding="utf-8") for api in FORBIDDEN_APIS):
            raise ValueError(f"Review required: checkpoint API reference in {source.relative_to(root)}")
    return policy


def evaluate_report(report: dict[str, Any], policy: dict[str, Any]) -> tuple[list[str], list[str]]:
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Audit returned no dependency inventory")
    accepted, failures = [], []
    observed: dict[str, str] = {}
    for dependency in dependencies:
        name = dependency["name"]
        if "skip_reason" in dependency:
            failures.append(f"Unaudited dependency: {name}: {dependency['skip_reason']}")
            continue
        version = dependency["version"]
        if name in observed:
            raise ValueError(f"Duplicate audited dependency: {name}")
        observed[name] = version
        for vulnerability in dependency["vulns"]:
            ids = {vulnerability["id"], *vulnerability.get("aliases", [])}
            label = f"{name}=={version}: {vulnerability['id']}"
            if (
                name == policy["package"] and version == policy["version"]
                and ids.intersection({policy["advisory"], policy["alias"]})
                and not vulnerability["fix_versions"]
            ):
                accepted.append(f"ACCEPTED until {policy['expires']}: {label}; see {policy['review']}")
            else:
                failures.append(label)
    for name, expected in policy["reviewed_packages"].items():
        if observed.get(name) != expected["version"]:
            failures.append(f"Full reviewed inventory missing or changed: {name}")
    return accepted, failures


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: audit_python_dependencies.py EXPORTED_REQUIREMENTS", file=sys.stderr)
        return 2
    try:
        policy = validate_policy(ROOT, datetime.now(UTC).date())
        result = subprocess.run(
            [sys.executable, "-m", "pip_audit", "-r", sys.argv[1],
             "--disable-pip", "--strict", "--format", "json"],
            stdout=subprocess.PIPE, text=True, timeout=180, check=False,
        )
        if result.returncode not in (0, 1):
            raise ValueError(f"Audit tool failed with exit code {result.returncode}")
        accepted, failures = evaluate_report(json.loads(result.stdout), policy)
        if result.returncode == 1 and not accepted and not failures:
            raise ValueError("Audit failed without a vulnerability result")
        for message in accepted:
            print(message)
        for message in failures:
            print(f"BLOCKED: {message}", file=sys.stderr)
        if not failures:
            print("Full Python audit passed subject to the explicitly listed risk acceptance.")
        return int(bool(failures))
    except (ValueError, KeyError, TypeError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"Audit blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
