"""Production evaluator for immutable context-bundle matched trials."""

from __future__ import annotations

import copy
import json
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from autocontext.agents.context_evaluation_isolation import (
    context_bundle_transport_control,
    isolate_context_bundle_client,
    require_context_bundle_hook_graph,
    require_context_bundle_transport_control,
)
from autocontext.agents.orchestrator_helpers import _run_competitor_phase, _run_translator_phase
from autocontext.context_bundles.assembly import (
    bundle_mutations,
    bundle_routing_config,
    bundle_text,
    bundle_tool_context,
)
from autocontext.context_bundles.evaluator_plan import (
    evaluator_settings_identity,
    scenario_instance_identity,
    source_identity,
)
from autocontext.context_bundles.models import ComponentKind, ContextBundle, stable_digest
from autocontext.context_bundles.promotion import (
    ContextBundleEvaluationOutcome,
    ContextBundleEvaluationUnit,
)
from autocontext.harness.core.llm_client import LanguageModelClient
from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.types import EvaluationLimits
from autocontext.loop.stage_helpers.harness_mutations import (
    apply_harness_mutations_to_prompts,
    render_context_policy_block,
    render_tool_instruction_block,
)
from autocontext.prompts.templates import build_prompt_bundle

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.context_bundles.store import ContextBundleStore
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.extensions import HookBus
    from autocontext.scenarios.base import Observation, ScenarioInterface


@dataclass(frozen=True, slots=True)
class RuntimeContextFixture:
    """Canonical materialization of one seeded scenario evaluation fixture."""

    state: Mapping[str, Any]
    observation: Observation
    digest: str


def runtime_fixture_digest(
    state: Mapping[str, Any],
    observation: Observation | Mapping[str, Any],
) -> str:
    """Hash only scenario-visible fixture content, never seed or lane labels."""

    model_dump = getattr(observation, "model_dump", None)
    if callable(model_dump):
        observation_payload = model_dump(mode="json")
    elif isinstance(observation, Mapping):
        observation_payload = dict(observation)
    else:
        raise TypeError("runtime fixture observation must be an Observation or mapping")
    return stable_digest(
        {
            "initial_state": dict(state),
            "observation": observation_payload,
        }
    )


def materialize_runtime_fixture(
    scenario: ScenarioInterface,
    seed: int,
) -> RuntimeContextFixture:
    """Create and canonically identify the real scenario fixture for ``seed``."""

    state = scenario.initial_state(seed=seed)
    observation = scenario.get_observation(state, player_id="challenger")
    return RuntimeContextFixture(
        state=state,
        observation=observation,
        digest=runtime_fixture_digest(state, observation),
    )


