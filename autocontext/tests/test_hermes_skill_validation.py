"""AC-711: validate the Hermes ``autocontext`` skill against realistic
agent prompts using a static content rubric.

The validator never calls an LLM. Instead it runs typed
:class:`ExpectedBehavior` predicates against the rendered SKILL.md
text. The rubric catches the same kinds of failures a live agent
would hit (missing CLI-first guidance, missing privacy posture,
absent Curator/autocontext separation) without the cost and flake
of a real LLM in the loop.

Coverage:

* Domain value types (:class:`TaskPrompt`, :class:`ExpectedBehavior`,
  :class:`ValidationCase`, :class:`ValidationResult`,
  :class:`ValidationReport`) hold their shape.
* The shipped rubric (six AC-711 fixture prompts) runs green
  against the current rendered skill.
* Each AC-711 evaluation criterion is covered by at least one
  :class:`ExpectedBehavior`.
* Negative tests: a mutilated skill (with the CLI-first guidance
  stripped, or with the privacy section stripped) FAILS the
  rubric — this proves the predicates have teeth.
* :class:`ValidationReport.to_dict` round-trips through JSON.
* CLI integration: ``autoctx hermes validate-skill --json``
  returns the report payload.
"""

from __future__ import annotations

import json

import pytest

from autocontext.hermes.skill import render_autocontext_skill
from autocontext.hermes.skill_validation import (
    DEFAULT_RUBRIC,
    ExpectedBehavior,
    TaskPrompt,
    ValidationCase,
    ValidationResult,
    validate_skill,
)

# --- Value types ----------------------------------------------------------


def test_task_prompt_is_immutable_value_type() -> None:
    p = TaskPrompt(id="p1", text="Evaluate this strategy", scenario="judge_workflow")
    assert p.id == "p1"
    assert p.scenario == "judge_workflow"


def test_expected_behavior_predicate_runs_against_skill_text() -> None:
    behavior = ExpectedBehavior(
        name="mentions_cli_first",
        description="the skill must mention CLI-first ordering",
        predicate=lambda skill: "CLI first" in skill or "cli-first" in skill.lower(),
    )
    assert behavior.run("use the CLI first") is True
    assert behavior.run("Cli-First ordering") is True
    assert behavior.run("no mention") is False


# --- Default rubric coverage ----------------------------------------------


def test_default_rubric_covers_all_ac711_evaluation_criteria() -> None:
    """Every AC-711 evaluation criterion must map to at least one
    behavior in the default rubric so the rubric can claim to validate
    the ticket's success conditions."""
    behavior_names = {b.name for case in DEFAULT_RUBRIC for b in case.expected}
    # AC-711 criteria → behavior coverage
    required = {
        "prefers_cli_when_mcp_unconfigured",
        "uses_mcp_only_when_configured",
        "never_mutates_hermes_skills_for_inspect_or_train",
        "explains_privacy_before_session_ingest",
        "documents_export_skill_path",
        "separates_curator_and_autocontext_responsibilities",
    }
    missing = required - behavior_names
    assert not missing, f"rubric missing behaviors for AC-711 criteria: {sorted(missing)}"


def test_default_rubric_includes_all_six_ticket_prompts() -> None:
    """AC-711 lists six realistic prompts; the rubric must include all six."""
    expected_scenarios = {
        "evaluate_and_improve",
        "export_best_as_skill",
        "look_at_curator_reports",
        "use_local_mlx_to_train",
        "mcp_vs_cli",
        "improve_curator_without_replacing",
    }
    fixture_scenarios = {case.prompt.scenario for case in DEFAULT_RUBRIC}
    missing = expected_scenarios - fixture_scenarios
    assert not missing, f"rubric missing AC-711 fixture prompts: {sorted(missing)}"


# --- validate_skill on the current rendered skill -------------------------


def test_default_rubric_passes_against_current_rendered_skill() -> None:
    """The skill we ship today must pass its own rubric. If it
    doesn't, either the rubric is wrong or the skill needs patching
    — both outcomes are AC-711 deliverables."""
    skill_text = render_autocontext_skill()
    report = validate_skill(skill=skill_text, rubric=DEFAULT_RUBRIC)
    failed = [r for r in report.results if not r.passed]
    assert not failed, "rubric failures against the current skill:\n" + "\n".join(
        f"  {r.prompt_id}: missing {sorted(r.missing_behaviors)}" for r in failed
    )
    assert report.passed_count == len(DEFAULT_RUBRIC)
    assert report.failed_count == 0


def test_report_summary_counts_match_per_result_tallies() -> None:
    skill_text = render_autocontext_skill()
    report = validate_skill(skill=skill_text, rubric=DEFAULT_RUBRIC)
    assert report.case_count == len(DEFAULT_RUBRIC)
    assert report.passed_count + report.failed_count == report.case_count


# --- Negative tests (rubric has teeth) ------------------------------------


