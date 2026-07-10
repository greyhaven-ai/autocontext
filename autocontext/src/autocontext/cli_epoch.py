"""``autoctx epoch`` commands: list candidate epochs and approve/reject them (AC-885 Slice C3).

The human trigger for the evaluator-epoch promotion workflow. approve/reject are pure human
overrides (``calibration_report=None``; the human is the authority, and C2 records the decision on
the epoch record). ``scenario`` keys the registry + sqlite; the charter target name is resolved
separately by selector (never conflate the two -- a valid charter forbids a target name that
collides with a registered scenario). See docs/ac-885-slice-c3-cli-enforcement-design.md.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer

from autocontext.ambient.charter import Charter, CharterTarget
from autocontext.ambient.charter_io import load_charter
from autocontext.ambient.eligibility import split_role_selector
from autocontext.config import load_settings
from autocontext.config.settings import AppSettings
from autocontext.execution.evaluator_epoch_promotion import ReviewerDecision, promote_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
from autocontext.execution.rubric_calibration import AlignmentTolerance
from autocontext.storage.sqlite_store import SQLiteStore

epoch_app = typer.Typer(help="Evaluator-epoch lifecycle: list candidates, approve or reject promotions.")


def _registry_for(settings: AppSettings) -> EvaluatorEpochRegistry:
    return EvaluatorEpochRegistry(settings.knowledge_root / "_evaluator_epochs")


def _all_scenarios(registry: EvaluatorEpochRegistry) -> list[str]:
    # Records are stored in per-scenario subdirs under the registry root (Slice C1).
    root = registry.root
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def _target_binds_scenario(target: CharterTarget, scenario: str) -> bool:
    """Whether a charter target's selector binds the given scenario.

    Selector semantics differ by target kind (a role selector uses ``role@scenario`` notation, a
    task-family selector is the scenario string itself), so parsing every selector as a role
    selector wrongly drops valid ``task_family`` targets whose selector has no ``@``.
    """
    if target.kind == "task_family":
        return target.selector == scenario
    # role: a scoped ``role@scenario`` binds only its named scenario; a bare ``role`` binds every one.
    _, target_scenario = split_role_selector(target.selector)
    return target_scenario is None or target_scenario == scenario


def _resolve_charter_target(charter: Charter, scenario: str) -> str:
    """Return the charter target NAME whose selector binds the given scenario.

    The promotion policy is looked up by charter target name, not by scenario; these are distinct
    keys. A valid charter forbids a target name that collides with a registered scenario, so passing
    the scenario in as the target name would raise inside ``decide``.

    Exactly one target must bind the scenario: zero is unresolvable, and more than one is ambiguous
    (silently promoting under the first target's autonomy would be a fail-open policy choice), so
    both raise rather than guess.
    """
    matches = [t.name for t in charter.targets if _target_binds_scenario(t, scenario)]
    if not matches:
        raise typer.BadParameter(f"no charter target selects scenario {scenario!r}")
    if len(matches) > 1:
        raise typer.BadParameter(
            f"charter is ambiguous for scenario {scenario!r}: targets {sorted(matches)!r} all bind it; "
            "scope their selectors (role@scenario) so exactly one target selects the scenario"
        )
    return matches[0]


@epoch_app.command("list")
def list_epochs(
    scenario: Annotated[str, typer.Option("--scenario", help="Limit to one scenario")] = "",
) -> None:
    """List evaluator-epoch records (candidate/active/disabled) as JSON."""
    registry = _registry_for(load_settings())
    scenarios = [scenario] if scenario else _all_scenarios(registry)
    records = [rec.model_dump(mode="json") for name in scenarios for rec in registry.snapshot_for_scenario(name)]
    typer.echo(json.dumps(records, indent=2))


def _decide(scenario: str, epoch_id: str, outcome: Literal["approved", "rejected"], by: str, charter_path: Path) -> None:
    settings = load_settings()
    charter = load_charter(charter_path)
    target_name = _resolve_charter_target(charter, scenario)
    decision = ReviewerDecision(outcome=outcome, reviewed_by=by, reviewed_at=datetime.now(UTC).isoformat())
    store = SQLiteStore(settings.db_path)
    store.ensure_core_tables()
    result = promote_evaluator_epoch(
        _registry_for(settings),
        scenario,
        epoch_id,
        target_name=target_name,
        calibration_report=None,
        tolerance=AlignmentTolerance.default_for_domain(scenario),
        charter=charter,
        reviewer_decision=decision,
        sqlite=store,
    )
    # The outcome must match the requested action. Because C2 reconciles an already-active
    # epoch as ``activated`` (to repair a crashed quarantine-clear), a bare "not noop" check would
    # let ``epoch reject <active-id>`` exit 0 reporting ``activated``. Require approve -> activated
    # and reject -> rejected; any other outcome (noop, activated-on-reject, pending_review, blocked)
    # is a non-zero failure so an operator never reads a contradictory result as success.
    expected = {"approved": "activated", "rejected": "rejected"}[outcome]
    typer.echo(json.dumps({"outcome": result.outcome, "reason": result.reason}, indent=2))
    if result.outcome != expected:
        raise typer.Exit(code=1)


_ScenarioArg = Annotated[str, typer.Argument(help="Registry scenario key, e.g. grid_ctf")]
_EpochArg = Annotated[str, typer.Argument(help="Candidate evaluator-epoch id")]
_ByOpt = Annotated[str, typer.Option("--by", help="Reviewer identity recorded on the decision")]
_CharterOpt = Annotated[Path, typer.Option("--charter", help="Path to the ambient charter yaml")]


@epoch_app.command("approve")
def approve(scenario: _ScenarioArg, epoch_id: _EpochArg, charter: _CharterOpt, by: _ByOpt = "operator") -> None:
    """Approve a candidate epoch (human override) and activate it."""
    _decide(scenario, epoch_id, "approved", by, charter)


@epoch_app.command("reject")
def reject(scenario: _ScenarioArg, epoch_id: _EpochArg, charter: _CharterOpt, by: _ByOpt = "operator") -> None:
    """Reject a candidate epoch; it stays a candidate and its scores stay quarantined."""
    _decide(scenario, epoch_id, "rejected", by, charter)