@dataclass(slots=True)
class RuntimeContextBundleEvaluator:
    """Generate and score a strategy from one explicitly supplied bundle.

    The evaluator never reads or writes ``active.json``. Candidate and incumbent
    arms therefore exercise their own immutable prompt/routing manifests while
    sharing the exact scenario seed and real execution supervisor.
    """

    scenario_name: str
    scenario: ScenarioInterface
    orchestrator: AgentOrchestrator
    supervisor: ExecutionSupervisor
    limits: EvaluationLimits
    hook_bus: HookBus | None = None
    generation_index: int = 0
    scenario_factory: Callable[[], ScenarioInterface] | None = None
    store: ContextBundleStore | None = None
    expected_evaluator_epoch: str | None = None
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def evaluate(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
    ) -> ContextBundleEvaluationOutcome:
        with self._lock:
            return self._evaluate_isolated(bundle, unit)

    def evaluate_with_control(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
        *,
        deadline: float | None,
        cancellation_check: Callable[[], bool] | None,
    ) -> ContextBundleEvaluationOutcome:
        with self._lock:
            return self._evaluate_isolated(
                bundle,
                unit,
                deadline=deadline,
                cancellation_check=cancellation_check,
            )

    def _evaluate_isolated(
        self,
        bundle: ContextBundle,
        unit: ContextBundleEvaluationUnit,
        *,
        deadline: float | None = None,
        cancellation_check: Callable[[], bool] | None = None,
    ) -> ContextBundleEvaluationOutcome:
        arm_deadline = time.monotonic() + self.limits.timeout_seconds
        if deadline is not None:
            arm_deadline = min(arm_deadline, deadline)
        _check_evaluation_control(arm_deadline, cancellation_check)
        _validate_hook_bus_composition(self.hook_bus, self.orchestrator)
        _require_context_executor_control(self.supervisor.executor)
        if bundle.scenario != self.scenario_name:
            raise ValueError("context bundle evaluator scenario does not match the supplied manifest")
        if self.expected_evaluator_epoch is not None and bundle.evaluator_epoch != self.expected_evaluator_epoch:
            raise RuntimeError("context bundle evaluator epoch changed after plan construction")
        _reject_executable_bundle_harness(bundle)
        prompt_scenario = self._fresh_scenario()
        execution_scenario = self._fresh_scenario()
        if execution_scenario is prompt_scenario:
            raise RuntimeError("context-bundle evaluation reused its prompt scenario for execution")
        fixture = materialize_runtime_fixture(prompt_scenario, unit.seed)
        if fixture.digest != unit.fixture_digest:
            raise RuntimeError("runtime scenario fixture does not match the predeclared fixture digest")
        observation = fixture.observation
        mutations = bundle_mutations(bundle)
        tool_context = bundle_tool_context(bundle)
        tool_instructions = render_tool_instruction_block(mutations)
        if tool_instructions:
            tool_context = f"{tool_context}\n\n{tool_instructions}".strip()
        context_policy = render_context_policy_block(mutations)
        prompts = build_prompt_bundle(
            scenario_rules=prompt_scenario.describe_rules(),
            strategy_interface=prompt_scenario.describe_strategy_interface(),
            evaluation_criteria=prompt_scenario.describe_evaluation_criteria(),
            # Evaluation identities remain in durable matched evidence only.
            # Revealing a held-out seed or fixture digest to the strategy model
            # would let it specialize to the supposedly hidden unit.
            previous_summary="isolated immutable context-bundle evaluation",
            observation=observation,
            current_playbook=bundle_text(bundle, ComponentKind.PLAYBOOK, "playbook"),
            available_tools=tool_context,
            coach_competitor_hints=bundle_text(bundle, ComponentKind.HINTS, "hints"),
            experiment_log=context_policy,
            semantic_compaction=False,
        )
        prompts = apply_harness_mutations_to_prompts(prompts, mutations)

        original_settings = self.orchestrator.settings
        # RLM competitors build their own store-backed context instead of
        # consuming this immutable bundle prompt. Matched bundle evaluation
        # must take the direct role path or it would silently score whichever
        # bundle active.json currently serves for both arms.
        evaluation_settings = original_settings.model_copy(update={"rlm_enabled": False})
        routing = bundle_routing_config(bundle)
        effective_settings = self.orchestrator.apply_active_context_routing(
            evaluation_settings,
            routing,
        )
        try:
            cancellation_probe = cancellation_check or (lambda: False)
            for role in ("competitor", "translator"):
                client, _ = self.orchestrator.resolve_role_execution(
                    role,
                    generation=self.generation_index,
                    scenario_name=self.scenario_name,
                    generation_deadline=arm_deadline,
                )
                try:
                    require_context_bundle_hook_graph(client, expected_hook_bus=self.hook_bus)
                    isolate_context_bundle_client(client, role=role)
                    require_context_bundle_transport_control(client)
                finally:
                    self.orchestrator._close_disposable_client(client)  # noqa: SLF001

            def _guard_role_client(client: object, *, role: str) -> AbstractContextManager[None]:
                if not isinstance(client, LanguageModelClient):
                    raise RuntimeError("context evaluation role did not resolve a language-model client")
                require_context_bundle_hook_graph(client, expected_hook_bus=self.hook_bus)
                isolate_context_bundle_client(client, role=role)
                return context_bundle_transport_control(
                    client,
                    deadline=arm_deadline,
                    cancellation_check=cancellation_probe,
                )

            def _ignore_role_event(_role: str, _status: str) -> None:
                return None

            raw_text, _ = _run_competitor_phase(
                self.orchestrator,
                prompts,
                self.generation_index,
                tool_context,
                f"context-eval-{bundle.digest[:12]}",
                self.scenario_name,
                prompt_scenario.describe_strategy_interface(),
                prompt_scenario.describe_rules(),
                None,
                arm_deadline,
                _ignore_role_event,
                role_client_guard=lambda client: _guard_role_client(client, role="competitor"),
            )
            _check_evaluation_control(arm_deadline, cancellation_check)
            strategy, _ = _run_translator_phase(
                self.orchestrator,
                raw_text,
                prompt_scenario.describe_strategy_interface(),
                self.generation_index,
                self.scenario_name,
                arm_deadline,
                _ignore_role_event,
                role_client_guard=lambda client: _guard_role_client(client, role="translator"),
            )
            _check_evaluation_control(arm_deadline, cancellation_check)
            if effective_settings.two_tier_gating_enabled:
                from autocontext.execution.harness_loader import HarnessLoader
                from autocontext.harness.pipeline.validity_gate import ValidityGate

                harness_loader = None
                if effective_settings.harness_validators_enabled:
                    if self.store is None:
                        raise RuntimeError("bundle-specific harness validation requires a context bundle store")
                    harness_dir = self.store.runtime_harness_dir(bundle.scenario, bundle.digest)
                    if harness_dir is not None:
                        harness_loader = HarnessLoader(
                            harness_dir,
                            timeout_seconds=effective_settings.harness_timeout_seconds,
                        )
                        harness_loader.load()
                validity = ValidityGate(
                    harness_loader,
                    execution_scenario,
                    max_retries=0,
                ).check(strategy, state=dict(fixture.state))
                if not validity.passed:
                    return ContextBundleEvaluationOutcome(score=0.0, valid=False)
            evaluator = ScenarioEvaluator(
                execution_scenario,
                self.supervisor,
                hook_bus=self.hook_bus,
            )
            remaining_seconds = arm_deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise TimeoutError("context bundle evaluation arm deadline was exhausted")
            execution_limits = EvaluationLimits(
                timeout_seconds=min(self.limits.timeout_seconds, remaining_seconds),
                max_memory_mb=self.limits.max_memory_mb,
                network_access=self.limits.network_access,
            )
            executor_guard = _context_executor_control(
                self.supervisor.executor,
                deadline=arm_deadline,
                cancellation_check=cancellation_probe,
            )
            with executor_guard:
                result = evaluator.evaluate(
                    strategy,
                    unit.seed,
                    execution_limits,
                    fixture_state=fixture.state,
                    fixture_observation=fixture.observation,
                    fixture_digest=fixture.digest,
                )
            _check_evaluation_control(arm_deadline, cancellation_check)
            return ContextBundleEvaluationOutcome(score=result.score, valid=result.passed)
        finally:
            # Restore generation serving routes after each arm, including when
            # generation, translation, or scenario execution raises.
            self.orchestrator.apply_active_context_routing(original_settings, {})

    def evaluation_plan_identity(self, bundle: ContextBundle) -> Mapping[str, Any]:
        """Describe all bundle-arm inputs which can change score or validity."""

        _validate_hook_bus_composition(self.hook_bus, self.orchestrator)
        _reject_executable_bundle_harness(bundle)

        from autocontext.agents.context_routing_activation import (
            resolve_active_context_settings,
        )
        from autocontext.providers.registry import resolve_auto_judge_provider

        original_settings = self.orchestrator.settings
        baseline = original_settings.model_copy(update={"rlm_enabled": False})
        effective = resolve_active_context_settings(
            baseline,
            self.orchestrator.settings,
            bundle_routing_config(bundle),
        )
        epoch = self.expected_evaluator_epoch or bundle.evaluator_epoch
        if bundle.evaluator_epoch != epoch:
            raise RuntimeError("bundle no longer matches the runtime evaluator epoch")
        resolved_routes: dict[str, Any] = {}
        self.orchestrator.apply_active_context_routing(baseline, bundle_routing_config(bundle))
        try:
            for role in ("competitor", "translator"):
                config = self.orchestrator._resolve_role_provider_config(  # noqa: SLF001
                    role,
                    generation=self.generation_index,
                    scenario_name=self.scenario_name,
                ) or self.orchestrator._role_router.route(role)  # noqa: SLF001
                client, model = self.orchestrator.resolve_role_execution(
                    role,
                    generation=self.generation_index,
                    scenario_name=self.scenario_name,
                )
                try:
                    require_context_bundle_hook_graph(client, expected_hook_bus=self.hook_bus)
                    resolved_routes[role] = {
                        "provider": config.provider_type,
                        "provider_class": config.provider_class.value,
                        "model": model or config.model,
                        "client": source_identity(type(client)),
                        "endpoint_digest": stable_digest(
                            {
                                "base_url": (
                                    effective.competitor_base_url
                                    if role == "competitor" and effective.competitor_base_url
                                    else effective.agent_base_url
                                )
                            }
                        ),
                    }
                finally:
                    self.orchestrator._close_disposable_client(client)  # noqa: SLF001
        finally:
            self.orchestrator.apply_active_context_routing(original_settings, {})
        executor = self.supervisor.executor
        executor_config: dict[str, Any] = {}
        for name in (
            "max_retries",
            "backoff_seconds",
            "allow_fallback",
            "_max_execution_time_seconds",
            "_max_external_calls",
        ):
            value = getattr(executor, name, None)
            if isinstance(value, (bool, int, float, str)):
                executor_config[name] = value
        executor_client = getattr(executor, "client", None)
        image = getattr(executor_client, "docker_image", None)
        if isinstance(image, str):
            executor_config["image"] = image
        harness_components = [
            {
                "key": component.key,
                "digest": component.digest,
                "media_type": component.media_type,
            }
            for component in bundle.components_of_kind(ComponentKind.HARNESS_VALIDATOR)
        ]
        runtime_validators: dict[str, str] = {}
        if self.store is not None:
            harness_dir = self.store.runtime_harness_dir(bundle.scenario, bundle.digest)
            if harness_dir is not None:
                runtime_validators = {
                    path.name: stable_digest({"source": path.read_text(encoding="utf-8")})
                    for path in sorted(harness_dir.glob("*.py"))
                }
        return {
            "bundle_digest": bundle.digest,
            "evaluator_epoch": epoch,
            "generation_index": self.generation_index,
            "scenario_instance": scenario_instance_identity(self.scenario),
            "scenario_factory": (
                source_identity(self.scenario_factory) if self.scenario_factory is not None else {"mode": "deepcopy"}
            ),
            "settings_identity": evaluator_settings_identity(effective),
            "role_generation": {
                "code_strategies_enabled": effective.code_strategies_enabled,
                "competitor": {
                    "runner": source_identity(type(self.orchestrator.competitor)),
                    "model": self.orchestrator.competitor.model,
                    "max_tokens": self.orchestrator.competitor.max_tokens,
                    "constrained_output": self.orchestrator.competitor.runtime.constrained_output,
                },
                "translator": {
                    "runner": source_identity(type(self.orchestrator.translator)),
                    "model": self.orchestrator.translator.model,
                    "max_tokens": self.orchestrator.translator.max_tokens,
                    "constrained_output": self.orchestrator.translator.runtime.constrained_output,
                },
            },
            "routes": {
                "agent_provider": effective.agent_provider,
                "competitor": resolved_routes["competitor"],
                "translator": resolved_routes["translator"],
                "judge_provider": (
                    resolve_auto_judge_provider(effective) if effective.judge_provider == "auto" else effective.judge_provider
                ),
                "judge_model": effective.judge_model,
                "judge_endpoint_digest": stable_digest({"base_url": effective.judge_base_url}),
            },
            "executor": {
                "implementation": source_identity(type(executor)),
                "mode": effective.executor_mode,
                "configuration": executor_config,
            },
            "limits": {
                "timeout_seconds": self.limits.timeout_seconds,
                "max_memory_mb": self.limits.max_memory_mb,
                "network_access": self.limits.network_access,
            },
            "validity": {
                "two_tier_gating_enabled": effective.two_tier_gating_enabled,
                "harness_validators_enabled": effective.harness_validators_enabled,
                "harness_timeout_seconds": effective.harness_timeout_seconds,
                "validity_max_retries": effective.validity_max_retries,
                "prevalidation_enabled": effective.prevalidation_enabled,
                "prevalidation_max_retries": effective.prevalidation_max_retries,
                "harness_components": harness_components,
                "runtime_validators": runtime_validators,
            },
            "hook_bus": source_identity(type(self.hook_bus)) if self.hook_bus is not None else None,
        }

    def _fresh_scenario(self) -> ScenarioInterface:
        """Create an arm-local scenario or fail before evaluating either arm."""

        try:
            scenario = self.scenario_factory() if self.scenario_factory is not None else copy.deepcopy(self.scenario)
        except Exception as exc:
            raise RuntimeError("context-bundle evaluation requires an arm-local scenario instance") from exc
        if scenario is self.scenario:
            raise RuntimeError("context-bundle scenario factory reused the shared scenario instance")
        if not isinstance(scenario, type(self.scenario)):
            raise RuntimeError("context-bundle scenario factory returned an incompatible scenario type")
        if scenario_instance_identity(scenario) != scenario_instance_identity(self.scenario):
            raise RuntimeError("context-bundle scenario factory changed the bound scenario instance state")
        return scenario


