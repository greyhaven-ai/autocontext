from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.investigation.browser_context import InvestigationBrowserContext

runner = CliRunner()


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _make_settings(tmp_path: Path):
    from autocontext.config.settings import AppSettings

    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "events.ndjson",
    )


def _completed_payload() -> dict[str, object]:
    return {
        "id": "inv_123",
        "name": "checkout_rca",
        "family": "investigation",
        "status": "completed",
        "description": "why did checkout fail",
        "question": "Why did checkout fail?",
        "hypotheses": [
            {"id": "h0", "statement": "Config regression", "status": "supported", "confidence": 0.74},
        ],
        "evidence": [
            {
                "id": "e0",
                "kind": "observation",
                "source": "scenario execution",
                "summary": "Primary evidence",
                "supports": ["h0"],
                "contradicts": [],
                "is_red_herring": False,
            }
        ],
        "conclusion": {
            "best_explanation": "Config regression",
            "confidence": 0.74,
            "limitations": [],
        },
        "unknowns": [],
        "recommended_next_steps": ["Inspect rollout diff"],
        "steps_executed": 3,
        "artifacts": {
            "investigation_dir": "/tmp/investigations/checkout_rca",
            "report_path": "/tmp/investigations/checkout_rca/report.json",
        },
    }


class _FakeInvestigationEngine:
    def __init__(self, **_: object) -> None:
        pass

    def run(self, _request):  # noqa: ANN001
        from autocontext.investigation.engine import InvestigationResult

        return InvestigationResult.from_dict(_completed_payload())


class _FailingInvestigationEngine:
    def __init__(self, **_: object) -> None:
        pass

    def run(self, _request):  # noqa: ANN001
        from autocontext.investigation.engine import InvestigationResult

        payload = _completed_payload()
        payload.update({"status": "failed", "error": "spec generation did not return valid JSON"})
        return InvestigationResult.from_dict(payload)


class _CapturingInvestigationEngine:
    captured_request = None

    def __init__(self, **_: object) -> None:
        pass

    def run(self, request):  # noqa: ANN001
        from autocontext.investigation.engine import InvestigationResult

        _CapturingInvestigationEngine.captured_request = request
        return InvestigationResult.from_dict(_completed_payload())


class TestInvestigateCli:
    def test_help_exists(self) -> None:
        result = runner.invoke(app, ["investigate", "--help"])
        help_text = _strip_ansi(result.output)

        assert result.exit_code == 0, result.output
        assert "investigate" in help_text.lower()
        assert "--description" in help_text
        assert "--hypotheses" in help_text
        assert "--browser-url" in help_text
        assert "--mode" in help_text

    def test_json_success(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_investigation_runtime", return_value=(object(), "mock-model")),
            patch("autocontext.investigation.engine.InvestigationEngine", _FakeInvestigationEngine),
        ):
            result = runner.invoke(app, ["investigate", "-d", "why did checkout fail", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output.strip())
        assert payload["status"] == "completed"
        assert payload["family"] == "investigation"
        assert payload["name"] == "checkout_rca"

    def test_json_failure_writes_to_stderr(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_investigation_runtime", return_value=(object(), "mock-model")),
            patch("autocontext.investigation.engine.InvestigationEngine", _FailingInvestigationEngine),
        ):
            result = runner.invoke(app, ["investigate", "-d", "broken investigation", "--json"])

        assert result.exit_code == 1
        payload = json.loads(result.output.strip())
        assert payload["status"] == "failed"
        assert payload["error"] == "spec generation did not return valid JSON"

    def test_browser_url_attaches_browser_context_to_the_investigation_request(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path).model_copy(
            update={
                "browser_enabled": True,
                "browser_backend": "chrome-cdp",
                "browser_allowed_domains": "example.com",
                "browser_debugger_url": "http://127.0.0.1:9333",
            }
        )
        browser_context = InvestigationBrowserContext(
            url="https://example.com/status",
            title="Status",
            visible_text="Checkout is degraded",
            html_path="/tmp/status.html",
            screenshot_path="/tmp/status.png",
        )
        _CapturingInvestigationEngine.captured_request = None

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_investigation_runtime", return_value=(object(), "mock-model")),
            patch("autocontext.cli_investigate.capture_investigation_browser_context", return_value=browser_context),
            patch("autocontext.investigation.engine.InvestigationEngine", _CapturingInvestigationEngine),
        ):
            result = runner.invoke(
                app,
                [
                    "investigate",
                    "-d",
                    "why did checkout fail",
                    "--browser-url",
                    "https://example.com/status",
                    "--json",
                ],
            )

        assert result.exit_code == 0, result.output
        assert _CapturingInvestigationEngine.captured_request is not None
        assert _CapturingInvestigationEngine.captured_request.browser_context == browser_context

    def test_mode_is_forwarded_to_investigation_request(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        _CapturingInvestigationEngine.captured_request = None

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_investigation_runtime", return_value=(object(), "mock-model")),
            patch("autocontext.investigation.engine.InvestigationEngine", _CapturingInvestigationEngine),
        ):
            result = runner.invoke(
                app,
                ["investigate", "-d", "why did checkout fail", "--mode", "iterative", "--json"],
            )

        assert result.exit_code == 0, result.output
        assert _CapturingInvestigationEngine.captured_request is not None
        assert _CapturingInvestigationEngine.captured_request.mode == "iterative"

    def test_iterative_mode_does_not_resolve_architect_runtime(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        resolved_roles: list[str] = []
        _CapturingInvestigationEngine.captured_request = None

        def _resolve_runtime(_settings, *, role: str):  # noqa: ANN001, ANN202
            resolved_roles.append(role)
            if role == "architect":
                raise AssertionError("iterative mode should not resolve the architect runtime")
            return object(), "mock-model"

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_investigation_runtime", side_effect=_resolve_runtime),
            patch("autocontext.investigation.engine.InvestigationEngine", _CapturingInvestigationEngine),
        ):
            result = runner.invoke(
                app,
                ["investigate", "-d", "why did checkout fail", "--mode", "iterative", "--json"],
            )

        assert result.exit_code == 0, result.output
        assert resolved_roles == ["analyst"]
        assert _CapturingInvestigationEngine.captured_request is not None
        assert _CapturingInvestigationEngine.captured_request.mode == "iterative"
