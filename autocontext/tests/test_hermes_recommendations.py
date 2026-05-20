"""AC-709: read-only recommendation surface for Hermes curator.

DDD/TDD coverage:

* :class:`SkillFeatures` is the prediction-time input shape: a
  :class:`CuratorDecisionExample` minus the label. Advisors take
  features, not labeled examples (clean split between training and
  inference).
* :func:`recommend` walks a :class:`HermesInventory`, runs the
  advisor over each active skill's features, and returns a list of
  :class:`Recommendation` rows.
* Protected skills (pinned, bundled, hub) are filtered out by
  default so the surface never recommends mutation against
  upstream-owned or operator-pinned skills.
* ``--include-protected`` surfaces protected skills anyway (for
  analysis / audit) but tags them with ``status: "protected"`` so a
  downstream consumer cannot accidentally act on them.
* The surface is read-only: it never writes to ``~/.hermes`` and the
  output JSONL lives wherever the operator specified.
* CLI: ``autoctx hermes recommend --home <path>
  --baseline-from <jsonl> --output <jsonl>`` trains a baseline on
  AC-705 export data, runs it against the live home, and emits the
  recommendations.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.hermes.advisor import (
    BaselineAdvisor,
    CuratorDecisionExample,
    SkillFeatures,
)
from autocontext.hermes.recommendations import (
    Recommendation,
    recommend,
)


def _plant_hermes_home(tmp_path: Path, *, skills: list[dict]) -> Path:
    """Build a minimal Hermes home matching the inspection layout."""

    home = tmp_path / "hermes"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)
    usage: dict[str, dict] = {}
    bundled: list[str] = []
    hub: list[str] = []
    for s in skills:
        name = s["name"]
        skill_dir = skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n# {name}\n",
            encoding="utf-8",
        )
        usage[name] = {
            "state": s.get("state", "active"),
            "pinned": bool(s.get("pinned", False)),
            "use_count": s.get("use_count", 0),
            "view_count": s.get("view_count", 0),
            "patch_count": s.get("patch_count", 0),
        }
        if s.get("provenance") == "bundled":
            bundled.append(name)
        elif s.get("provenance") == "hub":
            hub.append(name)
    (skills_dir / ".usage.json").write_text(json.dumps(usage), encoding="utf-8")
    if bundled:
        (skills_dir / ".bundled_manifest").write_text("\n".join(bundled) + "\n", encoding="utf-8")
    if hub:
        hub_dir = skills_dir / ".hub"
        hub_dir.mkdir()
        (hub_dir / "lock.json").write_text(
            json.dumps({"installed": {n: {} for n in hub}}),
            encoding="utf-8",
        )
    return home


def _baseline_predicting(label: str) -> BaselineAdvisor:
    """Build a baseline that always predicts ``label`` (no training needed)."""
    return BaselineAdvisor(majority_label=label, label_counts={label: 1})


# --- SkillFeatures + CuratorDecisionExample bridge -------------------------


def test_curator_decision_example_exposes_features() -> None:
    """Slice 1's CuratorDecisionExample now exposes a `.features`
    property that produces the SkillFeatures the advisor consumes.
    This is the bridge from training data to inference data."""
    ex = CuratorDecisionExample(
        skill_name="s1",
        label="consolidated",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=12,
        view_count=3,
        patch_count=1,
    )
    feats = ex.features
    assert isinstance(feats, SkillFeatures)
    assert feats.skill_name == "s1"
    assert feats.use_count == 12
    # Activity count is derived consistently on both sides.
    assert feats.activity_count == ex.activity_count == 16


def test_advisor_predicts_from_features_directly() -> None:
    """Advisors take SkillFeatures, not labeled examples — clean split
    between training inputs (labeled) and inference inputs (features)."""
    advisor = _baseline_predicting("consolidated")
    feats = SkillFeatures(
        skill_name="s1",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=0,
        view_count=0,
        patch_count=0,
    )
    assert advisor.predict(feats) == "consolidated"


# --- recommend() ----------------------------------------------------------


def test_recommend_emits_one_row_per_active_unprotected_skill(tmp_path: Path) -> None:
    from autocontext.hermes.inspection import inspect_hermes_home

    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "skill-a", "provenance": "agent-created", "use_count": 1},
            {"name": "skill-b", "provenance": "agent-created", "use_count": 5},
        ],
    )
    inventory = inspect_hermes_home(home)
    advisor = _baseline_predicting("consolidated")

    recs = recommend(inventory=inventory, advisor=advisor)
    assert len(recs) == 2
    assert all(isinstance(r, Recommendation) for r in recs)
    assert {r.skill_name for r in recs} == {"skill-a", "skill-b"}
    assert all(r.predicted_action == "consolidated" for r in recs)


def test_recommend_filters_pinned_bundled_and_hub_by_default(tmp_path: Path) -> None:
    """AC-709 invariant: protected skills (pinned, bundled, hub) must
    never appear as targets in the default output. The advisor may
    have an opinion about them; that opinion is not actionable so the
    surface withholds it unless --include-protected is passed."""
    from autocontext.hermes.inspection import inspect_hermes_home

    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "active", "provenance": "agent-created"},
            {"name": "pinned", "provenance": "agent-created", "pinned": True},
            {"name": "bundled-skill", "provenance": "bundled"},
            {"name": "hub-skill", "provenance": "hub"},
        ],
    )
    inventory = inspect_hermes_home(home)
    advisor = _baseline_predicting("consolidated")
    recs = recommend(inventory=inventory, advisor=advisor)
    target_names = {r.skill_name for r in recs}
    assert target_names == {"active"}


def test_recommend_include_protected_surfaces_them_with_protected_status(tmp_path: Path) -> None:
    """`--include-protected` lets operators audit what the advisor
    would say about pinned/bundled/hub skills without making the
    output actionable. Recommendations for protected skills carry
    ``status == "protected"`` so consumers cannot accidentally act
    on them."""
    from autocontext.hermes.inspection import inspect_hermes_home

    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "active", "provenance": "agent-created"},
            {"name": "pinned", "provenance": "agent-created", "pinned": True},
        ],
    )
    inventory = inspect_hermes_home(home)
    advisor = _baseline_predicting("consolidated")
    recs = recommend(inventory=inventory, advisor=advisor, include_protected=True)
    by_name = {r.skill_name: r for r in recs}
    assert by_name["active"].status == "actionable"
    assert by_name["pinned"].status == "protected"


def test_recommend_returns_empty_when_no_unprotected_skills(tmp_path: Path) -> None:
    from autocontext.hermes.inspection import inspect_hermes_home

    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "pinned", "provenance": "agent-created", "pinned": True}],
    )
    inventory = inspect_hermes_home(home)
    advisor = _baseline_predicting("consolidated")
    assert recommend(inventory=inventory, advisor=advisor) == []


def test_recommendation_serializes_to_json_friendly_dict() -> None:
    feats = SkillFeatures(
        skill_name="s1",
        state="active",
        provenance="agent-created",
        pinned=False,
        use_count=12,
        view_count=3,
        patch_count=1,
    )
    rec = Recommendation(
        skill_name="s1",
        predicted_action="consolidated",
        confidence="advisory",
        status="actionable",
        features=feats,
        reason="baseline majority class",
    )
    payload = rec.to_dict()
    assert payload["skill_name"] == "s1"
    assert payload["predicted_action"] == "consolidated"
    assert payload["features"]["use_count"] == 12
    json.dumps(payload)  # round-trips


def test_recommend_reason_explains_baseline_choice(tmp_path: Path) -> None:
    """Operators should be able to read why each recommendation was
    made. For the baseline that's "majority class from training" — a
    later trained advisor will carry richer reasons (e.g. top feature
    contributions)."""
    from autocontext.hermes.inspection import inspect_hermes_home

    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "active", "provenance": "agent-created"}],
    )
    inventory = inspect_hermes_home(home)
    advisor = _baseline_predicting("consolidated")
    recs = recommend(inventory=inventory, advisor=advisor)
    assert "baseline" in recs[0].reason.lower() or "majority" in recs[0].reason.lower()


# --- CLI integration ------------------------------------------------------


def _ac705_row(name: str, label: str, *, use_count: int = 0) -> dict:
    return {
        "example_id": f"r:{name}:{label}",
        "task_kind": "curator-decisions",
        "source": {"curator_run_path": "/tmp/r.json", "started_at": "2026-05-01T00:00:00Z"},
        "input": {
            "skill_name": name,
            "skill_state": "active",
            "skill_provenance": "agent-created",
            "skill_pinned": False,
            "skill_use_count": use_count,
            "skill_view_count": 0,
            "skill_patch_count": 0,
            "skill_activity_count": use_count,
            "skill_last_activity_at": None,
        },
        "label": label,
        "confidence": "strong",
        "redactions": [],
        "context": {"run_provider": "anthropic", "run_model": "x", "run_counts": {}},
    }


def test_cli_recommend_writes_jsonl_against_live_home(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    # AC-705-shaped training data: 4 consolidated, 1 pruned → baseline majority is "consolidated"
    training = tmp_path / "training.jsonl"
    with training.open("w", encoding="utf-8") as fh:
        for i in range(4):
            fh.write(json.dumps(_ac705_row(f"t{i}", "consolidated")) + "\n")
        fh.write(json.dumps(_ac705_row("t9", "pruned")) + "\n")

    # Live home with one active skill.
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "active-skill", "provenance": "agent-created", "use_count": 7}],
    )
    output = tmp_path / "recs.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["recommendation_count"] == 1
    assert payload["majority_label"] == "consolidated"

    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["skill_name"] == "active-skill"
    assert rows[0]["predicted_action"] == "consolidated"


def test_cli_recommend_rejects_same_path_for_training_and_output(tmp_path: Path) -> None:
    """PR-review-style same-file guard (matches AC-706 / AC-708 slice 1):
    refusing to overwrite the source training JSONL with recommendations."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text(json.dumps(_ac705_row("t1", "consolidated")) + "\n", encoding="utf-8")
    original = training.read_text(encoding="utf-8")
    home = _plant_hermes_home(tmp_path, skills=[{"name": "active", "provenance": "agent-created"}])

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(training),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert training.read_text(encoding="utf-8") == original


