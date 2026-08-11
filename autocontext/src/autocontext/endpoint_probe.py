"""Probe a configured LLM endpoint before a run starts (AC-914).

The point is to fail on a misconfiguration at setup time rather than partway
through a generation, when the loop has already spent tokens and the operator
has to read a traceback to learn that a base URL was wrong.

What is probeable, and what is not, was determined by asking a real endpoint
rather than by reading the OpenAI specification:

* **reachability** and **which models are served** come from ``GET /v1/models``,
  which every OpenAI-compatible server implements.
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
from dataclasses import dataclass
from typing import Any

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


def _get_json(url: str, api_key: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:  # noqa: S310 - operator-configured endpoint
        return json.loads(response.read())


def probe_reachable(base_url: str, api_key: str) -> ProbeResult:
    """Is anything answering at the configured base URL?"""
    try:
        _get_json(f"{base_url.rstrip('/')}/models", api_key)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return ProbeResult(
            name="endpoint_reachable",
            passed=False,
            certain=True,
            detail=f"{base_url} did not answer: {exc}",
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
    choices = payload.get("choices") or []
    if choices and isinstance(choices[0], dict):
        text = (choices[0].get("message") or {}).get("content") or ""
    try:
        json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return ProbeResult(
            name="structured_output",
            passed=False,
            certain=False,
            detail="endpoint accepted the schema but did not return JSON; treating as unsupported",
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
    return [reachable, probe_model_served(base_url, api_key, model), probe_structured_output(base_url, api_key, model)]


# Transports that speak the OpenAI-compatible HTTP surface these probes use.
# Everything else (CLI runtimes, mlx, anthropic) is skipped rather than guessed
# at: a probe that cannot apply must not report a failure.
_PROBEABLE = {"openai", "openai-compatible", "ollama", "vllm", "openrouter"}

# Mirrors providers/registry.create_provider's defaults. Duplicating them would
# recreate the AC-933 defect, so this is asserted equal to the registry in
# tests/test_endpoint_probe.py rather than trusted to stay in step.
_DEFAULT_BASE_URL = {
    "ollama": "http://localhost:11434/v1",
    "vllm": "http://localhost:8000/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "openai-compatible": "https://api.openai.com/v1",
}


def resolve_agent_endpoint(settings: Any) -> tuple[str, str, str] | None:
    """The (base_url, api_key, model) a run would actually use, or None.

    Returns None when the configured transport is not HTTP-probeable, so the
    caller reports "not applicable" rather than inventing a result.
    """
    from autocontext.providers.registry import transport_env_api_key

    provider = (settings.agent_provider or "").strip().lower()
    if provider not in _PROBEABLE:
        return None

    base_url = settings.agent_base_url or _DEFAULT_BASE_URL[provider]
    api_key = settings.agent_api_key or transport_env_api_key(provider, settings) or "no-key"
    model = (settings.local_model or "").strip() or settings.agent_default_model
    return base_url, api_key, model
