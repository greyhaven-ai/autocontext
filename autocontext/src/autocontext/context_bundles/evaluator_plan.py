"""Immutable evaluator-plan identities for context-bundle comparisons."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.context_bundles.models import (
    ConfirmationPolicy,
    ContextBundle,
    stable_digest,
)
from autocontext.util.json_io import read_json, write_json

if TYPE_CHECKING:
    from autocontext.context_bundles.promotion import (
        ContextBundleEvaluationUnit,
        ContextBundleEvaluator,
    )
    from autocontext.context_bundles.store import ContextBundleStore

EVALUATOR_PLAN_SCHEMA_VERSION = 1
_SECRET_SETTING_SUFFIXES = ("_api_key", "_password", "_secret")
_NON_EVALUATOR_SETTING_FIELDS = frozenset(
    {
        "audit_log_path",
        "claude_skills_path",
        "db_path",
        "event_stream_path",
        "knowledge_root",
        "runs_root",
        "skills_root",
    }
)


def source_identity(value: object) -> dict[str, str]:
    """Return a restart-stable identity for executable evaluation semantics."""

    module = inspect.getmodule(value)
    module_name = str(getattr(value, "__module__", getattr(module, "__name__", "")))
    qualname = str(getattr(value, "__qualname__", type(value).__qualname__))
    source_target = value if inspect.isclass(value) or inspect.isfunction(value) else type(value)
    try:
        source = inspect.getsource(source_target)
    except (OSError, TypeError):
        source_path = inspect.getsourcefile(source_target)
        if source_path is None:
            raise RuntimeError(f"evaluator semantic source is unavailable for {module_name}.{qualname}") from None
        source = Path(source_path).read_text(encoding="utf-8")
    return {
        "module": module_name,
        "qualname": qualname,
        "module_version": str(getattr(module, "__version__", "")),
        "source_digest": stable_digest({"source": source}),
    }


def complete_evaluator_epoch_payload(scenario: object, settings: Any) -> dict[str, Any]:
    """Capture every settings/code surface shared by all bundle arms."""

    from autocontext.agents.context_evaluation_isolation import (
        context_bundle_transport_control,
        require_context_bundle_transport_control,
    )
    from autocontext.agents.context_routing_activation import resolve_active_context_settings
    from autocontext.agents.orchestrator_helpers import (
        _run_competitor_phase,
        _run_translator_phase,
    )
    from autocontext.context_bundles.assembly import (
        bundle_mutations,
        bundle_text,
        bundle_tool_context,
    )
    from autocontext.context_bundles.comparison import evaluate_matched_trials
    from autocontext.context_bundles.evaluation_control import ContextEvaluationControl
    from autocontext.context_bundles.runtime_evaluator import (
        RuntimeContextBundleEvaluator,
    )
    from autocontext.execution.harness_loader import HarnessLoader
    from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
    from autocontext.harness.pipeline.validity_gate import ValidityGate
    from autocontext.loop.stage_helpers.harness_mutations import (
        apply_harness_mutations_to_prompts,
        render_context_policy_block,
        render_tool_instruction_block,
    )
    from autocontext.prompts.templates import build_prompt_bundle
    from autocontext.providers.registry import resolve_auto_judge_provider

    judge_provider = str(getattr(settings, "judge_provider", "auto"))
    if judge_provider == "auto":
        judge_provider = resolve_auto_judge_provider(settings)
    setting_names = (
        "agent_provider",
        "role_routing",
        "provider_capability",
        "provider_hosting",
        "competitor_provider",
        "competitor_provider_capability",
        "competitor_provider_hosting",
        "model_competitor",
        "model_translator",
        "tier_routing_enabled",
        "tier_haiku_model",
        "tier_sonnet_model",
        "tier_opus_model",
        "tier_competitor_haiku_max_gen",
        "tier_harness_aware_enabled",
        "tier_harness_coverage_demotion_threshold",
        "judge_model",
        "executor_mode",
        "primeintellect_docker_image",
        "primeintellect_cpu_cores",
        "primeintellect_memory_gb",
        "primeintellect_disk_size_gb",
        "primeintellect_accelerator_kind",
        "primeintellect_accelerator_count",
        "primeintellect_region",
        "primeintellect_required_telemetry",
        "context_bundle_promotion_eval_timeout_seconds",
        "context_bundle_promotion_eval_max_memory_mb",
        "harness_validators_enabled",
        "harness_timeout_seconds",
        "prevalidation_enabled",
        "prevalidation_max_retries",
        "prevalidation_dry_run_enabled",
        "two_tier_gating_enabled",
        "validity_max_retries",
    )
    routes = {name: _canonical_value(getattr(settings, name)) for name in setting_names if hasattr(settings, name)}
    routes["judge_provider_resolved"] = judge_provider
    scenario_identity, scenario_descriptions, scenario_is_test_double = _scenario_epoch_inputs(scenario)
    semantics = (
        build_prompt_bundle,
        bundle_text,
        bundle_tool_context,
        bundle_mutations,
        apply_harness_mutations_to_prompts,
        render_context_policy_block,
        render_tool_instruction_block,
        resolve_active_context_settings,
        _run_competitor_phase,
        _run_translator_phase,
        RuntimeContextBundleEvaluator._evaluate_isolated,
        RuntimeContextBundleEvaluator.evaluate_with_control,
        ContextEvaluationControl.evaluate_arm,
        context_bundle_transport_control,
        require_context_bundle_transport_control,
        ScenarioEvaluator.evaluate,
        ValidityGate.check,
        HarnessLoader.validate_strategy,
        evaluate_matched_trials,
    )
    return {
        "schema_version": EVALUATOR_PLAN_SCHEMA_VERSION,
        "scenario": scenario_identity,
        "scenario_is_test_double": scenario_is_test_double,
        "scenario_descriptions": scenario_descriptions,
        "scenario_instance": scenario_instance_identity(scenario),
        "settings_identity": evaluator_settings_identity(settings),
        "routes_and_limits": routes,
        "evaluation_semantics": [source_identity(item) for item in semantics],
    }


def complete_evaluator_epoch(scenario: object, settings: Any) -> str:
    return stable_digest(complete_evaluator_epoch_payload(scenario, settings))


def evaluator_settings_identity(settings: Any) -> dict[str, Any]:
    """Fingerprint settings fail-safe while keeping credentials out of evidence."""

    model_dump = getattr(settings, "model_dump", None)
    if not callable(model_dump):
        raise TypeError("evaluator settings must expose a canonical model_dump")
    raw = model_dump(mode="json")
    if not isinstance(raw, dict):
        raise TypeError("evaluator settings model_dump must return a mapping")
    sanitized = {
        str(name): _canonical_value(value)
        for name, value in raw.items()
        if name not in _NON_EVALUATOR_SETTING_FIELDS
        and not str(name).endswith(_SECRET_SETTING_SUFFIXES)
        and "webhook" not in str(name)
    }
    return {
        "implementation": source_identity(type(settings)),
        # New settings participate automatically. This deliberately prefers a
        # harmless epoch rollover over silently reusing evidence under a new
        # score, prompt, request, validator, or executor knob.
        "values_digest": stable_digest(sanitized),
    }


def scenario_instance_identity(scenario: object) -> dict[str, Any]:
    """Fingerprint exactly the instance configuration executors reconstruct."""

    from unittest.mock import Mock

    if isinstance(scenario, Mock):
        return {"test_double": True, "state_digest": stable_digest({})}
    state: dict[str, Any] = {}
    instance_dict = getattr(scenario, "__dict__", None)
    if isinstance(instance_dict, dict):
        state.update(instance_dict)
    for scenario_type in reversed(type(scenario).__mro__):
        declared_slots = scenario_type.__dict__.get("__slots__", ())
        slots = (declared_slots,) if isinstance(declared_slots, str) else declared_slots
        for name in slots:
            if name in {"__dict__", "__weakref__"} or name in state:
                continue
            try:
                state[name] = getattr(scenario, name)
            except AttributeError:
                continue
    canonical: dict[str, Any] = {}
    for name, value in state.items():
        normalized_name = str(name).lower()
        if any(marker in normalized_name for marker in ("api_key", "auth_token", "credential", "password", "secret")):
            raise ValueError(f"scenario instance state contains sensitive field {name!r}")
        canonical[str(name)] = _canonical_value(value)
    return {
        "state_keys": sorted(canonical),
        "state_digest": stable_digest(canonical),
    }


def build_evaluator_plan(
    evaluator: ContextBundleEvaluator,
    candidate: ContextBundle,
    incumbent: ContextBundle,
    units: Sequence[ContextBundleEvaluationUnit],
    *,
    cohort: str,
    policy: ConfirmationPolicy,
) -> dict[str, Any]:
    identity = getattr(evaluator, "evaluation_plan_identity", None)
    if callable(identity):
        candidate_identity = _mapping(identity(candidate), "candidate evaluator identity")
        incumbent_identity = _mapping(identity(incumbent), "incumbent evaluator identity")
    else:
        evaluator_source = source_identity(type(evaluator))
        candidate_identity = {"evaluator": evaluator_source, "bundle_digest": candidate.digest}
        incumbent_identity = {"evaluator": evaluator_source, "bundle_digest": incumbent.digest}
    return {
        "schema_version": EVALUATOR_PLAN_SCHEMA_VERSION,
        "scenario": candidate.scenario,
        "evaluator_epoch": candidate.evaluator_epoch,
        "candidate_digest": candidate.digest,
        "incumbent_digest": incumbent.digest,
        "cohort": cohort,
        "confirmation_policy": policy.to_dict(),
        "confirmation_policy_digest": stable_digest(policy.to_dict()),
        "units": [
            {
                "fixture": unit.fixture,
                "fixture_digest": unit.fixture_digest,
                "seed": unit.seed,
                "lane": unit.lane.value,
            }
            for unit in units
        ],
        "candidate_evaluator": candidate_identity,
        "incumbent_evaluator": incumbent_identity,
    }


def bind_evaluator_plan(
    store: ContextBundleStore,
    scenario: str,
    candidate_digest: str,
    plan: Mapping[str, Any],
) -> str:
    """Persist a plan once and reject every later semantic change."""

    payload = dict(plan)
    plan_digest = stable_digest(payload)
    artifact = {"plan": payload, "plan_digest": plan_digest}
    path = _plan_path(store, scenario, candidate_digest)
    with store._lock(scenario):  # noqa: SLF001 - candidate transaction boundary
        if path.exists():
            existing = _read_plan(path)
            if existing != artifact:
                raise ValueError("context bundle evaluator plan changed after evaluation began")
        else:
            write_json(path, artifact)
    return plan_digest


def require_evaluator_plan(
    store: ContextBundleStore,
    scenario: str,
    candidate_digest: str,
    expected_digest: str,
) -> dict[str, Any]:
    """Verify the exact persisted plan immediately before an evidence action."""

    if len(expected_digest) != 64 or any(character not in "0123456789abcdef" for character in expected_digest):
        raise ValueError("context bundle evaluator plan digest must be sha256 hex")
    artifact = _read_plan(_plan_path(store, scenario, candidate_digest))
    if artifact["plan_digest"] != expected_digest:
        raise ValueError("context bundle evaluator plan digest mismatch")
    return dict(artifact["plan"])


def require_live_evaluator_plan(
    store: ContextBundleStore,
    scenario: str,
    candidate: ContextBundle,
    incumbent: ContextBundle,
    expected_digest: str,
    evaluator: ContextBundleEvaluator,
) -> None:
    """Recompute mutable arm identities before every call/evidence boundary."""

    plan = require_evaluator_plan(store, scenario, candidate.digest, expected_digest)
    identity = getattr(evaluator, "evaluation_plan_identity", None)
    if callable(identity):
        current_candidate = _mapping(identity(candidate), "candidate evaluator identity")
        current_incumbent = _mapping(identity(incumbent), "incumbent evaluator identity")
    else:
        evaluator_source = source_identity(type(evaluator))
        current_candidate = {"evaluator": evaluator_source, "bundle_digest": candidate.digest}
        current_incumbent = {"evaluator": evaluator_source, "bundle_digest": incumbent.digest}
    if current_candidate != plan.get("candidate_evaluator") or current_incumbent != plan.get("incumbent_evaluator"):
        raise ValueError("context bundle evaluator identity changed after evaluation began")


def require_bound_evaluator_plan(
    store: ContextBundleStore,
    scenario: str,
    candidate_digest: str,
    expected_digest: str | None,
) -> None:
    """Require a caller digest whenever a candidate already has a bound plan."""

    path = _plan_path(store, scenario, candidate_digest)
    if not path.exists():
        if expected_digest is not None:
            raise ValueError("context bundle evaluator plan artifact is missing")
        return
    if expected_digest is None:
        raise ValueError("context bundle evidence action omitted its bound evaluator plan")
    require_evaluator_plan(store, scenario, candidate_digest, expected_digest)


def evaluator_plan_binding(
    store: ContextBundleStore,
    scenario: str,
    candidate_digest: str,
) -> tuple[str, str]:
    from autocontext.storage.scenario_paths import normalize_scenario_name_segment

    artifact = _read_plan(_plan_path(store, scenario, candidate_digest))
    relative = f"{normalize_scenario_name_segment(scenario)}/context_bundles/candidates/{candidate_digest}/evaluator_plan.json"
    return relative, str(artifact["plan_digest"])


def _read_plan(path: Path) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict) or not isinstance(data.get("plan"), dict):
        raise ValueError("context bundle evaluator plan artifact is malformed")
    digest = data.get("plan_digest")
    if not isinstance(digest, str) or stable_digest(data["plan"]) != digest:
        raise ValueError("context bundle evaluator plan artifact digest mismatch")
    return {"plan": dict(data["plan"]), "plan_digest": digest}


def _plan_path(store: ContextBundleStore, scenario: str, digest: str) -> Path:
    return store._candidate_dir(scenario, digest) / "evaluator_plan.json"  # noqa: SLF001


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    payload = {str(key): _canonical_value(item) for key, item in value.items()}
    stable_digest(payload)
    return payload


def _canonical_value(value: object) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise TypeError(f"evaluator plan value {type(value).__name__!r} is not canonical")


def _string_result(value: object, method_name: str) -> str:
    method = getattr(value, method_name, None)
    result = method() if callable(method) else ""
    if not isinstance(result, str):
        raise TypeError(f"scenario {method_name}() must return a string")
    return result


def _scenario_epoch_inputs(scenario: object) -> tuple[list[dict[str, str]], dict[str, str], bool]:
    """Bind real scenarios strictly while giving orchestration test doubles a stable contract."""

    from unittest.mock import Mock

    method_names = (
        "describe_rules",
        "describe_strategy_interface",
        "describe_evaluation_criteria",
    )
    if isinstance(scenario, Mock):
        from autocontext.scenarios.base import ScenarioInterface

        descriptions: dict[str, str] = {}
        for key, method_name in zip(("rules", "strategy_interface", "evaluation_criteria"), method_names, strict=True):
            method = getattr(scenario, method_name, None)
            result = method() if callable(method) else ""
            descriptions[key] = result if isinstance(result, str) else ""
        return [source_identity(ScenarioInterface)], descriptions, True
    descriptions = {
        key: _string_result(scenario, method_name)
        for key, method_name in zip(("rules", "strategy_interface", "evaluation_criteria"), method_names, strict=True)
    }
    identity = [source_identity(scenario_type) for scenario_type in type(scenario).__mro__ if scenario_type is not object]
    return identity, descriptions, False


__all__ = [
    "bind_evaluator_plan",
    "build_evaluator_plan",
    "complete_evaluator_epoch",
    "complete_evaluator_epoch_payload",
    "evaluator_settings_identity",
    "evaluator_plan_binding",
    "require_bound_evaluator_plan",
    "require_evaluator_plan",
    "require_live_evaluator_plan",
    "scenario_instance_identity",
    "source_identity",
]
