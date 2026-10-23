"""Full audits must verify the maintained build and accept no vulnerabilities."""
from __future__ import annotations

import copy
import json
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from autocontext.accelerate_patch import verified_bundle

ROOT = Path(__file__).resolve().parents[2]
AUDIT = runpy.run_path(str(ROOT / "scripts/audit_python_dependencies.py"))


def report() -> dict:
    return {"dependencies": [{"name": "accelerate", "version": "1.15.0", "vulns": []}]}


def exported() -> str:
    wheel, manifest = verified_bundle()
    return f"{wheel} --hash=sha256:{manifest['wheel_sha256']}\npytest==9.1.1\n"


def test_verified_local_build_is_audited_as_its_upstream_version() -> None:
    requirements = AUDIT["normalize_requirements"](exported())
    assert "accelerate==1.15.0 --hash=sha256:" in requirements
    assert "pytest==9.1.1" in requirements
    assert AUDIT["evaluate_report"](report()) == []


@pytest.mark.parametrize("change", ["hash", "path", "duplicate", "missing"])
def test_unverified_or_incomplete_export_blocks(change: str) -> None:
    requirements = exported()
    if change == "hash":
        requirements = requirements.replace("--hash=sha256:", "--hash=sha512:")
    elif change == "path":
        requirements = requirements.replace("accelerate-1.15.0+", "other-1.15.0+")
    elif change == "duplicate":
        requirements += exported()
    else:
        requirements = "pytest==9.1.1\n"
    with pytest.raises(ValueError):
        AUDIT["normalize_requirements"](requirements)


@pytest.mark.parametrize("package,advisory", [
    ("accelerate", "CVE-2026-69112"), ("accelerate", "CVE-2099-12345"), ("other", "CVE-2099-12345"),
])
def test_every_reported_vulnerability_blocks(package: str, advisory: str) -> None:
    result = report()
    if package != "accelerate":
        result["dependencies"].append({"name": package, "version": "1", "vulns": [{"id": advisory}]})
    else:
        result["dependencies"][0]["vulns"].append({"id": advisory})
    assert AUDIT["evaluate_report"](result)


@pytest.mark.parametrize("change", ["skipped", "missing", "duplicate", "wrong_version"])
def test_incomplete_inventory_blocks(change: str) -> None:
    result = report()
    if change == "skipped":
        result["dependencies"].append({"name": "unknown", "skip_reason": "metadata unavailable"})
    elif change == "missing":
        result["dependencies"] = [{"name": "other", "version": "1", "vulns": []}]
    elif change == "wrong_version":
        result["dependencies"][0]["version"] = "1.14.0"
    else:
        result["dependencies"].append(copy.deepcopy(result["dependencies"][0]))
        with pytest.raises(ValueError, match="Duplicate"):
            AUDIT["evaluate_report"](result)
        return
    assert AUDIT["evaluate_report"](result)


@pytest.mark.parametrize("result", [{}, {"dependencies": []}, {"dependencies": "invalid"}])
def test_missing_inventory_fails(result: dict) -> None:
    with pytest.raises(ValueError):
        AUDIT["evaluate_report"](result)


@pytest.mark.parametrize("exit_code,output", [(2, "valid"), (1, "malformed"), (1, "clean")])
def test_audit_execution_errors_cannot_pass(monkeypatch, tmp_path, exit_code: int, output: str) -> None:
    requirements = tmp_path / "requirements.lock"
    requirements.write_text(exported())
    monkeypatch.setattr(sys, "argv", ["audit_python_dependencies.py", str(requirements)])
    payload = "not-json" if output == "malformed" else json.dumps(report())
    def run(args, **kwargs):
        assert "--strict" in args and "--disable-pip" in args and "--ignore-vuln" not in args
        return subprocess.CompletedProcess(args, exit_code, stdout=payload)
    monkeypatch.setattr(subprocess, "run", run)
    assert AUDIT["main"]() == 1
