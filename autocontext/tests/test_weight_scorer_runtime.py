from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autocontext.weight_evolution import scorer_runtime as runtime
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem
from autocontext.weight_evolution.inference_models import GenerationSample, text_digest
from autocontext.weight_evolution.scorers import ScorerInfrastructureError

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"


def lock() -> runtime.ScorerLock:
    return runtime.load_scorer_lock(EXAMPLE / "scorer.lock.json")


def changed_lock(**values):
    value = lock().to_dict()
    value.update(values)
    return runtime.ScorerLock.model_validate_json(json.dumps(value))


def test_checked_in_lock_matches_installation_manifest_and_upstream_math_versions():
    value = lock()
    assert len(value.packages) == 21
    versions = {pin.name: pin.version for pin in value.packages}
    assert versions["math-verify"] == "0.9.0"
    assert versions["latex2sympy2-extended"] == "1.11.0"
    assert versions["antlr4-python3-runtime"] == "4.13.2"
    assert len(value.assets) == 5
    assert len(value.punkt_members) == 4
    assert len(value.digest) == 64


@pytest.mark.parametrize("change", ["package_missing", "package_duplicate", "math_version", "source", "resources", "seed"])
def test_lock_rejects_incomplete_or_changed_scoring_protocol(change):
    value = lock().to_dict()
    if change == "package_missing":
        value["packages"].pop()
    elif change == "package_duplicate":
        value["packages"].append(value["packages"][0])
    elif change == "math_version":
        for pin in value["packages"]:
            if pin["name"] == "math-verify":
                pin["version"] = "0.8.0"
    elif change == "source":
        value["assets"][0]["url"] = "https://example.com/scorer.py"
    elif change == "resources":
        value["punkt_members"].pop()
    else:
        value["random_seed"] = 1
    with pytest.raises(ValueError):
        runtime.ScorerLock.model_validate_json(json.dumps(value))


def test_lock_loader_rejects_changed_requirements(tmp_path):
    path = tmp_path / "scorer.lock.json"
    path.write_text((EXAMPLE / "scorer.lock.json").read_text())
    (tmp_path / "scorer-requirements.txt").write_text("math-verify==0.9.0\n")
    with pytest.raises(ValueError, match="requirements differ"):
        runtime.load_scorer_lock(path)


def test_lock_loader_rejects_duplicate_keys(tmp_path):
    path = tmp_path / "scorer.lock.json"
    path.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(ValueError, match="duplicate"):
        runtime.load_scorer_lock(path)


def test_manifest_and_runtime_package_pins_cannot_disagree(tmp_path):
    value = lock().to_dict()
    for pin in value["packages"]:
        if pin["name"] == "sympy":
            pin["version"] = "1.15.0"
    path = tmp_path / "scorer.lock.json"
    path.write_text(json.dumps(value))
    (tmp_path / "scorer-requirements.txt").write_bytes((EXAMPLE / "scorer-requirements.txt").read_bytes())
    with pytest.raises(ValueError, match="package versions differ"):
        runtime.load_scorer_lock(path)


def test_environment_records_exact_versions(monkeypatch):
    value = lock()
    versions = {pin.name: pin.version for pin in value.packages}
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.13.1")
    monkeypatch.setattr(runtime.importlib.metadata, "version", versions.__getitem__)
    result = runtime.check_scorer_environment(value)
    assert result["packages"] == versions
    assert result["python_version"] == "3.13.1"


@pytest.mark.parametrize("failure", ["python", "version", "missing"])
def test_environment_fails_before_imports(monkeypatch, failure):
    monkeypatch.setattr(runtime.platform, "python_version", lambda: "3.12.9" if failure == "python" else "3.13.1")
    if failure == "missing":
        monkeypatch.setattr(runtime.importlib.metadata, "version",
                            Mock(side_effect=runtime.importlib.metadata.PackageNotFoundError()))
    else:
        monkeypatch.setattr(runtime.importlib.metadata, "version", lambda name: "0.0.0")
    with pytest.raises(ScorerInfrastructureError):
        runtime.check_scorer_environment(lock())


def _synthetic_assets(tmp_path):
    value = lock().to_dict()
    for pin in value["assets"]:
        payload = b"this data must not be imported\n"
        pin["size_bytes"] = len(payload)
        pin["sha256"] = hashlib.sha256(payload).hexdigest()
        (tmp_path / pin["filename"]).write_bytes(payload)
    return runtime.ScorerLock.model_validate_json(json.dumps(value))


def test_asset_verification_is_data_only_and_checks_every_pin(tmp_path, monkeypatch):
    value = _synthetic_assets(tmp_path)
    monkeypatch.setattr(runtime, "_official_registry", Mock(side_effect=AssertionError("must not execute")))
    assert runtime.verify_scorer_assets(value, tmp_path)["code_executed"] is False
    (tmp_path / value.assets[0].filename).write_text("corrupt")
    with pytest.raises(ValueError, match="differs"):
        runtime.verify_scorer_assets(value, tmp_path)


def test_verification_rejects_symlink(tmp_path):
    value = _synthetic_assets(tmp_path)
    file = tmp_path / value.assets[0].filename
    contents = file.read_bytes()
    file.unlink()
    target = tmp_path / "real-source"
    target.write_bytes(contents)
    file.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        runtime.verify_scorer_assets(value, tmp_path)


