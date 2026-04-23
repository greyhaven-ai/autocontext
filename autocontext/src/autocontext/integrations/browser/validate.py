"""Validation helpers for browser exploration contract documents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, cast

from pydantic import BeforeValidator, TypeAdapter, ValidationError, model_validator

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


_ACTION_NON_NULL_OPTIONAL_PATHS = (
    ("params", "captureHtml"),
    ("params", "captureScreenshot"),
    ("params", "fieldKind"),
)

_SNAPSHOT_NON_NULL_OPTIONAL_PATHS = (
    ("refs", "*", "role"),
    ("refs", "*", "name"),
    ("refs", "*", "text"),
    ("refs", "*", "selector"),
    ("refs", "*", "disabled"),
)

_VALIDATED_BROWSER_ACTION_ADAPTER: TypeAdapter[BrowserAction] = TypeAdapter(
    Annotated[BrowserAction, BeforeValidator(lambda data: _reject_explicit_nulls_for_action(data))]
)
_VALIDATED_BROWSER_SNAPSHOT_ADAPTER: TypeAdapter[BrowserSnapshot] = TypeAdapter(
    Annotated[BrowserSnapshot, BeforeValidator(lambda data: _reject_explicit_nulls_for_snapshot(data))]
)


def validate_browser_session_config(data: Any) -> BrowserSessionConfig:
    return ValidatedBrowserSessionConfig.model_validate(data)


def validate_browser_action(data: Any) -> BrowserAction:
    return cast(BrowserAction, _VALIDATED_BROWSER_ACTION_ADAPTER.validate_python(data))


def validate_browser_snapshot(data: Any) -> BrowserSnapshot:
    return cast(BrowserSnapshot, _VALIDATED_BROWSER_SNAPSHOT_ADAPTER.validate_python(data))


def validate_browser_audit_event(data: Any) -> BrowserAuditEvent:
    return BrowserAuditEvent.model_validate(data)


def validate_browser_session_config_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, validate_browser_session_config)


def validate_browser_action_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, validate_browser_action)


def validate_browser_snapshot_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, validate_browser_snapshot)


def validate_browser_audit_event_dict(data: Any) -> tuple[bool, list[str]]:
    return _validate_dict(data, validate_browser_audit_event)


def _validate_dict(data: Any, validator: Any) -> tuple[bool, list[str]]:
    try:
        validator(data)
    except ValidationError as exc:
        return False, [_format_error(err) for err in exc.errors()]
    return True, []


def _reject_explicit_nulls_for_action(data: Any) -> Any:
    _reject_explicit_null_paths(data, _ACTION_NON_NULL_OPTIONAL_PATHS)
    return data


def _reject_explicit_nulls_for_snapshot(data: Any) -> Any:
    _reject_explicit_null_paths(data, _SNAPSHOT_NON_NULL_OPTIONAL_PATHS)
    return data


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


def _reject_explicit_null_paths(data: Any, paths: tuple[tuple[str, ...], ...]) -> None:
    for path in paths:
        _reject_explicit_null_path(data, path, ())


def _reject_explicit_null_path(data: Any, path: tuple[str, ...], current: tuple[str, ...]) -> None:
    if not path:
        return

    head, *tail = path
    if head == "*":
        if isinstance(data, list):
            for idx, item in enumerate(data):
                _reject_explicit_null_path(item, tuple(tail), (*current, str(idx)))
        return

    if not isinstance(data, Mapping) or head not in data:
        return

    value = data[head]
    if not tail:
        if value is None:
            raise ValueError(f"{'.'.join((*current, head))} must not be null")
        return

    _reject_explicit_null_path(value, tuple(tail), (*current, head))


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
