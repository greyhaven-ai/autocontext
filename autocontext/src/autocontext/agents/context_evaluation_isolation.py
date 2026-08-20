"""Fail-closed model-session isolation for matched context evaluation arms."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import TYPE_CHECKING, Any, cast

from autocontext.agents.llm_client import LanguageModelClient

if TYPE_CHECKING:
    from autocontext.agents.provider_bridge import RuntimeBridgeClient
    from autocontext.extensions import HookBus


_GRAPH_CONTAINERS = (list, tuple, set, frozenset)
_MAX_HOOK_GRAPH_NODES = 10_000


def isolate_context_bundle_client(client: LanguageModelClient, *, role: str = "") -> None:
    """Reset a role client or prove that it has no conversational state."""

    reset = getattr(client, "reset_context_for_evaluation", None)
    if callable(reset):
        if reset() is False:
            raise RuntimeError("language-model client refused context-bundle arm isolation")
        return
    if getattr(client, "context_bundle_evaluation_arm_isolated", False) is True:
        return

    # Recording/instrumentation state is not model context. The wrapped client
    # remains the boundary that must prove isolation.
    inner = getattr(client, "inner", None)
    if isinstance(inner, LanguageModelClient):
        isolate_context_bundle_client(inner, role=role)
        return

    from autocontext.agents.agent_sdk_client import AgentSdkClient
    from autocontext.agents.llm_client import (
        AnthropicClient,
        DeterministicDevClient,
        MLXClient,
        MLXLMClient,
    )
    from autocontext.agents.panel_runtime import PanelLanguageModelClient
    from autocontext.agents.provider_bridge import ProviderBridgeClient, RuntimeBridgeClient

    if isinstance(client, PanelLanguageModelClient):
        # Participant and synthesizer clients are created lazily from provider
        # names during ``generate``. Proving only the fallback client is safe
        # says nothing about those dynamic tool/session-capable routes. A panel
        # may opt in only through the explicit reset/isolated marker above.
        raise RuntimeError("dynamic model panel cannot prove context-bundle arm isolation")
    if isinstance(client, AgentSdkClient):
        from autocontext.agents.agent_sdk_client import ROLE_TOOL_CONFIG

        if role and not ROLE_TOOL_CONFIG.get(role, ROLE_TOOL_CONFIG["competitor"]):
            return
        raise RuntimeError("tool-capable Agent SDK client cannot prove context-bundle arm isolation")
    if isinstance(
        client,
        (
            AnthropicClient,
            DeterministicDevClient,
            MLXClient,
            MLXLMClient,
        ),
    ):
        # These clients issue one complete request per call and expose no
        # continuation/session identifier.
        return
    if isinstance(client, ProviderBridgeClient):
        _isolate_provider(client._provider)
        return
    if isinstance(client, RuntimeBridgeClient):
        _isolate_runtime_bridge(client)
        return
    raise RuntimeError(f"language-model client {type(client).__name__!r} cannot prove context-bundle arm isolation")


def require_context_bundle_transport_control(client: LanguageModelClient) -> None:
    """Require an explicit transport-level deadline and cancellation contract."""

    if (
        getattr(client, "context_bundle_evaluation_deadline_enforced", False) is not True
        or getattr(client, "context_bundle_evaluation_cancellation_enforced", False) is not True
        or not callable(getattr(client, "context_bundle_evaluation_control", None))
    ):
        raise RuntimeError(
            f"language-model client {type(client).__name__!r} cannot prove transport-enforced "
            "context evaluation deadline and cancellation"
        )


def require_context_bundle_hook_graph(
    roots: object,
    *,
    expected_hook_bus: HookBus | None,
) -> None:
    """Prove that every reachable model hook bus is the same empty bus.

    Client instrumentation can wrap an effective client through several layers
    and can keep alternate clients in routing containers.  Looking only at a
    top-level ``HookedLanguageModelClient`` is therefore not a security
    boundary.  Walk client/provider/runtime state and built-in containers
    without following opaque SDK objects; object and container cycles are
    intentionally tolerated and visited once.
    """

    from autocontext.extensions import HookBus as ConcreteHookBus
    from autocontext.extensions import HookedLanguageModelClient
    from autocontext.extensions.llm import HookedLLMProvider
    from autocontext.providers.base import LLMProvider
    from autocontext.runtimes.base import AgentRuntime

    graph_types = (LanguageModelClient, LLMProvider, AgentRuntime)

    def require_bus(bus: object) -> None:
        handlers = getattr(bus, "_handlers", None)
        if type(bus) is not ConcreteHookBus or not isinstance(handlers, dict):
            raise RuntimeError("context promotion cannot prove extension hook semantics")
        if bus is not expected_hook_bus:
            raise RuntimeError("context promotion client graph uses a different hook bus")
        if any(bool(registered) for registered in handlers.values()):
            raise RuntimeError(
                "context promotion with registered extension hooks is disabled until "
                "ordered hook semantics can be bound to the evaluator plan"
            )

    if expected_hook_bus is not None:
        require_bus(expected_hook_bus)

    pending = [roots]
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if node is None or isinstance(node, (str, bytes, int, float, bool)):
            continue
        node_id = id(node)
        if node_id in seen:
            continue
        seen.add(node_id)
        if len(seen) > _MAX_HOOK_GRAPH_NODES:
            raise RuntimeError("context promotion client hook graph exceeds the validation limit")

        if isinstance(node, ConcreteHookBus):
            require_bus(node)
            continue
        if isinstance(node, Mapping):
            pending.extend(node.values())
            continue
        if isinstance(node, _GRAPH_CONTAINERS):
            pending.extend(node)
            continue
        if not isinstance(node, graph_types):
            # SDK transports, callbacks, locks, and other terminal implementation
            # details cannot themselves be effective model wrappers.
            continue

        state = _hook_graph_object_state(node)
        declared_bus = state.get("hook_bus")
        if "hook_bus" in state and declared_bus is not None and not isinstance(declared_bus, ConcreteHookBus):
            raise RuntimeError("context promotion cannot prove extension hook semantics")
        if isinstance(node, (HookedLanguageModelClient, HookedLLMProvider)):
            if not isinstance(declared_bus, ConcreteHookBus):
                raise RuntimeError("context promotion cannot prove hooked model client semantics")
        for value in state.values():
            if isinstance(value, (ConcreteHookBus, Mapping, *_GRAPH_CONTAINERS, *graph_types)):
                pending.append(value)


def _hook_graph_object_state(value: object) -> dict[str, Any]:
    """Read instance fields without invoking a wrapper's ``__getattr__``."""

    try:
        state = dict(vars(value))
    except TypeError:
        state = {}
    for cls in type(value).__mro__:
        slots = cls.__dict__.get("__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        for name in slots:
            if name in {"__dict__", "__weakref__"} or name in state:
                continue
            try:
                state[name] = object.__getattribute__(value, name)
            except AttributeError:
                continue
            except Exception as exc:
                raise RuntimeError("context promotion cannot inspect client wrapper state") from exc
    return state


@contextmanager
def context_bundle_transport_control(
    client: LanguageModelClient,
    *,
    deadline: float,
    cancellation_check: Callable[[], bool],
) -> Iterator[None]:
    """Install one positively attested transport control around a model call."""

    require_context_bundle_transport_control(client)
    if cancellation_check():
        raise RuntimeError("context bundle evaluation was cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("context bundle evaluation arm deadline was exhausted")
    install = cast(
        Callable[..., AbstractContextManager[None]],
        client.context_bundle_evaluation_control,  # type: ignore[attr-defined]
    )
    guard = install(deadline=deadline, cancellation_check=cancellation_check)
    if not hasattr(guard, "__enter__") or not hasattr(guard, "__exit__"):
        raise RuntimeError("context evaluation transport control is not a context manager")
    with guard:
        yield
    if cancellation_check():
        raise RuntimeError("context bundle evaluation was cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("context bundle evaluation arm deadline was exhausted")


def _isolate_runtime_bridge(client: RuntimeBridgeClient) -> None:
    _isolate_agent_runtime(client._runtime)


def _isolate_provider(provider: object) -> None:
    reset = getattr(provider, "reset_context_for_evaluation", None)
    if callable(reset):
        if reset() is False:
            raise RuntimeError("LLM provider refused context-bundle arm isolation")
        return
    if getattr(provider, "context_bundle_evaluation_arm_isolated", False) is True:
        return
    if (
        getattr(provider, "context_bundle_evaluation_stateless", False) is True
        and getattr(provider, "context_bundle_evaluation_tool_free", False) is True
    ):
        return

    from autocontext.providers.anthropic import AnthropicProvider
    from autocontext.providers.mlx_lm_provider import MLXLMProvider
    from autocontext.providers.mlx_provider import MLXProvider
    from autocontext.providers.openai_compat import OpenAICompatibleProvider
    from autocontext.providers.retry import RetryProvider
    from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

    if isinstance(provider, RetryProvider):
        _isolate_provider(provider._provider)
        return
    if isinstance(provider, RuntimeBridgeProvider):
        _isolate_agent_runtime(provider._runtime)
        return
    if isinstance(
        provider,
        (AnthropicProvider, OpenAICompatibleProvider, MLXProvider, MLXLMProvider),
    ):
        return
    raise RuntimeError(f"LLM provider {type(provider).__name__!r} cannot prove context-bundle arm isolation")


def _isolate_agent_runtime(runtime: object) -> None:
    runtime_reset = getattr(runtime, "reset_context_for_evaluation", None)
    if callable(runtime_reset):
        if runtime_reset() is False:
            raise RuntimeError("agent runtime refused context-bundle arm isolation")
        return
    if getattr(runtime, "context_bundle_evaluation_arm_isolated", False) is True:
        return
    if (
        getattr(runtime, "context_bundle_evaluation_stateless", False) is True
        and getattr(runtime, "context_bundle_evaluation_tool_free", False) is True
    ):
        return

    from autocontext.runtimes.claude_cli import ClaudeCLIRuntime
    from autocontext.runtimes.direct_api import DirectAPIRuntime

    if isinstance(runtime, DirectAPIRuntime):
        _isolate_provider(runtime._provider)
        return
    runtime_config = getattr(runtime, "_config", None)
    if isinstance(runtime, ClaudeCLIRuntime):
        if (
            not getattr(runtime_config, "session_persistence", True)
            and not getattr(runtime_config, "session_id", None)
            and getattr(runtime_config, "tools", None) == ""
        ):
            return
        raise RuntimeError("tool-capable or persistent Claude runtime cannot prove context-bundle arm isolation")
    raise RuntimeError(f"agent runtime {type(runtime).__name__!r} cannot prove context-bundle arm isolation")


__all__ = [
    "context_bundle_transport_control",
    "isolate_context_bundle_client",
    "require_context_bundle_hook_graph",
    "require_context_bundle_transport_control",
]
