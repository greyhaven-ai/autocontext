"""AC-504: Evidence workspace tests."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from autocontext.evidence.manifest import render_artifact_detail, render_evidence_manifest
from autocontext.evidence.materializer import materialize_workspace
from autocontext.evidence.tracker import compute_utilization, load_access_log, record_access, save_access_log
from autocontext.evidence.workspace import EvidenceArtifact, EvidenceWorkspace


def _make_artifact(
    artifact_id: str = "test_abc123",
    kind: str = "trace",
    **overrides: object,
) -> EvidenceArtifact:
    defaults = {
        "artifact_id": artifact_id,
        "source_run_id": "run_001",
        "kind": kind,
        "path": "test_abc123_events.ndjson",
        "summary": f"{kind}: events.ndjson from run_001",
        "size_bytes": 1024,
        "generation": 1,
    }
    defaults.update(overrides)
    return EvidenceArtifact(**defaults)  # type: ignore[arg-type]


def _make_workspace(artifacts: list[EvidenceArtifact] | None = None, **overrides: object) -> EvidenceWorkspace:
    defaults = {
        "workspace_dir": "/tmp/test_workspace",
        "source_runs": ["run_001"],
        "artifacts": artifacts or [_make_artifact()],
        "total_size_bytes": sum(a.size_bytes for a in (artifacts or [_make_artifact()])),
        "materialized_at": "2026-04-06T00:00:00+00:00",
    }
    defaults.update(overrides)
    return EvidenceWorkspace(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def evidence_tmpdir():
    """Create a temporary directory with mock run and knowledge artifacts."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Run artifacts
        run_dir = root / "runs" / "run_001"
        (run_dir).mkdir(parents=True)
        (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")
        gen_dir = run_dir / "gen_1"
        gen_dir.mkdir()
        (gen_dir / "analyst_output.md").write_text("# Analysis\nFindings here.", encoding="utf-8")
        (gen_dir / "gate_decision.json").write_text('{"decision":"advance","delta":0.05}', encoding="utf-8")

        # Knowledge artifacts
        k_dir = root / "knowledge" / "test_scenario"
        k_dir.mkdir(parents=True)
        (k_dir / "playbook.md").write_text("# Playbook\nStep 1.", encoding="utf-8")
        (k_dir / "dead_ends.md").write_text("# Dead Ends\nApproach X failed.", encoding="utf-8")
        tools_dir = k_dir / "tools"
        tools_dir.mkdir()
        (tools_dir / "validator.py").write_text("def validate(): pass", encoding="utf-8")
        analysis_dir = k_dir / "analysis"
        analysis_dir.mkdir()
        (analysis_dir / "gen_1.md").write_text("Gen 1 analysis.", encoding="utf-8")

        yield root


# ---------------------------------------------------------------------------
# Workspace model tests
# ---------------------------------------------------------------------------


class TestWorkspaceModel:
    def test_get_artifact_by_id(self) -> None:
        a = _make_artifact(artifact_id="abc123")
        ws = _make_workspace(artifacts=[a])
        assert ws.get_artifact("abc123") is a

    def test_get_artifact_returns_none_for_missing(self) -> None:
        ws = _make_workspace()
        assert ws.get_artifact("nonexistent") is None

    def test_list_by_kind_filters_correctly(self) -> None:
        artifacts = [
            _make_artifact(artifact_id="a1", kind="trace"),
            _make_artifact(artifact_id="a2", kind="gate_decision"),
            _make_artifact(artifact_id="a3", kind="trace"),
        ]
        ws = _make_workspace(artifacts=artifacts)
        traces = ws.list_by_kind("trace")
        assert len(traces) == 2
        assert all(t.kind == "trace" for t in traces)

    def test_workspace_to_dict_roundtrip(self) -> None:
        ws = _make_workspace()
        d = ws.to_dict()
        restored = EvidenceWorkspace.from_dict(d)
        assert restored.workspace_dir == ws.workspace_dir
        assert len(restored.artifacts) == len(ws.artifacts)
        assert restored.materialized_at == ws.materialized_at

    def test_artifact_to_dict(self) -> None:
        a = _make_artifact(artifact_id="x1", kind="report", size_bytes=2048)
        d = a.to_dict()
        assert d["artifact_id"] == "x1"
        assert d["kind"] == "report"
        assert d["size_bytes"] == 2048


# ---------------------------------------------------------------------------
# Materializer tests
# ---------------------------------------------------------------------------


class TestMaterializer:
    def test_creates_workspace_dir(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )
        assert ws_dir.is_dir()

    def test_copies_artifacts(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        ws = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )
        assert len(ws.artifacts) > 0
        for a in ws.artifacts:
            assert (ws_dir / a.path).exists()

    def test_respects_budget(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        # Very tight budget — should skip some artifacts
        ws = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            budget_bytes=100,  # Very small
            scenario_name="test_scenario",
        )
        assert ws.total_size_bytes <= 100

    def test_prioritizes_gate_decisions(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        # With tight budget, gate decisions should be included first
        ws = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            budget_bytes=200,
            scenario_name="test_scenario",
        )
        if ws.artifacts:
            kinds = [a.kind for a in ws.artifacts]
            # Gate decisions should appear before traces/logs if both present
            if "gate_decision" in kinds and "log" in kinds:
                assert kinds.index("gate_decision") < kinds.index("log")

    def test_handles_empty_runs(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace_empty"
        ws = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["nonexistent_run"],
            workspace_dir=ws_dir,
        )
        # Should still succeed with just knowledge artifacts or empty
        assert isinstance(ws, EvidenceWorkspace)

    def test_writes_manifest_json(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )
        manifest = ws_dir / "manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert "artifacts" in data

    def test_rematerialization_removes_stale_workspace_files(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        first = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
        )
        trace_artifact = next(a for a in first.artifacts if a.kind == "trace")
        stale_path = ws_dir / trace_artifact.path
        assert stale_path.exists()

        (evidence_tmpdir / "runs" / "run_001" / "events.ndjson").unlink()

        second = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
        )
        assert all(a.kind != "trace" for a in second.artifacts)
        assert not stale_path.exists()

    def test_scan_knowledge_finds_playbook_and_tools(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        ws = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=[],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )
        kinds = {a.kind for a in ws.artifacts}
        assert "report" in kinds  # playbook.md, dead_ends.md
        assert "tool" in kinds  # tools/validator.py

    def test_reuses_cached_workspace_when_sources_are_unchanged(self, evidence_tmpdir: Path) -> None:
        ws_dir = evidence_tmpdir / "workspace"
        first = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )

        second = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scenario_name="test_scenario",
        )

        assert second.materialized_at == first.materialized_at

    def test_cached_workspace_rescans_for_secrets_when_enabled(
        self,
        evidence_tmpdir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autocontext.security.scanner import ScanFinding, ScanResult, SecretScanner

        calls: list[str] = []
        ws_dir = evidence_tmpdir / "workspace"

        def clean_scan(_self: SecretScanner, directory: str) -> ScanResult:
            calls.append("clean")
            return ScanResult(findings=[], scanned_path=directory, scanner_available=False)

        def dirty_scan(_self: SecretScanner, directory: str) -> ScanResult:
            calls.append("dirty")
            flagged_path = next(Path(directory).glob("*events.ndjson"))
            return ScanResult(
                findings=[
                    ScanFinding(
                        detector="GenericApiKey",
                        file_path=str(flagged_path),
                        verified=False,
                        raw_preview="sk-...",
                    )
                ],
                scanned_path=directory,
                scanner_available=True,
            )

        monkeypatch.setattr(SecretScanner, "scan", clean_scan)
        first = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scan_for_secrets=True,
        )

        assert first.artifacts

        monkeypatch.setattr(SecretScanner, "scan", dirty_scan)
        second = materialize_workspace(
            knowledge_root=evidence_tmpdir / "knowledge",
            runs_root=evidence_tmpdir / "runs",
            source_run_ids=["run_001"],
            workspace_dir=ws_dir,
            scan_for_secrets=True,
        )

        assert calls == ["clean", "dirty"]
        assert len(second.artifacts) < len(first.artifacts)
        assert all(not artifact.path.endswith("events.ndjson") for artifact in second.artifacts)
        manifest = json.loads((ws_dir / "manifest.json").read_text(encoding="utf-8"))
        assert all(not artifact["path"].endswith("events.ndjson") for artifact in manifest["artifacts"])


