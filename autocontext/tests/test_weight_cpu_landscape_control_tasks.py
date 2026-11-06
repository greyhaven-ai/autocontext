from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_campaign import _bundle_bytes
from autocontext.weight_evolution.cpu_campaign_tasks import token_count_ray
from autocontext.weight_evolution.cpu_landscape_control_models import (
    LANDSCAPE_CONTROL_ROSTER,
    CpuLandscapeControlCampaignSpec,
    CpuLandscapeControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_landscape_control_tasks import (
    build_landscape_control_campaign,
    landscape_control_schedule,
    landscape_control_task_payloads,
    load_landscape_control_campaign,
)


@pytest.fixture
def campaign(tmp_path: Path) -> tuple[Any, ...]:
    bundle = tmp_path / "bundle"
    spec = build_landscape_control_campaign(bundle)
    return bundle, spec, *load_landscape_control_campaign(bundle)[1:]


def test_fixed_campaign_pins_full_oracle_and_equal_search_capacity(campaign: tuple[Any, ...]) -> None:
    bundle, spec, templates, provenance = campaign
    assert len(templates) == len(provenance) == 6
    assert [repeat.inference_seed for repeat in spec.repeats] == [0, 1]
    rows = landscape_control_schedule(spec, templates)
    assert len(rows) == 36 and len({row["run_id"] for row, _ in rows}) == 36
    assert {child.minimum_confirmation_gain for _, child in rows} == {0.1}
    assert {child.rounds for _, child in rows} == {3}
    assert {child.budget.max_inference_calls for _, child in rows} == {208}
    assert spec.budget.to_dict() == {"max_runs": 36, "max_proposals": 324, "max_materializations": 252,
        "max_inference_calls": 7488, "max_input_tokens": 64800, "max_output_tokens": 7488}
    oracle_bytes = (bundle / "oracle.json").read_bytes()
    assert hashlib.sha256(oracle_bytes).hexdigest() == spec.oracle_sha256
    assert len(oracle_bytes) == spec.oracle_size_bytes == 1753087
    oracle = json.loads(oracle_bytes)
    assert [row["run_id"] for row in oracle["runs"]] == [row["run_id"] for row, _ in rows]
    assert {row["confirmation"]["status"] for row in oracle["runs"]} == {"vetoed"}
    assert {row["audit_status"] for row in oracle["runs"]} == {"withheld"}
    for row, child in rows:
        assert set(_bundle_bytes(bundle, row["task_id"], child)) == {"study.json", "bundle.lock.json", "base.safetensors"}
        assert all(name not in child.to_dict() for name in ("family", "oracle", "expected_joint_status",
                                                          "base_weights", "instance_index", "teacher_labels"))
        assert row["instance_index"] == provenance[row["task_id"]].instance_index


@pytest.mark.parametrize("task_id,family,instance_index,seed", LANDSCAPE_CONTROL_ROSTER)
def test_byte_determinism_balanced_teacher_and_role_ray_separation(
    task_id: str, family: Any, instance_index: int, seed: int,
) -> None:
    values = dict(task_id=task_id, generation_seed=seed, family=family, instance_index=instance_index)
    spec, provenance, payloads = landscape_control_task_payloads(**values)
    assert landscape_control_task_payloads(**values) == (spec, provenance, payloads)
    rays = [token_count_ray(case.prompt, spec.model.vocabulary) for case in spec.cases]
    assert len(set(rays)) == len(rays) == 24
    for role in ("practice", "confirmation", "audit"):
        cases = [case for case in spec.cases if case.role == role]
        assert sum(case.expected == "zero" for case in cases) == 4
        assert sum(case.expected == "one" for case in cases) == 4
    assert len(spec.candidates) == len({candidate.output_sha256 for candidate in spec.candidates}) == 7
    assert all(option.option_id[2:].isdigit() and option.direction.startswith("lane-") for option in spec.options)
    for mechanism in ("C", "W", "D"):
        options = spec.options_for(mechanism)
        assert len(options) == 6
        assert [(option.direction, option.step) for option in options] == [
            (f"lane-{lane:02}", step) for step in (1, 2) for lane in (1, 2, 3)]


def test_instances_change_teacher_margins_and_repair_effects_beyond_permutation(campaign: tuple[Any, ...]) -> None:
    _, spec, templates, provenance = campaign
    for first, second in zip(spec.tasks[::2], spec.tasks[1::2], strict=True):
        a, b = provenance[first.task_id], provenance[second.task_id]
        assert first.family == second.family and (a.instance_index, b.instance_index) == (1, 2)
        assert a.teacher_labels != b.teacher_labels
        margins_a = [max(a.base_weights[f * 2:f * 2 + 2]) for f in a.feature_indices[:4]]
        margins_b = [max(b.base_weights[f * 2:f * 2 + 2]) for f in b.feature_indices[:4]]
        assert sorted(margins_a) != sorted(margins_b)
        assert templates[first.task_id].candidates[1].program != templates[second.task_id].candidates[1].program


def _rewrite(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _repin_campaign(bundle: Path, spec: dict[str, Any]) -> None:
    _rewrite(bundle / "campaign.json", spec)
    data = (bundle / "campaign.json").read_bytes()
    lock = json.loads((bundle / "bundle.lock.json").read_bytes())
    lock.update(spec_sha256=hashlib.sha256(data).hexdigest(), spec_size_bytes=len(data))
    _rewrite(bundle / "bundle.lock.json", lock)


@pytest.mark.parametrize("surface", ["cases", "provenance", "oracle", "extra-task", "extra-asset", "capacity", "symlink"])
def test_consistently_rehashed_asset_or_capacity_drift_fails_closed(campaign: tuple[Any, ...], surface: str) -> None:
    bundle, _, _, _ = campaign
    spec = json.loads((bundle / "campaign.json").read_bytes())
    task = bundle / "tasks" / "task-01"
    if surface == "extra-task":
        (bundle / "tasks" / "unexpected").mkdir()
    elif surface == "extra-asset":
        (bundle / "unexpected").write_text("unowned")
    elif surface == "symlink":
        original = task / "task.provenance.json"
        other = bundle.parent / "other.json"
        other.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(other)
    elif surface == "oracle":
        path = bundle / "oracle.json"
        raw = json.loads(path.read_bytes())
        raw["runs"][0]["selected_options"]["W"] = None
        _rewrite(path, raw)
        spec.update(oracle_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), oracle_size_bytes=path.stat().st_size)
        _repin_campaign(bundle, spec)
    elif surface == "capacity":
        spec["budget"]["max_input_tokens"] += 1
        _repin_campaign(bundle, spec)
    else:
        provenance = json.loads((task / "task.provenance.json").read_bytes())
        if surface == "cases":
            raw = json.loads((task / "study.json").read_bytes())
            raw["cases"][0]["expected"] = "one" if raw["cases"][0]["expected"] == "zero" else "zero"
            _rewrite(task / "study.json", raw)
            data = (task / "study.json").read_bytes()
            lock = json.loads((task / "bundle.lock.json").read_bytes())
            lock.update(spec_sha256=hashlib.sha256(data).hexdigest(), spec_size_bytes=len(data))
            _rewrite(task / "bundle.lock.json", lock)
            provenance["cases_digest"] = canonical_digest([
                {key: case[key] for key in ("role", "prompt", "expected")} for case in raw["cases"]])
        else:
            provenance["instance_index"] = 2
        _rewrite(task / "task.provenance.json", provenance)
        for stem, filename in (("bundle_lock", "bundle.lock.json"), ("provenance", "task.provenance.json")):
            data = (task / filename).read_bytes()
            spec["tasks"][0][f"{stem}_sha256"] = hashlib.sha256(data).hexdigest()
            spec["tasks"][0][f"{stem}_size_bytes"] = len(data)
        _repin_campaign(bundle, spec)
    with pytest.raises(ValueError):
        load_landscape_control_campaign(bundle)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("gpu_execution_deferred", 1), ("promotion_eligible", 0),
    ("policies", ["fixed-order-v1", "adaptive-v1", "seeded-random-v1"]),
    ("repeats", []), ("tasks", []), ("oracle_size_bytes", 0), ("oracle_size_bytes", 4 * 1024 * 1024 + 1),
])
def test_contract_rejects_wrong_literals_or_roster(campaign: tuple[Any, ...], field: str, value: Any) -> None:
    raw = campaign[1].to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuLandscapeControlCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("instance_index", True), ("instance_index", 3), ("generation_seed", 1900042),
                                       ("family", "neutral-v1"), ("task_id", "task-99")])
