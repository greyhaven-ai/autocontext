"""Pinned, bounded CPU search campaigns with a complete retained schedule.

Each scheduled search reserves its full possible cost before dispatch. Child
search journals determine whether interrupted work is safely resumable; failed
or ambiguous child operations are never retried. Reports verify retained bytes
and replay completed searches without issuing model requests.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignLock, CpuCampaignSpec
from autocontext.weight_evolution.cpu_campaign_report import build_cpu_campaign_report
from autocontext.weight_evolution.cpu_campaign_tasks import validate_task_provenance
from autocontext.weight_evolution.cpu_runner import runtime_identity
from autocontext.weight_evolution.cpu_search import (
    CpuSearchBlockedError,
    load_cpu_search,
    run_cpu_search,
    search_implementation_digest,
    validate_cpu_search,
    verify_cpu_search,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchBundleLock, CpuSearchSpec
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same

METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")


class CpuCampaignError(ValueError):
    """Campaign inputs, ownership, or retained evidence are invalid."""


def campaign_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_campaign.py", "cpu_campaign_models.py", "cpu_campaign_tasks.py", "cpu_campaign_report.py")
    return canonical_digest({
        "campaign": {name: file_identity(package / name) for name in names},
        "search": search_implementation_digest(),
        "statistics": file_identity(package.parent / "analytics" / "paired_statistics.py"),
        "locking": file_identity(package.parent / "util" / "file_lock.py"),
    })


def _safe(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise CpuCampaignError("campaign paths must not traverse symlinks")


def _pin(path: Path, digest: str, size: int) -> None:
    _safe(path.absolute())
    actual = file_identity(path, max_bytes=size)
    if actual["sha256"] != digest or actual["size_bytes"] != size:
        raise CpuCampaignError("campaign asset byte/hash pin mismatch")


def _load(directory: Path) -> tuple[CpuCampaignSpec, dict[str, CpuSearchSpec]]:
    _safe(directory.absolute())
    raw_lock = load_small_json(directory / "bundle.lock.json")
    lock = CpuCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "campaign lock changed its exact representation")
    _pin(directory / "campaign.json", lock.spec_sha256, lock.spec_size_bytes)
    raw = load_small_json(directory / "campaign.json")
    spec = CpuCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "campaign contract changed its exact representation")
    if spec.implementation_digest != campaign_implementation_digest():
        raise CpuCampaignError("campaign implementation source changed")
    templates: dict[str, CpuSearchSpec] = {}
    capacities: list[dict[str, Any]] = []
    identities: set[str] = set()
    for task in spec.tasks:
        bundle = directory / "tasks" / task.task_id
        _pin(bundle / "bundle.lock.json", task.bundle_lock_sha256, task.bundle_lock_size_bytes)
        _pin(bundle / "task.provenance.json", task.provenance_sha256, task.provenance_size_bytes)
        validate_cpu_search(bundle)
        template = load_cpu_search(bundle)
        if template.policy.name != "adaptive-v1" or template.evaluation_seed != 0:
            raise CpuCampaignError("task templates require the canonical seed-zero adaptive policy")
        provenance = validate_task_provenance(bundle, template)
        if (provenance.task_id != task.task_id or provenance.generation_seed != task.generation_seed
                or provenance.family != task.family):
            raise CpuCampaignError("task generation identity differs from its campaign descriptor")
        # A task is a model/problem instance, not its label, seed alias, or file path.
        identity = provenance.semantic_digest
        if identity in identities:
            raise CpuCampaignError("campaign repeats a task instance under a different identity")
        identities.add(identity)
        capacities.append({"rounds": template.rounds, "budget": template.budget.to_dict(),
                           "limits": template.limits.to_dict(), "gain": template.minimum_confirmation_gain,
                           "case_counts": [sum(c.role == r for c in template.cases)
                                           for r in ("practice", "confirmation", "audit")],
                           "options": [len(template.options_for(m)) for m in ("C", "W", "D")]})
        templates[task.task_id] = template
    if any(capacity != capacities[0] for capacity in capacities):
        raise CpuCampaignError("campaign tasks must use equal caps, rounds, coverage and confirmation rules")
    required = {meter: sum(t.required_usage[meter] for t in templates.values()) * len(spec.repeats) * len(spec.policies)
                for meter in METERS}
    if any(value > getattr(spec.budget, f"max_{meter}") for meter, value in required.items()):
        raise CpuCampaignError("campaign cannot reserve the complete predeclared schedule")
    return spec, templates


def load_cpu_campaign(directory: Path) -> CpuCampaignSpec:
    return _load(directory)[0]


def _schedule(spec: CpuCampaignSpec, templates: dict[str, CpuSearchSpec]) -> list[tuple[dict[str, Any], CpuSearchSpec]]:
    schedule = []
    for task in spec.tasks:
        for repeat in spec.repeats:
            for policy in spec.policies:
                run_id = f"{task.task_id}.{repeat.repeat_id}.{policy}"
                raw = templates[task.task_id].to_dict()
                raw["study_id"] = run_id
                raw["evaluation_seed"] = repeat.inference_seed
                raw["baseline_decoding"]["seed"] = repeat.inference_seed
                raw["policy"] = {"name": policy,
                                 "order_seed": repeat.search_order_seed if policy == "seeded-random-v1" else 0}
                for option in raw["options"]:
                    if option["decoding"] is not None:
                        option["decoding"]["seed"] = repeat.inference_seed
                child = CpuSearchSpec.model_validate_json(json.dumps(raw))
                row = {"run_id": run_id, "task_id": task.task_id, "repeat_id": repeat.repeat_id,
                       "policy": policy, "inference_seed": repeat.inference_seed,
                       "order_seed": raw["policy"]["order_seed"], "study_digest": child.digest,
                       "reserved_capacity": child.required_usage}
                schedule.append((row, child))
    return schedule


def validate_cpu_campaign(directory: Path) -> dict[str, Any]:
    spec, templates = _load(directory)
    schedule = _schedule(spec, templates)
    return {"campaign_digest": spec.digest, "task_count": len(spec.tasks), "run_count": len(schedule),
            "required_usage": {m: sum(row["reserved_capacity"][m] for row, _ in schedule) for m in METERS},
            "policies": list(spec.policies), "model_execution_performed": False,
            "gpu_execution_deferred": True, "scope": "validation-only-no-proposals-or-inference"}


def _encoded(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _bundle_bytes(directory: Path, task_id: str, child: CpuSearchSpec) -> dict[str, bytes]:
    encoded = _encoded(child.to_dict())
    lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
                               base_sha256=child.baseline.output_sha256,
                               base_size_bytes=child.baseline.output_size_bytes)
    return {"study.json": encoded, "bundle.lock.json": _encoded(lock.to_dict()),
            "base.safetensors": (directory / "tasks" / task_id / "base.safetensors").read_bytes()}


def _derived_bundle(path: Path, expected: dict[str, bytes], *, write: bool) -> None:
    _safe(path)
    if not path.exists():
        if not write:
            raise CpuCampaignError("claimed search is missing its derived input bundle")
        path.mkdir()
    if write:
        _clear_staging(path)
    if {p.name for p in path.iterdir()} - set(expected):
        raise CpuCampaignError("derived search bundle contains unowned bytes")
    for name, content in expected.items():
        target = path / name
        if target.exists() or target.is_symlink():
            with regular_file(target) as stream:
                if stream.read(len(content) + 1) != content:
                    raise CpuCampaignError("derived search bundle differs from its pinned schedule")
        elif not write:
            raise CpuCampaignError("derived search bundle is incomplete")
        else:
            fd, temporary = tempfile.mkstemp(prefix=".session-publish-", dir=path)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temporary, target)
                directory_fd = os.open(path, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                os.unlink(temporary)


def _inventory(root: Path) -> dict[str, Any]:
    """Bounded byte inventory also seals failure evidence and lock existence."""
    if not root.exists():
        return {}
    paths: list[Path] = []
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if len(paths) == 1024:
                    raise CpuCampaignError("child evidence exceeds its file bound")
                path = Path(entry.path)
                _safe(path)
                paths.append(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    result: dict[str, Any] = {}
    for path in sorted(paths):
        name = str(path.relative_to(root))
        result[name] = {"directory": True} if path.is_dir() else file_identity(path, max_bytes=4 * 1024 * 1024)
    return result


@contextmanager
def _locked(root: Path, *, read_only: bool) -> Iterator[None]:
    lock = root / ".cpu-campaign.lock"
    _safe(root)
    if read_only:
        with regular_file(lock) as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    else:
        root.mkdir(parents=True, exist_ok=True)
        if lock.exists() or lock.is_symlink():
            with regular_file(lock):
                pass
        with advisory_path_lock(lock):
            yield


def _prepare(root: Path, manifest: dict[str, Any], *, write: bool) -> int:
    if write:
        _clear_staging(root)
    allowed = {".cpu-campaign.lock", "manifest.json", "claims", "results", "bundles", "searches", "report.json"}
    entries = {path.name for path in root.iterdir()}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-campaign.lock"}):
        raise CpuCampaignError("campaign output contains unowned evidence")
    if "manifest.json" in entries:
        _same(_read_record(root / "manifest.json"), manifest, "campaign manifest or runtime changed")
    elif write:
        _immutable_json(root / "manifest.json", manifest)
    else:
        raise CpuCampaignError("campaign manifest is missing")
    for name in ("claims", "results", "bundles", "searches"):
        path = root / name
        _safe(path)
        if write:
            path.mkdir(exist_ok=True)
            _clear_staging(path)
        if not path.is_dir():
            raise CpuCampaignError("campaign evidence directory is missing")
    claims = {p.name for p in (root / "claims").iterdir()}
    results = {p.name for p in (root / "results").iterdir()}
    if claims != {f"{i:08d}.json" for i in range(len(claims))} or not results <= claims:
        raise CpuCampaignError("campaign claims are not a contiguous schedule prefix")
    if results != {f"{i:08d}.json" for i in range(len(results))} or len(claims) - len(results) > 1:
        raise CpuCampaignError("campaign has skipped a scheduled run result")
    return len(claims)


def _completed_row(row: dict[str, Any], summary: dict[str, Any], child_root: Path) -> dict[str, Any]:
    confirmation = _read_record(child_root / "reports" / "confirmation.json")
    audit = (_read_record(child_root / "reports" / "audit.json")
             if summary["audit_status"] == "completed" else None)
    return {**row, "status": "completed", "charged_usage": summary["reserved_usage"],
            "summary_semantic_digest": summary["semantic_digest"],
            "confirmation": summary["confirmation"]["status"],
            "confirmation_scores": {k: v["accuracy"] for k, v in confirmation["conditions"].items()},
            "audit_scores": {k: v["accuracy"] for k, v in audit["conditions"].items()} if audit else None}


def _run_or_report(directory: Path, root: Path, *, execute: bool) -> dict[str, Any]:
    spec, templates = _load(directory)
    schedule = _schedule(spec, templates)
    runtime = runtime_identity()
    runtime_digest = canonical_digest(runtime)
    manifest = {"schema_version": 1, "campaign": spec.to_dict(), "campaign_digest": spec.digest,
                "runtime": runtime, "runtime_digest": runtime_digest,
                "schedule": [row for row, _ in schedule]}
    with _locked(root, read_only=not execute):
        claimed = _prepare(root, manifest, write=execute)
        if claimed > len(schedule):
            raise CpuCampaignError("campaign retained more claims than scheduled runs")
        allowed_runs = {row["run_id"] for row, _ in schedule[:claimed]}
        for folder in ("bundles", "searches"):
            if {p.name for p in (root / folder).iterdir()} - allowed_runs:
                raise CpuCampaignError("campaign retains an unclaimed child search")
        previous = "0" * 64
        rows = []
        # Verify the entire retained prefix before performing any further work.
        pending: list[tuple[int, dict[str, Any], CpuSearchSpec, dict[str, Any]]] = []
        for index, (row, child) in enumerate(schedule):
            claim = {"schema_version": 1, "campaign_digest": spec.digest, "runtime_digest": runtime_digest,
                     "index": index, "run": row, "previous_result_digest": previous}
            claim_path = root / "claims" / f"{index:08d}.json"
            result_path = root / "results" / claim_path.name
            bundle = root / "bundles" / row["run_id"]
            child_root = root / "searches" / row["run_id"]
            if index < claimed:
                _same(_read_record(claim_path), claim, "campaign claim differs from its schedule")
                if result_path.exists():
                    _derived_bundle(bundle, _bundle_bytes(directory, row["task_id"], child), write=False)
                    result = _read_record(result_path)
                    if result.get("claim_digest") != canonical_digest(claim):
                        raise CpuCampaignError("campaign result is not bound to its claim")
                    if result.get("evidence_digest") != canonical_digest(_inventory(child_root)):
                        raise CpuCampaignError("retained child evidence changed or disappeared")
                    if result.get("status") == "completed":
                        summary = verify_cpu_search(bundle, output_dir=child_root)
                        actual_row = _completed_row(row, summary, child_root)
                        _same(result, {"claim_digest": canonical_digest(claim), "status": "completed",
                                       "evidence_digest": canonical_digest(_inventory(child_root)),
                                       "row": actual_row}, "completed campaign result differs from verified child")
                    elif result.get("status") == "failed":
                        actual_row = {**row, "status": "failed", "charged_usage": None,
                                      "error_type": "CpuSearchBlockedError", "confirmation": None,
                                      "confirmation_scores": None, "audit_scores": None}
                        _same(result, {"claim_digest": canonical_digest(claim), "status": "failed",
                                       "evidence_digest": canonical_digest(_inventory(child_root)),
                                       "row": actual_row}, "failed campaign result has invalid fields")
                    else:
                        raise CpuCampaignError("campaign result has an invalid status")
                    rows.append(actual_row)
                    previous = canonical_digest(result)
                    continue
            rows.append({**row, "status": "pending" if index < claimed else "not-started", "charged_usage": None,
                         "confirmation": None, "confirmation_scores": None, "audit_scores": None})
            pending.append((index, row, child, claim))
        if pending and (root / "report.json").exists():
            raise CpuCampaignError("completed campaign report conflicts with an incomplete schedule")
        if execute:
            for index, row, child, _ in pending:
                claim = {"schema_version": 1, "campaign_digest": spec.digest, "runtime_digest": runtime_digest,
                         "index": index, "run": row, "previous_result_digest": previous}
                _immutable_json(root / "claims" / f"{index:08d}.json", claim)
                bundle = root / "bundles" / row["run_id"]
                child_root = root / "searches" / row["run_id"]
                _derived_bundle(bundle, _bundle_bytes(directory, row["task_id"], child), write=True)
                try:
                    summary = run_cpu_search(bundle, output_dir=child_root)
                    actual_row = _completed_row(row, summary, child_root)
                except CpuSearchBlockedError:
                    # No automatic retry of a failed/uncertain dispatch. Retain
                    # all bytes and burn its full reservation; continue schedule.
                    actual_row = {**row, "status": "failed", "charged_usage": None,
                                  "error_type": "CpuSearchBlockedError", "confirmation": None,
                                  "confirmation_scores": None, "audit_scores": None}
                result = {"claim_digest": canonical_digest(claim), "status": actual_row["status"],
                          "evidence_digest": canonical_digest(_inventory(child_root)), "row": actual_row}
                _immutable_json(root / "results" / f"{index:08d}.json", result)
                rows[index] = actual_row
                previous = canonical_digest(result)
        report = build_cpu_campaign_report(spec, rows, runtime_digest=runtime_digest)
        report["ledger_digest"] = previous
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "campaign report differs from verified ledger")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Run/resume every pinned search, keeping failed reservations charged."""
    return _run_or_report(directory.absolute(), output_dir.absolute(), execute=True)


def report_cpu_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Read-only verified report; never dispatches, materializes, or writes."""
    return _run_or_report(directory.absolute(), output_dir.absolute(), execute=False)
