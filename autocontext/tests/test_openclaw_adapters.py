"""Tests for AC-318: generalized OpenClaw agent adapters.

Covers: OpenClawRequest, OpenClawResponse, OpenClawAdapter ABC,
CLIOpenClawAdapter, HTTPOpenClawAdapter, AdapterCapability.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from autocontext.security.outbound_url import OutboundResponse

# ===========================================================================
# OpenClawRequest / OpenClawResponse
# ===========================================================================


class TestOpenClawRequest:
    def test_construction(self) -> None:
        from autocontext.openclaw.adapters import OpenClawRequest

        req = OpenClawRequest(
            task_prompt="Write an essay",
            system_prompt="You are a helpful agent",
            context={"scenario": "grid_ctf"},
        )
        assert req.task_prompt == "Write an essay"

    def test_to_json(self) -> None:
        from autocontext.openclaw.adapters import OpenClawRequest

        req = OpenClawRequest(task_prompt="test", context={})
        j = req.to_json()
        parsed = json.loads(j)
        assert parsed["task_prompt"] == "test"


class TestOpenClawResponse:
    def test_construction(self) -> None:
        from autocontext.openclaw.adapters import OpenClawResponse

        resp = OpenClawResponse(
            output="Essay content here",
            tool_calls=[{"tool": "search", "args": {"q": "topic"}}],
            cost_usd=0.05,
            model="claude-sonnet",
        )
        assert resp.output == "Essay content here"
        assert len(resp.tool_calls) == 1

    def test_from_json(self) -> None:
        from autocontext.openclaw.adapters import OpenClawResponse

        raw = json.dumps({"output": "result", "tool_calls": [], "cost_usd": 0.1})
        resp = OpenClawResponse.from_json(raw)
        assert resp.output == "result"
        assert resp.cost_usd == 0.1


# ===========================================================================
# CLIOpenClawAdapter
# ===========================================================================


class TestCLIOpenClawAdapter:
    def test_construction(self) -> None:
        from autocontext.openclaw.adapters import CLIOpenClawAdapter

        adapter = CLIOpenClawAdapter(command="hermes-fly")
        assert adapter.runtime_kind == "cli"
        assert adapter.command == "hermes-fly"

    def test_execute_calls_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from autocontext.openclaw.adapters import CLIOpenClawAdapter, OpenClawRequest

        adapter = CLIOpenClawAdapter(command="test-agent")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"output": "agent response", "tool_calls": []})
        mock_result.stderr = ""

        monkeypatch.setenv("AUTOCONTEXT_SERVER_TOKEN", "must-not-reach-adapter")
        with patch("subprocess.run", return_value=mock_result) as run:
            resp = adapter.execute(OpenClawRequest(task_prompt="test"))

        assert resp.output == "agent response"
        assert "AUTOCONTEXT_SERVER_TOKEN" not in run.call_args.kwargs["env"]

    def test_timeout_handled(self) -> None:
        import subprocess

        from autocontext.openclaw.adapters import CLIOpenClawAdapter, OpenClawRequest

        adapter = CLIOpenClawAdapter(command="slow-agent", timeout=0.1)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 0.1)):
            resp = adapter.execute(OpenClawRequest(task_prompt="test"))

        assert resp.output == ""
        assert resp.metadata.get("error") == "timeout"


# ===========================================================================
# HTTPOpenClawAdapter
# ===========================================================================


class TestHTTPOpenClawAdapter:
    def test_construction(self) -> None:
        from autocontext.openclaw.adapters import HTTPOpenClawAdapter

        adapter = HTTPOpenClawAdapter(endpoint="http://localhost:8080/execute")
        assert adapter.runtime_kind == "http"

    def test_execute_posts_request(self) -> None:
        from autocontext.openclaw.adapters import HTTPOpenClawAdapter, OpenClawRequest

        adapter = HTTPOpenClawAdapter(endpoint="http://localhost:8080/execute")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"output": "http response", "tool_calls": []}

        with patch("autocontext.openclaw.adapters._http_post", return_value=mock_resp):
            resp = adapter.execute(OpenClawRequest(task_prompt="test"))

        assert resp.output == "http response"

    def test_execute_passes_headers(self) -> None:
        from autocontext.openclaw.adapters import HTTPOpenClawAdapter, OpenClawRequest

        adapter = HTTPOpenClawAdapter(
            endpoint="http://localhost:8080/execute",
            headers={"Authorization": "Bearer test-token"},
        )

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"output": "http response", "tool_calls": []}

        with patch("autocontext.openclaw.adapters._http_post", return_value=mock_resp) as mock_post:
            adapter.execute(OpenClawRequest(task_prompt="test"))

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer test-token"}

    def test_execute_passes_private_network_opt_in_and_response_limit(self) -> None:
        from autocontext.openclaw.adapters import HTTPOpenClawAdapter, OpenClawRequest

        adapter = HTTPOpenClawAdapter(
            endpoint="http://localhost:8080/execute",
            allow_private_networks=True,
            max_response_bytes=4096,
        )
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"output": "http response", "tool_calls": []}

        with patch("autocontext.openclaw.adapters._http_post", return_value=mock_resp) as mock_post:
            adapter.execute(OpenClawRequest(task_prompt="test"))

        assert mock_post.call_args.kwargs["allow_private_networks"] is True
        assert mock_post.call_args.kwargs["max_response_bytes"] == 4096

    def test_http_post_enforces_json_response_policy(self) -> None:
        from autocontext.openclaw.adapters import _http_post

        with patch(
            "autocontext.openclaw.adapters.request_outbound_bytes",
            return_value=OutboundResponse(
                status=200,
                headers={"content-type": "application/json"},
                body=b'{"output":"ok"}',
            ),
        ) as request:
            response = _http_post("https://agent.example/execute", "{}", 4.0)

        assert response.json() == {"output": "ok"}
        policy = request.call_args.kwargs["policy"]
        assert policy.allowed_content_types == ("application/json", "application/*+json")
        assert policy.allow_private_networks is False


# ===========================================================================
# AdapterCapability
# ===========================================================================


class TestAdapterCapability:
    def test_construction(self) -> None:
        from autocontext.openclaw.adapters import AdapterCapability

        cap = AdapterCapability(
            runtime_kind="cli",
            compatibility_version="1.0",
            supports_tools=True,
            supports_streaming=False,
        )
        assert cap.runtime_kind == "cli"
        assert cap.supports_tools is True

    def test_roundtrip(self) -> None:
        from autocontext.openclaw.adapters import AdapterCapability

        cap = AdapterCapability(
            runtime_kind="http",
            compatibility_version="1.0",
            supports_tools=True,
            supports_streaming=True,
        )
        d = cap.to_dict()
        restored = AdapterCapability.from_dict(d)
        assert restored.supports_streaming is True