def test_contract_rejects_changed_task_design(campaign: tuple[Any, ...], field: str, value: Any) -> None:
    raw = campaign[1].to_dict()
    raw["tasks"][0][field] = value
    with pytest.raises(ValueError):
        CpuLandscapeControlCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("teacher_labels", [1, 0, 1, 0]), ("base_weights", [0.0] * 16),
                                       ("feature_indices", [0] * 8), ("expected_joint_status", "confirmed")])
def test_provenance_rejects_changed_semantics(campaign: tuple[Any, ...], field: str, value: Any) -> None:
    raw = campaign[3]["task-01"].to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuLandscapeControlTaskProvenance.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("generation_seed", True), ("generation_seed", -1),
                                       ("instance_index", True), ("instance_index", 0), ("family", "invented")])
def test_generator_rejects_undeclared_inputs(field: str, value: Any) -> None:
    arguments: dict[str, Any] = dict(task_id="task-01", generation_seed=1900001,
                                     family="informative-improvement-v1", instance_index=1)
    arguments[field] = value
    with pytest.raises(ValueError):
        landscape_control_task_payloads(**arguments)


def test_builder_refuses_existing_or_symlinked_directory(campaign: tuple[Any, ...], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_landscape_control_campaign(campaign[0])
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        build_landscape_control_campaign(link / "new")
