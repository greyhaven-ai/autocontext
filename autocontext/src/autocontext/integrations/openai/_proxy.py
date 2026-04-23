"""ClientProxy — attribute-delegating wrapper around an OpenAI client.

Intercepts ``.chat.completions.create`` / ``.chat.completions.create``-async /
``.responses.create`` / ``.responses.create``-async. All other attribute
access passes through transparently. Spec §4.1 + §6.2.

Streaming is handled by ``_stream.StreamProxy`` — this module only dispatches.
"""
from __future__ import annotations

import inspect
import time
import traceback
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

from autocontext.integrations._shared.identity import resolve_identity
from autocontext.integrations.openai._sink import TraceSink
from autocontext.integrations.openai._taxonomy import map_exception_to_reason
from autocontext.integrations.openai._trace_builder import (
    build_failure_trace,
    build_request_snapshot,
    build_success_trace,
)


def _is_async_client(client: Any) -> bool:
    """Return True if client is an AsyncOpenAI (or compatible async client)."""
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
        return isinstance(client, AsyncOpenAI)
    except ImportError:
        pass
    # Fallback: check class name
    return type(client).__name__.startswith("Async")

_WRAPPED_SENTINEL = "__autocontext_wrapped__"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class _ChatCompletionsProxy:
    def __init__(self, parent: ClientProxy, inner_create: Any) -> None:
        self._parent = parent
        self._inner_create = inner_create

    def create(self, **kwargs: Any) -> Any:
        if kwargs.get("stream", False):
            if self._parent._is_async:
                return self._parent._invoke_streaming_async(
                    inner_method=self._inner_create, kwargs=kwargs
                )
            return self._parent._invoke_streaming(
                inner_method=self._inner_create, kwargs=kwargs
            )
        if self._parent._is_async:
            return self._parent._invoke_non_streaming_async(
                inner_method=self._inner_create, kwargs=kwargs,
            )
        return self._parent._invoke_non_streaming(
            inner_method=self._inner_create, kwargs=kwargs,
        )


class _ChatProxy:
    def __init__(self, parent: ClientProxy, inner_chat: Any) -> None:
        self._parent = parent
        self._inner_chat = inner_chat

    @property
    def completions(self) -> _ChatCompletionsProxy:
        return _ChatCompletionsProxy(self._parent, self._inner_chat.completions.create)


class _ResponsesProxy:
    def __init__(self, parent: ClientProxy, inner_responses_create: Any) -> None:
        self._parent = parent
        self._inner_create = inner_responses_create

    def create(self, **kwargs: Any) -> Any:
        # responses.create currently shares the same request/response envelope
        # semantics vs chat.completions — messages come in as `input` (single
        # string or list of content blocks). For v1 coverage we pass the input
        # through as-is under `messages` key in the trace for schema compatibility.
        normalized_messages = kwargs.get("messages") or [
            {"role": "user", "content": kwargs.get("input", "")}
        ]
        kwargs_for_trace = dict(kwargs)
        kwargs_for_trace["messages"] = normalized_messages
        kwargs_for_trace.pop("input", None)
        if kwargs.get("stream", False):
            if self._parent._is_async:
                raise NotImplementedError("async responses streaming — see deferred list")
            return self._parent._invoke_streaming(
                inner_method=self._inner_create, kwargs=kwargs,
            )
        if self._parent._is_async:
            return self._parent._invoke_non_streaming_async_responses(
                inner_method=self._inner_create, kwargs=kwargs,
                normalized_messages=normalized_messages,
            )
        return self._parent._invoke_non_streaming_responses(
            inner_method=self._inner_create, kwargs=kwargs,
            normalized_messages=normalized_messages,
        )


