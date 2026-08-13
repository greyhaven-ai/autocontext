"""Probe a configured LLM endpoint before a run starts (AC-914).

The point is to fail on a misconfiguration at setup time rather than partway
through a generation, when the loop has already spent tokens and the operator
has to read a traceback to learn that a base URL was wrong.

What is probeable, and what is not, was determined by asking a real endpoint
rather than by reading the OpenAI specification:

* **reachability** and **which models are served** come from ``GET /v1/models``.
  Servers that omit or restrict that discovery route produce an advisory rather
  than evidence that their completion route is broken.
* **structured-output support** is established by attempting one tiny
  constrained completion. There is no capability field to read; the only honest
  test is to try it.
* **context window** is NOT probeable here. Ollama's ``/v1/models`` returns
  only ``{created, id, object, owned_by}`` -- no length field, and the OpenAI
  surface has nowhere to put one. Ollama's native ``/api/show`` does carry
  ``llama.context_length``, but that is transport-specific and would make this
  module know about particular servers. AC-914 asked for it; it is reported as
  unknown rather than guessed, and the reason is here so nobody re-derives it.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from autocontext.offline import require_endpoint_available

# Long enough to survive a cold local model load, short enough that a wrong
# base URL fails at setup instead of looking like a hang.
_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """What one probe established, and whether it is a blocking answer.

    ``certain`` separates "this run cannot succeed" from "this could not be
    determined". A probe that could not answer is not evidence of a problem,
    and treating it as one would make preflight the thing that breaks runs.
    """

    name: str
    passed: bool
    certain: bool
    detail: str


@dataclass(frozen=True, slots=True)
class EndpointTarget:
    """One distinct OpenAI-compatible endpoint/model combination used by a run."""

    name: str
    provider: str
    base_url: str
    api_key: str = field(repr=False)
    model: str


def _get_json(url: str, api_key: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    require_endpoint_available("probe an endpoint", url)
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 - operator-configured endpoint
        return json.loads(response.read())


def probe_reachable(base_url: str, api_key: str) -> ProbeResult:
    """Is anything answering at the configured base URL?"""
    try:
        _get_json(f"{base_url.rstrip('/')}/models", api_key)
    except urllib.error.HTTPError as exc:
        # A response proves the host is reachable. Only an authentication
        # rejection applies uniformly to the completion surface; rate limits,
        # server failures, and a restricted/absent model-list route do not.
        certain = exc.code == 401
        status_detail = "credentials were rejected" if certain else "cannot determine completion availability"
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=certain,
            detail=f"{base_url} returned HTTP {exc.code} from /models; {status_detail}",
        )
    except TimeoutError as exc:
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=False,
            detail=f"{base_url} timed out; cannot determine availability: {exc}",
        )
    except urllib.error.URLError as exc:
        refused = isinstance(exc.reason, ConnectionRefusedError)
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=refused,
            detail=(
                f"{base_url} refused the connection: {exc}"
                if refused
                else f"{base_url} could not be reached; cannot determine availability: {exc}"
            ),
        )
    except OSError as exc:
        refused = isinstance(exc, ConnectionRefusedError)
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=refused,
            detail=(
                f"{base_url} refused the connection: {exc}"
                if refused
                else f"{base_url} could not be reached; cannot determine availability: {exc}"
            ),
        )
    except (json.JSONDecodeError, ValueError) as exc:
        # Something answered but is not an OpenAI-compatible API -- a proxy
        # login page, say. Reachable, but not usable, and worth saying so
        # precisely because "connection refused" and "wrong service" have
        # different fixes.
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=True,
            detail=f"{base_url} answered but not with JSON ({exc}); is it an OpenAI-compatible endpoint?",
        )
    return ProbeResult(name="endpoint_reachable", passed=True, certain=True, detail=f"{base_url} answered")


def probe_model_served(base_url: str, api_key: str, model: str) -> ProbeResult:
    """Is the configured model actually served here?"""
    try:
        payload = _get_json(f"{base_url.rstrip('/')}/models", api_key)
    except Exception as exc:  # noqa: BLE001 - reachability reports the cause; this is the dependent check
        return ProbeResult(
            name="model_served",
            passed=False,
            certain=False,
            detail=f"could not list models ({exc})",
        )
    if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
        return ProbeResult(
            name="model_served",
            passed=False,
            certain=False,
            detail="endpoint returned an unexpected model-list shape; cannot confirm",
        )
    served = [str(entry["id"]) for entry in payload.get("data", []) if isinstance(entry, dict) and entry.get("id")]
    if not served:
        return ProbeResult(
            name="model_served",
            passed=False,
            certain=False,
            detail="endpoint listed no models; cannot confirm",
        )
    if model in served:
        return ProbeResult(name="model_served", passed=True, certain=True, detail=f"{model} is served")
    return ProbeResult(
        name="model_served",
        passed=False,
        certain=True,
        detail=f"{model} is not served. Available: {', '.join(sorted(served)[:8])}",
    )


def probe_structured_output(base_url: str, api_key: str, model: str) -> ProbeResult:
    """Does this endpoint honor response_format json_schema?

    Sends the smallest possible constrained request. There is no capability
    field to consult, so attempting it is the only way to know -- and knowing
    matters because role output silently falls back to markdown scraping when
    it is unsupported, which is where format drift costs whole sections.
    """
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    try:
        payload = _get_json(
            f"{base_url.rstrip('/')}/chat/completions",
            api_key,
            {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with {\"ok\": true}"}],
                "max_tokens": 32,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "probe", "strict": True, "schema": schema},
                },
            },
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "do not rely on it"
        return ProbeResult(
            name="structured_output",
            passed=False,
            certain=False,
            detail=f"endpoint rejected a constrained request ({exc}); role output will fall back to markdown",
        )
    text = ""
    choices = payload.get("choices") or [] if isinstance(payload, dict) else []
    if choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict):
            text = message.get("content") or ""
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ProbeResult(
            name="structured_output",
            passed=False,
            certain=False,
            detail="endpoint accepted the schema but did not return JSON; treating as unsupported",
        )
    if not isinstance(decoded, dict) or set(decoded) != {"ok"} or decoded.get("ok") is not True:
        return ProbeResult(
            name="structured_output",
            passed=False,
            certain=False,
            detail="endpoint returned JSON that did not match the requested schema; treating as unsupported",
        )
    return ProbeResult(name="structured_output", passed=True, certain=True, detail="response_format honored")


def probe_endpoint(base_url: str, api_key: str, model: str) -> list[ProbeResult]:
    """Run the probes in dependency order, stopping when the endpoint is dead.

    An unreachable endpoint makes every later probe fail for the same reason,
    and a wall of consequential failures buries the one that matters.
    """
    reachable = probe_reachable(base_url, api_key)
    if not reachable.passed:
        return [reachable]
    model_result = probe_model_served(base_url, api_key, model)
    if not model_result.passed and model_result.certain:
        return [reachable, model_result]
    return [reachable, model_result, probe_structured_output(base_url, api_key, model)]


# Transports that speak the OpenAI-compatible HTTP surface these probes use.
# Everything else (CLI runtimes, mlx, anthropic) is skipped rather than guessed
# at: a probe that cannot apply must not report a failure.
_PROBEABLE = {"openai", "openai-compatible", "ollama", "vllm", "openrouter"}


def resolve_agent_endpoint(settings: Any) -> tuple[str, str, str] | None:
    """The (base_url, api_key, model) a run would actually use, or None.

    Returns None when the configured transport is not HTTP-probeable, so the
    caller reports "not applicable" rather than inventing a result.
    """
    target = _resolve_agent_target(settings)
    if target is None:
        return None
    return target.base_url, target.api_key, target.model


def _request_api_key(provider: str, resolved: str | None) -> str:
    """Mirror the harmless sentinels used by OpenAI-compatible clients."""
    if resolved:
        return resolved
    return "ollama" if provider == "ollama" else "no-key"


def _endpoint_target(
    *,
    name: str,
    provider: str,
    base_url: str | None,
    api_key: str | None,
    model: str | None,
) -> EndpointTarget | None:
    from autocontext.providers.registry import resolve_provider_base_url

    normalized = provider.strip().lower()
    if normalized not in _PROBEABLE:
        return None
    resolved_url = resolve_provider_base_url(normalized, base_url)
    if resolved_url is None or not model:
        return None
    return EndpointTarget(
        name=name,
        provider=normalized,
        base_url=resolved_url,
        api_key=_request_api_key(normalized, api_key),
        model=model,
    )


def _resolve_agent_target(settings: Any) -> EndpointTarget | None:
    from autocontext.agents.provider_bridge import _provider_api_key, _provider_base_url, _provider_model

    provider = (settings.agent_provider or "").strip().lower()
    return _endpoint_target(
        name="agent",
        provider=provider,
        base_url=_provider_base_url(settings),
        api_key=_provider_api_key(provider, settings),
        model=_provider_model(provider, settings),
    )


def resolve_run_endpoints(settings: Any) -> list[EndpointTarget]:
    """Resolve every distinct probeable endpoint/model a configured run uses."""
    from autocontext.agents.provider_bridge import (
        _provider_api_key,
        _provider_base_url,
        _provider_model,
        configured_role_provider,
        has_role_client_override,
    )
    from autocontext.agents.role_router import RoleRouter, RoutingContext
    from autocontext.providers.registry import resolve_auto_judge_provider, transport_env_api_key

    candidates: list[EndpointTarget | None] = [_resolve_agent_target(settings)]
    roles = ("competitor", "analyst", "coach", "architect")

    # Dedicated role clients are constructed even when automatic role routing
    # is disabled, so they are independently capable of failing a run.
    for role in roles:
        if not has_role_client_override(role, settings):
            continue
        provider = configured_role_provider(role, settings) or settings.agent_provider.strip().lower()
        candidates.append(
            _endpoint_target(
                name=role,
                provider=provider,
                base_url=_provider_base_url(settings, role=role),
                api_key=_provider_api_key(provider, settings, role=role),
                model=_provider_model(provider, settings),
            )
        )

    # Automatic routing can select role/tier models that differ from the base
    # client. Probe the initial routing decision for every executing role.
    if settings.role_routing == "auto":
        router = RoleRouter(settings)
        for role in roles:
            config = router.route(role, context=RoutingContext())
            candidates.append(
                _endpoint_target(
                    name=f"{role}-routed",
                    provider=config.provider_type,
                    base_url=_provider_base_url(settings, role=role),
                    api_key=_provider_api_key(config.provider_type, settings, role=role),
                    model=config.model,
                )
            )

    judge_provider = settings.judge_provider.strip().lower()
    if judge_provider == "auto":
        judge_provider = resolve_auto_judge_provider(settings)
    candidates.append(
        _endpoint_target(
            name="judge",
            provider=judge_provider,
            base_url=settings.judge_base_url,
            api_key=settings.judge_api_key or transport_env_api_key(judge_provider, settings),
            model=settings.judge_model,
        )
    )

    # The structured-output check spends a small completion. Avoid repeating it
    # when several roles share the exact same credentials, endpoint, and model.
    distinct: list[EndpointTarget] = []
    seen: set[tuple[str, str, str]] = set()
    for target in candidates:
        if target is None:
            continue
        identity = (target.base_url.rstrip("/"), target.api_key, target.model)
        if identity in seen:
            continue
        seen.add(identity)
        distinct.append(target)
    return distinct
