"""Tests for the OpenAI exception → reason-key mapper."""
from __future__ import annotations

import httpx
import openai
import pytest

from autocontext.integrations.openai._taxonomy import map_exception_to_reason


def _make_httpx_response(status: int) -> httpx.Response:
    """Create a minimal httpx.Response with a request attached."""
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx.Response(status, request=req, json={"error": {"message": "err"}})


def _make_exc(exc_cls: type) -> Exception:
    """Construct an OpenAI exception instance in a SDK-version-safe way."""
    import inspect
    sig = inspect.signature(exc_cls.__init__)
    params = list(sig.parameters.keys())
    # APITimeoutError(request)
    if "request" in params and "response" not in params and "message" not in params:
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        return exc_cls(request=req)
    # APIConnectionError(message, request)
    if "request" in params and "message" in params and "response" not in params:
        req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        return exc_cls(message="boom", request=req)
    # ContentFilterFinishReasonError / LengthFinishReasonError — no args
    if len(params) == 1:  # just self
        return exc_cls()
    # APIStatusError subclasses (RateLimitError, BadRequestError, etc.)
    if "response" in params:
        resp = _make_httpx_response(400)
        return exc_cls("boom", response=resp, body={"error": {"message": "boom"}})
    # Fallback
    return exc_cls("boom")


@pytest.mark.parametrize(
    "exc_cls, expected",
    [
        (openai.RateLimitError, "rateLimited"),
        (openai.APITimeoutError, "timeout"),
        (openai.BadRequestError, "badRequest"),
        (openai.AuthenticationError, "authentication"),
        (openai.PermissionDeniedError, "permissionDenied"),
        (openai.NotFoundError, "notFound"),
        (openai.APIConnectionError, "apiConnection"),
    ],
)
def test_mapped_classes(exc_cls: type[Exception], expected: str) -> None:
    exc = _make_exc(exc_cls)
    assert map_exception_to_reason(exc) == expected


def test_missing_class_falls_through_to_uncategorized() -> None:
    class NotAnOpenAiError(Exception):
        pass
    assert map_exception_to_reason(NotAnOpenAiError("x")) == "uncategorized"


def test_content_filter_presence_guard() -> None:
    """If ContentFilterFinishReasonError is absent on this SDK version, pass-through uncategorized."""
    cls = getattr(openai, "ContentFilterFinishReasonError", None)
    if cls is None:
        # SDK too old — we can't raise the class; assert the guard kicks in
        # by checking the mapper with a synthetic subclass stand-in.
        class FakeCF(Exception):
            pass
        assert map_exception_to_reason(FakeCF("x")) == "uncategorized"
    else:
        exc = _make_exc(cls)
        assert map_exception_to_reason(exc) == "contentFilter"
