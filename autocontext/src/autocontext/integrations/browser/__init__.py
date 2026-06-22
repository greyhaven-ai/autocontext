"""Browser exploration contract, settings, and policy helpers."""

# pyright: reportUnsupportedDunderAll=false

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "BrowserArtifactPaths": "autocontext.integrations.browser.evidence",
    "BrowserEvidenceStore": "autocontext.integrations.browser.evidence",
    "ConfiguredBrowserRuntime": "autocontext.integrations.browser.factory",
    "ChromeCdpDiscoveryError": "autocontext.integrations.browser.chrome_cdp_discovery",
    "ChromeCdpSession": "autocontext.integrations.browser.chrome_cdp",
    "ChromeCdpTransport": "autocontext.integrations.browser.chrome_cdp",
    "ChromeCdpRuntime": "autocontext.integrations.browser.chrome_cdp_runtime",
    "ChromeCdpTarget": "autocontext.integrations.browser.chrome_cdp_discovery",
    "ChromeCdpTargetDiscovery": "autocontext.integrations.browser.chrome_cdp_discovery",
    "ChromeCdpTargetDiscoveryPort": "autocontext.integrations.browser.chrome_cdp_discovery",
    "ChromeCdpTransportError": "autocontext.integrations.browser.chrome_cdp_transport",
    "ChromeCdpWebSocketTransport": "autocontext.integrations.browser.chrome_cdp_transport",
    "CapturedBrowserContext": "autocontext.integrations.browser.context_capture",
    "BrowserPolicyDecision": "autocontext.integrations.browser.policy",
    "browser_runtime_from_settings": "autocontext.integrations.browser.factory",
    "build_default_browser_session_config": "autocontext.integrations.browser.policy",
    "capture_browser_context": "autocontext.integrations.browser.context_capture",
    "evaluate_browser_action_policy": "autocontext.integrations.browser.policy",
    "normalize_browser_allowed_domains": "autocontext.integrations.browser.policy",
    "render_captured_browser_context": "autocontext.integrations.browser.context_capture",
    "resolve_browser_session_config": "autocontext.integrations.browser.policy",
    "select_chrome_cdp_target": "autocontext.integrations.browser.chrome_cdp_discovery",
    "validate_browser_action": "autocontext.integrations.browser.validate",
    "validate_browser_action_dict": "autocontext.integrations.browser.validate",
    "validate_browser_audit_event": "autocontext.integrations.browser.validate",
    "validate_browser_audit_event_dict": "autocontext.integrations.browser.validate",
    "validate_browser_session_config": "autocontext.integrations.browser.validate",
    "validate_browser_session_config_dict": "autocontext.integrations.browser.validate",
    "validate_browser_snapshot": "autocontext.integrations.browser.validate",
    "validate_browser_snapshot_dict": "autocontext.integrations.browser.validate",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name), name)
