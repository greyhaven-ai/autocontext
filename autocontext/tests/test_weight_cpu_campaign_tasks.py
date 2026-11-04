from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.artifacts import file_identity
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignLock
from autocontext.weight_evolution.cpu_campaign_tasks import (
    FIXTURE_TASK_SEEDS,
    PILOT_REPEATS,
    PILOT_TASK_SEEDS,
    build_cpu_campaign,
    build_cpu_task,
    token_count_ray,
    validate_task_provenance,
)
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.tensor_edits import apply_tensor_edit, build_checkpoint_contract


def _files(directory: Path) -> dict[str, bytes]:
    return {str(path.relative_to(directory)): path.read_bytes() for path in directory.rglob("*") if path.is_file()}


def test_same_seed_rebuilds_identical_bytes_without_outcome_execution(tmp_path: Path) -> None:
    first = build_cpu_task(tmp_path / "first", task_id="t", generation_seed=123)
    second = build_cpu_task(tmp_path / "second", task_id="t", generation_seed=123)
    assert first == second
    assert _files(tmp_path / "first") == _files(tmp_path / "second")
    assert first.evaluation_seed == 0
    assert first.rounds == 3
    assert [len(first.options_for(mechanism)) for mechanism in ("C", "W", "D")] == [4, 4, 4]
    assert {case.role for case in first.cases} == {"practice", "confirmation", "audit"}
    assert first.required_usage["inference_calls"] == 104
    assert set(_files(tmp_path / "first")) == {"study.json", "base.safetensors", "bundle.lock.json", "task.provenance.json"}


def test_semantic_identity_ignores_task_aliases_but_changes_with_generated_instance(tmp_path: Path) -> None:
    for name, seed in (("original", 123), ("alias", 123), ("different", 124)):
        build_cpu_task(tmp_path / name, task_id=name, generation_seed=seed)
    provenance = {name: validate_task_provenance(tmp_path / name) for name in ("original", "alias", "different")}
    assert provenance["original"].semantic_digest == provenance["alias"].semantic_digest
    assert provenance["original"].semantic_digest != provenance["different"].semantic_digest
    assert provenance["original"].base_sha256 != provenance["different"].base_sha256
    assert provenance["original"].teacher_weights != provenance["different"].teacher_weights


def test_generated_labels_follow_independent_teacher_and_splits_have_distinct_rays(tmp_path: Path) -> None:
    for seed in PILOT_TASK_SEEDS:
        directory = tmp_path / str(seed)
        spec = build_cpu_task(directory, task_id=f"seed-{seed}", generation_seed=seed, cases_per_role=16)
        provenance = validate_task_provenance(directory, spec)
        rays = [token_count_ray(case.prompt, spec.model.vocabulary) for case in spec.cases]
        assert len(set(rays)) == 48
        for case in spec.cases:
            tokens = case.prompt.split()
            margin = sum(weight * tokens.count(token)
                         for token, weight in zip(spec.model.vocabulary, provenance.teacher_weights, strict=True))
            assert case.expected == ("zero" if margin >= 0 else "one")
        assert all("helpful" not in candidate.candidate_id and "harmful" not in candidate.candidate_id
                   for candidate in spec.candidates)


def test_ray_identity_collapses_order_and_positive_scalar_repetition() -> None:
    vocabulary = ("a", "b", "c")
    assert token_count_ray("a a b", vocabulary) == token_count_ray("b a a", vocabulary)
    assert token_count_ray("a a b", vocabulary) == token_count_ray("a b a a a b", vocabulary)
    assert token_count_ray("a a b", vocabulary) != token_count_ray("a b b", vocabulary)
    with pytest.raises(ValueError):
        token_count_ray("", vocabulary)
    with pytest.raises(ValueError):
        token_count_ray("unknown", vocabulary)


def test_generated_checkpoint_and_each_edit_are_independently_verified(tmp_path: Path) -> None:
    spec = build_cpu_task(tmp_path / "task", task_id="tensor-check", generation_seed=123)
    base = tmp_path / "task" / "base.safetensors"
    independent = build_checkpoint_contract(base, editable_names=("projection.weight",), max_changed_entries=1)
    assert independent == spec.baseline.contract
    for candidate in spec.candidates:
        if candidate.program is None:
            continue
        receipt = apply_tensor_edit(independent, candidate.program, input_path=base,
                                   output_dir=tmp_path / candidate.candidate_id)
        assert receipt.output_sha256 == candidate.output_sha256
        assert receipt.output_size_bytes == candidate.output_size_bytes
        assert receipt.changed_entries == 1


def test_provenance_validation_is_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = tmp_path / "task"
    spec = build_cpu_task(directory, task_id="readonly", generation_seed=123)
    before = _files(directory)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("verification must not write or materialize assets")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr("autocontext.weight_evolution.artifacts.write_tensor_file", forbidden)
    monkeypatch.setattr("autocontext.weight_evolution.tensor_edits.apply_tensor_edit", forbidden)
    monkeypatch.setattr("autocontext.weight_evolution.cpu_runner.run_cpu_batch", forbidden)
    assert validate_task_provenance(directory, spec).generation_seed == 123
    assert before == _files(directory)


