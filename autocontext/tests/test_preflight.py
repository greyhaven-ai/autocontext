"""Tests for preflight checks."""
from __future__ import annotations

from pathlib import Path

from autocontext.preflight import CheckResult, PreflightChecker


def test_scenario_exists_check_passes() -> None:
    checker = PreflightChecker(scenario="grid_ctf")
    result = checker.check_scenario_exists()
    assert result.passed


def test_scenario_exists_check_fails() -> None:
    checker = PreflightChecker(scenario="nonexistent_scenario")
    result = checker.check_scenario_exists()
    assert not result.passed


def test_knowledge_dir_writable(tmp_path: Path) -> None:
    checker = PreflightChecker(scenario="grid_ctf", knowledge_root=tmp_path)
    result = checker.check_knowledge_writable()
    assert result.passed


def test_run_all_checks() -> None:
    checker = PreflightChecker(scenario="grid_ctf")
    results = checker.run_all()
    assert isinstance(results, list)
    assert all(isinstance(r, CheckResult) for r in results)


def test_to_markdown() -> None:
    checker = PreflightChecker(scenario="grid_ctf")
    results = checker.run_all()
    md = PreflightChecker.to_markdown(results)
    assert "Preflight" in md
    assert "PASS" in md or "FAIL" in md
