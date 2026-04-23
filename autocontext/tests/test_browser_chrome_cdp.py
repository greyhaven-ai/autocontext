from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.integrations.browser.chrome_cdp import ChromeCdpSession
from autocontext.integrations.browser.evidence import BrowserEvidenceStore
from autocontext.integrations.browser.policy import build_default_browser_session_config


class FakeTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def send(self, method: str, params: dict | None = None) -> dict:
        self.calls.append((method, params or {}))
        if not self.responses:
            return {}
        return self.responses.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_navigate_blocks_disallowed_domain_before_transport(tmp_path: Path) -> None:
    session = ChromeCdpSession(
        session_id="session_1",
        config=build_default_browser_session_config(allowed_domains=["example.com"]),
        transport=FakeTransport([]),
        evidence_store=BrowserEvidenceStore(tmp_path),
    )

    event = await session.navigate("https://blocked.example.net/dashboard")

    assert event.allowed is False
    assert event.policyReason == "domain_not_allowed"
    assert session.transport.calls == []


@pytest.mark.asyncio
async def test_snapshot_persists_artifacts_and_click_uses_ref_mapping(tmp_path: Path) -> None:
    transport = FakeTransport([
        {},
        {},
        {
            "result": {
                "value": {
                    "url": "https://example.com/dashboard",
                    "title": "Dashboard",
                    "visibleText": "Welcome back",
                    "refs": [
                        {
                            "id": "@e1",
                            "role": "button",
                            "name": "Continue",
                            "selector": "button:nth-of-type(1)",
                        }
                    ],
                    "html": "<html><body>Welcome back</body></html>",
                }
            }
        },
        {"data": "cG5nLWJ5dGVz"},
        {"result": {"value": {"ok": True}}},
        {"result": {"value": "https://example.com/dashboard"}},
    ])
    session = ChromeCdpSession(
        session_id="session_1",
        config=build_default_browser_session_config(allowed_domains=["example.com"]),
        transport=transport,
        evidence_store=BrowserEvidenceStore(tmp_path),
    )

    snapshot = await session.snapshot()
    event = await session.click("@e1")

    assert snapshot.url == "https://example.com/dashboard"
    assert snapshot.htmlPath is not None
    assert snapshot.screenshotPath is not None
    assert Path(snapshot.htmlPath).exists()
    assert Path(snapshot.screenshotPath).read_bytes() == b"png-bytes"
    assert "selectorFor(element)" in transport.calls[2][1]["expression"]
    assert event.allowed is True
    assert event.afterUrl == "https://example.com/dashboard"
    assert transport.calls[-2][0] == "Runtime.evaluate"
    assert "button:nth-of-type(1)" in transport.calls[-2][1]["expression"]


@pytest.mark.asyncio
async def test_snapshot_normalizes_null_ref_fields(tmp_path: Path) -> None:
    transport = FakeTransport([
        {},
        {},
        {
            "result": {
                "value": {
                    "url": "https://example.com/dashboard",
                    "title": "Dashboard",
                    "visibleText": "Welcome back",
                    "refs": [
                        {
                            "id": "@e1",
                            "role": "button",
                            "name": None,
                            "text": None,
                            "selector": "button:nth-of-type(1)",
                        }
                    ],
                    "html": "<html><body>Welcome back</body></html>",
                }
            }
        },
    ])
    session = ChromeCdpSession(
        session_id="session_1",
        config=build_default_browser_session_config(
            allowed_domains=["example.com"],
            capture_screenshots=False,
        ),
        transport=transport,
        evidence_store=BrowserEvidenceStore(tmp_path),
    )

    snapshot = await session.snapshot()

    ref = snapshot.model_dump(mode="json", exclude_none=True)["refs"][0]
    assert "name" not in ref
    assert "text" not in ref


@pytest.mark.asyncio
async def test_click_records_blocked_when_interaction_leaves_allowlist(tmp_path: Path) -> None:
    transport = FakeTransport([
        {},
        {},
        {
            "result": {
                "value": {
                    "url": "https://example.com/dashboard",
                    "title": "Dashboard",
                    "visibleText": "Continue",
                    "refs": [{"id": "@e1", "selector": "a:nth-of-type(1)"}],
                    "html": "<html><body><a href='https://blocked.example.net'>Continue</a></body></html>",
                }
            }
        },
        {"result": {"value": {"ok": True}}},
        {"result": {"value": "https://blocked.example.net"}},
    ])
    session = ChromeCdpSession(
        session_id="session_1",
        config=build_default_browser_session_config(
            allowed_domains=["example.com"],
            capture_screenshots=False,
        ),
        transport=transport,
        evidence_store=BrowserEvidenceStore(tmp_path),
    )

    await session.snapshot()
    event = await session.click("@e1")

    assert event.allowed is False
    assert event.policyReason == "domain_not_allowed"
    assert event.afterUrl == "https://blocked.example.net"


@pytest.mark.asyncio
async def test_fill_password_denied_when_auth_disabled(tmp_path: Path) -> None:
    session = ChromeCdpSession(
        session_id="session_1",
        config=build_default_browser_session_config(allowed_domains=["example.com"]),
        transport=FakeTransport([]),
        evidence_store=BrowserEvidenceStore(tmp_path),
    )

    event = await session.fill("@e1", "super-secret", field_kind="password")

    assert event.allowed is False
    assert event.policyReason == "auth_blocked"
    assert session.transport.calls == []
