"""AC-1020: explicit applicability, verified fallback and one pure-request budget."""

from __future__ import annotations

import hashlib
import math
import threading
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from autocontext import offline
from autocontext.artifacts.policy_candidate import CandidateLimits, json_payload
from autocontext.context_bundles.models import stable_digest
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution import executable_skills, routed_model, skill_routing_models
from autocontext.execution.docker_skill import encode_skill_payload
from autocontext.execution.executable_skills import (
    CONTRACT,
    SCENARIO,
    ProfileV1,
    invoke_executable_skill,
    verify_migration_output,
)
from autocontext.execution.routed_model import ModelCallError, complete_routed_model, model_system_prompt
from autocontext.execution.skill_routing_models import ModelTarget, Route, RouteAttempt, SkillRoutingConfig, SkillRoutingResult
from autocontext.harness.cost import calculator
from autocontext.kernel_evolution import _generation_usage
from autocontext.kernel_evolution._generation_usage import validate_directional_token_aliases
from autocontext.offline import OfflineError, require_endpoint_available
from autocontext.providers import anthropic, openai_compat, scenario_routing, token_caps, usage_receipt
from autocontext.providers import base as provider_base
from autocontext.providers.base import CompletionResult
from autocontext.providers.scenario_routing import ScenarioRoutingContext, resolve_provider_for_context
from autocontext.runtimes import _workspace_process, runtime_budget
from autocontext.runtimes.runtime_budget import RuntimeBudget
from autocontext.training import model_registry
from autocontext.training.model_registry import ModelRegistry
from autocontext.util.json_io import write_json


def routing_evaluator_identity() -> str:
    """Reuse canonical digests and the bridge verifier epoch; bind all route code."""
    modules = (routed_model, skill_routing_models, scenario_routing, _workspace_process, anthropic, openai_compat,
               provider_base, token_caps, usage_receipt, model_registry, offline, _generation_usage, calculator, runtime_budget)
    packages = {}
    for package in ("openai", "anthropic"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = "unavailable"
    paths = [Path(__file__), *(Path(str(module.__file__)) for module in modules)]
    return stable_digest({"skill_verifier": executable_skills.evaluator_identity(), "packages": packages,
                          "source": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}})


def _model_receipt(completion: CompletionResult, target: ModelTarget) -> tuple[int, float, str]:
    """No missing counters, alias disagreement or reported overrun can authorize retry."""
    usage = dict(completion.raw_usage if completion.raw_usage is not None else completion.usage)
    # These OpenAI details are subsets of the directional totals, not additional
    # billed tokens. Unknown/nonzero counters (including Anthropic cache reads
    # and creation, which are additive) remain ineligible for this first route.
    for group, total_key, fields in (
        ("prompt_tokens_details", "prompt_tokens", {"audio_tokens", "cached_tokens"}),
        ("completion_tokens_details", "completion_tokens",
         {"reasoning_tokens", "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"}),
    ):
        if group not in usage:
            continue
        details = usage.pop(group)
        total = usage.get(total_key)
        if (not isinstance(details, dict) or type(total) is not int
                or any(key not in fields or type(value) is not int or not 0 <= value <= total
                       for key, value in details.items())):
            raise ValueError("unsupported_provider_usage")
    for metadata in ("service_tier", "inference_geo"):
        usage.pop(metadata, None)
    if not isinstance(usage, dict) or any(type(v) is not int or v < 0 for v in usage.values()):
        raise ValueError("invalid_provider_usage")
    validate_directional_token_aliases(usage)
    inputs = usage.get("input_tokens", usage.get("prompt_tokens"))
    outputs = usage.get("output_tokens", usage.get("completion_tokens"))
    if inputs is None or outputs is None:
        raise ValueError("missing_provider_usage")
    known = {"input_tokens", "prompt_tokens", "output_tokens", "completion_tokens", "total_tokens"}
    if any(value and key not in known for key, value in usage.items()):
        raise ValueError("unsupported_provider_usage")
    total = usage.get("total_tokens", inputs + outputs)
    if total != inputs + outputs or inputs > target.max_input_tokens or outputs > target.max_output_tokens:
        raise ValueError("provider_token_bound_violated")
    cost, source = target.cost(inputs, outputs), "declared-pricing"
    if completion.cost_usd is not None:
        value = completion.cost_usd
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("invalid_provider_cost")
        cost, source = max(cost, value), "provider-reported"
    if cost > target.cost(target.max_input_tokens, target.max_output_tokens):
        raise ValueError("provider_cost_bound_violated")
    return total, cost, source


