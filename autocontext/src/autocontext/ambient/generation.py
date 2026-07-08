"""production candidate-generation seam for the ambient evaluate stage (AC-891).

The evaluate stage scores a candidate on its held-out suite. In placeholder mode it judges the
suite's reference text; in real-generation mode it judges the candidate model's own output for each
case. This module builds the production closure that does the real generation: it serves the
candidate's trained model (via the same serving resolver the agent runtime uses) and generates.

The client is cached PER RECORD (keyed by artifact_id) inside the closure so a candidate's model is
loaded once and reused across all its eval cases, rather than reloaded on every case. A record that
has no local client plan is not servable, so the closure raises a clear error; the runtime (mlx /
torch) being absent surfaces from the client build and propagates, which the evaluate stage's
per-candidate try/except records as evaluate_candidate_failed (cannot serve -> not evaluated ->
not promotable), the correct behavior.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from autocontext.agents.scenario_bound_clients import build_planned_client, plan_local_client

if TYPE_CHECKING:
    from autocontext.agents.llm_client import LanguageModelClient
    from autocontext.ambient.charter import CharterAnchor
    from autocontext.config import AppSettings
    from autocontext.training.model_registry import DistilledModelRecord


def generation_config_id(settings: AppSettings) -> str:
    """A stable identity for the generation config the closure below scores under.

    Real generation output depends on the sampling settings the closure passes to the served client
    (max tokens, temperature), so this id is folded into the eval fingerprint: changing a generation
    setting changes the id, which re-triggers evaluation rather than leaving candidates skipped on a
    stale score. Keep this in sync with the settings that build_candidate_generation_fn actually uses.
    """
    return hashlib.sha256(f"mt={settings.mlx_max_tokens}|t={settings.mlx_temperature}".encode()).hexdigest()[:16]


def build_candidate_generation_fn(
    settings: AppSettings,
) -> Callable[[DistilledModelRecord, CharterAnchor, str], str]:
    """Build the evaluate stage's real candidate-generation closure.

    The returned closure serves the candidate's model and generates for one eval case. It holds a
    SINGLE-SLOT client cache: only the most-recently-served candidate's client is kept resident, so
    at most one model is loaded at a time. The evaluate stage scores all of a candidate's cases
    before moving to the next, so this reuses the client across a candidate's cases without pinning
    every candidate's model for the daemon's lifetime; when the next candidate is served the previous
    client is dropped (a promoted or disabled candidate's model does not leak). An unservable record
    (no local client plan) raises; a runtime-absent client build propagates for the stage to record
    as a failure.
    """
    # single slot: {} or {artifact_id: (served client, model id to pass to generate)}. The plan's
    # model is the checkpoint path (full checkpoint) or the base model id (adapter), cached alongside
    # the client rather than recomputed per call.
    slot: dict[str, tuple[LanguageModelClient, str]] = {}

    def generate(record: DistilledModelRecord, anchor: CharterAnchor, prompt: str) -> str:
        del anchor  # the anchor is the frozen judge, not the served candidate; unused here
        cached = slot.get(record.artifact_id)
        if cached is None:
            plan = plan_local_client(record)
            if plan is None:
                raise RuntimeError(f"record {record.artifact_id} is not servable (no local client plan)")
            client = build_planned_client(plan, settings)
            # drop the previous candidate's client before caching the new one, so at most one served
            # model is resident at a time (bounded eviction for the resident daemon).
            slot.clear()
            slot[record.artifact_id] = (client, plan.model)
            cached = slot[record.artifact_id]
        client, model = cached
        resp = client.generate(
            model=model,
            prompt=prompt,
            max_tokens=settings.mlx_max_tokens,
            temperature=settings.mlx_temperature,
            role="ambient-evaluate",
        )
        return resp.text

    return generate
