from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_campaign import _bundle_bytes
from autocontext.weight_evolution.cpu_campaign_tasks import token_count_ray
from autocontext.weight_evolution.cpu_joint_control_models import (
    JOINT_CONTROL_FAMILIES,
    JOINT_CONTROL_TASK_SEEDS,
    CpuJointControlCampaignSpec,
)
from autocontext.weight_evolution.cpu_joint_control_tasks import (
    build_joint_control_campaign,
    joint_control_schedule,
    joint_control_task_payloads,
    load_joint_control_campaign,
)


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[Any, ...]:
    bundle = tmp_path / "bundle"
    spec = build_joint_control_campaign(bundle)
    return bundle, spec, *load_joint_control_campaign(bundle)[1:]


def test_fixed_campaign_predeclares_full_oracle_and_equal_search_capacity(campaign: tuple[Any, ...]) -> None:
    bundle, spec, templates, provenance = campaign
    assert len(templates) == len(provenance) == 3
    assert [repeat.inference_seed for repeat in spec.repeats] == [0, 1]
    rows = joint_control_schedule(spec, templates)
    assert len(rows) == 18 and len({row["run_id"] for row, _ in rows}) == 18
    assert {child.minimum_confirmation_gain for _, child in rows} == {0.1}
    assert {child.rounds for _, child in rows} == {3}
    assert {child.budget.max_inference_calls for _, child in rows} == {208}
    assert spec.budget.to_dict() == {"max_runs": 18, "max_proposals": 162, "max_materializations": 90,
        "max_inference_calls": 3744, "max_input_tokens": 25776, "max_output_tokens": 3744}
    oracle_bytes = (bundle / "oracle.json").read_bytes()
    assert hashlib.sha256(oracle_bytes).hexdigest() == spec.oracle_sha256
    assert len(oracle_bytes) == spec.oracle_size_bytes
    oracle = json.loads(oracle_bytes)
    assert [row["run_id"] for row in oracle["runs"]] == [row["run_id"] for row, _ in rows]
    assert sum(row["confirmation"]["status"] == "confirmed" for row in oracle["runs"]) == 6
    for row, child in rows:
        assert set(_bundle_bytes(bundle, row["task_id"], child)) == {"study.json", "bundle.lock.json", "base.safetensors"}
        assert all(name not in child.to_dict() for name in ("family", "oracle", "expected_joint_status", "base_weights"))


@pytest.mark.parametrize("family", JOINT_CONTROL_FAMILIES)
def test_byte_determinism_balanced_teacher_and_role_ray_separation(family: Any) -> None:
    values = dict(task_id="test-task", generation_seed=JOINT_CONTROL_TASK_SEEDS[family], family=family)
    spec, provenance, payloads = joint_control_task_payloads(**values)
    assert joint_control_task_payloads(**values) == (spec, provenance, payloads)
    rays = [token_count_ray(case.prompt, spec.model.vocabulary) for case in spec.cases]
    assert len(set(rays)) == len(rays) == 24
    for role in ("practice", "confirmation", "audit"):
        cases = [case for case in spec.cases if case.role == role]
        assert sum(case.expected == "zero" for case in cases) == 4
        assert sum(case.expected == "one" for case in cases) == 4
    assert all(option.option_id[2:].isdigit() and option.direction.startswith("lane-") for option in spec.options)


@pytest.mark.parametrize("surface", ["cases", "weights", "oracle", "extra-task", "extra-asset", "capacity"])
def test_rehashed_asset_or_capacity_drift_fails_closed(campaign: tuple[Any, ...], surface: str) -> None:
    bundle, _, _, _ = campaign
    if surface == "extra-task":
        (bundle / "tasks" / "unexpected").mkdir()
    elif surface == "extra-asset":
        (bundle / "unexpected").write_text("unowned")
    elif surface == "oracle":
        path = bundle / "oracle.json"
        raw = json.loads(path.read_bytes())
        raw["runs"][0]["selected_options"]["C"] = None
        path.write_text(json.dumps(raw))
        spec_path = bundle / "campaign.json"
        spec = json.loads(spec_path.read_bytes())
        spec.update(oracle_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), oracle_size_bytes=path.stat().st_size)
        spec_path.write_text(json.dumps(spec))
        lock = json.loads((bundle / "bundle.lock.json").read_bytes())
        lock.update(spec_sha256=hashlib.sha256(spec_path.read_bytes()).hexdigest(), spec_size_bytes=spec_path.stat().st_size)
        (bundle / "bundle.lock.json").write_text(json.dumps(lock))
    elif surface == "capacity":
        path = bundle / "campaign.json"
        spec = json.loads(path.read_bytes())
        spec["budget"]["max_input_tokens"] += 1
        path.write_text(json.dumps(spec))
        lock = json.loads((bundle / "bundle.lock.json").read_bytes())
        lock.update(spec_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), spec_size_bytes=path.stat().st_size)
        (bundle / "bundle.lock.json").write_text(json.dumps(lock))
    else:
        task = bundle / "tasks" / "task-01"
        path = task / ("study.json" if surface == "cases" else "base.safetensors")
        if surface == "cases":
            raw = json.loads(path.read_bytes())
            raw["cases"][0]["expected"] = "one" if raw["cases"][0]["expected"] == "zero" else "zero"
            path.write_text(json.dumps(raw))
        else:
            data = path.read_bytes()
            path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(ValueError):
        load_joint_control_campaign(bundle)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("gpu_execution_deferred", 1), ("promotion_eligible", 0),
    ("policies", ["fixed-order-v1", "adaptive-v1", "seeded-random-v1"]),
    ("repeats", []), ("tasks", []), ("oracle_size_bytes", 0),
])
def test_contract_rejects_wrong_literals_or_roster(campaign: tuple[Any, ...], field: str, value: Any) -> None:
    raw = campaign[1].to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuJointControlCampaignSpec.model_validate_json(json.dumps(raw))


def test_builder_refuses_existing_or_symlinked_directory(campaign: tuple[Any, ...], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_joint_control_campaign(campaign[0])
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        build_joint_control_campaign(link / "new")
