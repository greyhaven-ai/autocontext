from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_control_tasks, cpu_model, cpu_runner
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_campaign_tasks import PILOT_TASK_SEEDS, token_count_ray
from autocontext.weight_evolution.cpu_control_models import CONTROL_FAMILIES, CONTROL_ROLES, ControlFamily, CpuControlCampaignLock
from autocontext.weight_evolution.cpu_control_tasks import (
    CONTROL_REPEATS,
    CONTROL_TASK_SEEDS,
    _task_payloads,
    build_cpu_control_campaign,
    build_cpu_control_task,
    validate_control_task_provenance,
)
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("generation and verification must not run or filter by model outcomes")


@pytest.fixture(autouse=True)
def control_source_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_control_tasks, "_implementation_digest", lambda: "d" * 64)


@pytest.mark.parametrize("family", CONTROL_FAMILIES)
def test_control_algebra_matches_independent_tensor_materialization_and_cpu_inference(
    tmp_path: Path, family: ControlFamily,
) -> None:
    for seed in CONTROL_TASK_SEEDS[family]:
        directory = tmp_path / str(seed)
        spec = build_cpu_control_task(directory, task_id="t", generation_seed=seed, family=family)
        provenance = validate_control_task_provenance(directory, spec)
        registry = TensorCandidateRegistry(spec.candidates, input_path=directory / "base.safetensors",
                                           output_dir=directory / "verified-candidates")
        expected = provenance.expected_behavior
        designated = {option.candidate_id for option in spec.options
                      if option.option_id in expected.designated_weight_option_ids}
        for candidate in spec.candidates:
            registry.materialize(candidate.handle)
            for role in CONTROL_ROLES:
                cohort = [case for case in spec.cases if case.role == role]
                results = [cpu_model.infer_cpu(CpuInferenceRequest(
                    request_id=case.case_id, model=spec.model, checkpoint_sha256=candidate.output_sha256,
                    checkpoint_size_bytes=candidate.output_size_bytes, prompt=case.prompt,
                    decoding=spec.baseline_decoding, limits=spec.limits), registry.checkpoint_path(candidate.handle))
                    for case in cohort]
                observed = sum(result.text == case.expected for result, case in zip(results, cohort, strict=True)) / len(cohort)
                intended = (expected.designated_weight_accuracy if candidate.candidate_id in designated else
                            expected.baseline_accuracy)
                assert observed == intended[role], (family, seed, candidate.candidate_id, role)


@pytest.mark.parametrize("family", CONTROL_FAMILIES)
@pytest.mark.parametrize("count", [4, 8, 16])
def test_control_cases_are_balanced_structurally_distinct_and_fit_equal_bounds(family: ControlFamily, count: int) -> None:
    spec, provenance, _ = _task_payloads(task_id="t", generation_seed=CONTROL_TASK_SEEDS[family][0],
                                        family=family, cases_per_role=count)
    rays = [token_count_ray(case.prompt, spec.model.vocabulary) for case in spec.cases]
    assert len(rays) == len(set(rays)) == 3 * count
    for role in CONTROL_ROLES:
        cohort = [case for case in spec.cases if case.role == role]
        assert sum(case.expected == "one" for case in cohort) == count // 2
        assert sum(case.expected == "zero" for case in cohort) == count // 2
    for case in spec.cases:
        tokens = case.prompt.split()
        counts = [tokens.count(token) for token in spec.model.vocabulary]
        margin = sum(weight * amount for weight, amount in zip(provenance.teacher_weights, counts, strict=True))
        assert case.expected == ("zero" if margin >= 0 else "one")
        assert counts[provenance.feature_indices[-1]] == 0
        assert len(tokens) <= 11
    assert spec.limits.max_input_tokens == 14 and spec.minimum_confirmation_gain == 0.1
    assert spec.rounds == 3 and len(spec.candidates) == 5
    assert [len(spec.options_for(mechanism)) for mechanism in ("C", "W", "D")] == [4, 4, 4]


def test_neutral_seeded_placement_changes_without_outcome_filtering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cpu_model, "infer_cpu", forbid)
    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    placements, identities, semantics = set(), set(), set()
    for family in CONTROL_FAMILIES:
        for seed in CONTROL_TASK_SEEDS[family]:
            spec, provenance, _ = _task_payloads(task_id="t", generation_seed=seed, family=family, cases_per_role=8)
            designated = provenance.expected_behavior.designated_weight_option_ids
            placements.add(tuple(index for index, option in enumerate(spec.options_for("W")) if option.option_id in designated))
            identities.add(designated)
            semantics.add(provenance.semantic_digest)
            exposed = json.dumps([option.to_dict() for option in spec.options])
            assert all(word not in exposed for word in (*CONTROL_FAMILIES, "teacher", "expected", "repair", "shortcut"))
    assert placements == {(0, 1), (2, 3)} and len(identities) > 1 and len(semantics) == 12
    assert not set(PILOT_TASK_SEEDS) & {seed for seeds in CONTROL_TASK_SEEDS.values() for seed in seeds}
    build_cpu_control_campaign(tmp_path / "campaign", instances_per_family=1)


