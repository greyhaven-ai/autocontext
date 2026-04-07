"""AC-519: Redacted session sharing pipeline tests.

Tests the full sharing pipeline: collect → redact → scan → bundle → attest.
Also tests publishers (gist, hf) with mocked CLI calls.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Content Redactor
# ---------------------------------------------------------------------------


class TestContentRedactor:
    """Multi-layer redaction: env vars, API keys, PII, paths, high-risk files."""

    def test_redacts_anthropic_api_key(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "My key is sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaa and more text."
        result = redact_content(text)
        assert "sk-ant-" not in result
        assert "[REDACTED_API_KEY]" in result

    def test_redacts_openai_api_key(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "Using key sk-proj-abcdef1234567890abcdef1234567890 for requests."
        result = redact_content(text)
        assert "sk-proj-" not in result

    def test_redacts_aws_access_key(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        result = redact_content(text)
        assert "AKIA" not in result

    def test_redacts_github_token(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "token: ghp_ABCDEFghijklmnopqrstuvwxyz123456"
        result = redact_content(text)
        assert "ghp_" not in result

    def test_redacts_slack_token(self) -> None:
        from autocontext.sharing.redactor import redact_content

        # Use a truncated pattern that won't trigger GitHub push protection
        # but still matches the xoxb- prefix pattern in our redactor
        text = "SLACK_TOKEN=" + "xoxb" + "-" + "0" * 20
        result = redact_content(text)
        assert "xoxb" + "-" not in result

    def test_redacts_email_addresses(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "Contact jay@greyhaven.ai for details."
        result = redact_content(text)
        assert "jay@greyhaven.ai" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_redacts_ip_addresses(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "Server at 192.168.1.100 on port 8080."
        result = redact_content(text)
        assert "192.168.1.100" not in result
        assert "[REDACTED_IP]" in result

    def test_redacts_absolute_paths(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "File at /Users/jayscambler/secret/project/main.py"
        result = redact_content(text)
        assert "/Users/jayscambler" not in result

    def test_redacts_env_file_content(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "Tool read .env:\nDATABASE_URL=postgresql://user:pass@host/db\nSECRET_KEY=abc123"
        result = redact_content(text)
        assert "postgresql://" not in result
        assert "abc123" not in result

    def test_preserves_non_sensitive_content(self) -> None:
        from autocontext.sharing.redactor import redact_content

        text = "The agent scored 0.85 on generation 3. Strategy improved."
        result = redact_content(text)
        assert result == text  # no changes

    def test_returns_redaction_report(self) -> None:
        from autocontext.sharing.redactor import redact_content_with_report

        text = "Key: sk-ant-api03-aaaaaaaaaaaaaaaaaaaaaaaaaaaa and email user@test.com"
        result, report = redact_content_with_report(text)
        assert len(report.redactions) >= 2
        assert any("api_key" in r.category for r in report.redactions)
        assert any(r.category == "email" for r in report.redactions)


# ---------------------------------------------------------------------------
# Session Collector
# ---------------------------------------------------------------------------


class TestSessionCollector:
    """Finds and packages source artifacts for a given run or scenario."""

    def test_collects_from_run_directory(self) -> None:
        from autocontext.sharing.collector import collect_session_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "events.ndjson").write_text('{"event":"start"}\n', encoding="utf-8")
            (run_dir / "pi_session.json").write_text('{"turns":[]}', encoding="utf-8")
            (run_dir / "pi_output.txt").write_text("Agent output here.", encoding="utf-8")

            k_dir = root / "knowledge" / "test_scenario"
            k_dir.mkdir(parents=True)
            (k_dir / "playbook.md").write_text("# Playbook", encoding="utf-8")

            artifacts = collect_session_artifacts(
                runs_root=root / "runs",
                knowledge_root=root / "knowledge",
                run_id="run_001",
                scenario_name="test_scenario",
            )
            assert len(artifacts) >= 2
            assert any(a.name == "pi_session.json" for a in artifacts)

    def test_collects_empty_for_missing_run(self) -> None:
        from autocontext.sharing.collector import collect_session_artifacts

        with tempfile.TemporaryDirectory() as tmp:
            artifacts = collect_session_artifacts(
                runs_root=Path(tmp) / "runs",
                knowledge_root=Path(tmp) / "knowledge",
                run_id="nonexistent",
            )
            assert artifacts == []


# ---------------------------------------------------------------------------
# Export Bundle
# ---------------------------------------------------------------------------


class TestExportBundle:
    """The shareable artifact structure."""

    def test_bundle_structure(self) -> None:
        from autocontext.sharing.bundle import create_bundle

        with tempfile.TemporaryDirectory() as tmp:
            # Create a fake artifact
            src = Path(tmp) / "source"
            src.mkdir()
            (src / "session.json").write_text('{"turns":[{"role":"user","content":"hello"}]}', encoding="utf-8")

            bundle = create_bundle(
                source_files=[src / "session.json"],
                output_dir=Path(tmp) / "export",
                run_id="run_001",
                scenario_name="test_scenario",
            )
            assert bundle.output_dir.exists()
            assert (bundle.output_dir / "manifest.json").exists()
            assert (bundle.output_dir / "redaction_report.json").exists()
            assert bundle.attestation is None  # not yet attested

    def test_bundle_contains_redacted_content(self) -> None:
        from autocontext.sharing.bundle import create_bundle

        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "source"
            src.mkdir()
            (src / "trace.txt").write_text("API key: sk-ant-api03-fakekey123456789\nScore: 0.9", encoding="utf-8")

            bundle = create_bundle(
                source_files=[src / "trace.txt"],
                output_dir=Path(tmp) / "export",
                run_id="run_001",
            )
            exported = (bundle.output_dir / "trace.txt").read_text()
            assert "sk-ant-" not in exported
            assert "0.9" in exported  # non-sensitive preserved


# ---------------------------------------------------------------------------
# Attestation
# ---------------------------------------------------------------------------


class TestAttestation:
    """Operator sign-off before export is finalized."""

    def test_create_attestation(self) -> None:
        from autocontext.sharing.attestation import create_attestation

        record = create_attestation(
            operator="jay",
            bundle_id="bundle_abc123",
            decision="approved",
        )
        assert record.operator == "jay"
        assert record.decision == "approved"
        assert record.timestamp

    def test_attestation_to_dict(self) -> None:
        from autocontext.sharing.attestation import create_attestation

        record = create_attestation(operator="jay", bundle_id="b1", decision="approved")
        d = record.to_dict()
        assert d["decision"] == "approved"
        assert d["operator"] == "jay"

    def test_rejected_attestation(self) -> None:
        from autocontext.sharing.attestation import create_attestation

        record = create_attestation(operator="jay", bundle_id="b1", decision="rejected", reason="contains PII")
        assert record.decision == "rejected"
        assert record.reason == "contains PII"


# ---------------------------------------------------------------------------
# Review Surface (pure functions, no interactive I/O)
# ---------------------------------------------------------------------------


class TestReviewSurface:
    """Highlights suspicious content for operator review."""

    def test_highlight_suspicious_patterns(self) -> None:
        from autocontext.sharing.review import find_suspicious_patterns

        text = "Normal text. But here is /home/user/.ssh/id_rsa and also SECRET_KEY=abc"
        findings = find_suspicious_patterns(text)
        assert len(findings) >= 1
        assert any("ssh" in f.description.lower() or "secret" in f.description.lower() for f in findings)

    def test_no_suspicious_in_clean_text(self) -> None:
        from autocontext.sharing.review import find_suspicious_patterns

        text = "The agent improved its score from 0.3 to 0.85 over 5 generations."
        findings = find_suspicious_patterns(text)
        assert findings == []

    def test_generate_review_summary(self) -> None:
        from autocontext.sharing.review import generate_review_summary

        summary = generate_review_summary(
            total_files=5,
            redaction_count=12,
            suspicious_count=2,
            trufflehog_findings=0,
        )
        assert "5 files" in summary
        assert "12" in summary
        assert "2 suspicious" in summary or "2" in summary


# ---------------------------------------------------------------------------
# Publishers
# ---------------------------------------------------------------------------


class TestGistPublisher:
    """GitHub Gist publisher wraps `gh gist create`."""

    def test_publish_calls_gh_cli(self) -> None:
        from autocontext.sharing.publishers.gist import publish_to_gist

        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "session.json").write_text("{}", encoding="utf-8")
            (bundle_dir / "manifest.json").write_text('{"run_id":"r1"}', encoding="utf-8")

            with patch("autocontext.sharing.publishers.gist._run_gh_command") as mock_gh:
                mock_gh.return_value = "https://gist.github.com/abc123"
                url = publish_to_gist(bundle_dir, description="Test share")
                assert url == "https://gist.github.com/abc123"
                mock_gh.assert_called_once()

    def test_publish_raises_on_gh_failure(self) -> None:
        from autocontext.sharing.publishers.gist import GistPublishError, publish_to_gist

        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "manifest.json").write_text("{}", encoding="utf-8")

            with patch("autocontext.sharing.publishers.gist._run_gh_command", side_effect=RuntimeError("gh not found")):
                with pytest.raises(GistPublishError):
                    publish_to_gist(bundle_dir)


class TestHfPublisher:
    """Hugging Face dataset repo publisher."""

    def test_publish_calls_hf_cli(self) -> None:
        from autocontext.sharing.publishers.hf import publish_to_hf

        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "session.json").write_text("{}", encoding="utf-8")
            (bundle_dir / "manifest.json").write_text('{"run_id":"r1"}', encoding="utf-8")

            with patch("autocontext.sharing.publishers.hf._run_hf_command") as mock_hf:
                mock_hf.return_value = "https://huggingface.co/datasets/org/repo"
                url = publish_to_hf(bundle_dir, repo_id="org/repo")
                assert "huggingface.co" in url
                mock_hf.assert_called_once()

    def test_publish_raises_on_hf_failure(self) -> None:
        from autocontext.sharing.publishers.hf import HfPublishError, publish_to_hf

        with tempfile.TemporaryDirectory() as tmp:
            bundle_dir = Path(tmp)
            (bundle_dir / "manifest.json").write_text("{}", encoding="utf-8")

            with patch("autocontext.sharing.publishers.hf._run_hf_command", side_effect=RuntimeError("hf not found")):
                with pytest.raises(HfPublishError):
                    publish_to_hf(bundle_dir, repo_id="org/repo")


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end: collect → redact → scan → bundle → attest."""

    def test_share_pipeline_produces_clean_bundle(self) -> None:
        from autocontext.sharing.pipeline import share_session

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Set up run with sensitive content
            run_dir = root / "runs" / "run_001"
            run_dir.mkdir(parents=True)
            (run_dir / "pi_session.json").write_text(
                json.dumps(
                    {
                        "turns": [
                            {"role": "user", "content": "Deploy with key sk-ant-api03-secret123456789"},
                            {"role": "assistant", "content": "Deploying to 192.168.1.50..."},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "events.ndjson").write_text('{"event":"gen_start"}\n', encoding="utf-8")

            k_dir = root / "knowledge" / "test_scenario"
            k_dir.mkdir(parents=True)
            (k_dir / "playbook.md").write_text("# Playbook\nUse conservative approach.", encoding="utf-8")

            result = share_session(
                runs_root=root / "runs",
                knowledge_root=root / "knowledge",
                run_id="run_001",
                scenario_name="test_scenario",
                output_dir=root / "export",
                operator="test_user",
            )

            assert result.bundle.output_dir.exists()
            assert result.attestation is not None
            assert result.attestation.decision == "auto_approved"  # no interactive review in test mode

            # Verify secrets are gone from exported content
            for path in result.bundle.output_dir.rglob("*"):
                if (
                    path.is_file()
                    and path.name != "manifest.json"
                    and path.name != "redaction_report.json"
                    and path.name != "secret_scan_report.json"
                    and path.name != "attestation.json"
                ):
                    content = path.read_text(encoding="utf-8")
                    assert "sk-ant-" not in content, f"Secret leaked in {path.name}"
                    assert "192.168.1.50" not in content, f"IP leaked in {path.name}"
