"""Thin CDP-backed browser session helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from autocontext.integrations.browser.contract.models import (
    BrowserAuditEvent,
    BrowserSessionConfig,
    BrowserSnapshot,
)
from autocontext.integrations.browser.contract.types import BrowserAction
from autocontext.integrations.browser.evidence import BrowserArtifactPaths, BrowserEvidenceStore
from autocontext.integrations.browser.policy import (
    BrowserPolicyDecision,
    evaluate_browser_action_policy,
)
from autocontext.integrations.browser.types import BrowserSessionPort
from autocontext.integrations.browser.validate import (
    validate_browser_action,
    validate_browser_audit_event,
    validate_browser_snapshot,
)

_SNAPSHOT_EXPRESSION = """
(() => {
  const cssEscape = (value) =>
    globalThis.CSS?.escape
      ? CSS.escape(value)
      : String(value).replace(/[^a-zA-Z0-9_-]/g, "\\\\$&");
  const selectorFor = (element) => {
    if (element.id) return "#" + cssEscape(element.id);
    const parts = [];
    let current = element;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.documentElement) {
      const tag = current.tagName.toLowerCase();
      const parent = current.parentElement;
      if (!parent) {
        parts.unshift(tag);
        break;
      }
      const siblings = Array.from(parent.children).filter((sibling) => sibling.tagName === current.tagName);
      const index = siblings.indexOf(current) + 1;
      parts.unshift(siblings.length > 1 ? tag + ":nth-of-type(" + index + ")" : tag);
      current = parent;
      if (parts.length >= 4) break;
    }
    return parts.join(" > ");
  };
  const candidates = Array.from(
    document.querySelectorAll("a,button,input,select,textarea,[role],[tabindex]")
  ).slice(0, 200);
  const refs = candidates.map((element, index) => ({
    id: `@e${index + 1}`,
    role: element.getAttribute("role") ?? element.tagName.toLowerCase(),
    name:
      element.getAttribute("aria-label") ??
      element.getAttribute("name") ??
      element.textContent?.trim() ??
      "",
    text: element.textContent?.trim() ?? "",
    selector: selectorFor(element),
    disabled: element.hasAttribute("disabled"),
  }));
  return {
    url: window.location.href,
    title: document.title ?? "",
    visibleText: document.body?.innerText ?? "",
    refs,
    html: document.documentElement?.outerHTML ?? "",
  };
})()
""".strip()


class ChromeCdpTransport(Protocol):
    async def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]: ...
    async def close(self) -> None: ...


class ChromeCdpSession(BrowserSessionPort):
    """Policy-aware CDP session wrapper with local evidence capture."""

    def __init__(
        self,
        *,
        session_id: str,
        config: BrowserSessionConfig,
        transport: ChromeCdpTransport,
        evidence_store: BrowserEvidenceStore | None = None,
    ) -> None:
        self.session_id = session_id
        self.config = config
        self.transport = transport
        self.evidence_store = evidence_store
        self._current_url = "about:blank"
        self._domains_enabled = False
        self._ref_selectors: dict[str, str] = {}

    async def navigate(self, url: str) -> BrowserAuditEvent:
        action = self._build_action("navigate", {"url": url})
        decision = evaluate_browser_action_policy(self.config, action)
        if not decision.allowed:
            return self._record_action_result(
                action=action,
                decision=decision,
                before_url=self._current_url,
                after_url=self._current_url,
                message="navigation blocked by browser policy",
            )

        await self._ensure_domains_enabled()
        before_url = self._current_url
        await self.transport.send("Page.navigate", {"url": url})
        self._current_url = url
        return self._record_action_result(
            action=action,
            decision=decision,
            before_url=before_url,
            after_url=url,
            message="navigation allowed",
        )

    async def snapshot(self) -> BrowserSnapshot:
        action = self._build_action(
            "snapshot",
            {
                "captureHtml": True,
                "captureScreenshot": bool(self.config.captureScreenshots),
            },
        )
        await self._ensure_domains_enabled()

        response = await self.transport.send(
            "Runtime.evaluate",
            {
                "expression": _SNAPSHOT_EXPRESSION,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        payload = _extract_result_value(response)
        parsed_refs = _normalize_snapshot_refs(payload.get("refs"))
        self._ref_selectors = {
            str(ref.get("id")): str(ref.get("selector"))
            for ref in parsed_refs
            if isinstance(ref, dict) and ref.get("id") and ref.get("selector")
        }

        screenshot_base64: str | None = None
        if bool(self.config.captureScreenshots):
            screenshot_response = await self.transport.send("Page.captureScreenshot", {"format": "png"})
            raw_data = screenshot_response.get("data")
            screenshot_base64 = str(raw_data) if isinstance(raw_data, str) else None

        artifacts = self._persist_snapshot_artifacts(
            basename=str(action.actionId),
            html=payload.get("html") if isinstance(payload.get("html"), str) else None,
            screenshot_base64=screenshot_base64,
        )

        url = payload.get("url")
        self._current_url = str(url) if isinstance(url, str) and url else self._current_url
        return validate_browser_snapshot({
            "schemaVersion": "1.0",
            "sessionId": self.session_id,
            "capturedAt": _utcnow(),
            "url": self._current_url,
            "title": str(payload.get("title") or ""),
            "refs": parsed_refs,
            "visibleText": str(payload.get("visibleText") or ""),
            "htmlPath": artifacts["htmlPath"],
            "screenshotPath": artifacts["screenshotPath"],
        })

    async def click(self, ref: str) -> BrowserAuditEvent:
        action = self._build_action("click", {"ref": ref})
        decision = evaluate_browser_action_policy(self.config, action)
        if not decision.allowed:
            return self._record_action_result(
                action=action,
                decision=decision,
                before_url=self._current_url,
                after_url=self._current_url,
            )

        await self._ensure_domains_enabled()
        selector = self._selector_for_ref(ref)
        await self.transport.send(
            "Runtime.evaluate",
            {
                "expression": _click_expression(selector),
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return await self._record_interactive_result(
            action=action,
            decision=decision,
            before_url=self._current_url,
            message="click allowed",
        )

    async def fill(
        self,
        ref: str,
        text: str,
        *,
        field_kind: str | None = None,
    ) -> BrowserAuditEvent:
        action = self._build_action("fill", {"ref": ref, "text": text, "fieldKind": field_kind})
        decision = evaluate_browser_action_policy(self.config, action)
        if not decision.allowed:
            return self._record_action_result(
                action=action,
                decision=decision,
                before_url=self._current_url,
                after_url=self._current_url,
                message="fill blocked by browser policy",
            )

        await self._ensure_domains_enabled()
        selector = self._selector_for_ref(ref)
        await self.transport.send(
            "Runtime.evaluate",
            {
                "expression": _fill_expression(selector, text),
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return await self._record_interactive_result(
            action=action,
            decision=decision,
            before_url=self._current_url,
            message="fill allowed",
        )

    async def press(self, key: str) -> BrowserAuditEvent:
        action = self._build_action("press", {"key": key})
        decision = evaluate_browser_action_policy(self.config, action)
        if not decision.allowed:
            return self._record_action_result(
                action=action,
                decision=decision,
                before_url=self._current_url,
                after_url=self._current_url,
            )

        await self._ensure_domains_enabled()
        await self.transport.send(
            "Runtime.evaluate",
            {
                "expression": _press_expression(key),
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        return await self._record_interactive_result(
            action=action,
            decision=decision,
            before_url=self._current_url,
            message="key press allowed",
        )

    async def screenshot(self, name: str) -> BrowserAuditEvent:
        action = self._build_action("screenshot", {"name": name})
        decision = evaluate_browser_action_policy(self.config, action)
        if not decision.allowed:
            return self._record_action_result(
                action=action,
                decision=decision,
                before_url=self._current_url,
                after_url=self._current_url,
            )

        await self._ensure_domains_enabled()
        response = await self.transport.send("Page.captureScreenshot", {"format": "png"})
        screenshot_base64 = response.get("data")
        artifacts = self._persist_snapshot_artifacts(
            basename=name,
            screenshot_base64=str(screenshot_base64) if isinstance(screenshot_base64, str) else None,
        )
        return self._record_action_result(
            action=action,
            decision=decision,
            before_url=self._current_url,
            after_url=self._current_url,
            message="screenshot captured",
            artifacts=artifacts,
        )

    async def close(self) -> None:
        await self.transport.close()

    async def _ensure_domains_enabled(self) -> None:
        if self._domains_enabled:
            return
        await self.transport.send("Page.enable", {})
        await self.transport.send("Runtime.enable", {})
        self._domains_enabled = True

    def _build_action(self, action_type: str, params: dict[str, Any]) -> BrowserAction:
        return validate_browser_action({
            "schemaVersion": "1.0",
            "actionId": _new_id("act"),
            "sessionId": self.session_id,
            "timestamp": _utcnow(),
            "type": action_type,
            "params": params,
        })

    def _record_action_result(
        self,
        *,
        action: BrowserAction,
        decision: BrowserPolicyDecision,
        before_url: str | None,
        after_url: str | None,
        message: str | None = None,
        artifacts: BrowserArtifactPaths | None = None,
    ) -> BrowserAuditEvent:
        event = validate_browser_audit_event({
            "schemaVersion": "1.0",
            "eventId": _new_id("evt"),
            "sessionId": self.session_id,
            "actionId": str(action.actionId),
            "kind": "action_result",
            "allowed": decision.allowed,
            "policyReason": decision.reason,
            "timestamp": _utcnow(),
            "message": message,
            "beforeUrl": before_url,
            "afterUrl": after_url,
            "artifacts": artifacts or _empty_artifacts(),
        })
        if self.evidence_store is not None:
            self.evidence_store.append_audit_event(event)
        return event

    def _persist_snapshot_artifacts(
        self,
        *,
        basename: str,
        html: str | None = None,
        screenshot_base64: str | None = None,
    ) -> BrowserArtifactPaths:
        if self.evidence_store is None:
            return _empty_artifacts()
        return self.evidence_store.persist_snapshot_artifacts(
            session_id=self.session_id,
            basename=basename,
            html=html,
            screenshot_base64=screenshot_base64,
        )

    def _selector_for_ref(self, ref: str) -> str:
        return self._ref_selectors.get(ref, ref)

    async def _record_interactive_result(
        self,
        *,
        action: BrowserAction,
        decision: BrowserPolicyDecision,
        before_url: str | None,
        message: str,
    ) -> BrowserAuditEvent:
        after_url = await self._read_current_url()
        self._current_url = after_url
        after_decision = _evaluate_navigation_url_policy(self.config, after_url)
        if not after_decision.allowed:
            return self._record_action_result(
                action=action,
                decision=after_decision,
                before_url=before_url,
                after_url=after_url,
                message="interaction navigated outside browser policy",
            )
        return self._record_action_result(
            action=action,
            decision=decision,
            before_url=before_url,
            after_url=after_url,
            message=message,
        )

    async def _read_current_url(self) -> str:
        response = await self.transport.send(
            "Runtime.evaluate",
            {
                "expression": "(() => window.location.href)()",
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        result = response.get("result")
        if not isinstance(result, dict):
            return self._current_url
        value = result.get("value")
        return value if isinstance(value, str) and value else self._current_url


def _extract_result_value(response: dict[str, Any]) -> dict[str, Any]:
    result = response.get("result")
    if not isinstance(result, dict):
        return {}
    value = result.get("value")
    if not isinstance(value, dict):
        return {}
    return value


def _normalize_snapshot_refs(refs: Any) -> list[dict[str, Any]]:
    if not isinstance(refs, list):
        return []
    normalized_refs: list[dict[str, Any]] = []
    for item in refs:
        if not isinstance(item, dict):
            continue
        ref_id = item.get("id")
        if not isinstance(ref_id, str):
            continue
        normalized: dict[str, Any] = {"id": ref_id}
        for key in ("role", "name", "text", "selector"):
            value = item.get(key)
            if isinstance(value, str):
                normalized[key] = value
        disabled = item.get("disabled")
        if isinstance(disabled, bool):
            normalized["disabled"] = disabled
        normalized_refs.append(normalized)
    return normalized_refs


def _evaluate_navigation_url_policy(config: BrowserSessionConfig, url: str) -> BrowserPolicyDecision:
    return evaluate_browser_action_policy(
        config,
        {
            "schemaVersion": "1.0",
            "actionId": "act_interaction_url_probe",
            "sessionId": "session_interaction_url_probe",
            "timestamp": _utcnow(),
            "type": "navigate",
            "params": {"url": url},
        },
    )


def _click_expression(selector: str) -> str:
    selector_json = json.dumps(selector)
    return f"""
