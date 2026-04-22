"""StreamProxy — wraps OpenAI Stream / AsyncStream; finalize-on-end/exception/abandon.

Spec §6.3. Accumulates deltas in-memory (bounded by context window). Injects
``stream_options.include_usage=True`` when customer didn't set it, so
terminal-chunk usage is authoritative. ``weakref.finalize`` triggers abandoned
trace emission when the proxy is GC'd without completion.
"""
from __future__ import annotations

import traceback
import weakref
from collections.abc import Callable
from typing import Any


class StreamProxy:
    def __init__(
        self,
        *,
        inner_stream: Any,
        on_finalize: Callable[[dict[str, Any]], None],
    ) -> None:
        self._inner = inner_stream
        self._on_finalize = on_finalize
        self._accumulator: dict[str, Any] = {
            "content": [],
            "usage": None,
            "tool_calls": None,
        }
        # Use a mutable cell to track finalization state WITHOUT creating a
        # strong reference cycle. The cell is shared between the proxy and the
        # finalizer callback.
        self._state: dict[str, bool] = {"finalized": False}
        state = self._state
        cb = on_finalize
        self._finalizer = weakref.finalize(
            self, _abandoned_callback, state, cb
        )

    @property
    def _finalized(self) -> bool:
        return self._state["finalized"]

    @_finalized.setter
    def _finalized(self, value: bool) -> None:
        self._state["finalized"] = value

    def __enter__(self) -> StreamProxy:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._state["finalized"]:
            return
        if exc_type is not None:
            from autocontext.integrations.openai._taxonomy import map_exception_to_reason
            self._on_finalize({
                "label": "failure",
                "error": {
                    "type": map_exception_to_reason(exc_val),
                    "message": str(exc_val),
                    "stack": traceback.format_exc(),
                },
            })
        else:
            self._on_finalize({"label": "success"})
        self._state["finalized"] = True
        self._finalizer.detach()
        if hasattr(self._inner, "close"):
            try:
                self._inner.close()
            except Exception:
                pass

    def __iter__(self) -> StreamProxy:
        return self

    def __next__(self) -> Any:
        try:
            chunk = next(iter(self._inner))
        except StopIteration:
            if not self._state["finalized"]:
                self._on_finalize({"label": "success"})
                self._state["finalized"] = True
                self._finalizer.detach()
            raise
        self._accumulate(chunk)
        return chunk

    def _accumulate(self, chunk: Any) -> None:
        if getattr(chunk, "usage", None):
            self._accumulator["usage"] = (
                chunk.usage.model_dump()
                if hasattr(chunk.usage, "model_dump")
                else dict(chunk.usage)
            )
        if chunk.choices:
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                self._accumulator["content"].append(delta.content)
            if getattr(delta, "tool_calls", None):
                if self._accumulator["tool_calls"] is None:
                    self._accumulator["tool_calls"] = []
                for tc in delta.tool_calls:
                    self._accumulator["tool_calls"].append(
                        tc.model_dump() if hasattr(tc, "model_dump") else dict(tc)
                    )

    def accumulated(self) -> dict[str, Any]:
        return dict(self._accumulator)


def _abandoned_callback(
    state: dict[str, bool],
    on_finalize: Callable[[dict[str, Any]], None],
) -> None:
    """Called by weakref.finalize when a StreamProxy is GC'd without completion."""
    if state.get("finalized"):
        return
    try:
        on_finalize({"label": "partial", "reasoning": "abandonedStream"})
    except Exception:
        pass
    state["finalized"] = True


class AsyncStreamProxy:
    def __init__(
        self,
        *,
        inner_stream: Any,
        on_finalize: Callable[[dict[str, Any]], None],
    ) -> None:
        self._inner = inner_stream
        self._on_finalize = on_finalize
        self._accumulator: dict[str, Any] = {"content": [], "usage": None, "tool_calls": None}
        self._state: dict[str, bool] = {"finalized": False}
        state = self._state
        cb = on_finalize
        self._finalizer = weakref.finalize(self, _abandoned_callback, state, cb)

    @property
    def _finalized(self) -> bool:
        return self._state["finalized"]

    async def __aenter__(self) -> AsyncStreamProxy:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._state["finalized"]:
            return
        if exc_type is not None:
            from autocontext.integrations.openai._taxonomy import map_exception_to_reason
            self._on_finalize({
                "label": "failure",
                "error": {
                    "type": map_exception_to_reason(exc_val),
                    "message": str(exc_val),
                    "stack": traceback.format_exc(),
                },
            })
        else:
            self._on_finalize({"label": "success"})
        self._state["finalized"] = True
        self._finalizer.detach()

    def __aiter__(self) -> AsyncStreamProxy:
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._inner.__anext__()
        except StopAsyncIteration:
            if not self._state["finalized"]:
                self._on_finalize({"label": "success"})
                self._state["finalized"] = True
                self._finalizer.detach()
            raise
        StreamProxy._accumulate(self, chunk)  # type: ignore[arg-type]  # reuse impl
        return chunk

    def accumulated(self) -> dict[str, Any]:
        return dict(self._accumulator)
