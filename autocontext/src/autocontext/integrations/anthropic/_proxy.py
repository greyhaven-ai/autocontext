"""ClientProxy — attribute-delegating wrapper around Anthropic clients."""
from __future__ import annotations

import time
import traceback
from datetime import UTC, datetime
from typing import Any

from ulid import ULID

from autocontext.integrations._shared.identity import resolve_identity
from autocontext.integrations._shared.sink import TraceSink
from autocontext.integrations.anthropic._taxonomy import map_exception_to_reason
from autocontext.integrations.anthropic._trace_builder import (
    build_failure_trace,
    build_request_snapshot,
    build_success_trace,
    finalize_streaming_trace,
)

_WRAPPED_SENTINEL = "__autocontext_wrapped__"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _is_async_client(client: Any) -> bool:
    try:
        from anthropic import AsyncAnthropic  # noqa: PLC0415
        return isinstance(client, AsyncAnthropic)
    except ImportError:
        pass
    return type(client).__name__.startswith("Async")


def _response_usage_and_content(response: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    usage = None
    if getattr(response, "usage", None):
        usage = response.usage.model_dump() if hasattr(response.usage, "model_dump") else dict(response.usage)
    content = response.content if hasattr(response, "content") else []
    stop_reason = response.stop_reason if hasattr(response, "stop_reason") else None
    if content and not isinstance(content[0], dict):
        content_list = [b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in content]
    else:
        content_list = list(content)
    return usage, content_list, stop_reason


class _MessagesProxy:
    def __init__(self, parent: ClientProxy) -> None:
        self._parent = parent

    def create(self, **kwargs: Any) -> Any:
        stream = kwargs.get("stream", False)
        if stream:
            if self._parent._is_async:
                return self._parent._invoke_streaming_async(kwargs=kwargs)
            return self._parent._invoke_streaming(kwargs=kwargs)
        if self._parent._is_async:
            return self._parent._invoke_non_streaming_async(kwargs=kwargs)
        return self._parent._invoke_non_streaming(kwargs=kwargs)

    def stream(self, **kwargs: Any) -> Any:
        if self._parent._is_async:
            return self._parent._invoke_helper_streaming_async(kwargs=kwargs)
        return self._parent._invoke_helper_streaming(kwargs=kwargs)


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
        if name == "messages":
            return _MessagesProxy(self)
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

    def _invoke_non_streaming(self, *, kwargs: dict[str, Any]) -> Any:
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = self._inner.messages.create(**kwargs)
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
        usage, content_list, stop_reason = _response_usage_and_content(response)
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_content=content_list,
            response_usage=usage,
            response_stop_reason=stop_reason,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    async def _invoke_non_streaming_async(self, *, kwargs: dict[str, Any]) -> Any:
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        try:
            response = await self._inner.messages.create(**kwargs)
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
        usage, content_list, stop_reason = _response_usage_and_content(response)
        trace = build_success_trace(
            request_snapshot=request_snapshot,
            response_content=content_list,
            response_usage=usage,
            response_stop_reason=stop_reason,
            identity=identity,
            timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
            env=self._env(),
            source_info=self._source_info(),
            trace_id=str(ULID()),
        )
        self._sink.add(trace)
        return response

    def _invoke_streaming(self, *, kwargs: dict[str, Any]) -> Any:
        from autocontext.integrations.anthropic._stream import StreamProxy  # noqa: PLC0415
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        inner_stream = self._inner.messages.create(**kwargs)
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_finalize(acc: Any, outcome: dict[str, Any]) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = finalize_streaming_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                accumulated_content_blocks=acc.content_blocks,
                accumulated_usage=acc.usage or None,
                accumulated_stop_reason=acc.stop_reason,
                outcome=outcome,
            )
            sink.add(trace)

        return StreamProxy(inner_stream=inner_stream, on_finalize=on_finalize)

    def _invoke_helper_streaming(self, *, kwargs: dict[str, Any]) -> Any:
        from autocontext.integrations.anthropic._stream import HelperStreamManagerProxy  # noqa: PLC0415

        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_success(message: Any) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            usage, content_list, stop_reason = _response_usage_and_content(message)
            trace = build_success_trace(
                request_snapshot=request_snapshot,
                response_content=content_list,
                response_usage=usage,
                response_stop_reason=stop_reason,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
            )
            sink.add(trace)

        def on_failure(exc: BaseException) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            sink.add(trace)

        def on_partial(message: Any) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            usage, content_list, stop_reason = _response_usage_and_content(message)
            trace = build_success_trace(
                request_snapshot=request_snapshot,
                response_content=content_list,
                response_usage=usage,
                response_stop_reason=stop_reason,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
            )
            trace["outcome"] = {"label": "partial", "reasoning": "abandonedStream"}
            sink.add(trace)

        inner_manager = self._inner.messages.stream(**kwargs)
        return HelperStreamManagerProxy(
            inner_manager=inner_manager,
            on_success=on_success,
            on_failure=on_failure,
            on_partial=on_partial,
        )

    def _invoke_streaming_async(self, *, kwargs: dict[str, Any]) -> Any:
        import inspect  # noqa: PLC0415

        from autocontext.integrations.anthropic._stream import AsyncStreamProxy  # noqa: PLC0415
        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_finalize(acc: Any, outcome: dict[str, Any]) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = finalize_streaming_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                accumulated_content_blocks=acc.content_blocks,
                accumulated_usage=acc.usage or None,
                accumulated_stop_reason=acc.stop_reason,
                outcome=outcome,
            )
            sink.add(trace)

        async def _make_proxy() -> AsyncStreamProxy:
            raw = self._inner.messages.create(**kwargs)
            if inspect.iscoroutine(raw) or hasattr(raw, "__await__"):
                inner_stream = await raw
            else:
                inner_stream = raw
            return AsyncStreamProxy(inner_stream=inner_stream, on_finalize=on_finalize)

        return _make_proxy()

    def _invoke_helper_streaming_async(self, *, kwargs: dict[str, Any]) -> Any:
        from autocontext.integrations.anthropic._stream import AsyncHelperStreamManagerProxy  # noqa: PLC0415

        per_call = kwargs.pop("autocontext", None)
        identity = resolve_identity(per_call)
        request_snapshot = build_request_snapshot(
            model=kwargs.get("model", ""),
            messages=kwargs.get("messages", []),
            extra_kwargs={k: v for k, v in kwargs.items() if k not in {"model", "messages"}},
        )
        started_at = _now_iso()
        started_monotonic = time.monotonic()
        sink = self._sink
        env = self._env()
        source_info = self._source_info()

        def on_success(message: Any) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            usage, content_list, stop_reason = _response_usage_and_content(message)
            trace = build_success_trace(
                request_snapshot=request_snapshot,
                response_content=content_list,
                response_usage=usage,
                response_stop_reason=stop_reason,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
            )
            sink.add(trace)

        def on_failure(exc: BaseException) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            trace = build_failure_trace(
                request_snapshot=request_snapshot,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
                reason_key=map_exception_to_reason(exc),
                error_message=str(exc),
                stack=traceback.format_exc(),
            )
            sink.add(trace)

        def on_partial(message: Any) -> None:
            ended_at = _now_iso()
            latency_ms = int((time.monotonic() - started_monotonic) * 1000)
            usage, content_list, stop_reason = _response_usage_and_content(message)
            trace = build_success_trace(
                request_snapshot=request_snapshot,
                response_content=content_list,
                response_usage=usage,
                response_stop_reason=stop_reason,
                identity=identity,
                timing={"startedAt": started_at, "endedAt": ended_at, "latencyMs": latency_ms},
                env=env,
                source_info=source_info,
                trace_id=str(ULID()),
            )
            trace["outcome"] = {"label": "partial", "reasoning": "abandonedStream"}
            sink.add(trace)

        inner_manager = self._inner.messages.stream(**kwargs)
        return AsyncHelperStreamManagerProxy(
            inner_manager=inner_manager,
            on_success=on_success,
            on_failure=on_failure,
            on_partial=on_partial,
        )
