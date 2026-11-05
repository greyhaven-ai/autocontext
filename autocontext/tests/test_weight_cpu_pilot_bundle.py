from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_pilot_bundle as pilot
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.cpu_campaign import METERS
from autocontext.weight_evolution.cpu_control_models import CONTROL_FAMILIES
from autocontext.weight_evolution.cpu_control_tasks import CONTROL_REPEATS, CONTROL_TASK_SEEDS, build_cpu_control_campaign


def snapshot(root: Path) -> dict[str, Any]:
    return {str(path.relative_to(root)): file_identity(path) for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.fixture(scope="module")
def original(tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("pilot-contract") / "bundle"
    pilot.build_cpu_pilot_bundle(directory)
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


def test_full_measured_schedule_and_exact_separate_capacity(original: Path) -> None:
    spec, control, templates, runtime = pilot.load_cpu_pilot_bundle(original)
    validation = pilot.validate_cpu_pilot_bundle(original)
    rows = validation["schedule"]
    assert validation["searches"] == 72 and validation["conditional_references"] == 24
    assert validation["task_count"] == 12 and len(rows) == 96
    assert validation["model_execution_performed"] is False
    assert validation["gpu_execution_deferred"] and not validation["promotion_eligible"]
    assert {row["profile"] for row in rows} == {"measured-v2"}
    assert {row["stage"] for row in rows[:72]} == {"search"}
    assert {row["stage"] for row in rows[72:]} == {"reference"}
    assert len({(row["stage"], row["run_id"]) for row in rows}) == 96
    assert spec.schedule_digest == canonical_digest(rows)
    assert spec.execution_implementation_digest == runtime["implementation_digest"]
    assert spec.implementation_digest == pilot.pilot_implementation_digest()
    assert spec.implementation_digest != spec.execution_implementation_digest
    assert control.repeats == CONTROL_REPEATS
    assert [(task.family, task.generation_seed) for task in control.tasks] == [
        (family, seed) for family in CONTROL_FAMILIES for seed in CONTROL_TASK_SEEDS[family]]
    for family in CONTROL_FAMILIES:
        assert sum(row["family"] == family for row in rows[:72]) == 24
        assert sum(row["family"] == family for row in rows[72:]) == 8
    for stage, budget in (("search", spec.search_budget), ("reference", spec.reference_budget)):
        assert {meter: sum(row["reserved_capacity"][meter] for row in rows if row["stage"] == stage)
                for meter in METERS} == {meter: getattr(budget, f"max_{meter}") for meter in METERS}
    for task in control.tasks:
        template = templates[task.task_id]
        assert template.rounds == 3 and template.minimum_confirmation_gain == 0.1
        assert template.limits.memory_mb == 512 and template.limits.wall_seconds == 5
        assert template.limits.max_input_tokens == 14
        assert [sum(case.role == role for case in template.cases)
                for role in ("practice", "confirmation", "audit")] == [8, 8, 8]
        assert [len(template.options_for(mechanism)) for mechanism in ("C", "W", "D")] == [4, 4, 4]
        for repeat in control.repeats:
            searches = [row for row in rows[:72] if row["task_id"] == task.task_id and row["repeat_id"] == repeat.repeat_id]
            assert tuple(row["policy"] for row in searches) == control.policies
            assert all(row["inference_seed"] == repeat.inference_seed for row in searches)
            assert len({canonical_digest(row["reserved_capacity"]) for row in searches}) == 1
            reference = next(row for row in rows[72:] if row["task_id"] == task.task_id and row["repeat_id"] == repeat.repeat_id)
            assert reference["inference_seed"] == repeat.inference_seed
            assert reference["reserved_capacity"]["proposals"] == 0
            assert reference["reserved_capacity"]["materializations"] == 5
            assert reference["reserved_capacity"]["inference_calls"] == 13 * 8


def test_offline_capacity_and_schedule_golden_fixture(original: Path) -> None:
    fixture = json.loads((Path(__file__).parent / "fixtures" / "weight_reproduction" /
                          "cpu_pilot_schedule.json").read_bytes())
    spec, control, templates, _ = pilot.load_cpu_pilot_bundle(original)
    rows = pilot.pilot_schedule(control, templates)
    assert len(control.tasks) == fixture["tasks"]
    assert len(control.repeats) == fixture["repeats"]
    assert len(control.policies) == fixture["policies"]
    assert len(rows) == fixture["searches"] + fixture["references"]
    assert rows[0]["run_id"] == fixture["first_search"]
    assert rows[71]["run_id"] == fixture["last_search"]
    assert rows[72]["run_id"] == fixture["first_reference"]
    assert rows[-1]["run_id"] == fixture["last_reference"]
    for stage, budget in (("search", spec.search_budget), ("reference", spec.reference_budget)):
        assert {meter: getattr(budget, f"max_{meter}") for meter in METERS} == fixture[f"{stage}_capacity"]


def test_build_and_validation_are_deterministic_and_do_not_execute(original: Path, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.weight_evolution import cpu_measured_runner, cpu_runner

    def forbid(*args: Any, **kwargs: Any) -> None:
        pytest.fail("bundle construction or validation dispatched inference")

    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(cpu_measured_runner, "run_measured_cpu_batch", forbid)
    directory = tmp_path / "copy"
    pilot.build_cpu_pilot_bundle(directory)
    assert snapshot(directory) == snapshot(original)
    before = snapshot(directory)
    pilot.validate_cpu_pilot_bundle(directory)
    assert snapshot(directory) == before


def test_existing_control_task_assets_are_reused_byte_for_byte(original: Path, tmp_path: Path) -> None:
    native = tmp_path / "native"
    build_cpu_control_campaign(native, preset="pilot")
    assert snapshot(original / "campaign" / "tasks") == snapshot(native / "tasks")


@pytest.mark.parametrize("field,value", [
    ("roster_digest", "0" * 64), ("schedule_digest", "0" * 64),
    ("implementation_digest", "0" * 64), ("execution_implementation_digest", "0" * 64),
    ("native_runtime_digest", "0" * 64), ("measured_profile_digest", "0" * 64),
    ("control_campaign_digest", "0" * 64), ("campaign_id", "changed"),
])
def test_rehashed_pilot_pin_drift_is_rejected(bundle: Path, field: str, value: Any) -> None:
    raw = json.loads((bundle / "pilot.json").read_bytes())
    raw[field] = value
    repin(bundle / "pilot.json", lock=bundle / "bundle.lock.json", prefix="spec", value=raw)
    with pytest.raises(ValueError):
        pilot.validate_cpu_pilot_bundle(bundle)


@pytest.mark.parametrize("stage", ["search", "reference"])
@pytest.mark.parametrize("meter", METERS)
def test_rehashed_capacity_cannot_expand_or_refund(bundle: Path, stage: str, meter: str) -> None:
    raw = json.loads((bundle / "pilot.json").read_bytes())
    raw[f"{stage}_budget"][f"max_{meter}"] += 1
    repin(bundle / "pilot.json", lock=bundle / "bundle.lock.json", prefix="spec", value=raw)
    with pytest.raises(ValueError):
        pilot.validate_cpu_pilot_bundle(bundle)


def test_rehashed_runtime_is_not_a_substitute_for_current_sources(bundle: Path) -> None:
    raw = json.loads((bundle / "runtime.json").read_bytes())
    raw["implementation_digest"] = "0" * 64
    repin(bundle / "runtime.json", lock=bundle / "bundle.lock.json", prefix="runtime", value=raw)
    with pytest.raises(ValueError, match="runtime or source"):
        pilot.validate_cpu_pilot_bundle(bundle)


@pytest.mark.parametrize("mutation", ["task-order", "inference-seed", "order-seed", "smaller-cohorts"])
def test_consistently_rehashed_alternate_design_still_cannot_change_fixed_pilot(bundle: Path, mutation: str) -> None:
    campaign = bundle / "campaign"
    if mutation == "smaller-cohorts":
        shutil.rmtree(campaign)
        build_cpu_control_campaign(campaign, preset="pilot", cases_per_role=4, campaign_id="cpu-measured-pilot-v1")
    else:
        raw = json.loads((campaign / "campaign.json").read_bytes())
        if mutation == "task-order":
            raw["tasks"][0], raw["tasks"][1] = raw["tasks"][1], raw["tasks"][0]
        else:
            field = "inference_seed" if mutation == "inference-seed" else "search_order_seed"
            raw["repeats"][0][field] += 1
        repin(campaign / "campaign.json", lock=campaign / "bundle.lock.json", prefix="spec", value=raw)
    control, templates, _ = pilot._load_control(campaign)
    raw = json.loads((bundle / "pilot.json").read_bytes())
    identity = file_identity(campaign / "bundle.lock.json")
    search, reference = pilot._budgets(control, templates)
    raw.update(control_campaign_digest=control.digest, control_lock_sha256=identity["sha256"],
        control_lock_size_bytes=identity["size_bytes"], roster_digest=canonical_digest(pilot._roster(control)),
        schedule_digest=canonical_digest(pilot.pilot_schedule(control, templates)),
        search_budget=search.to_dict(), reference_budget=reference.to_dict())
    repin(bundle / "pilot.json", lock=bundle / "bundle.lock.json", prefix="spec", value=raw)
    with pytest.raises(ValueError, match="fixed tasks, schedule or full capacity"):
        pilot.validate_cpu_pilot_bundle(bundle)


@pytest.mark.parametrize("location", ["", "campaign", "campaign/tasks", "campaign/tasks/task-01"])
def test_bundle_has_a_bounded_exact_inventory(bundle: Path, location: str) -> None:
    (bundle / location / "unexpected").write_text("extra")
    with pytest.raises(ValueError):
        pilot.validate_cpu_pilot_bundle(bundle)


def test_missing_checkpoint_and_symlink_are_rejected(bundle: Path, tmp_path: Path) -> None:
    checkpoint = bundle / "campaign" / "tasks" / "task-01" / "base.safetensors"
    external = tmp_path / "checkpoint"
    checkpoint.rename(external)
    with pytest.raises(ValueError):
        pilot.validate_cpu_pilot_bundle(bundle)
    checkpoint.symlink_to(external)
    with pytest.raises(ValueError):
        pilot.validate_cpu_pilot_bundle(bundle)


def test_no_overwrite_or_symlinked_build_target(original: Path, tmp_path: Path) -> None:
    before = snapshot(original)
    with pytest.raises(ValueError, match="fresh"):
        pilot.build_cpu_pilot_bundle(original)
    assert snapshot(original) == before
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        pilot.build_cpu_pilot_bundle(alias / "new")