(() => {{
  const element = document.querySelector({selector_json});
  if (!element) return {{ ok: false, error: "selector_not_found" }};
  element.click();
  return {{ ok: true }};
}})()
""".strip()


def _fill_expression(selector: str, text: str) -> str:
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    return f"""
(() => {{
  const element = document.querySelector({selector_json});
  if (!element) return {{ ok: false, error: "selector_not_found" }};
  element.focus?.();
  if ("value" in element) {{
    element.value = {text_json};
  }}
  element.dispatchEvent(new Event("input", {{ bubbles: true }}));
  element.dispatchEvent(new Event("change", {{ bubbles: true }}));
  return {{ ok: true }};
}})()
""".strip()


def _press_expression(key: str) -> str:
    key_json = json.dumps(key)
    return f"""
(() => {{
  const target = document.activeElement ?? document.body;
  if (!target) return {{ ok: false, error: "missing_target" }};
  target.dispatchEvent(new KeyboardEvent("keydown", {{ key: {key_json}, bubbles: true }}));
  target.dispatchEvent(new KeyboardEvent("keyup", {{ key: {key_json}, bubbles: true }}));
  return {{ ok: true }};
}})()
""".strip()


def _empty_artifacts() -> BrowserArtifactPaths:
    return {"htmlPath": None, "screenshotPath": None, "downloadPath": None}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


__all__ = ["ChromeCdpSession", "ChromeCdpTransport"]