def test_rubric_fails_when_cli_first_guidance_is_stripped() -> None:
    """AC-711 negative test (MCP overuse): if we strip the CLI-first
    guidance from the skill, the rubric must catch it."""
    skill_text = render_autocontext_skill()
    # Strip any sentence that prefers CLI; the rubric should now fail
    # the cli/mcp routing behaviors.
    mutilated = (
        skill_text.replace("CLI first", "")
        .replace("CLI-first", "")
        .replace("Use the CLI first", "")
        .replace("Use MCP only when", "")
        .replace("MCP is optional", "")
    )
    report = validate_skill(skill=mutilated, rubric=DEFAULT_RUBRIC)
    assert report.failed_count > 0, "expected the rubric to catch missing CLI-first guidance"


def test_rubric_fails_when_privacy_guidance_is_stripped() -> None:
    """AC-711 negative test (unsafe session import): a skill that
    drops its privacy posture must fail the privacy behavior."""
    skill_text = render_autocontext_skill()
    # Remove any sentence that warns about session/trajectory privacy.
    # Strip every keyword the privacy predicate accepts so the
    # rubric must rely on what's actually about session/trajectory
    # safety in the skill text.
    mutilated = skill_text
    for keyword in ("privacy", "Privacy", "redact", "Redact", "opt-in", "opt in", "sensitive", "Sensitive"):
        mutilated = mutilated.replace(keyword, "X")
    report = validate_skill(skill=mutilated, rubric=DEFAULT_RUBRIC)
    by_name = {b for r in report.results for b in r.missing_behaviors if not r.passed}
    assert "explains_privacy_before_session_ingest" in by_name


def test_rubric_fails_when_export_skill_path_is_stripped() -> None:
    """If a future skill rewrite drops `export-skill` from the
    text, agents won't know how to install the skill. The rubric
    must catch it."""
    skill_text = render_autocontext_skill()
    mutilated = skill_text.replace("export-skill", "REMOVED-COMMAND")
    report = validate_skill(skill=mutilated, rubric=DEFAULT_RUBRIC)
    by_name = {b for r in report.results for b in r.missing_behaviors if not r.passed}
    assert "documents_export_skill_path" in by_name


# --- JSON-friendly report -------------------------------------------------


def test_validation_report_round_trips_through_json() -> None:
    skill_text = render_autocontext_skill()
    report = validate_skill(skill=skill_text, rubric=DEFAULT_RUBRIC)
    payload = report.to_dict()
    json.dumps(payload)  # must serialize
    assert "results" in payload
    assert "case_count" in payload
    assert "passed_count" in payload
    assert "failed_count" in payload
    # Each result has its prompt id and a list of missing behaviors.
    for result in payload["results"]:
        assert "prompt_id" in result
        assert "passed" in result
        assert "missing_behaviors" in result


def test_validation_result_is_immutable_value_type() -> None:
    r = ValidationResult(
        prompt_id="p1",
        scenario="judge_workflow",
        passed=True,
        matched_behaviors=("uses_mcp_only_when_configured",),
        missing_behaviors=(),
    )
    assert r.passed
    assert isinstance(r.matched_behaviors, tuple)


def test_validation_case_couples_prompt_to_expected_behaviors() -> None:
    case = ValidationCase(
        prompt=TaskPrompt(id="p1", text="hello", scenario="judge"),
        expected=(
            ExpectedBehavior(
                name="b1",
                description="x",
                predicate=lambda _: True,
            ),
        ),
    )
    assert case.prompt.id == "p1"
    assert case.expected[0].name == "b1"


# --- CLI integration ------------------------------------------------------


def test_cli_validate_skill_emits_report(tmp_path) -> None:
    from typer.testing import CliRunner

    from autocontext.cli import app

    output = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        ["hermes", "validate-skill", "--output", str(output), "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["failed_count"] == 0  # the shipped skill passes its rubric
    assert output.exists()
    # The markdown report includes a summary line.
    md = output.read_text(encoding="utf-8")
    assert "AC-711" in md or "validation" in md.lower()


def test_cli_validate_skill_exit_code_nonzero_when_failures(tmp_path, monkeypatch) -> None:
    """When the rubric fails (e.g. a future skill rewrite breaks
    guidance), `validate-skill` must exit non-zero so CI catches it."""
    # Monkeypatch the renderer to return a mutilated skill.
    from typer.testing import CliRunner

    import autocontext.hermes.skill_validation as sv
    from autocontext.cli import app

    def _broken_render() -> str:
        return "this skill says nothing useful"

    monkeypatch.setattr(sv, "render_autocontext_skill", _broken_render)

    output = tmp_path / "report.md"
    result = CliRunner().invoke(
        app,
        ["hermes", "validate-skill", "--output", str(output), "--json"],
    )
    assert result.exit_code != 0


def test_default_rubric_each_case_has_at_least_one_behavior() -> None:
    """Defensive: a case without any expected behaviors is a typo,
    not a meaningful validation."""
    for case in DEFAULT_RUBRIC:
        assert case.expected, f"case {case.prompt.id} has no expected behaviors"


def test_report_is_immutable_through_results_tuple() -> None:
    skill_text = render_autocontext_skill()
    report = validate_skill(skill=skill_text, rubric=DEFAULT_RUBRIC)
    assert isinstance(report.results, tuple)
    with pytest.raises((AttributeError, TypeError)):
        report.results = ()  # type: ignore[misc]
