"""Retain one Linux/amd64 CPU search-robustness campaign attempt and prove replay without side effects.

The execution context is declared by the operator, not a hardware attestation.
An emulated Linux/amd64 run must be labelled as such. No GPU path is invoked.
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from autocontext.weight_evolution import (
    cpu_execution_journal,
    cpu_execution_search,
    cpu_measured_runner,
    cpu_robustness_campaign,
    session,
)
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_robustness_campaign_bundle import (
    build_cpu_robustness_campaign_bundle,
    validate_cpu_robustness_campaign_bundle,
)

CONTEXTS = ("github-actions-linux-amd64", "local-linux-amd64", "emulated-linux-amd64")
_MAX_FILES = 100_000
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_PROGRESS_INTERVAL_SECONDS = 60.0
_PROGRESS_MAX_ENTRIES = 60 + 16
_ORACLE_CHECK_COUNT = 915


class RobustnessAcceptanceError(RuntimeError):
    """The retained campaign does not satisfy its declared acceptance requirements."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _progress_counts(campaign: Path) -> dict[str, Any]:
    """Bounded directory observations only; never parse or validate evidence."""
    results = campaign / "results"
    counts = {"published_search_results": 0, "published_reference_results": 0}
    try:
        if results.is_symlink():
            return {"count_status": "unavailable"}
        with os.scandir(results) as entries:
            for index, entry in enumerate(entries):
                if index >= _PROGRESS_MAX_ENTRIES:
                    return {"count_status": "entry-bound-exceeded"}
                name = entry.name
                if (len(name) == 13 and name.endswith(".json") and name[:8].isascii()
                        and name[:8].isdigit() and int(name[:8]) < 60 and entry.is_file(follow_symlinks=False)):
                    key = "published_search_results" if int(name[:8]) < 45 else "published_reference_results"
                    counts[key] += 1
    except FileNotFoundError:
        return {"count_status": "not-created"}
    except OSError:
        return {"count_status": "unavailable"}
    return {"count_status": "observed", "published_parent_results": sum(counts.values()), **counts}


