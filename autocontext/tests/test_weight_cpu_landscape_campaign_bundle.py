from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_landscape_campaign_bundle as campaign
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_campaign import METERS


def snapshot(root: Path) -> dict[str, Any]:
    return {str(path.relative_to(root)): file_identity(path) for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture(scope="module")
def original(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("landscape-campaign-contract") / "bundle"
    campaign.build_cpu_landscape_campaign_bundle(directory)
    return directory


@pytest.fixture
def bundle(tmp_path: Path, original: Path) -> Path:
    directory = tmp_path / "bundle"
    shutil.copytree(original, directory)
    return directory


def repin(path: Path, *, lock: Path, prefix: str, value: dict[str, Any]) -> None:
    encoded = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path.write_bytes(encoded)
    raw = json.loads(lock.read_bytes())
    raw[f"{prefix}_sha256"] = hashlib.sha256(encoded).hexdigest()
    raw[f"{prefix}_size_bytes"] = len(encoded)
    lock.write_text(json.dumps(raw))


def test_fixed_schedule_equal_policy_opportunities_and_separate_budgets(original: Path) -> None:
    spec, control, templates, runtime = campaign.load_cpu_landscape_campaign_bundle(original)
    validation = campaign.validate_cpu_landscape_campaign_bundle(original)
    rows = validation["schedule"]
    assert validation["searches"] == 36 and validation["conditional_references"] == 12
    assert validation["task_count"] == 6 and len(rows) == 48
    assert not validation["model_execution_performed"] and validation["gpu_execution_deferred"]
    assert not validation["promotion_eligible"]
    assert {row["stage"] for row in rows[:36]} == {"search"}
    assert {row["stage"] for row in rows[36:]} == {"reference"}
    assert {row["profile"] for row in rows} == {"measured-v2"}
    assert len({(row["stage"], row["run_id"]) for row in rows}) == 48
    assert spec.schedule_digest == canonical_digest(rows)
    assert spec.execution_implementation_digest == runtime["implementation_digest"]
    assert spec.implementation_digest == campaign.landscape_campaign_implementation_digest()
    assert spec.implementation_digest != spec.execution_implementation_digest
    assert len(control.repeats) == 2 and len(control.policies) == 3
    assert len({task.family for task in control.tasks}) == 3
    assert {row["instance_index"] for row in rows} == {1, 2}
    for stage, budget in (("search", spec.search_budget), ("reference", spec.reference_budget)):
        assert {meter: sum(row["reserved_capacity"][meter] for row in rows if row["stage"] == stage)
                for meter in METERS} == {meter: getattr(budget, f"max_{meter}") for meter in METERS}
    for task in control.tasks:
        template = templates[task.task_id]
        assert all(row["instance_index"] == task.instance_index for row in rows if row["task_id"] == task.task_id)
        assert template.rounds == 3 and template.minimum_confirmation_gain == 0.1
        assert [len(template.options_for(mechanism)) for mechanism in ("C", "W", "D")] == [6, 6, 6]
        for repeat in control.repeats:
            searches = [r for r in rows[:36] if (r["task_id"], r["repeat_id"]) == (task.task_id, repeat.repeat_id)]
            assert tuple(r["policy"] for r in searches) == control.policies
            assert all(r["inference_seed"] == repeat.inference_seed for r in searches)
            assert len({canonical_digest(r["reserved_capacity"]) for r in searches}) == 1
            reference = next(r for r in rows[36:] if (r["task_id"], r["repeat_id"]) == (task.task_id, repeat.repeat_id))
            assert reference["reserved_capacity"]["proposals"] == 0
            count = sum(c.role == "practice" for c in template.cases)
            assert reference["reserved_capacity"]["inference_calls"] == 19 * count


def test_build_validate_are_deterministic_readonly_and_never_launch_workers(original: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import cpu_measured_runner, cpu_runner

    def forbid(*args: Any, **kwargs: Any) -> None:
        pytest.fail("bundle construction or validation dispatched inference")

    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_measured_runner, "run_measured_cpu_batch", forbid)
    directory = tmp_path / "copy"
    campaign.build_cpu_landscape_campaign_bundle(directory)
    assert snapshot(directory) == snapshot(original)
    before = snapshot(directory)
    campaign.validate_cpu_landscape_campaign_bundle(directory)
    assert snapshot(directory) == before


@pytest.mark.parametrize("field,value", [
    ("roster_digest", "0" * 64), ("schedule_digest", "0" * 64),
    ("implementation_digest", "0" * 64), ("execution_implementation_digest", "0" * 64),
    ("native_runtime_digest", "0" * 64), ("measured_profile_digest", "0" * 64),
    ("control_campaign_digest", "0" * 64), ("campaign_id", "changed"),
    ("kind", "measured-cpu-control-pilot-v1"),
    ("kind", "measured-cpu-joint-campaign-v1"),
])
def test_rehashed_pin_or_prior_milestone_identity_is_rejected(bundle: Path, field: str, value: Any) -> None:
    raw = json.loads((bundle / "landscape-campaign.json").read_bytes())
    raw[field] = value
    repin(bundle / "landscape-campaign.json", lock=bundle / "bundle.lock.json", prefix="spec", value=raw)
    with pytest.raises(ValueError):
        campaign.validate_cpu_landscape_campaign_bundle(bundle)


@pytest.mark.parametrize("stage", ["search", "reference"])
@pytest.mark.parametrize("meter", METERS)
def test_rehashed_capacity_cannot_expand_or_refund(bundle: Path, stage: str, meter: str) -> None:
    raw = json.loads((bundle / "landscape-campaign.json").read_bytes())
    raw[f"{stage}_budget"][f"max_{meter}"] += 1
    repin(bundle / "landscape-campaign.json", lock=bundle / "bundle.lock.json", prefix="spec", value=raw)
    with pytest.raises(ValueError):
        campaign.validate_cpu_landscape_campaign_bundle(bundle)


def test_rehashed_runtime_is_not_a_substitute_for_current_sources(bundle: Path) -> None:
    raw = json.loads((bundle / "runtime.json").read_bytes())
    raw["implementation_digest"] = "0" * 64
    repin(bundle / "runtime.json", lock=bundle / "bundle.lock.json", prefix="runtime", value=raw)
    with pytest.raises(ValueError, match="runtime or source"):
        campaign.validate_cpu_landscape_campaign_bundle(bundle)


@pytest.mark.parametrize("location", ["", "campaign", "campaign/tasks", "campaign/tasks/task-01"])
def test_exact_bounded_inventory_rejects_unowned_assets(bundle: Path, location: str) -> None:
    (bundle / location / "unexpected").write_text("extra")
    with pytest.raises(ValueError):
        campaign.validate_cpu_landscape_campaign_bundle(bundle)


def test_changed_checkpoint_is_rejected(bundle: Path) -> None:
    checkpoint = bundle / "campaign/tasks/task-01/base.safetensors"
    original = checkpoint.read_bytes()
    checkpoint.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    with pytest.raises(ValueError):
        campaign.validate_cpu_landscape_campaign_bundle(bundle)


def test_no_overwrite_or_symlinked_build_target(original: Path, tmp_path: Path) -> None:
    before = snapshot(original)
    with pytest.raises(ValueError, match="fresh"):
        campaign.build_cpu_landscape_campaign_bundle(original)
    assert snapshot(original) == before
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        campaign.build_cpu_landscape_campaign_bundle(alias / "new")
