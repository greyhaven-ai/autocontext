"""Syntactic validation for outbound HTTP requests."""

from __future__ import annotations

import string
from collections.abc import Mapping

_HTTP_TOKEN_CHARACTERS = frozenset(string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~")


class _OutboundInputError(ValueError):
    """An outbound method or header is unsafe or malformed."""


def _contains_c0_or_del(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _validated_request(
    method: str,
    headers: Mapping[str, str] | None,
) -> tuple[str, dict[str, str]]:
    if not isinstance(method, str) or not method or any(character not in _HTTP_TOKEN_CHARACTERS for character in method):
        raise _OutboundInputError("outbound HTTP method is invalid")

    validated_headers: dict[str, str] = {}
    for name, value in (headers or {}).items():
        if not isinstance(name, str) or not name or any(character not in _HTTP_TOKEN_CHARACTERS for character in name):
            raise _OutboundInputError("outbound HTTP header name is invalid")
        if name.casefold() == "host":
            raise _OutboundInputError("outbound HTTP Host header overrides are not allowed")
        if not isinstance(value, str) or _contains_c0_or_del(value):
            raise _OutboundInputError("outbound HTTP header value is invalid")
        try:
            value.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise _OutboundInputError("outbound HTTP header value is invalid") from exc
        validated_headers[name] = value
    return method.upper(), validated_headers