# ---------------------------------------------------------------------------
# Manifest tests
# ---------------------------------------------------------------------------


class TestManifest:
    def test_includes_artifact_counts(self) -> None:
        artifacts = [
            _make_artifact(artifact_id="a1", kind="trace"),
            _make_artifact(artifact_id="a2", kind="trace"),
            _make_artifact(artifact_id="a3", kind="gate_decision"),
        ]
        ws = _make_workspace(artifacts=artifacts)
        output = render_evidence_manifest(ws)
        assert "Traces" in output
        assert "2" in output
        assert "Gate decisions" in output

    def test_includes_total_size(self) -> None:
        ws = _make_workspace()
        ws.total_size_bytes = 5 * 1024 * 1024  # 5 MB
        output = render_evidence_manifest(ws)
        assert "5.0 MB" in output

    def test_includes_source_run_count(self) -> None:
        ws = _make_workspace(source_runs=["run_001", "run_002"])
        output = render_evidence_manifest(ws)
        assert "2 prior run" in output

    def test_renders_evidence_cards_with_provenance(self) -> None:
        artifacts = [
            _make_artifact(
                artifact_id="gate_abc123",
                kind="gate_decision",
                source_run_id="run_002",
                generation=3,
                source_path="/tmp/source/run_002/gate_decision.json",
                path="gate_abc123_gate_decision.json",
            ),
        ]
        ws = _make_workspace(artifacts=artifacts, source_runs=["run_002"])
        output = render_evidence_manifest(ws, role="analyst")
        assert "Top evidence cards" in output
        assert "gate_abc123" in output
        assert "run_002" in output
        assert "gen 3" in output.lower()

    def test_render_artifact_detail_reads_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "test_file.md").write_text("Hello evidence!", encoding="utf-8")
            artifact = _make_artifact(path="test_file.md", source_path="/tmp/source/test_file.md")
            result = render_artifact_detail(artifact, tmp)
            assert "Source path" in result
            assert "Hello evidence!" in result

    def test_render_artifact_detail_supports_excerpt_windows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "test_file.md").write_text(
                "line 1\nline 2\nline 3\nline 4\nline 5\n",
                encoding="utf-8",
            )
            artifact = _make_artifact(path="test_file.md", source_path="/tmp/source/test_file.md")
            result = render_artifact_detail(artifact, tmp, excerpt_lines=3)
            assert "line 1" in result
            assert "line 3" in result
            assert "line 4" not in result
            assert "request full artifact" in result.lower()

    def test_render_artifact_detail_rejects_workspace_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace_dir = Path(tmp) / "workspace"
            workspace_dir.mkdir()
            outside_path = Path(tmp) / "outside.txt"
            outside_path.write_text("secret outside workspace", encoding="utf-8")
            artifact = _make_artifact(path="../../outside.txt", source_path=str(outside_path))

            result = render_artifact_detail(artifact, str(workspace_dir))

            assert "secret outside workspace" not in result
            assert "not found" in result.lower()

    def test_render_artifact_detail_handles_missing(self) -> None:
        artifact = _make_artifact(path="nonexistent.md")
        result = render_artifact_detail(artifact, "/tmp/does_not_exist")
        assert "not found" in result.lower()