@pytest.mark.parametrize("mutation", ["label", "scaled-prompt", "teacher", "seed", "base", "candidate", "source", "lock"])
def test_provenance_rejects_tampering_even_when_json_is_well_formed(tmp_path: Path, mutation: str) -> None:
    directory = tmp_path / "task"
    build_cpu_task(directory, task_id="tampered", generation_seed=123)
    if mutation == "base":
        path = directory / "base.safetensors"
        original = path.read_bytes()
        path.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    else:
        name = "task.provenance.json" if mutation in {"teacher", "seed", "source"} else "study.json"
        if mutation == "lock":
            name = "bundle.lock.json"
        path = directory / name
        value = json.loads(path.read_bytes())
        if mutation == "label":
            value["cases"][0]["expected"] = "one" if value["cases"][0]["expected"] == "zero" else "zero"
        elif mutation == "scaled-prompt":
            prompt = value["cases"][0]["prompt"]
            value["cases"][4]["prompt"] = f"{prompt} {prompt}"
        elif mutation == "teacher":
            value["teacher_weights"][0] *= -1
        elif mutation == "seed":
            value["generation_seed"] += 1
        elif mutation == "source":
            value["generator_source_sha256"] = "0" * 64
        elif mutation == "candidate":
            value["candidates"][1]["output_sha256"] = "0" * 64
        else:
            value["spec_sha256"] = "0" * 64
        path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="deterministic generation"):
        validate_task_provenance(directory)


def test_task_builder_refuses_reuse_and_symlink_paths(tmp_path: Path) -> None:
    directory = tmp_path / "task"
    build_cpu_task(directory, task_id="t", generation_seed=1)
    with pytest.raises(ValueError, match="fresh directory"):
        build_cpu_task(directory, task_id="t", generation_seed=1)
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        validate_task_provenance(alias)
    with pytest.raises(ValueError, match="symlinks"):
        build_cpu_task(alias / "child", task_id="t", generation_seed=1)


@pytest.mark.parametrize("cases", [1, 17, True])
def test_task_builder_rejects_invalid_case_bounds_before_writing(tmp_path: Path, cases: int) -> None:
    with pytest.raises(ValueError, match="cases per role"):
        build_cpu_task(tmp_path / "task", task_id="t", generation_seed=1, cases_per_role=cases)
    assert not (tmp_path / "task").exists()


def test_fixture_campaign_pins_assets_and_exact_full_schedule_reservations(tmp_path: Path) -> None:
    directory = tmp_path / "campaign"
    spec = build_cpu_campaign(directory, task_seeds=FIXTURE_TASK_SEEDS, repeats=PILOT_REPEATS[:1])
    assert len(spec.tasks) == 3 and len(spec.repeats) == 1 and spec.budget.max_runs == 9
    templates = []
    for task in spec.tasks:
        task_directory = directory / "tasks" / task.task_id
        provenance = validate_task_provenance(task_directory)
        assert (provenance.task_id, provenance.generation_seed) == (task.task_id, task.generation_seed)
        assert file_identity(task_directory / "bundle.lock.json")["sha256"] == task.bundle_lock_sha256
        assert file_identity(task_directory / "task.provenance.json")["sha256"] == task.provenance_sha256
        templates.append(CpuSearchSpec.model_validate_json((task_directory / "study.json").read_bytes()))
    for meter in templates[0].required_usage:
        assert getattr(spec.budget, f"max_{meter}") == 3 * sum(template.required_usage[meter] for template in templates)
    lock = CpuCampaignLock.model_validate_json((directory / "bundle.lock.json").read_bytes())
    assert lock.spec_sha256 == hashlib.sha256((directory / "campaign.json").read_bytes()).hexdigest()
    assert lock.spec_size_bytes == (directory / "campaign.json").stat().st_size
    build_cpu_campaign(tmp_path / "rebuild", task_seeds=FIXTURE_TASK_SEEDS, repeats=PILOT_REPEATS[:1])
    assert _files(directory) == _files(tmp_path / "rebuild")


def test_default_pilot_declares_eight_tasks_three_repeats_and_all_policy_runs(tmp_path: Path) -> None:
    spec = build_cpu_campaign(tmp_path / "pilot")
    assert len(spec.tasks) == 8 and len(spec.repeats) == 3
    assert spec.budget.max_runs == 72
    assert spec.budget.max_inference_calls == 7488


@pytest.mark.parametrize("seeds", [(1,), (1, 1), (1, True)])
def test_campaign_refuses_seed_aliases_before_writing(tmp_path: Path, seeds: tuple[int, ...]) -> None:
    with pytest.raises(ValueError):
        build_cpu_campaign(tmp_path / "campaign", task_seeds=seeds)
    assert not (tmp_path / "campaign").exists()