def route_schema_migration(
    store: ContextBundleStore, registry: ModelRegistry, input_json: str, *, config: SkillRoutingConfig,
    trace_root: Path, mode: Literal["evaluation", "serving"], cancel: threading.Event | None = None,
) -> SkillRoutingResult:
    """Return a verified proposal or explicit abstention, without applying side effects.

    ``evaluation`` explicitly pins inactive skill candidates. ``serving`` uses
    the existing durable active-bundle gate. AC-1021 still owns promotion of the
    complete route configuration; this API is not wired to production routing.
    """
    if mode not in {"evaluation", "serving"}:
        raise ValueError("explicit evaluation or serving mode required")
    started = time.monotonic()
    wall = RuntimeBudget(config.budget.wall_seconds, started)
    cancel = cancel if cancel is not None else threading.Event()
    identity = routing_evaluator_identity()
    request_id = uuid.uuid4().hex
    path = (trace_root / "execution-routing" / f"{request_id}.json").resolve()
    attempts: list[dict[str, Any]] = []
    raw_input: str | None = None
    input_digest: str | None = None

    def stop_reason() -> str | None:
        if cancel.is_set():
            return "cancelled"
        return "request_timeout" if wall.expired() else None

    def persist(status: Literal["started", "success", "abstention", "execution_failure"], reason: str,
                output: str | None = None, route: Route | None = None) -> SkillRoutingResult:
        stopped = stop_reason()
        if status != "started" and stopped:
            status, reason, output, route = "execution_failure", stopped, None, None
        rows = tuple(RouteAttempt.model_validate(row) for row in attempts)
        result = SkillRoutingResult(
            request_id=request_id, status=status, reason=reason, selected_route=route, output_json=output,
            input_json=raw_input, input_sha256=input_digest, config_json=json_payload(config), config_digest=config.digest,
            evaluator_identity=identity, mode=mode, attempts=rows, elapsed_seconds=time.monotonic() - started,
            model_calls=sum(r.model_calls for r in rows), accounted_tokens=sum(r.accounted_tokens for r in rows),
            accounted_model_cost_usd=sum(r.accounted_cost_usd for r in rows),
            model_cost_complete=all(r.accounting in {"none", "provider-reported"} for r in rows), trace_path=str(path),
        )
        write_json(path, result.model_dump(mode="json"))
        # A cancellation/deadline during the final durable write cannot leak output.
        if status == "success" and (stopped := stop_reason()):
            result = result.model_copy(update={"status": "execution_failure", "reason": stopped,
                                               "selected_route": None, "output_json": None})
            write_json(path, result.model_dump(mode="json"))
        return result

    def skip(route: Route, reason: str, target: ModelTarget | None = None) -> None:
        attempts.append({"route": route, "status": "skipped", "reason": reason,
                         "model": target.model if target else None, "target_digest": target.digest if target else None})

    def exhausted() -> str | None:
        if stopped := stop_reason():
            return stopped
        if sum(row["status"] != "skipped" for row in attempts) >= config.budget.max_attempts:
            return "attempt_budget_exhausted"
        return None

    if not config.enabled:
        return persist("abstention", "routing_disabled")
    if stopped := stop_reason():
        return persist("execution_failure", stopped)
    try:
        encoded = encode_skill_payload(input_json)
    except UnicodeEncodeError:
        return persist("abstention", "invalid_input")
    if encoded is None:
        return persist("abstention", "input_limit")
    raw_input, input_digest = input_json, hashlib.sha256(encoded).hexdigest()
    profile: ProfileV1 | None = None
    applicability = "applicable"
    try:
        profile = ProfileV1.model_validate(executable_skills._json(input_json))
        json_payload(profile)  # Reject escaped lone surrogates before any dispatch.
        if profile.schema_version != 1:
            profile, applicability = None, "unsupported_schema_version"
    except (ValueError, TypeError, RecursionError):
        profile, applicability = None, "invalid_input"
    persist("started", applicability)

    if config.override is not None:
        skip("skill", "explicit_model_override")
    elif config.bundle_digest is None:
        skip("skill", "missing_skill_artifact")
    elif profile is None:
        skip("skill", applicability)
    else:
        if stopped := exhausted():
            return persist("execution_failure", stopped)
        row: dict[str, Any] = {"route": "skill", "status": "started", "reason": "applicable"}
        attempts.append(row)
        persist("started", "skill_dispatch")
        if stopped := stop_reason():
            row.update(status="execution_failure", reason=stopped)
            return persist("execution_failure", stopped)
        call_start = time.monotonic()
        # A candidate's immutable limits must fit the remaining request budget;
        # never rewrite its manifest or reset the wall clock on fallback.
        limits = CandidateLimits(timeout_seconds=max(0.001, min(30, wall.remaining())), max_memory_mb=256)
        result = invoke_executable_skill(store, config.bundle_digest, input_json, mode=mode, caller_limits=limits,
                                         cancel=cancel, request_budget=wall)
        row.update(status=result.status, reason=result.reason, artifact_digest=result.artifact_digest,
                   environment_digest=result.environment_digest, elapsed_seconds=time.monotonic() - call_start)
        if result.status == "success":
            return persist("success", "verified_proposal", result.output_json, "skill")
        if result.reason in {"cleanup_unverified", "cancelled"}:
            return persist("execution_failure", result.reason)

    targets: list[tuple[Route, ModelTarget | None]] = (
        [("override", config.override)] if config.override is not None
        else [("specialized", config.specialized), ("general", config.general)]
    )
    seen: set[tuple[str, str, str]] = set()
    for route, target in targets:
        if stopped := stop_reason():
            return persist("execution_failure", stopped)
        if target is None:
            skip(route, "optional_tier_unconfigured")
            continue
        endpoint_key = (target.provider, target.model, target.resolved_endpoint)
        if endpoint_key in seen:
            skip(route, "duplicate_model_route", target)
            continue
        record_id: str | None = None
        record_digest: str | None = None
        try:
            backend = (config.specialized_backend or target.provider) if route == "specialized" else target.provider
            context = ScenarioRoutingContext(scenario=SCENARIO, backend=backend, runtime_type="provider",
                                             manual_model_override=target.model if route == "override" else "")
            if route in {"specialized", "override"}:
                resolved = resolve_provider_for_context(context, registry)
                if resolved.provider_type != backend or (route == "override" and resolved.model != target.model):
                    skip(route, "missing_or_incompatible_registered_model", target)
                    continue
                if route == "specialized":
                    if resolved.source != "registry":
                        skip(route, "missing_or_incompatible_registered_model", target)
                        continue
                    if profile is None:
                        skip(route, applicability, target)
                        continue
                    record_id = resolved.artifact_id
                    record = registry.load(record_id) if record_id is not None else None
                    if (record is None or record.activation_state != "active" or record.scenario != SCENARIO
                            or record.backend != backend or "provider" not in record.runtime_types):
                        skip(route, "missing_or_incompatible_registered_model", target)
                        continue
                    if record.metadata.get("skill_routing") != {
                        "contract": CONTRACT, "target_digest": target.digest, "evaluator_identity": identity,
                    }:
                        skip(route, "stale_model_contract", target)
                        continue
                    record_digest = stable_digest(record.model_dump(mode="json"))
            if not config.allow_network:
                skip(route, "network_not_granted", target)
                continue
            require_endpoint_available("invoke a routed model", target.resolved_endpoint)
        except OfflineError:
            skip(route, "offline_endpoint_unavailable", target)
            continue
        except (OSError, ValueError, TypeError):
            skip(route, "invalid_model_registry", target)
            continue
        # Byte-per-token plus a declared envelope allowance is deliberately
        # conservative. Operators must configure a valid bound for their backend.
        if len(model_system_prompt(config.learned_playbook).encode()) + len(encoded) + 2048 > target.max_input_tokens:
            skip(route, "model_input_bound_exceeded", target)
            continue
        if stopped := exhausted():
            return persist("execution_failure", stopped)
        reserve_tokens = target.max_input_tokens + target.max_output_tokens
        reserve_cost = target.cost(target.max_input_tokens, target.max_output_tokens)
        if sum(r.get("accounted_tokens", 0) for r in attempts) + reserve_tokens > config.budget.max_tokens:
            skip(route, "token_budget_exhausted", target)
            continue
        if sum(r.get("accounted_cost_usd", 0) for r in attempts) + reserve_cost > config.budget.max_model_cost_usd:
            skip(route, "cost_budget_exhausted", target)
            continue
        row = {"route": route, "status": "started", "reason": applicability, "model": target.model,
               "target_digest": target.digest, "artifact_digest": record_digest, "registered_artifact_id": record_id,
               "environment_digest": identity,
               "model_calls": 1, "reserved_tokens": reserve_tokens, "reserved_cost_usd": reserve_cost,
               "accounted_tokens": reserve_tokens, "accounted_cost_usd": reserve_cost, "accounting": "reservation"}
        attempts.append(row)
        persist("started", "model_dispatch")
        if stopped := stop_reason():
            row.update(status="skipped", reason=stopped, model_calls=0, accounted_tokens=0, accounted_cost_usd=0,
                       reserved_tokens=0, reserved_cost_usd=0, accounting="none")
            return persist("execution_failure", stopped)
        call_start = time.monotonic()
        seen.add(endpoint_key)
        try:
            completion = complete_routed_model(target, input_json, timeout_seconds=min(target.timeout_seconds, wall.remaining()),
                                               cancel=cancel, request_budget=wall, learned_playbook=config.learned_playbook)
        except (ModelCallError, OSError, OfflineError) as exc:
            row.update(status="execution_failure", reason=str(exc) if isinstance(exc, ModelCallError) else "provider_unavailable",
                       elapsed_seconds=time.monotonic() - call_start)
            if row["reason"] in {"model_cleanup_unverified", "invalid_provider_receipt", "cancelled"}:
                return persist("execution_failure", row["reason"])
            continue
        row["elapsed_seconds"] = time.monotonic() - call_start
        row["reported_model"] = completion.served_model if isinstance(completion.served_model, str) else None
        if completion.model != target.model or completion.served_model != target.model:
            row.update(status="verification_failure", reason="reported_model_mismatch")
            return persist("execution_failure", "reported_model_mismatch")
        try:
            tokens, cost, accounting = _model_receipt(completion, target)
        except (ValueError, TypeError, AttributeError):
            row.update(status="execution_failure", reason="provider_accounting_unverified")
            return persist("execution_failure", "provider_accounting_unverified")
        row.update(reported_tokens=tokens, accounted_tokens=tokens, accounted_cost_usd=cost,
                   reported_cost_usd=completion.cost_usd, accounting=accounting)
        if stopped := stop_reason():
            row.update(status="execution_failure", reason=stopped)
            return persist("execution_failure", stopped)
        try:
            if record_id is not None:
                current = registry.load(record_id)
                if current is None or stable_digest(current.model_dump(mode="json")) != record_digest:
                    raise ValueError("model_registry_changed")
            if completion.stop_reason in {"max_tokens", "length", "error", "aborted"}:
                raise ValueError("incomplete_model_output")
            if not isinstance(completion.text, str):
                raise ValueError("invalid_output")
            if encode_skill_payload(completion.text) is None:
                raise ValueError("model_output_limit")
            response = executable_skills._json(completion.text)
            if isinstance(response, dict) and set(response) == {"abstain"} and response["abstain"] is True:
                row.update(status="abstention", reason="model_abstained")
                continue
            if profile is None:
                raise ValueError("unsupported_input_proposal")
            output_json = verify_migration_output(profile, completion.text)
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            reason = str(exc)
            allowed = {"model_registry_changed", "incomplete_model_output", "model_output_limit", "unsupported_input_proposal",
                       "invalid_output", "migration_postcondition_failed"}
            row.update(status="verification_failure", reason=reason if reason in allowed else "invalid_output")
            continue
        row.update(status="success", reason="verified_proposal")
        return persist("success", "verified_proposal", output_json, route)
    return persist("abstention", "no_verified_route")