def build_runtime_context_bundle_evaluator(
    *,
    scenario_name: str,
    scenario: ScenarioInterface,
    orchestrator: AgentOrchestrator,
    supervisor: ExecutionSupervisor,
    limits: EvaluationLimits | None = None,
    hook_bus: HookBus | None = None,
    generation_index: int = 0,
    scenario_factory: Callable[[], ScenarioInterface] | None = None,
    store: ContextBundleStore | None = None,
    expected_evaluator_epoch: str | None = None,
) -> RuntimeContextBundleEvaluator:
    """Build the concrete evaluator used by live promotion wiring."""

    _require_context_executor_control(supervisor.executor)
    return RuntimeContextBundleEvaluator(
        scenario_name=scenario_name,
        scenario=scenario,
        orchestrator=orchestrator,
        supervisor=supervisor,
        limits=limits or EvaluationLimits(),
        hook_bus=hook_bus,
        generation_index=generation_index,
        scenario_factory=scenario_factory,
        store=store,
        expected_evaluator_epoch=expected_evaluator_epoch,
    )


def _check_evaluation_control(
    deadline: float,
    cancellation_check: Callable[[], bool] | None,
) -> None:
    if cancellation_check is not None and cancellation_check():
        raise RuntimeError("context bundle evaluation was cancelled")
    if time.monotonic() >= deadline:
        raise TimeoutError("context bundle evaluation arm deadline was exhausted")


