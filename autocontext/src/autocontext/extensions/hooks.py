from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class HookEvents(StrEnum):
    """Stable built-in hook names inspired by Pi's extension event contracts."""

    RUN_START = "run_start"
    RUN_END = "run_end"
    GENERATION_START = "generation_start"
    GENERATION_END = "generation_end"
    CONTEXT_COMPONENTS = "context_components"
    CONTEXT = "context"
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"
    BEFORE_PROVIDER_REQUEST = "before_provider_request"
    AFTER_PROVIDER_RESPONSE = "after_provider_response"
    BEFORE_JUDGE = "before_judge"
    AFTER_JUDGE = "after_judge"
    ARTIFACT_WRITE = "artifact_write"


@dataclass(frozen=True, slots=True)
class HookResult:
    """A hook's requested changes to the in-flight event.

    ``payload`` is merged into the event payload by default. Set
    ``replace_payload`` when a hook wants to replace the complete payload.
    """

    payload: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None
    replace_payload: bool = False
    block: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class HookError:
    """Non-fatal hook failure captured when the bus is not fail-fast."""

    event_name: str
    handler: str
    message: str


@dataclass(slots=True)
class HookEvent:
    """Mutable event object passed to extension handlers."""

    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    errors: list[HookError] = field(default_factory=list)
    blocked: bool = False
    block_reason: str = ""

    def raise_if_blocked(self) -> None:
        if self.blocked:
            raise event_block_error(self)


HookHandler = Callable[[HookEvent], HookResult | Mapping[str, Any] | None]


def event_name(value: HookEvents | str) -> str:
    return value.value if isinstance(value, HookEvents) else str(value)


def event_block_error(event: HookEvent) -> RuntimeError:
    reason = f": {event.block_reason}" if event.block_reason else ""
    return RuntimeError(f"extension hook blocked {event.name}{reason}")


def _handler_name(handler: HookHandler) -> str:
    module = getattr(handler, "__module__", "")
    qualname = getattr(handler, "__qualname__", "")
    if module and qualname:
        return f"{module}.{qualname}"
    return repr(handler)


class HookBus:
    """Ordered, fail-open extension hook bus.

    Hooks run in registration order. They may mutate the event directly or return
    a ``HookResult`` / mapping. Handler exceptions are recorded on the event by
    default so production runs do not die because an optional extension failed.
    """

    def __init__(self, *, fail_fast: bool = False) -> None:
        self.fail_fast = fail_fast
        self._handlers: dict[str, list[HookHandler]] = {}

    def on(self, name: HookEvents | str, handler: HookHandler) -> HookHandler:
        self._handlers.setdefault(event_name(name), []).append(handler)
        return handler

    def has_handlers(self, name: HookEvents | str) -> bool:
        normalized = event_name(name)
        return bool(self._handlers.get(normalized) or self._handlers.get("*"))

    def emit(
        self,
        name: HookEvents | str,
        payload: Mapping[str, Any] | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> HookEvent:
        normalized = event_name(name)
        event = HookEvent(
            name=normalized,
            payload=dict(payload or {}),
            metadata=dict(metadata or {}),
        )
        handlers = [*self._handlers.get(normalized, ()), *self._handlers.get("*", ())]
        for handler in handlers:
            try:
                result = handler(event)
            except Exception as exc:
                if self.fail_fast:
                    raise
                event.errors.append(
                    HookError(
                        event_name=normalized,
                        handler=_handler_name(handler),
                        message=str(exc),
                    )
                )
                continue
            self._apply_result(event, result)
            if event.blocked:
                break
        return event

    @staticmethod
    def _apply_result(event: HookEvent, result: HookResult | Mapping[str, Any] | None) -> None:
        if result is None:
            return
        if isinstance(result, HookResult):
            if result.payload is not None:
                if result.replace_payload:
                    event.payload = dict(result.payload)
                else:
                    event.payload.update(result.payload)
            if result.metadata is not None:
                event.metadata.update(result.metadata)
            if result.block:
                event.blocked = True
                event.block_reason = result.reason
            return
        event.payload.update(dict(result))


_CURRENT_HOOK_BUS: ContextVar[HookBus | None] = ContextVar("autocontext_current_hook_bus", default=None)


def get_current_hook_bus() -> HookBus | None:
    return _CURRENT_HOOK_BUS.get()


@contextmanager
def active_hook_bus(hook_bus: HookBus | None) -> Iterator[None]:
    if hook_bus is None:
        yield
        return
    token = _CURRENT_HOOK_BUS.set(hook_bus)
    try:
        yield
    finally:
        _CURRENT_HOOK_BUS.reset(token)


class ExtensionAPI:
    """Small registration facade passed to extension modules."""

    def __init__(self, bus: HookBus) -> None:
        self.bus = bus

    def on(
        self,
        name: HookEvents | str,
        handler: HookHandler | None = None,
    ) -> HookHandler | Callable[[HookHandler], HookHandler]:
        if handler is not None:
            return self.bus.on(name, handler)

        def decorator(actual: HookHandler) -> HookHandler:
            return self.bus.on(name, actual)

        return decorator

    def emit(
        self,
        name: HookEvents | str,
        payload: Mapping[str, Any] | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> HookEvent:
        return self.bus.emit(name, payload, metadata=metadata)
