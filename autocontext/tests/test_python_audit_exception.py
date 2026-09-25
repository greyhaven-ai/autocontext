"""The single advisory acceptance must not hide other audit failures."""
from __future__ import annotations

import copy
import json
import runpy
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = runpy.run_path(str(ROOT / "scripts/audit_python_dependencies.py"))
POLICY = json.loads((ROOT / "scripts/python_audit_exception.json").read_text())


def report() -> dict:
    dependencies = [
        {"name": name, "version": expected["version"], "vulns": []}
        for name, expected in POLICY["reviewed_packages"].items()
    ]
    dependencies[0]["vulns"] = [{"id": POLICY["advisory"], "fix_versions": []}]
    return {"dependencies": dependencies}


def test_accepts_only_reviewed_finding_and_keeps_it_visible() -> None:
    accepted, failures = AUDIT["evaluate_report"](report(), POLICY)
    assert not failures
    assert len(accepted) == 1 and POLICY["advisory"] in accepted[0]
    assert POLICY["expires"] in accepted[0]


@pytest.mark.parametrize("change", ["other_cve", "other_package", "other_version", "fix_available", "skipped", "missing"])
def test_other_findings_and_incomplete_audits_block(change: str) -> None:
    result = report()
    dependency = result["dependencies"][0]
    if change == "other_cve":
        dependency["vulns"].append({"id": "CVE-2099-12345", "fix_versions": []})
    elif change == "other_package":
        result["dependencies"].append({**copy.deepcopy(dependency), "name": "unrelated"})
    elif change == "other_version":
        dependency["version"] = "1.13.0"
    elif change == "fix_available":
        dependency["vulns"][0]["fix_versions"] = ["1.14.1"]
    elif change == "skipped":
        result["dependencies"].append({"name": "unknown", "skip_reason": "metadata unavailable"})
    else:
        result["dependencies"].pop()
    assert AUDIT["evaluate_report"](result, POLICY)[1]


@pytest.mark.parametrize("result", [{}, {"dependencies": []}, {"dependencies": "invalid"}])
def test_missing_inventory_fails(result: dict) -> None:
    with pytest.raises(ValueError):
        AUDIT["evaluate_report"](result, POLICY)


def fixture_root(tmp_path: Path) -> Path:
    for name in ("scripts/python_audit_exception.json", "autocontext/uv.lock", "autocontext/pyproject.toml", POLICY["review"]):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (tmp_path / "autocontext/src/autocontext").mkdir(parents=True)
    return tmp_path


def test_reviewed_policy_passes_before_expiry_and_fails_at_expiry(tmp_path: Path) -> None:
    root = fixture_root(tmp_path)
    assert AUDIT["validate_policy"](root, date(2026, 10, 7)) == POLICY
    with pytest.raises(ValueError, match="expired"):
        AUDIT["validate_policy"](root, date(2026, 10, 8))


@pytest.mark.parametrize("change", ["hash", "pin", "api"])
def test_source_or_graph_changes_require_review(tmp_path: Path, change: str) -> None:
    root = fixture_root(tmp_path)
    if change == "hash":
        path = root / "autocontext/uv.lock"
        path.write_text(path.read_text().replace(POLICY["reviewed_packages"]["accelerate"]["wheel_hash"], "sha256:changed"))
    elif change == "pin":
        path = root / "autocontext/pyproject.toml"
        path.write_text(path.read_text().replace("accelerate==1.14.0", "accelerate>=1.14.0"))
    else:
        (root / "autocontext/src/autocontext/new_loader.py").write_text("from accelerate import load_checkpoint_and_dispatch\n")
    with pytest.raises(ValueError, match="Review required"):
        AUDIT["validate_policy"](root, date(2026, 10, 7))


@pytest.mark.parametrize("exit_code,output", [(2, "valid"), (1, "malformed"), (1, "clean")])
def test_audit_execution_errors_cannot_be_accepted(monkeypatch: pytest.MonkeyPatch, exit_code: int, output: str) -> None:
    main = AUDIT["main"]
    monkeypatch.setitem(main.__globals__, "validate_policy", lambda *_: POLICY)
    monkeypatch.setattr(sys, "argv", ["audit_python_dependencies.py", "all-requirements.lock"])
    result = report()
    if output == "clean":
        result["dependencies"][0]["vulns"] = []
    payload = "not-json" if output == "malformed" else json.dumps(result)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
        assert "--strict" in args and "--disable-pip" in args
        assert "--ignore-vuln" not in args
        return subprocess.CompletedProcess(args, exit_code, stdout=payload)

    monkeypatch.setattr(subprocess, "run", run)
    assert main() == 1
