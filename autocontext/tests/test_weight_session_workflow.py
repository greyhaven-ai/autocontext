from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution.comparison_io import load_comparison_fixture
from autocontext.weight_evolution.comparison_models import ContextArtifact
from autocontext.weight_evolution.session import EditingSession
from autocontext.weight_evolution.session_fixtures import ScriptedSessionAdapter
from autocontext.weight_evolution.session_workflow import (
    load_session_workflow,
    run_session_workflow,
    validate_session_workflow,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_sessions"
COMPARISON = EXAMPLE.parent / "qwen_comparison" / "fixtures"


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return Path(shutil.copytree(EXAMPLE / "fixtures", tmp_path / "fixtures"))


def repin(bundle: Path, name: str, data: bytes) -> None:
    (bundle / f"{name}.json").write_bytes(data)
    path = bundle / "fixtures.lock.json"
    lock = json.loads(path.read_bytes())
    lock[f"{name}_sha256"], lock[f"{name}_size_bytes"] = hashlib.sha256(data).hexdigest(), len(data)
    path.write_text(json.dumps(lock))


def run(output: Path, bundle: Path = EXAMPLE / "fixtures") -> dict[str, Any]:
    return run_session_workflow(bundle, comparison_directory=COMPARISON, output_dir=output)


def test_full_workflow_exports_into_identical_m5_cells_and_preserves_failed_cost(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = run(output)
    source, _ = load_comparison_fixture(COMPARISON)
    plan = json.loads((output / "comparison-plan.json").read_bytes())
    assert plan["cells"] == [cell.to_dict() for cell in source.cells]
    assert tuple(condition["condition_id"] for condition in plan["conditions"]) == ("A", "C", "W", "CW", "D")
    assert result["gpu_execution_deferred"] is True
    assert result["model_execution_performed"] is result["confirmation_used"] is result["promotion_eligible"] is False
    for mechanism, artifact_name in (("C", "learned_context"), ("W", "edited_checkpoint"), ("D", "selected_decoding")):
        session_dir = output / "sessions" / mechanism
        assert result["sessions"][mechanism]["reserved_usage"]["operations"] == 4
        assert len(tuple((session_dir / "failures").iterdir())) == 1
        exported = json.loads((session_dir / "export.json").read_bytes())["payload"]
        assert exported["status"] == "sealed"
        assert exported["artifact"] == plan["artifacts"][artifact_name]
        assert exported["artifact"]["source"]["declared_search_calls"] == 4
        assert exported["artifact"]["source"]["declared_search_units"] == 98304
        assert exported["artifact"]["source"]["source_problem_ids"] == [
            "practice.pears", "practice.sum", "practice.words",
        ]
    failed = json.loads((output / "sessions" / "W" / "failures" / "00000003.json").read_bytes())["payload"]
    assert failed["feedback"] == [] and failed["actual_usage_unknown"]
    successful = json.loads((output / "sessions" / "W" / "completed" / "00000004.json").read_bytes())["payload"]
    assert [row["score"] for row in successful["feedback"]] == [1, 0, 1]


def test_exact_resume_never_redispatches_and_is_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "run"
    first = run(output)
    before = {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resume dispatched the adapter")

    monkeypatch.setattr(ScriptedSessionAdapter, "execute", forbidden)
    assert run(output) == first
    assert {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()} == before


def test_workflow_requests_never_include_gold_or_hidden_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []
    execute = ScriptedSessionAdapter.execute

    def capture(self: ScriptedSessionAdapter, request: Any) -> Any:
        seen.append(request.to_dict())
        return execute(self, request)

    monkeypatch.setattr(ScriptedSessionAdapter, "execute", capture)
    run(tmp_path / "run")
    assert len(seen) == 12
    encoded = json.dumps(seen)
    for secret in ('"answer"', '"instructions"', "confirmation.", "audit.", "What is eight plus six?",
                   "What is 17 plus 25?"):
        assert secret not in encoded
    assert all(set(row) == {"dataset", "problem_id", "text"} for request in seen for row in request["practice"])


def test_no_network_process_or_model_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline session attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    run(tmp_path / "run")


def test_builder_recreates_pinned_bundle(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("session_example_builder", EXAMPLE / "build_fixture.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "rebuilt"
    module.build_fixture(target)
    for name in ("data.json", "workflow.json", "fixtures.lock.json"):
        assert (target / name).read_bytes() == (EXAMPLE / "fixtures" / name).read_bytes()
    with pytest.raises(ValueError, match="new directory"):
        module.build_fixture(target)


@pytest.mark.parametrize("name", ["data", "workflow"])
def test_changed_bundle_bytes_rejected(bundle: Path, name: str) -> None:
    with (bundle / f"{name}.json").open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="size/hash|size/hash mismatch"):
        load_session_workflow(bundle)


@pytest.mark.parametrize("name", ["data", "workflow"])
def test_duplicate_keys_rejected_even_after_repin(bundle: Path, name: str) -> None:
    payload = (bundle / f"{name}.json").read_bytes()
    repin(bundle, name, b'{"schema_version":1,' + payload[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_session_workflow(bundle)


@pytest.mark.parametrize("field", ["source_comparison_plan_digest", "source_comparison_evidence_digest"])
def test_different_source_identity_fails_before_output(bundle: Path, tmp_path: Path, field: str) -> None:
    workflow = json.loads((bundle / "workflow.json").read_bytes())
    workflow[field] = "f" * 64
    repin(bundle, "workflow", json.dumps(workflow).encode())
    with pytest.raises(ValueError, match="source comparison identity"):
        run(tmp_path / "run", bundle)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("filename", ["report.json", "report.md", "complete.json"])
def test_report_tampering_stops_resume(tmp_path: Path, filename: str) -> None:
    output = tmp_path / "run"
    run(output)
    path = output / "comparison" / filename
    if filename == "complete.json":
        value = json.loads(path.read_bytes())
        value["report_digest"] = "f" * 64
        path.write_text(json.dumps(value))
    else:
        path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="differs|byte budget"):
        run(output)


def test_incomplete_report_is_not_repaired(tmp_path: Path) -> None:
    output = tmp_path / "run"
    run(output)
    (output / "comparison" / "complete.json").unlink()
    with pytest.raises(ValueError, match="incomplete comparison report"):
        run(output)
    assert not (output / "comparison" / "complete.json").exists()


def test_validate_only_does_not_run_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validate dispatched an adapter")

    monkeypatch.setattr(ScriptedSessionAdapter, "execute", forbidden)
    result = validate_session_workflow(EXAMPLE / "fixtures", COMPARISON)
    assert {role: value["count"] for role, value in result["splits"].items()} == {
        "practice": 3, "confirmation": 3, "final_audit": 6,
    }


def test_output_symlink_and_unowned_directory_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(link)
    (real / "unrelated").write_text("preserve me")
    with pytest.raises(ValueError, match="nonempty directory"):
        run(real)
    assert (real / "unrelated").read_text() == "preserve me"


@pytest.mark.parametrize("filename", ["workflow.json", "comparison-plan.json", "complete.json"])
def test_workflow_artifact_tampering_stops_resume(tmp_path: Path, filename: str) -> None:
    output = tmp_path / "run"
    run(output)
    (output / filename).write_text("{}")
    with pytest.raises(ValueError, match="immutable workflow artifact changed"):
        run(output)


@pytest.mark.parametrize("filename,field,replacement", [
    ("complete.json", "model_execution_performed", 0),
    ("complete.json", "schema_version", True),
    ("comparison/complete.json", "schema_version", True),
])
def test_type_changing_tampering_is_not_treated_as_equal(
    tmp_path: Path, filename: str, field: str, replacement: Any,
) -> None:
    output = tmp_path / "run"
    run(output)
    path = output / filename
    value = json.loads(path.read_bytes())
    value[field] = replacement
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="changed|differs"):
        run(output)


@pytest.mark.parametrize("field,value", [
    ("max_search_calls_per_mechanism", 3), ("max_search_units_per_mechanism", 98303),
])
def test_underfunded_comparison_search_rejected_before_dispatch(
    bundle: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: int,
) -> None:
    workflow = json.loads((bundle / "workflow.json").read_bytes())
    workflow["budgets"][field] = value
    repin(bundle, "workflow", json.dumps(workflow).encode())

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("underfunded program dispatched")

    monkeypatch.setattr(ScriptedSessionAdapter, "execute", forbidden)
    with pytest.raises(ValueError, match="reservations exceed"):
        validate_session_workflow(bundle, COMPARISON)
    with pytest.raises(ValueError, match="reservations exceed"):
        run(tmp_path / "run", bundle)
    assert not (tmp_path / "run").exists()


def test_different_candidate_content_cannot_reuse_recorded_audit_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = EditingSession.export_candidate

    def changed(self: EditingSession) -> Any:
        artifact = export(self)
        if isinstance(artifact, ContextArtifact):
            return artifact.model_copy(update={"text": "A different instruction without evidence."})
        return artifact

    monkeypatch.setattr(EditingSession, "export_candidate", changed)
    output = tmp_path / "run"
    with pytest.raises(ValueError, match="cell identities"):
        run(output)
    assert not (output / "comparison").exists()
    assert not (output / "complete.json").exists()
