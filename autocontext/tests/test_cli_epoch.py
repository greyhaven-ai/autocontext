"""CLI contract for ``autoctx epoch list|approve|reject`` (AC-885 Slice C3).

scenario (``grid_ctf``) is the registry + sqlite key; the charter target NAME (``competitor-local``)
is a deliberately distinct value resolved from the charter by selector (the C2 lesson: never pass the
scenario as the charter target name). approve/reject are pure human overrides.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry

runner = CliRunner()

_SCENARIO = "grid_ctf"

# Minimal valid charter ``load_charter`` accepts: a single autocontext source, one role target whose
# selector maps to ``grid_ctf`` but whose NAME is NOT the scenario, and the three required budgets.
_CHARTER_YAML = """\
tier: oss
sources:
  - name: native
    kind: autocontext
targets:
  - name: competitor-local
    kind: role
    selector: competitor@{selector_scenario}
    base_model: Qwen/Qwen2.5-3B-Instruct
    min_dataset_records: 10
    eval_suite: grid_ctf_holdout
budgets:
  gpu_hours_per_window: 8.0
  window_hours: 24
  disk_quota_gb: 10.0
"""


def _charter_file(tmp_path: Path, selector_scenario: str = "grid_ctf") -> Path:
    path = tmp_path / "ambient-charter.yaml"
    path.write_text(_CHARTER_YAML.format(selector_scenario=selector_scenario), encoding="utf-8")
    return path


def _seed(tmp_path: Path) -> tuple[EvaluatorEpochRegistry, str, str]:
    """Point the CLI at tmp and seed one active + one candidate epoch for grid_ctf."""
    kroot = tmp_path / "knowledge"
    reg = EvaluatorEpochRegistry(kroot / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe(_SCENARIO, e1, now_fn=lambda: "t0")  # active (bootstrap)
    reg.observe(_SCENARIO, e2, now_fn=lambda: "t1")  # candidate
    return reg, e1.epoch_id, e2.epoch_id


def _env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AUTOCONTEXT_KNOWLEDGE_ROOT", str(tmp_path / "knowledge"))
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "t.sqlite3"))


def test_epoch_list_and_approve(tmp_path: Path, monkeypatch) -> None:
    reg, _active, candidate = _seed(tmp_path)
    _env(monkeypatch, tmp_path)
    charter = _charter_file(tmp_path)

    listed = runner.invoke(app, ["epoch", "list", "--scenario", _SCENARIO])
    assert listed.exit_code == 0, listed.output
    assert candidate in listed.stdout

    approved = runner.invoke(
        app,
        ["epoch", "approve", _SCENARIO, candidate, "--by", "jay", "--charter", str(charter)],
    )
    assert approved.exit_code == 0, approved.output
    active = reg.active_for(_SCENARIO)
    assert active is not None and active.epoch_id == candidate
    assert "activated" in approved.stdout


def test_epoch_reject_does_not_activate(tmp_path: Path, monkeypatch) -> None:
    reg, prior_active, candidate = _seed(tmp_path)
    _env(monkeypatch, tmp_path)
    charter = _charter_file(tmp_path)

    rejected = runner.invoke(
        app,
        ["epoch", "reject", _SCENARIO, candidate, "--by", "jay", "--charter", str(charter)],
    )
    assert rejected.exit_code == 0, rejected.output
    assert "rejected" in rejected.stdout
    active = reg.active_for(_SCENARIO)
    assert active is not None and active.epoch_id == prior_active


def test_epoch_approve_missing_candidate_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    _seed(tmp_path)
    _env(monkeypatch, tmp_path)
    charter = _charter_file(tmp_path)

    missing = "0" * 64
    result = runner.invoke(
        app,
        ["epoch", "approve", _SCENARIO, missing, "--by", "jay", "--charter", str(charter)],
    )
    assert result.exit_code != 0


def test_epoch_list_all_scenarios(tmp_path: Path, monkeypatch) -> None:
    # bare `epoch list` (no --scenario) enumerates every scenario subdir under the registry root.
    reg, _active, candidate = _seed(tmp_path)
    _env(monkeypatch, tmp_path)
    listed = runner.invoke(app, ["epoch", "list"])
    assert listed.exit_code == 0, listed.output
    assert candidate in listed.stdout


def test_epoch_approve_no_matching_target_errors(tmp_path: Path, monkeypatch) -> None:
    # a charter whose only target selects a DIFFERENT scenario cannot resolve target_name for _SCENARIO;
    # approve must error clearly (non-zero) and not mutate the registry.
    reg, active, candidate = _seed(tmp_path)
    _env(monkeypatch, tmp_path)
    charter = _charter_file(tmp_path, selector_scenario="othello")  # does not match _SCENARIO
    result = runner.invoke(
        app,
        ["epoch", "approve", _SCENARIO, candidate, "--by", "jay", "--charter", str(charter)],
    )
    assert result.exit_code != 0
    # registry unchanged: the candidate is still a candidate, prior active preserved
    assert reg.active_for(_SCENARIO).epoch_id == active