def test_cli_recommend_handles_empty_training_data(tmp_path: Path) -> None:
    """Training a baseline on an empty AC-705 export raises ValueError
    in train_baseline; the CLI must surface that clearly rather than
    crashing with a traceback."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text("", encoding="utf-8")
    home = _plant_hermes_home(tmp_path, skills=[{"name": "active", "provenance": "agent-created"}])
    output = tmp_path / "recs.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code != 0


def test_cli_recommend_emits_empty_jsonl_when_no_unprotected_skills(tmp_path: Path) -> None:
    """An all-protected home should still produce a valid (empty)
    output file so downstream pipelines can rely on its existence."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text(
        "\n".join(json.dumps(_ac705_row(f"t{i}", "consolidated")) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "pinned", "provenance": "agent-created", "pinned": True}],
    )
    output = tmp_path / "recs.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["recommendation_count"] == 0
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_cli_rejects_output_inside_hermes_home(tmp_path: Path) -> None:
    """PR #973 review (P2): the recommendation surface claims it never
    writes to ~/.hermes. An --output path inside the resolved home
    would break that contract. Reject it at the boundary."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text(
        "\n".join(json.dumps(_ac705_row(f"t{i}", "consolidated")) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    home = _plant_hermes_home(tmp_path, skills=[{"name": "active", "provenance": "agent-created"}])
    output_inside = home / "recommendations.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output_inside),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert not output_inside.exists()


def test_cli_rejects_output_in_nested_dir_under_hermes_home(tmp_path: Path) -> None:
    """A nested subdir under the home is still under the home; rejection
    must check resolved containment, not just direct equality."""
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text(
        "\n".join(json.dumps(_ac705_row(f"t{i}", "consolidated")) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    home = _plant_hermes_home(tmp_path, skills=[{"name": "active", "provenance": "agent-created"}])
    output_inside = home / "exports" / "subdir" / "recommendations.jsonl"

    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output_inside),
            "--json",
        ],
    )
    assert result.exit_code != 0
    assert not output_inside.exists()


def test_cli_include_protected_flag_surfaces_pinned_skills(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    training = tmp_path / "training.jsonl"
    training.write_text(
        "\n".join(json.dumps(_ac705_row(f"t{i}", "consolidated")) for i in range(3)) + "\n",
        encoding="utf-8",
    )
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "active", "provenance": "agent-created"},
            {"name": "pinned", "provenance": "agent-created", "pinned": True},
        ],
    )
    output = tmp_path / "recs.jsonl"
    result = CliRunner().invoke(
        app,
        [
            "hermes",
            "recommend",
            "--home",
            str(home),
            "--baseline-from",
            str(training),
            "--output",
            str(output),
            "--include-protected",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    statuses = {r["skill_name"]: r["status"] for r in rows}
    assert statuses == {"active": "actionable", "pinned": "protected"}
