from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.cpu_campaign import _bundle_bytes
from autocontext.weight_evolution.cpu_landscape_control_tasks import landscape_control_task_payloads
from autocontext.weight_evolution.cpu_robustness_control_models import (
    MAX_ROBUSTNESS_CONTROL_BYTES,
    ROBUSTNESS_CONTROL_ROSTER,
    CpuRobustnessControlCampaignSpec,
    CpuRobustnessControlTaskProvenance,
)
from autocontext.weight_evolution.cpu_robustness_control_tasks import (
    build_robustness_control_campaign,
    load_robustness_control_campaign,
    robustness_control_schedule,
    robustness_control_task_payloads,
)


@pytest.fixture(scope="module")
def original(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, ...]:
    directory = tmp_path_factory.mktemp("robustness-controls") / "bundle"
    spec = build_robustness_control_campaign(directory)
    return directory, spec, *load_robustness_control_campaign(directory)[1:]


@pytest.fixture
def bundle(original: tuple[Any, ...], tmp_path: Path) -> Path:
    return Path(shutil.copytree(original[0], tmp_path / "bundle"))


def test_pinned_roster_schedule_and_policy_input_separation(original: tuple[Any, ...]) -> None:
    directory, spec, templates, provenances = original
    assert len(templates) == len(provenances) == 15
    assert len({task.base_task_id for task in spec.tasks}) == 6
    assert [task.base_task_id for task in spec.tasks if task.rounds == 6] == ["task-01", "task-03", "task-05"]
    assert spec.sampling_unit == "base-task-instance"
    assert spec.budget.to_dict() == {"max_runs": 45, "max_proposals": 486, "max_materializations": 315,
        "max_inference_calls": 10008, "max_input_tokens": 85860, "max_output_tokens": 10008}
    schedule = robustness_control_schedule(spec, templates)
    assert len(schedule) == len({row["run_id"] for row, _ in schedule}) == 45
    oracle_bytes = (directory / "oracle.json").read_bytes()
    assert len(oracle_bytes) == spec.oracle_size_bytes < MAX_ROBUSTNESS_CONTROL_BYTES
    assert hashlib.sha256(oracle_bytes).hexdigest() == spec.oracle_sha256
    oracle = json.loads(oracle_bytes)
    assert [row["run_id"] for row in oracle["runs"]] == [row["run_id"] for row, _ in schedule]
    assert {row["confirmation"]["status"] for row in oracle["runs"]} == {"vetoed"}
    for index in range(0, 45, 3):
        triplet = schedule[index:index + 3]
        assert len({json.dumps(row["reserved_capacity"], sort_keys=True) for row, _ in triplet}) == 1
        for row, child in triplet:
            assert row["base_task_id"] == provenances[row["task_id"]].base_task_id
            assert row["inference_seed"] == row["order_seed"] == child.evaluation_seed == 0
            assert set(_bundle_bytes(directory, row["task_id"], child)) == {
                "study.json", "bundle.lock.json", "base.safetensors"}
            assert not {"family", "base_task_id", "source", "oracle", "expected_joint_status",
                        "panel", "teacher_labels", "instance_index"}.intersection(child.to_dict())


@pytest.mark.parametrize("task_id,base_task_id,family,instance_index,seed,order_id,rounds,panel", ROBUSTNESS_CONTROL_ROSTER)
def test_variants_preserve_exact_m19_semantics_and_source_pins(
    task_id: str, base_task_id: str, family: Any, instance_index: int, seed: int,
    order_id: Any, rounds: int, panel: Any,
) -> None:
    args = dict(task_id=task_id, base_task_id=base_task_id, generation_seed=seed, family=family,
                instance_index=instance_index, order_id=order_id, rounds=rounds, panel=panel)
    template, facts, payloads = robustness_control_task_payloads(**args)
    assert robustness_control_task_payloads(**args) == (template, facts, payloads)
    source, old_facts, old_payloads = landscape_control_task_payloads(task_id=base_task_id, generation_seed=seed,
        family=family, instance_index=instance_index)
    assert payloads["base.safetensors"] == old_payloads["base.safetensors"]
    assert template.candidates == source.candidates and template.cases == source.cases and template.model == source.model
    assert template.baseline_decoding == source.baseline_decoding
    assert facts.source == old_facts
    for field, name in (("source_bundle_lock", "bundle.lock.json"), ("source_provenance", "task.provenance.json")):
        assert getattr(facts, field + "_sha256") == hashlib.sha256(old_payloads[name]).hexdigest()
        assert getattr(facts, field + "_size_bytes") == len(old_payloads[name])
    assert template.options_for("C") == source.options_for("C")
    assert template.options_for("D") == source.options_for("D")
    assert {o.option_id: o for o in template.options_for("W")} == {o.option_id: o for o in source.options_for("W")}
    indices = range(6) if order_id == "canonical" else (2, 1, 0, 5, 4, 3)
    assert template.options_for("W") == tuple(source.options_for("W")[index] for index in indices)
    raw = template.to_dict()
    raw.update(study_id=source.study_id, rounds=source.rounds, budget=source.budget.to_dict(),
               options=[option.to_dict() for option in source.options])
    assert raw == source.to_dict()