class ClientProxy:
    def __init__(
        self,
        *,
        inner: Any,
        sink: TraceSink,
        app_id: str,
        environment_tag: str,
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_sink", sink)
        object.__setattr__(self, "_app_id", app_id)
        object.__setattr__(self, "_environment_tag", environment_tag)
        object.__setattr__(self, "_is_async", _is_async_client(inner))
        object.__setattr__(self, _WRAPPED_SENTINEL, True)

    def __getattr__(self, name: str) -> Any:
        if name == "chat":
            return _ChatProxy(self, self._inner.chat)
        if name == "responses":
            return _ResponsesProxy(self, self._inner.responses.create)
        return getattr(self._inner, name)

    def _source_info(self) -> dict[str, Any]:
        try:
            from importlib.metadata import version
            ver = version("autocontext")
        except Exception:
            ver = "0.0.0"
        return {"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": ver}}

    def _env(self) -> dict[str, Any]:
        return {"environmentTag": self._environment_tag, "appId": self._app_id}

    def _invoke_non_streaming(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        if kwargs.get("stream", False):
            raise NotImplementedError("streaming not yet wired")
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = inner_method(**kwargs)
        except Exception as exc:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=self._env(),
                source_info=self._source_info(),
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            self._sink.add(trace)
            raise
        ended_at = _now_iso()
        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        tool_calls = None
        if hasattr(response, "choices") and response.choices and response.choices[0].message.tool_calls:
            tool_calls = [tc.model_dump() for tc in response.choices[0].message.tool_calls]
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_usage=usage,
            response_tool_calls=tool_calls,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    def _invoke_non_streaming_responses(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
        normalized_messages: list[dict[str, Any]],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        model = kwargs.get("model", "")
        request_snapshot = build_request_snapshot(
            model=model,
            messages=normalized_messages,
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages", "input"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = inner_method(**kwargs)
        except Exception as exc:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=self._env(),
                source_info=self._source_info(),
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            self._sink.add(trace)
            raise
        ended_at = _now_iso()
        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_usage=usage,
            response_tool_calls=None,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    async def _invoke_non_streaming_async(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        if kwargs.get("stream", False):
            raise NotImplementedError("async streaming wired in Task 2.8")
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = await inner_method(**kwargs)
        except Exception as exc:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=self._env(),
                source_info=self._source_info(),
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            self._sink.add(trace)
            raise
        ended_at = _now_iso()
        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        tool_calls = None
        if hasattr(response, "choices") and response.choices and response.choices[0].message.tool_calls:
            tool_calls = [tc.model_dump() for tc in response.choices[0].message.tool_calls]
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_usage=usage,
            response_tool_calls=tool_calls,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    async def _invoke_non_streaming_async_responses(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
        normalized_messages: list[dict[str, Any]],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        model = kwargs.get("model", "")
        request_snapshot = build_request_snapshot(
            model=model,
            messages=normalized_messages,
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages", "input"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = await inner_method(**kwargs)
        except Exception as exc:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=self._env(),
                source_info=self._source_info(),
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            self._sink.add(trace)
            raise
        ended_at = _now_iso()
        latency_ms = int((time.monotonic() - started_monotonic) * 1000)
        usage = response.usage.model_dump() if getattr(response, "usage", None) else None
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_usage=usage,
            response_tool_calls=None,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    def _invoke_streaming(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        # Auto-inject stream_options.include_usage = True if absent.
        stream_opts = dict(kwargs.get("stream_options") or {})
        if "include_usage" not in stream_opts:
            stream_opts["include_usage"] = True
            kwargs["stream_options"] = stream_opts
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()

        inner_stream = inner_method(**kwargs)

        from autocontext.integrations.openai._stream import StreamProxy
        from autocontext.integrations.openai._trace_builder import finalize_streaming_trace

        # Store the accumulator reference in a dict so that the closure captures
        # the dict (not the proxy), avoiding a reference cycle that would prevent GC.
        acc_ref: dict[str, Any] = {"accumulator": None}
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_finalize(outcome: dict[str, Any]) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            acc = acc_ref["accumulator"] or {"usage": None, "tool_calls": None}
            trace = finalize_streaming_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                accumulated_usage=acc["usage"],
                accumulated_tool_calls=acc["tool_calls"],
                outcome=outcome,
            )
            sink.add(trace)

        proxy = StreamProxy(inner_stream=inner_stream, on_finalize=on_finalize)
        # Store the proxy's accumulator in acc_ref using a weakref to avoid a
        # cycle: proxy → on_finalize → acc_ref → proxy's accumulator
        # We link via the accumulator dict (not the proxy itself)
        acc_ref["accumulator"] = proxy._accumulator
        return proxy

    def _invoke_streaming_async(
        self,
        *,
        inner_method: Any,
        kwargs: dict[str, Any],
    ) -> Any:
        per_call = kwargs.pop("autocontext", None)
        # Auto-inject stream_options.include_usage = True if absent.
        stream_opts = dict(kwargs.get("stream_options") or {})
        if "include_usage" not in stream_opts:
            stream_opts["include_usage"] = True
            kwargs["stream_options"] = stream_opts
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()

        from autocontext.integrations.openai._stream import AsyncStreamProxy
        from autocontext.integrations.openai._trace_builder import finalize_streaming_trace

        acc_ref_async: dict[str, Any] = {"accumulator": None}
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_finalize(outcome: dict[str, Any]) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            acc = acc_ref_async["accumulator"] or {"usage": None, "tool_calls": None}
            trace = finalize_streaming_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                accumulated_usage=acc["usage"],
                accumulated_tool_calls=acc["tool_calls"],
                outcome=outcome,
            )
            sink.add(trace)

        async def _make_proxy() -> AsyncStreamProxy:
            coro_or_stream = inner_method(**kwargs)
            # AsyncCompletions.create may be a coroutine or direct async context manager
            if inspect.iscoroutine(coro_or_stream):
                inner_stream = await coro_or_stream
            else:
                inner_stream = coro_or_stream
            # If the stream itself is an async context manager, enter it
            if hasattr(inner_stream, "__aenter__"):
                inner_stream = await inner_stream.__aenter__()
            proxy = AsyncStreamProxy(inner_stream=inner_stream, on_finalize=on_finalize)
            # Link accumulator into acc_ref_async to avoid cycle
            acc_ref_async["accumulator"] = proxy._accumulator
            return proxy

        return _make_proxy()
