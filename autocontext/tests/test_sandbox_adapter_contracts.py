from __future__ import annotations

from autocontext.execution.sandbox_adapter_contracts import (
    SANDBOX_CAPABILITY_NAMES,
    lifecycle_hooks_for_boot_mode,
    normalize_sandbox_adapter_capabilities,
    plan_sandbox_startup,
)
from autocontext.session.background_session_events import build_sandbox_capability_session_event  # type: ignore[import-untyped]

_TIMESTAMP = "2026-06-01T00:07:00.000Z"
_SESSION_ID = "run:run-123:runtime"


class LocalAdapter:
    pass


class RemoteAdapter:
    sandbox_capabilities = {
        "snapshot": True,
        "restore": True,
        "prebuild_repo_image": False,
        "warm": True,
        "resolve_tunnel_ports": True,
        "provider_internal": True,
    }


def test_sandbox_adapter_capabilities_default_to_false_for_local_execution() -> None:
    assert SANDBOX_CAPABILITY_NAMES == (
        "snapshot",
        "restore",
        "prebuild_repo_image",
        "warm",
        "resolve_tunnel_ports",
    )
    assert normalize_sandbox_adapter_capabilities(LocalAdapter()) == {
        "snapshot": False,
        "restore": False,
        "prebuild_repo_image": False,
        "warm": False,
        "resolve_tunnel_ports": False,
    }


def test_sandbox_adapter_capability_detection_ignores_unknown_provider_fields() -> None:
    assert normalize_sandbox_adapter_capabilities(RemoteAdapter()) == {
        "snapshot": True,
        "restore": True,
        "prebuild_repo_image": False,
        "warm": True,
        "resolve_tunnel_ports": True,
    }


def test_restore_startup_plan_uses_advertised_capability_and_skips_setup_hook() -> None:
    plan = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="restore",
        capabilities=RemoteAdapter(),
        snapshot_id="snap-123",
    )

    assert plan == {
        "session_id": _SESSION_ID,
        "requested_boot_mode": "restore",
        "boot_mode": "restored",
        "capability": "restore",
        "supported": True,
        "degraded": False,
        "terminal": False,
        "unsupported_policy": "degrade_to_fresh",
        "reason": "",
        "lifecycle_hooks": ["start"],
    }


def test_restore_startup_plan_requires_snapshot_ref_even_when_capability_is_advertised() -> None:
    degraded = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="restore",
        capabilities=RemoteAdapter(),
    )
    assert degraded == {
        "session_id": _SESSION_ID,
        "requested_boot_mode": "restore",
        "boot_mode": "fresh",
        "capability": "restore",
        "supported": True,
        "degraded": True,
        "terminal": False,
        "unsupported_policy": "degrade_to_fresh",
        "reason": "missing_snapshot_ref",
        "lifecycle_hooks": ["setup", "start"],
    }

    failed = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="restore",
        capabilities=RemoteAdapter(),
        unsupported_policy="fail_closed",
    )
    assert failed == {
        "session_id": _SESSION_ID,
        "requested_boot_mode": "restore",
        "boot_mode": "restored",
        "capability": "restore",
        "supported": True,
        "degraded": False,
        "terminal": True,
        "unsupported_policy": "fail_closed",
        "reason": "missing_snapshot_ref",
        "lifecycle_hooks": [],
    }


def test_warm_and_repo_image_requests_map_to_optional_capabilities() -> None:
    warm_plan = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="warm",
        capabilities=RemoteAdapter(),
        provider="remote",
    )
    assert warm_plan["boot_mode"] == "warmed"
    assert warm_plan["capability"] == "warm"
    assert warm_plan["supported"] is True
    assert warm_plan["lifecycle_hooks"] == ["start"]

    repo_image_plan = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="repo_image",
        capabilities=RemoteAdapter(),
        repo_image_id="repo-img-1",
    )
    assert repo_image_plan["boot_mode"] == "fresh"
    assert repo_image_plan["capability"] == "prebuild_repo_image"
    assert repo_image_plan["degraded"] is True
    assert repo_image_plan["reason"] == "unsupported_prebuild_repo_image"


def test_missing_restore_capability_degrades_or_fails_closed_by_policy() -> None:
    degraded = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="restore",
        capabilities=LocalAdapter(),
        snapshot_id="snap-123",
        unsupported_policy="degrade_to_fresh",
    )
    assert degraded == {
        "session_id": _SESSION_ID,
        "requested_boot_mode": "restore",
        "boot_mode": "fresh",
        "capability": "restore",
        "supported": False,
        "degraded": True,
        "terminal": False,
        "unsupported_policy": "degrade_to_fresh",
        "reason": "unsupported_restore",
        "lifecycle_hooks": ["setup", "start"],
    }

    failed = plan_sandbox_startup(
        session_id=_SESSION_ID,
        requested_boot_mode="restore",
        capabilities=LocalAdapter(),
        snapshot_id="snap-123",
        unsupported_policy="fail_closed",
    )
    assert failed == {
        "session_id": _SESSION_ID,
        "requested_boot_mode": "restore",
        "boot_mode": "restored",
        "capability": "restore",
        "supported": False,
        "degraded": False,
        "terminal": True,
        "unsupported_policy": "fail_closed",
        "reason": "unsupported_restore",
        "lifecycle_hooks": [],
    }


def test_lifecycle_hook_defaults_are_tied_to_boot_mode() -> None:
    assert lifecycle_hooks_for_boot_mode("fresh") == ["setup", "start"]
    assert lifecycle_hooks_for_boot_mode("build") == ["setup", "start"]
    assert lifecycle_hooks_for_boot_mode("restored") == ["start"]
    assert lifecycle_hooks_for_boot_mode("repo_image") == ["start"]
    assert lifecycle_hooks_for_boot_mode("warmed") == ["start"]


def test_sandbox_capability_events_drop_free_form_reason_text() -> None:
    event = build_sandbox_capability_session_event(
        session_id=_SESSION_ID,
        sequence=69,
        timestamp=_TIMESTAMP,
        capability="restore",
        phase="failed",
        boot_mode="restored",
        provider="primeintellect",
        unsupported_policy="fail_closed",
        reason="TOKEN=SECRET_VALUE",
        degraded=False,
        error="also SECRET_VALUE",
    )

    assert "reason" not in event["payload_summary"]
    assert "SECRET_VALUE" not in str(event)


def test_sandbox_capability_events_record_failures_without_raw_adapter_errors() -> None:
    event = build_sandbox_capability_session_event(
        session_id=_SESSION_ID,
        sequence=70,
        timestamp=_TIMESTAMP,
        capability="restore",
        phase="failed",
        boot_mode="restored",
        provider="primeintellect",
        unsupported_policy="fail_closed",
        reason="adapter_error",
        degraded=False,
        error="TOKEN=SECRET_VALUE",
    )

    assert event == {
        "event_id": "sandbox:run:run-123:runtime:restore:failed:70",
        "session_id": _SESSION_ID,
        "sequence": 70,
        "ts": _TIMESTAMP,
        "event": "session_status",
        "source_event_type": "sandbox_capability",
        "status": "failed",
        "title": "Sandbox restore failed",
        "payload_summary": {
            "capability": "restore",
            "phase": "failed",
            "boot_mode": "restored",
            "provider": "primeintellect",
            "unsupported_policy": "fail_closed",
            "reason": "adapter_error",
            "degraded": False,
        },
    }
    assert "SECRET_VALUE" not in str(event)
