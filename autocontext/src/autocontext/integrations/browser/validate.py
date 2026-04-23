"""Validation helpers for browser exploration contract documents."""

from __future__ import annotations

from typing import Any, cast

from pydantic import TypeAdapter, ValidationError, model_validator

from autocontext.integrations.browser.contract.types import (
    BrowserAction,
    BrowserAuditEvent,
    BrowserSessionConfig,
    BrowserSnapshot,
)


class ValidatedBrowserSessionConfig(BrowserSessionConfig):
    @model_validator(mode="after")
    def _validate_cross_field_policy(self) -> ValidatedBrowserSessionConfig:
        if self.allowDownloads and not self.downloadsRoot:
            raise ValueError("downloadsRoot is required when allowDownloads=true")
        if self.allowUploads and not self.uploadsRoot:
            raise ValueError("uploadsRoot is required when allowUploads=true")
        if self.profileMode == "user-profile" and not self.allowAuth:
            raise ValueError("allowAuth must be true when profileMode=user-profile")
        return self


def validate_browser_session_config(data: Any) -> BrowserSessionConfig:
    return ValidatedBrowserSessionConfig.model_validate(data)


def validate_browser_action(data: Any) -> BrowserAction:
    return cast(BrowserAction, _validate_any(data, BrowserAction))


def validate_browser_snapshot(data: Any) -> BrowserSnapshot:
    return BrowserSnapshot.model_validate(data)


def validate_browser_audit_event(data: Any) -> BrowserAuditEvent:
    return BrowserAuditEvent.model_validate(data)


def validate_browser_session_config_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, ValidatedBrowserSessionConfig)


def validate_browser_action_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, BrowserAction)


def validate_browser_snapshot_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, BrowserSnapshot)


def validate_browser_audit_event_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, BrowserAuditEvent)


def _validate_dict(data: Any, model: Any) -> tuple[bool, list[str]]:
    try:
        _validate_any(data, model)
    except ValidationError as exc:
        return False, [_format_error(err) for err in exc.errors()]
    return True, []


def _validate_any(data: Any, model: Any) -> Any:
    model_validate = getattr(model, "model_validate", None)
    if callable(model_validate):
        return model_validate(data)
    return TypeAdapter(model).validate_python(data)


def _format_error(err: Any) -> str:
    loc = err.get("loc") or ()
    path = ".".join(str(part) for part in loc) if loc else "<root>"
    msg = err.get("msg", "invalid")
    return f"{path}: {msg}"


__all__ = [
    "validate_browser_action",
    "validate_browser_action_dict",
    "validate_browser_audit_event",
    "validate_browser_audit_event_dict",
    "validate_browser_session_config",
    "validate_browser_session_config_dict",
    "validate_browser_snapshot",
    "validate_browser_snapshot_dict",
]