# ---------------------------------------------------------------------------
# Tracker tests
# ---------------------------------------------------------------------------


class TestTracker:
    def test_record_access_adds_to_list(self) -> None:
        ws = _make_workspace()
        record_access(ws, "abc123")
        assert "abc123" in ws.accessed_artifacts

    def test_record_access_deduplicates(self) -> None:
        ws = _make_workspace()
        record_access(ws, "abc123")
        record_access(ws, "abc123")
        assert ws.accessed_artifacts.count("abc123") == 1

    def test_save_and_load_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ws = _make_workspace(workspace_dir=tmp)
            record_access(ws, "a1")
            record_access(ws, "a2")
            save_access_log(ws)
            loaded = load_access_log(tmp)
            assert loaded == ["a1", "a2"]

    def test_utilization_counts_correctly(self) -> None:
        artifacts = [
            _make_artifact(artifact_id="a1", kind="trace"),
            _make_artifact(artifact_id="a2", kind="gate_decision"),
            _make_artifact(artifact_id="a3", kind="trace"),
        ]
        ws = _make_workspace(artifacts=artifacts)
        record_access(ws, "a1")
        record_access(ws, "a2")

        stats = compute_utilization(ws)
        assert stats["total_artifacts"] == 3
        assert stats["accessed_count"] == 2
        assert stats["utilization_percent"] == pytest.approx(66.7, abs=0.1)

    def test_utilization_zero_when_nothing_accessed(self) -> None:
        ws = _make_workspace()
        stats = compute_utilization(ws)
        assert stats["accessed_count"] == 0
        assert stats["utilization_percent"] == 0.0
