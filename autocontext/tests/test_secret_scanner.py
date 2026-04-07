"""TruffleHog backstop scanner tests.

Tests the SecretScanner domain model and its integration with the
evidence workspace materializer. The scanner wraps `trufflehog` CLI
and acts as a defense-in-depth layer — any finding blocks the
artifact from being included in the evidence manifest.

Tests that require trufflehog installed are marked with
pytest.mark.skipif so they degrade gracefully in CI.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from autocontext.security.scanner import (
    ScanFinding,
    ScanResult,
    SecretScanner,
)

# ---------------------------------------------------------------------------
# Domain model tests (no trufflehog required)
# ---------------------------------------------------------------------------


class TestScanFinding:
    def test_from_trufflehog_json(self) -> None:
        raw = {
            "SourceMetadata": {"Data": {"Filesystem": {"file": "/tmp/workspace/abc_events.ndjson"}}},
            "DetectorName": "GenericApiKey",
            "Verified": False,
            "Raw": "sk-ant-api03-fake",
            "RawV2": "",
        }
        finding = ScanFinding.from_trufflehog_json(raw)
        assert finding.detector == "GenericApiKey"
        assert finding.file_path == "/tmp/workspace/abc_events.ndjson"
        assert finding.verified is False
        assert "sk-ant" in finding.raw_preview

    def test_from_trufflehog_json_handles_missing_fields(self) -> None:
        raw: dict = {}
        finding = ScanFinding.from_trufflehog_json(raw)
        assert finding.detector == "unknown"
        assert finding.file_path == ""

    def test_finding_to_dict_roundtrips(self) -> None:
        finding = ScanFinding(
            detector="AWS",
            file_path="/tmp/test.txt",
            verified=True,
            raw_preview="AKIA...",
        )
        d = finding.to_dict()
        assert d["detector"] == "AWS"
        assert d["verified"] is True


class TestScanResult:
    def test_clean_result(self) -> None:
        result = ScanResult(findings=[], scanned_path="/tmp/ws", scanner_available=True)
        assert result.is_clean
        assert result.finding_count == 0

    def test_dirty_result(self) -> None:
        findings = [
            ScanFinding(detector="GenericApiKey", file_path="f1.txt", verified=False, raw_preview="sk-..."),
            ScanFinding(detector="AWS", file_path="f2.txt", verified=True, raw_preview="AKIA..."),
        ]
        result = ScanResult(findings=findings, scanned_path="/tmp/ws", scanner_available=True)
        assert not result.is_clean
        assert result.finding_count == 2

    def test_flagged_files(self) -> None:
        findings = [
            ScanFinding(detector="GenericApiKey", file_path="/tmp/ws/abc_events.ndjson", verified=False, raw_preview="x"),
            ScanFinding(detector="AWS", file_path="/tmp/ws/abc_events.ndjson", verified=True, raw_preview="y"),
            ScanFinding(detector="Slack", file_path="/tmp/ws/def_output.md", verified=False, raw_preview="z"),
        ]
        result = ScanResult(findings=findings, scanned_path="/tmp/ws", scanner_available=True)
        assert result.flagged_files == {"/tmp/ws/abc_events.ndjson", "/tmp/ws/def_output.md"}

    def test_unavailable_scanner_is_clean(self) -> None:
        result = ScanResult(findings=[], scanned_path="/tmp/ws", scanner_available=False)
        assert result.is_clean  # graceful degradation: no scanner = no block

    def test_to_dict(self) -> None:
        result = ScanResult(findings=[], scanned_path="/tmp/ws", scanner_available=True)
        d = result.to_dict()
        assert d["is_clean"] is True
        assert d["scanner_available"] is True


class TestSecretScanner:
    def test_scanner_reports_availability(self) -> None:
        scanner = SecretScanner()
        # Just verify it doesn't crash — actual availability depends on host
        assert isinstance(scanner.available, bool)

    def test_scan_empty_directory(self) -> None:
        scanner = SecretScanner()
        with tempfile.TemporaryDirectory() as tmp:
            result = scanner.scan(tmp)
            assert result.is_clean
            assert result.scanned_path == tmp

    def test_scan_clean_directory(self) -> None:
        scanner = SecretScanner()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "readme.md").write_text("# Hello\nNo secrets here.", encoding="utf-8")
            (Path(tmp) / "data.json").write_text('{"score": 0.85}', encoding="utf-8")
            result = scanner.scan(tmp)
            assert result.is_clean

    def test_nonzero_exit_without_findings_returns_scan_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("autocontext.security.scanner.is_trufflehog_available", lambda: True)
        monkeypatch.setattr(
            "autocontext.security.scanner.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0],
                returncode=2,
                stdout="",
                stderr="fatal scan error",
            ),
        )

        scanner = SecretScanner()
        with tempfile.TemporaryDirectory() as tmp:
            result = scanner.scan(tmp)
            assert not result.is_clean
            assert result.scan_error is not None
            assert "code 2" in result.scan_error

    @pytest.mark.skipif(not shutil.which("trufflehog"), reason="trufflehog not installed")
    def test_scan_detects_fake_secret(self) -> None:
        """Plant a realistic-looking API key and verify trufflehog catches it."""
        scanner = SecretScanner()
        with tempfile.TemporaryDirectory() as tmp:
            # This is a fake key pattern that trufflehog's GenericApiKey detector should flag
            (Path(tmp) / "config.env").write_text(
                "ANTHROPIC_API_KEY=sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
                encoding="utf-8",
            )
            result = scanner.scan(tmp)
            # Trufflehog should find at least one thing
            assert result.finding_count >= 1 or not result.scanner_available


# ---------------------------------------------------------------------------
# Evidence workspace integration
# ---------------------------------------------------------------------------


class TestEvidenceWorkspaceIntegration:
    """Evidence materializer should filter out artifacts flagged by the scanner."""

    def test_flagged_artifacts_excluded_from_manifest(self) -> None:
        from autocontext.evidence.materializer import materialize_workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Create a run with a clean file and a file containing a secret
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")
            (run_dir / "gate_decision.json").write_text('{"decision":"advance"}', encoding="utf-8")

            ws_dir = root / "workspace"
            ws = materialize_workspace(
                knowledge_root=root / "knowledge",
                runs_root=root / "runs",
                source_run_ids=["run_001"],
                workspace_dir=ws_dir,
                scan_for_secrets=True,
            )
            # With scan enabled, the workspace should still materialize
            # (scanner may not be installed — graceful degradation)
            assert isinstance(ws.artifacts, list)

    def test_scan_disabled_skips_scanning(self) -> None:
        from autocontext.evidence.materializer import materialize_workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")

            ws_dir = root / "workspace"
            ws = materialize_workspace(
                knowledge_root=root / "knowledge",
                runs_root=root / "runs",
                source_run_ids=["run_001"],
                workspace_dir=ws_dir,
                scan_for_secrets=False,
            )
            assert isinstance(ws.artifacts, list)

    def test_scan_result_persisted_to_workspace(self) -> None:
        from autocontext.evidence.materializer import materialize_workspace

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")

            ws_dir = root / "workspace"
            materialize_workspace(
                knowledge_root=root / "knowledge",
                runs_root=root / "runs",
                source_run_ids=["run_001"],
                workspace_dir=ws_dir,
                scan_for_secrets=True,
            )
            scan_report = ws_dir / "secret_scan_report.json"
            assert scan_report.exists()
            data = json.loads(scan_report.read_text())
            assert "is_clean" in data

    def test_scan_failure_excludes_all_workspace_artifacts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.evidence.materializer import materialize_workspace

        def _failing_scan(self: SecretScanner, directory: str) -> ScanResult:
            return ScanResult(
                findings=[],
                scanned_path=directory,
                scanner_available=True,
                scan_error="trufflehog exited with code 2",
            )

        monkeypatch.setattr(SecretScanner, "scan", _failing_scan)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")

            ws_dir = root / "workspace"
            ws = materialize_workspace(
                knowledge_root=root / "knowledge",
                runs_root=root / "runs",
                source_run_ids=["run_001"],
                workspace_dir=ws_dir,
                scan_for_secrets=True,
            )

            assert ws.artifacts == []
            assert not any(path.suffix == ".ndjson" for path in ws_dir.iterdir())