def _reject_executable_bundle_harness(bundle: ContextBundle) -> None:
    for component in bundle.components_of_kind(ComponentKind.HARNESS_VALIDATOR):
        executable = component.media_type == "text/x-python" and bool(component.content.strip())
        if component.media_type == "application/json":
            payload = json.loads(component.content)
            executable = isinstance(payload, dict) and bool(str(payload.get("code", "")).strip())
        if executable:
            raise RuntimeError(
                "context promotion with executable bundle validators is disabled until "
                "serving-equivalent revision semantics are available"
            )


def _validate_hook_bus_composition(
    hook_bus: HookBus | None,
    orchestrator: AgentOrchestrator,
) -> None:
    orchestrator_bus = getattr(orchestrator, "hook_bus", None)
    if hook_bus is not orchestrator_bus:
        raise RuntimeError("context promotion evaluator and orchestrator hook buses do not match")
    clients: list[object] = [
        getattr(orchestrator, "client", None),
    ]
    for name in ("_role_clients", "_routed_clients"):
        container = getattr(orchestrator, name, None)
        if container is not None:
            if not isinstance(container, Mapping):
                raise RuntimeError("context promotion cannot prove orchestrator client graph semantics")
            clients.append(container)
    for name in ("competitor", "translator"):
        runner = getattr(orchestrator, name, None)
        runtime = getattr(runner, "runtime", None)
        clients.append(getattr(runtime, "client", None))
    require_context_bundle_hook_graph(clients, expected_hook_bus=hook_bus)


def _require_context_executor_control(executor: object) -> None:
    if (
        getattr(executor, "context_bundle_evaluation_deadline_enforced", False) is not True
        or getattr(executor, "context_bundle_evaluation_cancellation_enforced", False) is not True
        or not callable(getattr(executor, "context_bundle_evaluation_control", None))
    ):
        raise RuntimeError(
            f"scenario executor {type(executor).__name__!r} cannot prove killable context evaluation deadline and cancellation"
        )


def _context_executor_control(
    executor: object,
    *,
    deadline: float,
    cancellation_check: Callable[[], bool],
) -> AbstractContextManager[None]:
    _require_context_executor_control(executor)
    install = executor.context_bundle_evaluation_control  # type: ignore[attr-defined]
    guard = install(deadline=deadline, cancellation_check=cancellation_check)
    if not hasattr(guard, "__enter__") or not hasattr(guard, "__exit__"):
        raise RuntimeError("context evaluation executor control is not a context manager")
    return cast(AbstractContextManager[None], guard)


__all__ = [
    "RuntimeContextBundleEvaluator",
    "RuntimeContextFixture",
    "build_runtime_context_bundle_evaluator",
    "materialize_runtime_fixture",
    "runtime_fixture_digest",
]