def _rewrite(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def _repin(bundle: Path, spec: dict[str, Any]) -> None:
    _rewrite(bundle / "campaign.json", spec)
    data = (bundle / "campaign.json").read_bytes()
    lock = json.loads((bundle / "bundle.lock.json").read_bytes())
    lock.update(spec_sha256=hashlib.sha256(data).hexdigest(), spec_size_bytes=len(data))
    _rewrite(bundle / "bundle.lock.json", lock)


@pytest.mark.parametrize("surface", ("cases", "options", "candidates", "provenance", "oracle", "offline-oracle", "capacity"))
def test_consistently_rehashed_semantic_or_oracle_drift_fails_closed(bundle: Path, surface: str) -> None:
    spec = json.loads((bundle / "campaign.json").read_bytes())
    task = bundle / "tasks" / spec["tasks"][0]["task_id"]
    if surface == "capacity":
        spec["budget"]["max_input_tokens"] += 1
    elif surface in {"oracle", "offline-oracle"}:
        path = bundle / "oracle.json"
        raw = json.loads(path.read_bytes())
        if surface == "oracle":
            raw["runs"][0]["selected_options"]["W"] = None
        else:
            raw["offline"]["tasks"][0]["trajectories"][0]["regret_correct"] += 1
        _rewrite(path, raw)
        spec.update(oracle_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), oracle_size_bytes=path.stat().st_size)
    else:
        if surface == "provenance":
            raw = json.loads((task / "task.provenance.json").read_bytes())
            raw["source_provenance_sha256"] = "0" * 64
            _rewrite(task / "task.provenance.json", raw)
        else:
            raw = json.loads((task / "study.json").read_bytes())
            if surface == "cases":
                raw["cases"][0]["expected"] = "one" if raw["cases"][0]["expected"] == "zero" else "zero"
            elif surface == "options":
                raw["options"][6]["direction"] = "other-lane"
            else:
                raw["candidates"][1]["output_sha256"] = "0" * 64
            _rewrite(task / "study.json", raw)
            data = (task / "study.json").read_bytes()
            lock = json.loads((task / "bundle.lock.json").read_bytes())
            lock.update(spec_sha256=hashlib.sha256(data).hexdigest(), spec_size_bytes=len(data))
            _rewrite(task / "bundle.lock.json", lock)
        for field, name in (("bundle_lock", "bundle.lock.json"), ("provenance", "task.provenance.json")):
            data = (task / name).read_bytes()
            spec["tasks"][0].update({field + "_sha256": hashlib.sha256(data).hexdigest(), field + "_size_bytes": len(data)})
    _repin(bundle, spec)
    with pytest.raises(ValueError):
        load_robustness_control_campaign(bundle)


@pytest.mark.parametrize("surface", ("extra-root", "extra-task", "extra-file", "missing", "symlink", "oversized"))
def test_exact_bounded_inventory_is_enforced(bundle: Path, surface: str) -> None:
    task = bundle / "tasks" / "task-01.canonical.r3"
    if surface == "extra-root":
        (bundle / "extra").write_text("unowned")
    elif surface == "extra-task":
        (bundle / "tasks" / "extra").mkdir()
    elif surface == "extra-file":
        (task / "extra").write_text("unowned")
    elif surface == "missing":
        (task / "base.safetensors").unlink()
    elif surface == "symlink":
        original = task / "task.provenance.json"
        other = bundle.parent / "other.json"
        other.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(other)
    else:
        with (task / "base.safetensors").open("wb") as stream:
            stream.truncate(MAX_ROBUSTNESS_CONTROL_BYTES + 1)
    with pytest.raises(ValueError):
        load_robustness_control_campaign(bundle)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("gpu_execution_deferred", 1), ("promotion_eligible", 0),
    ("base_task_count", 6.0), ("legal_order_count", "90"), ("offline_trajectory_count", 6480.0),
    ("offline_budgets", [True, 3, 6]), ("offline_budgets", [1, 2, 6]),
    ("policies", ["fixed-order-v1", "adaptive-v1", "seeded-random-v1"]), ("tasks", []), ("repeats", []),
    ("oracle_size_bytes", 0), ("oracle_size_bytes", MAX_ROBUSTNESS_CONTROL_BYTES + 1),
])
def test_contract_rejects_type_coercion_changed_roster_or_domain(
    original: tuple[Any, ...], field: str, value: Any,
) -> None:
    raw = original[1].to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuRobustnessControlCampaignSpec.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("base_task_id", "task-02"), ("instance_index", True),
    ("order_id", "reverse-lanes"), ("rounds", 6), ("rounds", 3.0), ("panel", "full-budget")])
def test_provenance_rejects_relabelled_variant(original: tuple[Any, ...], field: str, value: Any) -> None:
    raw = original[3]["task-01.canonical.r3"].to_dict()
    raw[field] = value
    with pytest.raises(ValueError):
        CpuRobustnessControlTaskProvenance.model_validate_json(json.dumps(raw))


@pytest.mark.parametrize("field,value", [("generation_seed", True), ("instance_index", True),
    ("rounds", 3.0), ("rounds", 1), ("family", "invented"), ("order_id", "invented"), ("base_task_id", "task-02")])
def test_generator_accepts_only_predeclared_variants(field: str, value: Any) -> None:
    args = dict(zip(("task_id", "base_task_id", "family", "instance_index", "generation_seed", "order_id", "rounds", "panel"),
                    ROBUSTNESS_CONTROL_ROSTER[0], strict=True))
    args[field] = value
    with pytest.raises(ValueError):
        robustness_control_task_payloads(**args)


def test_builder_refuses_existing_or_symlinked_target(original: tuple[Any, ...], tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        build_robustness_control_campaign(original[0])
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        build_robustness_control_campaign(link / "new")