def _emit_progress(stage: str, event: str, *, campaign: Path | None = None, elapsed: float = 0.0) -> None:
    message: dict[str, Any] = {
        "kind": "cpu-search-robustness-progress", "stage": stage, "event": event, "at": _utc_now(),
        "elapsed_seconds": round(elapsed, 3), "authoritative": False, "used_for_acceptance": False,
        "counts_are_unverified_directory_observations": True,
    }
    if campaign is not None:
        message.update(_progress_counts(campaign))
    try:
        # Keep stdout reserved for the final acceptance receipt. Progress failure
        # cannot change the scientific result or suppress retained evidence.
        print(json.dumps(message, sort_keys=True), file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


@contextmanager
def _progress(stage: str, *, campaign: Path | None = None) -> Iterator[None]:
    """Report liveness with a read-only observer, including under replay guards."""
    started = time.monotonic()
    _emit_progress(stage, "started", campaign=campaign)
    stopped = threading.Event()

    def observe() -> None:
        while not stopped.wait(_PROGRESS_INTERVAL_SECONDS):
            _emit_progress(stage, "heartbeat", campaign=campaign, elapsed=time.monotonic() - started)

    observer = None
    if campaign is not None:
        candidate = threading.Thread(target=observe, name="cpu-search-robustness-advisory-progress", daemon=True)
        try:
            candidate.start()
        except RuntimeError:
            _emit_progress(stage, "observer-unavailable", campaign=campaign)
        else:
            observer = candidate
    event = "finished"
    try:
        yield
    except BaseException:
        event = "interrupted"
        raise
    finally:
        stopped.set()
        if observer is not None:
            # Advisory I/O must not indefinitely delay acceptance or exception
            # preservation. A delayed daemon can only finish its read/log call.
            observer.join(timeout=1.0)
        _emit_progress(stage, event, campaign=campaign, elapsed=time.monotonic() - started)


def _json_new(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    with path.open("xb") as stream:
        stream.write(encoded)


def _file_identity(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("acceptance evidence must contain only regular files")
    size = path.stat().st_size
    if size > _MAX_FILE_BYTES:
        raise ValueError("acceptance evidence file exceeds its byte bound")
    data = path.read_bytes()
    if len(data) != size:
        raise ValueError("acceptance evidence changed during hashing")
    return {"sha256": hashlib.sha256(data).hexdigest(), "size_bytes": size}


def _inventory(root: Path) -> dict[str, Any]:
    inventory: dict[str, Any] = {}
    total = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError("acceptance evidence cannot contain symlinks")
        if path.is_dir():
            continue
        if len(inventory) >= _MAX_FILES:
            raise ValueError("acceptance evidence exceeds its file bound")
        identity = _file_identity(path)
        total += identity["size_bytes"]
        if total > _MAX_TOTAL_BYTES:
            raise ValueError("acceptance evidence exceeds its total byte bound")
        inventory[path.relative_to(root).as_posix()] = identity
    return inventory


def _source_inventory() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    package = root / "autocontext" / "src" / "autocontext" / "weight_evolution"
    paths = [*package.glob("*.py"), *Path(__file__).resolve().parent.glob("*.py"),
             root / "autocontext" / "pyproject.toml", root / "autocontext" / "uv.lock",
             root / ".github" / "workflows" / "cpu-search-robustness.yml"]
    return {path.relative_to(root).as_posix(): _file_identity(path) for path in sorted(paths)}


def _environment(execution_context: str) -> dict[str, Any]:
    if execution_context not in CONTEXTS:
        raise ValueError("unknown CPU search-robustness campaign execution context")
    if platform.system() != "Linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise ValueError("declared search-robustness acceptance requires a Linux/amd64 process")
    if execution_context == "github-actions-linux-amd64" and (
        os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_OS") != "Linux"
        or os.environ.get("RUNNER_ARCH") != "X64"
    ):
        raise ValueError("GitHub acceptance requires the declared Linux/X64 runner metadata")
    root = Path(__file__).resolve().parents[3]
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True).stdout.strip()
    ci = {name: os.environ[name] for name in (
        "GITHUB_ACTIONS", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA", "GITHUB_EVENT_NAME",
        "RUNNER_OS", "RUNNER_ARCH", "ImageOS", "ImageVersion",
    ) if name in os.environ}
    return {"execution_context": execution_context, "context_is_operator_declared": True,
            "system": platform.system(), "machine": platform.machine(), "platform": platform.platform(),
            "python": sys.version, "python_implementation": platform.python_implementation(),
            "git_commit": commit, "ci": ci, "gpu_execution_deferred": True}


def _forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("guarded search-robustness replay attempted dispatch or mutation")


@contextmanager
def _readonly_guards() -> Iterator[None]:
    """Block worker launch, materialization, publication and filesystem mutation."""
    original_open, original_io_open, original_os_open = builtins.open, io.open, os.open

    def readonly_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            _forbid()
        return original_open(file, mode, *args, **kwargs)

    def readonly_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if any(flag in mode for flag in "wax+"):
            _forbid()
        return original_io_open(file, mode, *args, **kwargs)

    def readonly_os_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC):
            _forbid()
        return original_os_open(path, flags, *args, **kwargs)

    with ExitStack() as stack:
        for module, name in (
            (cpu_robustness_campaign, "_dispatch"), (cpu_robustness_campaign, "_immutable_json"),
            (cpu_execution_journal, "run_measured_cpu_batch"),
            (cpu_measured_runner, "run_measured_cpu_batch"),
            (cpu_execution_journal, "_immutable_json"), (cpu_execution_search, "_immutable_json"),
            (session, "_immutable_json"), (TensorCandidateRegistry, "materialize"),
            (subprocess, "Popen"),
        ):
            stack.enter_context(patch.object(module, name, _forbid))
        stack.enter_context(patch.object(builtins, "open", readonly_open))
        stack.enter_context(patch.object(io, "open", readonly_io_open))
        stack.enter_context(patch.object(os, "open", readonly_os_open))
        for name in ("mkdir", "makedirs", "unlink", "remove", "rename", "replace", "rmdir", "truncate", "chmod"):
            stack.enter_context(patch.object(os, name, _forbid))
        yield


def _verify_replay(bundle: Path, campaign: Path, report: dict[str, Any]) -> dict[str, Any]:
    before = {"bundle": _inventory(bundle), "campaign": _inventory(campaign)}
    with _readonly_guards():
        with _progress("report-replay", campaign=campaign):
            readonly = cpu_robustness_campaign.report_cpu_robustness_campaign(bundle, output_dir=campaign)
            if canonical_digest(readonly) != canonical_digest(report):
                raise RobustnessAcceptanceError("read-only report changed")
        # The coordinator seals report.json only after every scheduled decision.
        # A sealed partial search-robustness campaign may include failed searches and blocked refs.
        terminal_replayed = (campaign / "report.json").is_file()
        if terminal_replayed:
            with _progress("terminal-replay", campaign=campaign):
                replayed = cpu_robustness_campaign.run_cpu_robustness_campaign(bundle, output_dir=campaign)
                if canonical_digest(replayed) != canonical_digest(report):
                    raise RobustnessAcceptanceError("terminal campaign replay changed")
    after = {"bundle": _inventory(bundle), "campaign": _inventory(campaign)}
    if before != after:
        raise RobustnessAcceptanceError("guarded replay changed retained evidence")
    return {"report_digest": canonical_digest(report), "read_only_report_verified": True,
            "terminal_run_replay_verified": terminal_replayed, "dispatch_and_writes_blocked": True,
            "files_unchanged": True, "bundle_file_count": len(before["bundle"]),
            "campaign_file_count": len(before["campaign"]), "inventory": before}


def _expected_roster() -> dict[str, dict[str, Any]]:
    """Independent acceptance roster; task variants never count as new task instances."""
    variants = [(index, order, 3, "order-comparison") for index in range(1, 7)
                for order in ("canonical", "reverse-lanes")]
    variants += [(index, "canonical", 6, "full-budget") for index in (1, 3, 5)]
    families = ("informative-improvement-v1", "misleading-improvement-v1", "neutral-v1")
    expected = {}
    for index, order, rounds, panel in variants:
        task = f"task-{index:02}.{order}.r{rounds}"
        metadata = {"task_id": task, "base_task_id": f"task-{index:02}", "order_id": order,
                    "rounds": rounds, "panel": panel, "family": families[(index - 1) // 2],
                    "instance_index": (index - 1) % 2 + 1, "repeat_id": "repeat-01"}
        for policy in ("adaptive-v1", "fixed-order-v1", "seeded-random-v1"):
            expected[f"{task}.repeat-01.{policy}"] = {**metadata, "stage": "search", "policy": policy}
        expected[f"{task}.repeat-01"] = {**metadata, "stage": "reference"}
    return expected



def _expected_robustness_checks() -> set[tuple[str, str]]:
    expected = _expected_roster()
    identities = {(kind, f"task-{index:02}") for index in range(1, 7)
                  for kind in ("reference-score-tables", "seeded-random-declaration-order")}
    for run_id, row in expected.items():
        if row["stage"] != "search":
            continue
        identities.add(("neutral-context-decoding-anchors", run_id))
        if row["family"] == "neutral-v1":
            identities.add(("neutral-weight-null", run_id))
        if row["panel"] == "full-budget":
            identities.update((kind, run_id) for kind in (
                "full-budget-zero-practice-regret", "short-full-budget-history-prefix"))
    return identities

def _acceptance_requirements(report: dict[str, Any]) -> dict[str, bool]:
    """Require the pinned measured panel and finite oracle coverage without rewarding a policy."""
    expected = _expected_roster()
    requirements = {key: report.get(key) is True for key in (
        "evidence_integrity_verified", "campaign_completed", "acceptance_passed")}
    requirements["cpu_only_scope"] = (
        report.get("gpu_execution_deferred") is True and report.get("promotion_eligible") is False
        and report.get("benchmark_result_claimed") is False
    )
    statuses = ("completed", "failed", "uncertain", "blocked", "not-started")
    requirements["complete_fixed_roster"] = report.get("status_counts") == {
        stage: {status: count if status == "completed" else 0 for status in statuses}
        for stage, count in (("search", 45), ("reference", 15))
    }
    rows = report.get("rows", [])
    row_ids = [row.get("run_id") for row in rows]
    roster_valid = len(rows) == 60 and set(row_ids) == set(expected) and len(set(row_ids)) == 60
    if roster_valid:
        roster_valid = all(all(row.get(key) == value for key, value in expected[row["run_id"]].items()) for row in rows)
    requirements["exact_measured_variants"] = roster_valid
    calibration = report.get("calibration", {})
    scheduled = calibration.get("scheduled_checks")
    requirements["complete_oracle_calibration"] = (
        type(scheduled) is int and scheduled == _ORACLE_CHECK_COUNT and calibration.get("matches") == scheduled
        and calibration.get("mismatches") == 0 and calibration.get("unavailable_or_nondesignated") == 0
        and len(calibration.get("checks", [])) == scheduled
        and all(check.get("matches") is True for check in calibration.get("checks", []))
    )
    routing = report.get("audit_routing", {})
    requirements["complete_audit_routing"] = (
        routing.get("scheduled") == 45 and routing.get("verified") == 45
        and routing.get("mismatches") == 0 and routing.get("unavailable") == 0
        and routing.get("positive_count") == 0 and routing.get("veto_count") == 45
    )
    robustness = report.get("robustness_checks", {})
    checks = robustness.get("checks", [])
    requirements["complete_measured_robustness"] = (
        robustness.get("scheduled_checks") == 90 and robustness.get("matches") == 90
        and robustness.get("mismatches") == 0 and robustness.get("unavailable") == 0
        and robustness.get("aggregate_adaptive_win_required") is False
        and robustness.get("references_used_for_selection") is False
        and len(checks) == 90
        and {(check.get("check"), check.get("identity")) for check in checks} == _expected_robustness_checks()
        and all(check.get("matches") is True for check in checks)
    )
    offline = report.get("offline_expectations", {})
    invariants = offline.get("invariants", {})
    expected_invariants = dict.fromkeys(("score_tables_invariant", "random_order_invariant",
        "full_budget_zero_regret", "budget_prefixes", "neutral_null", "all_passed"), True)
    seeds = [{"policy": policy, "order_seed": seed} for policy in (
        "adaptive-v1", "fixed-order-v1", "seeded-random-v1") for seed in ((0, 1) if policy == "seeded-random-v1" else (0,))]
    task_summaries = offline.get("task_summaries", [])
    requirements["complete_offline_expectations"] = (
        offline.get("scope") == "finite-construction-expectations-not-measured-evidence"
        and offline.get("measured_evidence") is False and offline.get("verified") is True
        and offline.get("legal_order_count") == 90 and offline.get("trajectory_count") == 6480
        and offline.get("base_task_count") == 6 and offline.get("budgets") == [1, 3, 6]
        and offline.get("policy_seeds") == seeds
        and offline.get("used_for_policy_selection") is False and offline.get("population_generalization_claimed") is False
        and invariants == expected_invariants and all(value is True for value in invariants.values())
        and len(task_summaries) == 6
        and {task.get("base_task_id") for task in task_summaries} == {f"task-{index:02}" for index in range(1, 7)}
        and all(task.get("legal_order_count") == 90 and task.get("trajectory_count") == 1080
                and task.get("invariants") == expected_invariants for task in task_summaries)
    )
    curves = report.get("practice_prefix_curves", [])
    expected_curves = {(run_id, mechanism): metadata for run_id, metadata in expected.items()
                       if metadata["stage"] == "search" for mechanism in ("C", "W", "D")}
    curve_keys = [(curve.get("run_id"), curve.get("mechanism")) for curve in curves]
    curves_valid = len(curves) == 135 and set(curve_keys) == set(expected_curves)
    if curves_valid:
        for curve in curves:
            metadata = expected_curves[(curve["run_id"], curve["mechanism"])]
            rounds = metadata["rounds"]
            points = curve.get("points")
            curves_valid = curves_valid and (
                curve.get("available") is True and curve.get("additional_inference_performed") is False
                and all(curve.get(key) == metadata[key] for key in ("base_task_id", "order_id", "rounds", "panel"))
                and isinstance(points, list) and len(points) == rounds + 1
                and [point.get("trial") for point in points] == list(range(rounds + 1))
                and [point.get("practice_case_evaluations") for point in points] == [8 * (i + 1) for i in range(rounds + 1)]
            )
    requirements["complete_practice_prefix_curves"] = curves_valid
    coverage = report.get("process_coverage", {})
    process_rows = coverage.get("rows", [])
    requirements["complete_measured_cpu_processes"] = (
        coverage.get("scheduled_rows") == 60 and coverage.get("verified_rows") == 60
        and coverage.get("mismatches") == 0 and coverage.get("unavailable") == 0
        and coverage.get("expected_inference_batches") == 1311
        and coverage.get("observed_inference_batches") == 1311 and coverage.get("reaped_inference_batches") == 1311
        and len(process_rows) == 60 and {row.get("run_id") for row in process_rows} == set(expected)
        and all(row.get("matches") is True for row in process_rows)
    )
    valid = roster_valid
    veto = 0
    for row in rows:
        if row.get("status") != "completed" or row.get("result_published") is not True:
            valid = False
        audit = [observation for observation in (row.get("observation_coverage") or [])
                 if observation.get("phase") == "audit"]
        if row.get("stage") == "reference":
            valid = valid and not audit
            continue
        summary = row.get("summary") or {}
        gate = summary.get("confirmation") or {}
        outcomes = gate.get("outcomes") or {}
        valid = valid and gate.get("minimum_gain") == 0.1 and set(outcomes) == {"C", "W", "D"}
        valid = valid and all(type(value) is bool for value in outcomes.values())
        if gate.get("status") == "vetoed" and summary.get("audit_status") == "withheld":
            veto += 1
            valid = valid and not audit and outcomes.get("C") is False and outcomes.get("D") is False
        else:
            valid = False
    requirements["declared_veto_process_evidence"] = valid and veto == 45
    return requirements


def run_acceptance(output: Path, *, campaign_id: str, execution_context: str) -> dict[str, Any]:
    """Execute one fresh declared attempt; never resume or overwrite an older attempt."""
    environment = _environment(execution_context)
    if output.exists() or any(path.is_symlink() for path in (output.absolute(), *output.absolute().parents)):
        raise ValueError("acceptance requires a fresh directory without symlinks")
    output = output.resolve()
    output.mkdir(parents=True)
    sources = _source_inventory()
    _json_new(output / "attempt.json", {"schema_version": 1, "started_at": _utc_now(),
        "campaign_id": campaign_id, "environment": environment, "sources": sources,
        "schedule": {"searches": 45, "conditional_references": 15, "profile": "measured-v2"},
        "gpu_execution_deferred": True, "prior_attempt_replaced": False})
    try:
        bundle, campaign = output / "bundle", output / "campaign"
        with _progress("build"):
            build_cpu_robustness_campaign_bundle(bundle, campaign_id=campaign_id)
            _json_new(output / "validation.json", validate_cpu_robustness_campaign_bundle(bundle))
        with _progress("execute", campaign=campaign):
            report = cpu_robustness_campaign.run_cpu_robustness_campaign(bundle, output_dir=campaign)
        _json_new(output / "report.json", report)
        replay = _verify_replay(bundle, campaign, report)
        _json_new(output / "replay.json", replay)
        if sources != _source_inventory():
            raise RobustnessAcceptanceError("source snapshot changed during the declared attempt")
        requirements = _acceptance_requirements(report)
        accepted = all(requirements.values())
        receipt = {"schema_version": 1, "finished_at": _utc_now(), "campaign_id": campaign_id,
            "execution_context": execution_context, "report_digest": canonical_digest(report),
            "evidence_integrity_verified": report.get("evidence_integrity_verified"),
            "campaign_completed": report.get("campaign_completed"), "acceptance_passed": accepted,
            "requirements": requirements,
            "sources_unchanged": True, "replay_verified": True, "gpu_execution_deferred": True}
        _json_new(output / "acceptance.json", receipt)
        if not accepted:
            raise RobustnessAcceptanceError(
                "search-robustness campaign remains partial or fails calibration; retained evidence is not replaced")
    except BaseException as error:
        _json_new(output / "attempt-failure.json", {"schema_version": 1, "finished_at": _utc_now(),
            "exception_type": type(error).__name__, "gpu_execution_deferred": True,
            "partial_evidence_retained": True, "retry_performed": False})
        raise
    _emit_progress("complete", "finished", campaign=campaign)
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-id", default="cpu-search-robustness-v1")
    parser.add_argument("--execution-context", choices=CONTEXTS, required=True)
    args = parser.parse_args()
    print(json.dumps(run_acceptance(args.output, campaign_id=args.campaign_id,
                                   execution_context=args.execution_context), sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