def test_control_verification_is_read_only_and_rebuilds_exact_bytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    spec = build_cpu_control_task(first, task_id="t", generation_seed=1200007, family="practice-shortcut-v1")
    build_cpu_control_task(second, task_id="t", generation_seed=1200007, family="practice-shortcut-v1")
    assert files(first) == files(second)
    before = files(first)
    for name in ("write_bytes", "write_text", "mkdir"):
        monkeypatch.setattr(Path, name, forbid)
    monkeypatch.setattr(cpu_model, "infer_cpu", forbid)
    monkeypatch.setattr(cpu_runner, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert validate_control_task_provenance(first, spec).family == "practice-shortcut-v1"
    assert files(first) == before


@pytest.mark.parametrize("mutation", ["family", "expected", "label", "prompt", "options", "base", "seed", "source"])
def test_control_regeneration_rejects_relabeling_and_tampered_bytes(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "task"
    build_cpu_control_task(directory, task_id="t", generation_seed=1000003, family="transfer-repair-v1")
    if mutation == "base":
        path = directory / "base.safetensors"
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 1]))
    else:
        filename = "study.json" if mutation in {"label", "prompt", "options"} else "task.provenance.json"
        path = directory / filename
        value = json.loads(path.read_bytes())
        if mutation == "family":
            value["family"] = "practice-shortcut-v1"
        elif mutation == "expected":
            value["expected_behavior"]["designated_weight_option_ids"].reverse()
        elif mutation == "label":
            value["cases"][0]["expected"] = "one" if value["cases"][0]["expected"] == "zero" else "zero"
        elif mutation == "prompt":
            value["cases"][8]["prompt"] = value["cases"][0]["prompt"]
        elif mutation == "options":
            value["options"].reverse()
        elif mutation == "seed":
            value["generation_seed"] += 1
        else:
            value["generator_source_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        validate_control_task_provenance(directory)


@pytest.mark.parametrize("preset,count,runs", [("fixture", 6, 18), ("pilot", 12, 72)])
def test_campaign_preset_pins_balanced_schedule_and_full_reservations(
    tmp_path: Path, preset: str, count: int, runs: int,
) -> None:
    directory = tmp_path / preset
    spec = build_cpu_control_campaign(directory, preset=preset)
    assert len(spec.tasks) == count and spec.budget.max_runs == runs
    assert {sum(task.family == family for task in spec.tasks) for family in CONTROL_FAMILIES} == {count // 3}
    assert len(spec.repeats) == (1 if preset == "fixture" else 2)
    assert spec.repeats == (CONTROL_REPEATS[:1] if preset == "fixture" else CONTROL_REPEATS)
    totals = {name: 0 for name in ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")}
    for task in spec.tasks:
        bundle = directory / "tasks" / task.task_id
        template, _, _ = _task_payloads(task_id=task.task_id, generation_seed=task.generation_seed,
                                       family=task.family, cases_per_role=8)
        provenance = validate_control_task_provenance(bundle, template)
        assert provenance.family == task.family
        assert file_identity(bundle / "task.provenance.json")["sha256"] == task.provenance_sha256
        for name, value in template.required_usage.items():
            totals[name] += value * len(spec.repeats) * 3
    assert all(getattr(spec.budget, f"max_{name}") == value for name, value in totals.items())
    lock = CpuControlCampaignLock.model_validate_json((directory / "bundle.lock.json").read_bytes())
    assert file_identity(directory / "campaign.json")["sha256"] == lock.spec_sha256
    assert canonical_digest(json.loads((directory / "campaign.json").read_bytes())) == spec.digest


def test_small_campaign_override_keeps_every_family(tmp_path: Path) -> None:
    repeats = (CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),)
    spec = build_cpu_control_campaign(tmp_path / "small", instances_per_family=1, repeats=repeats, cases_per_role=4)
    assert len(spec.tasks) == 3 and spec.budget.max_runs == 9 and spec.repeats == repeats


@pytest.mark.parametrize("count", [3, 5, 17, True])
def test_invalid_cohort_refuses_before_writes(tmp_path: Path, count: int) -> None:
    directory = tmp_path / "task"
    with pytest.raises(ValueError, match="even case count"):
        build_cpu_control_task(directory, task_id="t", generation_seed=1000003,
                               family="transfer-repair-v1", cases_per_role=count)
    assert not directory.exists()


def test_builders_reject_reuse_symlinks_and_m11_seed_aliases(tmp_path: Path) -> None:
    directory = tmp_path / "task"
    build_cpu_control_task(directory, task_id="t", generation_seed=1000003, family="transfer-repair-v1")
    with pytest.raises(ValueError, match="fresh"):
        build_cpu_control_task(directory, task_id="t", generation_seed=1000003, family="transfer-repair-v1")
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_control_task_provenance(alias)
    with pytest.raises(ValueError, match="M11"):
        build_cpu_control_task(tmp_path / "bad", task_id="t", generation_seed=PILOT_TASK_SEEDS[0],
                               family="transfer-repair-v1")
