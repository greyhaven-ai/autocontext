from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autocontext.weight_evolution.comparison_evaluation import evaluate_comparison_fixture
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import MAX_FIXTURE_BYTES, load_comparison_fixture

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_comparison"


def module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"comparison_example_{name}", EXAMPLE / f"{name}.py")
    assert spec is not None and spec.loader is not None
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return Path(shutil.copytree(EXAMPLE / "fixtures", tmp_path / "fixtures"))


def repin(bundle: Path, name: str, data: bytes) -> None:
    (bundle / f"{name}.json").write_bytes(data)
    path = bundle / "fixtures.lock.json"
    lock = json.loads(path.read_bytes())
    lock[f"{name}_sha256"], lock[f"{name}_size_bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    path.write_text(json.dumps(lock))


def test_bundled_fixture_covers_five_conditions_and_reports_negative_interactions() -> None:
    plan, evidence = load_comparison_fixture(EXAMPLE / "fixtures")
    report = evaluate_comparison_fixture(plan, evidence, SyntheticComparisonScorer()).to_dict()
    assert len(plan.cases) == 6 and len(plan.cells) == len(evidence.observations) == 60
    assert plan.protocol.seeds == (0, 17)
    metrics = {(r["dataset"], r["condition_id"]): r for r in report["metrics"]}
    assert metrics["math500", "A"]["mean_sample_accuracy"] == 0.5
    assert metrics["math500", "C"]["mean_sample_accuracy"] == 0.75
    assert metrics["math500", "CW"]["mean_sample_accuracy"] == 1.0
    assert metrics["ifeval", "D"]["mean_sample_accuracy"] == 0.25
    assert all(r["n_problem_blocks"] == 2 for r in report["contrasts"])
    assert any(r["mean"] < 0 for r in report["factorial_interactions"])
    assert report["model_execution_performed"] is False
    assert report["benchmark_result_claimed"] is False


def test_fixture_builder_reproduces_checked_in_bundle_without_model_execution(tmp_path: Path) -> None:
    target = tmp_path / "rebuilt"
    module("build_fixture").build_fixture(target)
    for name in ("study.json", "responses.json", "fixtures.lock.json"):
        assert (target / name).read_bytes() == (EXAMPLE / "fixtures" / name).read_bytes()
    with pytest.raises(ValueError, match="new directory"):
        module("build_fixture").build_fixture(target)


@pytest.mark.parametrize("name", ["study", "responses"])
def test_byte_tampering_fails_before_parsing(bundle: Path, name: str) -> None:
    with (bundle / f"{name}.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="size/hash mismatch"):
        load_comparison_fixture(bundle)


@pytest.mark.parametrize("name", ["study", "responses"])
def test_duplicate_json_keys_rejected_even_if_bytes_re_pinned(bundle: Path, name: str) -> None:
    original = (bundle / f"{name}.json").read_bytes()
    repin(bundle, name, b'{"schema_version":1,' + original[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_comparison_fixture(bundle)


def test_fixture_cannot_be_relabelled_as_live(bundle: Path) -> None:
    data = json.loads((bundle / "responses.json").read_bytes())
    data["model_execution_performed"] = True
    repin(bundle, "responses", json.dumps(data).encode())
    with pytest.raises(ValueError, match="model_execution_performed"):
        load_comparison_fixture(bundle)


def test_response_plan_identity_must_match_even_if_bytes_re_pinned(bundle: Path) -> None:
    data = json.loads((bundle / "responses.json").read_bytes())
    data["plan_digest"] = "f" * 64
    repin(bundle, "responses", json.dumps(data).encode())
    with pytest.raises(ValueError, match="different frozen study"):
        load_comparison_fixture(bundle)


def test_bundle_byte_limit_checked_before_reading_payload(bundle: Path) -> None:
    path = bundle / "fixtures.lock.json"
    lock = json.loads(path.read_bytes())
    lock["study_size_bytes"] = MAX_FIXTURE_BYTES + 1
    path.write_text(json.dumps(lock))
    with pytest.raises(ValueError, match="study_size_bytes"):
        load_comparison_fixture(bundle)


@pytest.mark.parametrize("name", ["study.json", "responses.json", "fixtures.lock.json"])
def test_bundle_rejects_symlink_payloads(bundle: Path, name: str) -> None:
    path = bundle / name
    path.unlink()
    path.symlink_to(EXAMPLE / "fixtures" / name)
    with pytest.raises(ValueError, match="symlink"):
        load_comparison_fixture(bundle)


def test_commands_replay_offline_and_publish_deterministic_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    example = module("run")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline comparison cannot contact a service or spawn a model worker")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert example.main(["validate"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["gpu_execution_deferred"] is True and checked["model_execution_performed"] is False
    for name in ("first", "second"):
        assert example.main(["run-fixtures", "--output", str(tmp_path / name)]) == 0
        assert json.loads(capsys.readouterr().out)["report_digest"] == checked["report_digest"]
    for name in ("report.json", "report.md", "complete.json"):
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes()
    assert example.main(["run-fixtures", "--output", str(tmp_path / "first")]) == 2
    assert json.loads(capsys.readouterr().err)["ok"] is False


def test_command_rejects_execute_and_gpu_flags(capsys: pytest.CaptureFixture[str]) -> None:
    example = module("run")
    for flag in ("--execute", "--gpu-device"):
        with pytest.raises(SystemExit) as exc:
            example.main(["validate", flag])
        assert exc.value.code == 2
        capsys.readouterr()


def test_command_checksum_failure_publishes_no_output(bundle: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (bundle / "responses.json").write_text("{}")
    output = tmp_path / "report"
    assert module("run").main(["--fixture-root", str(bundle), "run-fixtures", "--output", str(output)]) == 2
    assert "size/hash mismatch" in json.loads(capsys.readouterr().err)["error"]
    assert not output.exists()
