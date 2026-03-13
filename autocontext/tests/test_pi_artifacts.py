"""Tests for AC-224: Pi session and artifact contract."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from autocontext.runtimes.pi_artifacts import PiExecutionTrace
from autocontext.runtimes.pi_cli import PiCLIConfig, PiCLIRuntime
from autocontext.storage.artifacts import ArtifactStore

# ---------------------------------------------------------------------------
# PiExecutionTrace construction and roundtrip
# ---------------------------------------------------------------------------


def test_trace_construction() -> None:
    trace = PiExecutionTrace(session_id="s1", raw_output="hello", exit_code=0)
    assert trace.session_id == "s1"
    assert trace.raw_output == "hello"
    assert trace.model == "pi"
    assert trace.cost_usd == 0.0


def test_trace_to_dict_from_dict_roundtrip() -> None:
    trace = PiExecutionTrace(
        session_id="s1",
        branch_id="b1",
        prompt_context="prompt",
        raw_output="raw",
        normalized_output="normalized",
        exit_code=0,
        duration_ms=150,
        cost_usd=0.05,
        model="pi-turbo",
        metadata={"key": "value"},
    )
    d = trace.to_dict()
    restored = PiExecutionTrace.from_dict(d)
    assert restored.session_id == "s1"
    assert restored.branch_id == "b1"
    assert restored.prompt_context == "prompt"
    assert restored.raw_output == "raw"
    assert restored.normalized_output == "normalized"
    assert restored.exit_code == 0
    assert restored.duration_ms == 150
    assert restored.cost_usd == 0.05
    assert restored.model == "pi-turbo"
    assert restored.metadata == {"key": "value"}


def test_trace_from_dict_defaults() -> None:
    trace = PiExecutionTrace.from_dict({})
    assert trace.session_id == ""
    assert trace.model == "pi"
    assert trace.exit_code == 0


# ---------------------------------------------------------------------------
# ArtifactStore.persist_pi_session / read_pi_session
# ---------------------------------------------------------------------------


def test_persist_and_read_pi_session(tmp_path: Path) -> None:
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )
    trace = PiExecutionTrace(
        session_id="sess-123",
        raw_output="raw output text",
        normalized_output="normalized",
        exit_code=0,
        duration_ms=200,
    )

    path = store.persist_pi_session("run-1", 3, trace)
    assert path.exists()
    assert path.name == "pi_session.json"

    # Verify pi_output.txt
    output_path = path.parent / "pi_output.txt"
    assert output_path.exists()
    assert output_path.read_text(encoding="utf-8") == "raw output text"

    # Read back
    data = store.read_pi_session("run-1", 3)
    assert data is not None
    assert data["session_id"] == "sess-123"
    assert data["raw_output"] == "raw output text"
    assert data["duration_ms"] == 200


def test_read_pi_session_returns_none_for_missing(tmp_path: Path) -> None:
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )
    result = store.read_pi_session("nonexistent-run", 1)
    assert result is None


def test_persist_pi_session_correct_directory(tmp_path: Path) -> None:
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )
    trace = PiExecutionTrace(session_id="s1", raw_output="data")
    path = store.persist_pi_session("my-run", 5, trace)
    expected_dir = tmp_path / "runs" / "my-run" / "generations" / "gen_5"
    assert path.parent == expected_dir


# ---------------------------------------------------------------------------
# Trace metadata preserved through AgentOutput
# ---------------------------------------------------------------------------


def test_trace_attached_to_agent_output() -> None:
    runtime = PiCLIRuntime(PiCLIConfig())
    json_output = json.dumps({"result": "hello", "model": "pi-1", "session_id": "sess-42"})
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        output = runtime.generate("test prompt")

    assert "pi_trace" in output.metadata
    trace = output.metadata["pi_trace"]
    assert isinstance(trace, PiExecutionTrace)
    assert trace.session_id == "sess-42"
    assert trace.normalized_output == "hello"
    assert trace.raw_output == json_output
    assert trace.prompt_context == "test prompt"
    assert trace.exit_code == 0


def test_trace_roundtrip_through_artifact_store(tmp_path: Path) -> None:
    """Full flow: runtime produces trace → persist → read back."""
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )

    runtime = PiCLIRuntime(PiCLIConfig())
    json_output = json.dumps({"result": "output", "model": "pi-1"})
    mock_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json_output, stderr="")

    with patch("subprocess.run", return_value=mock_result), patch("shutil.which", return_value="/usr/bin/pi"):
        agent_output = runtime.generate("original prompt")

    trace = agent_output.metadata["pi_trace"]
    store.persist_pi_session("run-x", 2, trace)

    data = store.read_pi_session("run-x", 2)
    assert data is not None
    restored = PiExecutionTrace.from_dict(data)
    assert restored.normalized_output == "output"
    assert restored.prompt_context == "original prompt"
    assert restored.model == "pi-1"


# ---------------------------------------------------------------------------
# Compatible with generation_dir layout
# ---------------------------------------------------------------------------


def test_generation_dir_layout(tmp_path: Path) -> None:
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )
    gen_dir = store.generation_dir("run-1", 3)
    trace = PiExecutionTrace(session_id="s1", raw_output="data")
    store.persist_pi_session("run-1", 3, trace)

    # Verify files are in the same gen_dir
    assert (gen_dir / "pi_session.json").exists()
    assert (gen_dir / "pi_output.txt").exists()


def test_persist_pi_session_per_role_does_not_overwrite(tmp_path: Path) -> None:
    store = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude/skills",
    )
    competitor_trace = PiExecutionTrace(session_id="comp", raw_output="competitor")
    analyst_trace = PiExecutionTrace(session_id="analyst", raw_output="analyst")

    store.persist_pi_session("run-1", 3, competitor_trace, role="competitor")
    store.persist_pi_session("run-1", 3, analyst_trace, role="analyst")

    gen_dir = store.generation_dir("run-1", 3)
    assert (gen_dir / "pi_competitor_session.json").exists()
    assert (gen_dir / "pi_competitor_output.txt").read_text(encoding="utf-8") == "competitor"
    assert (gen_dir / "pi_analyst_session.json").exists()
    assert (gen_dir / "pi_analyst_output.txt").read_text(encoding="utf-8") == "analyst"
    assert store.read_pi_session("run-1", 3, role="competitor")["session_id"] == "comp"
    assert store.read_pi_session("run-1", 3, role="analyst")["session_id"] == "analyst"