def test_fetch_preserves_corrupt_existing_asset_and_never_imports(tmp_path, monkeypatch):
    value = _synthetic_assets(tmp_path)
    corrupt = tmp_path / value.assets[0].filename
    corrupt.write_text("keep me")
    opener = Mock(side_effect=AssertionError("must not download"))
    monkeypatch.setattr(runtime.urllib.request, "urlopen", opener)
    with pytest.raises(ValueError, match="differs"):
        runtime.fetch_scorer_assets(value, tmp_path)
    assert corrupt.read_text() == "keep me"
    opener.assert_not_called()


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_fetch_requires_bounded_timeout(tmp_path, timeout):
    with pytest.raises(ValueError, match="timeout"):
        runtime.fetch_scorer_assets(lock(), tmp_path, timeout_seconds=timeout)


def test_official_loader_refuses_ambient_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "instruction_following_eval", types.ModuleType("instruction_following_eval"))
    with pytest.raises(ScorerInfrastructureError, match="ambient"):
        runtime._official_registry({})


def test_official_loader_isolates_module_search_and_cleans_on_failure():
    sources = {"instructions_util.py": b"raise ValueError('stop')"}
    before = sys.path[:]
    with pytest.raises(ValueError, match="stop"):
        runtime._official_registry(sources)
    assert sys.path == before
    assert "instruction_following_eval" not in sys.modules
    assert "instruction_following_eval.instructions_util" not in sys.modules


def _fake_scorer():
    # This is only a boundary fixture. It cannot create an official report.
    scorer = runtime.PinnedScorer.__new__(runtime.PinnedScorer)
    scorer.lock = lock()
    scorer._gold = {}
    scorer._math = SimpleNamespace(parse=lambda text, **kwargs: [] if "invalid" in text else [text],
                                   verify=lambda gold, pred, **kwargs: gold == pred)
    from contextlib import nullcontext

    scorer._context = nullcontext
    scorer.registry = {}
    return scorer


def _sample(text="no boxed answer", problem_id="p"):
    return GenerationSample(problem_id=problem_id, sample_index=0, rendered_prompt="prompt",
                            prompt_sha256=text_digest("prompt"), prompt_token_ids=(1,), input_tokens=1,
                            text=text, response_sha256=text_digest(text), token_ids=(2,), output_tokens=1,
                            finish_reason="stop", stop_reason=None)


def test_all_gold_answers_are_validated_even_without_a_boxed_response():
    scorer = _fake_scorer()
    problem = EvaluationProblem(problem_id="p", text="Question", answer="invalid")
    with pytest.raises(ScorerInfrastructureError, match="unparseable gold"):
        scorer.score_sample("math500", problem, _sample())


def test_wrong_and_missing_answers_remain_zero_score_observations():
    scorer = _fake_scorer()
    problem = EvaluationProblem(problem_id="p", text="Question", answer="2")
    assert scorer.score_sample("math500", problem, _sample()) == 0
    assert scorer.score_sample("math500", problem, _sample(r"\boxed{3}")) == 0
    assert scorer.score_sample("math500", problem, _sample(r"\boxed{2}")) == 1


def test_scoring_rejects_foreign_problem_identity():
    scorer = _fake_scorer()
    problem = EvaluationProblem(problem_id="p", text="Question", answer="2")
    with pytest.raises(ScorerInfrastructureError, match="identity"):
        scorer.score_sample("math500", problem, _sample(problem_id="other"))


def test_ifeval_unknown_instruction_invalidates_cohort_before_scoring():
    scorer = _fake_scorer()
    problem = EvaluationProblem(problem_id="p", text="Question", instructions=(
        DatasetInstruction(instruction_id="unknown:checker", kwargs={}),
    ))
    with pytest.raises(ScorerInfrastructureError, match="invalid IFEval"):
        scorer.validate_cohort("ifeval", (problem,))


def test_invalid_resource_archive_fails_before_official_code_load(tmp_path, monkeypatch):
    value = _synthetic_assets(tmp_path)
    monkeypatch.setattr(runtime, "check_scorer_environment", lambda lock: {})
    load = Mock(side_effect=AssertionError("must not execute official code"))
    monkeypatch.setattr(runtime, "_official_registry", load)
    with pytest.raises(ScorerInfrastructureError, match="initialization failed"):
        runtime.load_pinned_scorer(value, tmp_path)
    load.assert_not_called()


def test_scoring_context_replaces_ambient_resources_and_restores_state(tmp_path):
    import random

    scorer = runtime.PinnedScorer.__new__(runtime.PinnedScorer)
    scorer.lock = lock()
    scorer.resource_root = tmp_path
    clear_cache = Mock()
    nltk = SimpleNamespace(data=SimpleNamespace(path=["ambient"], clear_cache=clear_cache),
                           tokenize=SimpleNamespace(_get_punkt_tokenizer=SimpleNamespace(cache_clear=clear_cache)))
    factory = object()
    scorer._nltk = nltk
    scorer._langdetect = SimpleNamespace(DetectorFactory=SimpleNamespace(seed=123))
    scorer._language_factory_module = SimpleNamespace(_factory="ambient factory")
    scorer._language_factory = factory
    old_random = random.getstate()
    with pytest.raises(RuntimeError, match="fixture failure"), scorer._context():
        assert nltk.data.path == [str(tmp_path)]
        assert scorer._langdetect.DetectorFactory.seed == 0
        assert scorer._language_factory_module._factory is factory
        assert random.random() == random.Random(0).random()
        raise RuntimeError("fixture failure")
    assert nltk.data.path == ["ambient"]
    assert scorer._langdetect.DetectorFactory.seed == 123
    assert scorer._language_factory_module._factory == "ambient factory"
    assert random.getstate() == old_random


def test_math_gold_parser_errors_are_infrastructure_failures():
    scorer = _fake_scorer()
    scorer._math.parse = Mock(side_effect=RuntimeError("parser crashed"))
    with pytest.raises(ScorerInfrastructureError, match="gold parsing failed"):
        scorer.validate_cohort("math500", (EvaluationProblem(problem_id="p", text="Q", answer="2"),))
