"""Pinned observation plans must reserve the complete declared experiment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.weight_evolution import cpu_failure_campaign
from autocontext.weight_evolution.cpu_control_tasks import build_cpu_control_campaign
from autocontext.weight_evolution.cpu_failure_campaign import (
    build_cpu_failure_campaign,
    run_cpu_failure_campaign,
    validate_cpu_failure_campaign,
)
from autocontext.weight_evolution.cpu_failure_campaign_models import CpuFailureCampaignSpec


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("contract validation dispatched a search")


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    build_cpu_failure_campaign(root, preset="fixture", campaign_id="contract-fixture-v1")
    return root


def repin(root: Path, raw: dict[str, Any]) -> None:
    encoded = (json.dumps(raw, sort_keys=True, indent=2) + "\n").encode()
    (root / "supervisor.json").write_bytes(encoded)
    lock = json.loads((root / "bundle.lock.json").read_text())
    lock.update(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
    (root / "bundle.lock.json").write_text(json.dumps(lock))


def test_builder_is_execution_free_and_preserves_task_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    root, old = tmp_path / "bundle", tmp_path / "native"
    spec = build_cpu_failure_campaign(root, campaign_id="contract-fixture-v1")
    build_cpu_control_campaign(old, campaign_id="contract-fixture-v1")
    expected = {str(p.relative_to(old)): p.read_bytes() for p in old.rglob("*") if p.is_file()}
    actual = {str(p.relative_to(root / "campaign")): p.read_bytes()
              for p in (root / "campaign").rglob("*") if p.is_file()}
    assert actual == expected
    report = validate_cpu_failure_campaign(root)
    assert report["search_count"] == 18
    assert report["conditional_reference_budget"]["max_references"] == 6
    assert report["conditional_reference_budget"]["max_inference_calls"] == 624
    assert report["runtime_matches"] is True
    assert report["model_execution_performed"] is False
    assert spec.failure_contract.retry_permitted is False
    assert spec.failure_contract.refund_permitted is False


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", 2), ("gpu_execution_deferred", 1),
    ("gpu_execution_deferred", False), ("promotion_eligible", 0), ("promotion_eligible", True),
    ("dispatch_rule", "retry-until-success"), ("reference_rule", "ignore-failed-searches"),
    ("campaign_id", "../escape"), ("runtime_digest", "invalid"), ("implementation_digest", "invalid"),
])
def test_contract_rejects_changed_scope_and_coercion(bundle: Path, field: str, value: Any) -> None:
    raw = json.loads((bundle / "supervisor.json").read_text())
    raw[field] = value
    with pytest.raises(ValidationError):
        CpuFailureCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize(("budget", "meter"), [
    ("search_budget", "max_runs"), ("search_budget", "max_inference_calls"),
    ("reference_budget", "max_references"), ("reference_budget", "max_input_tokens"),
])
def test_rehashed_capacity_change_is_rejected(bundle: Path, budget: str, meter: str,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    raw = json.loads((bundle / "supervisor.json").read_text())
    raw[budget][meter] += 1
    repin(bundle, raw)
    with pytest.raises(ValueError, match="capacity"):
        validate_cpu_failure_campaign(bundle)


def test_runtime_mismatch_never_creates_output(bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = cpu_failure_campaign.runtime_identity()
    monkeypatch.setattr(cpu_failure_campaign, "runtime_identity", lambda: {**runtime, "machine": "other-host"})
    monkeypatch.setattr(cpu_failure_campaign, "run_cpu_search", forbid)
    assert validate_cpu_failure_campaign(bundle)["runtime_matches"] is False
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="pinned host/runtime"):
        run_cpu_failure_campaign(bundle, output_dir=output)
    assert not output.exists()


def test_existing_bundle_is_never_replaced(bundle: Path) -> None:
    before = (bundle / "supervisor.json").read_bytes()
    with pytest.raises(ValueError, match="fresh"):
        build_cpu_failure_campaign(bundle)
    assert (bundle / "supervisor.json").read_bytes() == before


def test_unowned_output_rejected_before_lock_creation(bundle: Path, tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "unowned").write_text("preserve")
    with pytest.raises(ValueError, match="unowned"):
        run_cpu_failure_campaign(bundle, output_dir=output)
    assert {p.name for p in output.iterdir()} == {"unowned"}
