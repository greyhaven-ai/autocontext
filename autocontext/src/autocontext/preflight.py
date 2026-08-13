"""Pre-run preflight checks.

Inspired by Plankton's prereqs.py with 11 static + 4 live checks that
validate the environment before any work begins.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.config.settings import load_settings
from autocontext.offline import check_offline_configuration
from autocontext.scenarios import resolve_scenario_class


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of a single preflight check."""

    name: str
    passed: bool
    detail: str
    # AC-914: whether a failure is certain enough to stop the run. Static
    # checks are; a probe that could not reach a conclusion is not.
    blocking: bool = True


class PreflightChecker:
    """Validates the runtime environment before a generation run."""

    def __init__(
        self,
        scenario: str,
        knowledge_root: Path | None = None,
        db_path: Path | None = None,
        settings: Any | None = None,
    ) -> None:
        self._scenario = scenario
        self._knowledge_root = knowledge_root or Path("knowledge")
        self._db_path = db_path
        # AC-914: optional so every existing construction keeps working.
        # Without it the endpoint checks are skipped rather than guessed at.
        self._settings = settings

    def check_scenario_exists(self) -> CheckResult:
        """Check if the scenario is registered."""
        try:
            exists = resolve_scenario_class(self._scenario, self._knowledge_root) is not None
        except Exception as exc:  # noqa: BLE001 - a scenario that cannot load cannot run
            return CheckResult(
                name="scenario_exists",
                passed=False,
                detail=f"Scenario '{self._scenario}' could not be loaded: {exc}",
            )
        return CheckResult(
            name="scenario_exists",
            passed=exists,
            detail=f"Scenario '{self._scenario}' {'found' if exists else 'not found'} in registry",
        )

    def check_knowledge_writable(self) -> CheckResult:
        """Check if the knowledge directory is writable."""
        test_file = None
        try:
            self._knowledge_root.mkdir(parents=True, exist_ok=True)
            test_file = self._knowledge_root / ".preflight_test"
            test_file.write_text("test")
            return CheckResult(name="knowledge_writable", passed=True, detail="Knowledge dir writable")
        except OSError as e:
            return CheckResult(name="knowledge_writable", passed=False, detail=str(e))
        finally:
            if test_file is not None:
                test_file.unlink(missing_ok=True)

    def check_endpoint(self) -> list[CheckResult]:
        """Probe the distinct LLM endpoints and models a run would use (AC-914).

        Returns [] when no settings were supplied or the transport is not
        HTTP-probeable (CLI runtimes, mlx, anthropic). An absent check is
        honest; a fabricated pass is not.
        """
        if self._settings is None:
            return []
        from autocontext.endpoint_probe import probe_endpoint, resolve_run_endpoints

        try:
            targets = resolve_run_endpoints(self._settings)
        except Exception as exc:  # noqa: BLE001 - endpoint discovery is advisory when indeterminate
            return [
                CheckResult(
                    name="endpoint_probe",
                    passed=False,
                    detail=f"could not resolve endpoint checks ({exc})",
                    blocking=False,
                )
            ]

        results: list[CheckResult] = []
        for target in targets:
            try:
                probes = probe_endpoint(target.base_url, target.api_key, target.model)
            except Exception as exc:  # noqa: BLE001 - malformed probe responses must not erase static failures
                results.append(
                    CheckResult(
                        name=f"{target.name}.endpoint_probe",
                        passed=False,
                        detail=f"endpoint checks errored ({exc}); continuing",
                        blocking=False,
                    )
                )
                continue
            results.extend(
                CheckResult(
                    name=f"{target.name}.{probe.name}",
                    passed=probe.passed,
                    detail=probe.detail,
                    blocking=probe.certain,
                )
                for probe in probes
            )
        return results

    def check_budget_gates_can_fire(self) -> list[CheckResult]:
        """Warn when a configured dollar gate cannot receive non-zero spend.

        The two generation gates and the consultation gate use different
        accumulators. Generation prices ``RoleUsage`` by model; consultation
        sums provider-reported ``CompletionResult.cost_usd``. Reachability must
        therefore be evaluated independently rather than inferred from one
        provider or one routing estimate.
        """
        if self._settings is None:
            return []

        generation_budgets = {
            "AUTOCONTEXT_COST_BUDGET_LIMIT": float(getattr(self._settings, "cost_budget_limit", 0.0) or 0.0),
            "AUTOCONTEXT_COST_THROTTLE_ABOVE_TOTAL": float(
                getattr(self._settings, "cost_throttle_above_total", 0.0) or 0.0
            ),
        }
        configured_generation = sorted(name for name, value in generation_budgets.items() if value > 0)
        consultation_budget = float(getattr(self._settings, "consultation_cost_budget", 0.0) or 0.0)
        results: list[CheckResult] = []

        if configured_generation:
            reason = self._generation_budget_inert_reason()
            if reason is not None:
                time_budget = int(getattr(self._settings, "generation_time_budget_seconds", 0) or 0)
                if time_budget <= 0:
                    hint = (
                        "AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS can stop later stages after elapsed "
                        "wall clock, but it does not cancel an in-flight provider call"
                    )
                else:
                    hint = (
                        f"a time budget is already set ({time_budget}s); it limits later stages after calls "
                        "return, but it does not cancel an in-flight provider call"
                    )
                results.append(
                    CheckResult(
                        name="generation_budget_gate_inert",
                        passed=False,
                        detail=(
                            f"{', '.join(configured_generation)} is set, but {reason}, so its accumulated "
                            f"spend stays at zero and the budget cannot be reached. {hint}."
                        ),
                        blocking=False,
                    )
                )

        if consultation_budget > 0:
            provider = str(getattr(self._settings, "consultation_provider", "") or "configured provider")
            if not bool(getattr(self._settings, "consultation_enabled", False)):
                reason = "consultation is disabled"
            else:
                # ConsultationRunner persists CompletionResult.cost_usd exactly
                # as returned. None of the providers constructible by the
                # consultation path currently populate that field.
                reason = f"the {provider} consultation path does not report CompletionResult.cost_usd"
            results.append(
                CheckResult(
                    name="consultation_budget_gate_inert",
                    passed=False,
                    detail=(
                        "AUTOCONTEXT_CONSULTATION_COST_BUDGET is set, but "
                        f"{reason}, so new consultations add no recorded spend and the budget cannot be reached."
                    ),
                    blocking=False,
                )
            )

        return results

    def _generation_budget_inert_reason(self) -> str | None:
        """Explain why generation's model-priced accumulator cannot advance."""
        assert self._settings is not None
        if not bool(getattr(self._settings, "cost_tracking_enabled", False)):
            return "generation cost tracking is disabled"
        if getattr(self._settings, "exploration_mode", "linear") == "tree":
            return "tree exploration bypasses generation cost accounting"

        from autocontext.agents.role_router import (
            DEFAULT_ROUTING_TABLE,
            RoleRouter,
            RoutingContext,
            available_local_models,
        )

        context = RoutingContext(
            available_local_models=available_local_models(
                self._settings,
                scenario_name=self._scenario,
            ),
            scenario_name=self._scenario,
        )
        router = RoleRouter(self._settings)
        # Generation gates price RoleUsage via CostCalculator. MLX completions
        # currently expose no token usage, so their calculated contribution is
        # zero. Other routes can contribute through model-based estimation even
        # when their endpoint is locally hosted (for example Ollama or vLLM).
        if all(router.route(role, context=context).provider_type == "mlx" for role in DEFAULT_ROUTING_TABLE):
            return "every generation role uses MLX, whose completions expose no token usage to the cost tracker"
        return None

    def run_without_scenario_check(self) -> list[CheckResult]:
        """Everything except the registry lookup.

        Agent-task scenarios are resolved outside SCENARIO_REGISTRY, so that
        check would reject them; the endpoint checks apply just the same.
        """
        return [
            self.check_knowledge_writable(),
            *self.check_endpoint(),
            *self.check_budget_gates_can_fire(),
        ]

    def run_all(self) -> list[CheckResult]:
        """Run all preflight checks."""
        return [
            self.check_scenario_exists(),
            self.check_knowledge_writable(),
            *self.check_endpoint(),
            *self.check_budget_gates_can_fire(),
        ]

    @staticmethod
    def blocking_failures(results: Sequence[CheckResult]) -> list[CheckResult]:
        """Failures certain enough to stop a run before it spends tokens.

        A probe that could not determine an answer is not evidence of a
        problem. Treating "unknown" as "broken" would make preflight the thing
        that breaks runs, which is a worse failure than the one it prevents.
        """
        return [r for r in results if not r.passed and r.blocking]

    @staticmethod
    def to_markdown(results: Sequence[CheckResult]) -> str:
        """Format check results as a markdown table."""
        lines = ["## Preflight Checks", ""]
        lines.append("| Check | Status | Detail |")
        lines.append("|-------|--------|--------|")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"| {r.name} | {status} | {r.detail} |")
        return "\n".join(lines)


class PreflightBlocked(Exception):
    """Raised when a preflight check is certain the run cannot succeed.

    Carries the failures so the caller can present them; preflight decides,
    the CLI formats. Keeping typer and rich out of this module is what lets
    the server and MCP paths reuse it later.
    """

    def __init__(self, failures: Sequence[CheckResult]) -> None:
        self.failures = list(failures)
        super().__init__("; ".join(f"{f.name}: {f.detail}" for f in self.failures))


def run_preflight(
    scenario: str,
    preset: str | None,
    *,
    check_scenario: bool = True,
) -> list[CheckResult]:
    """Check the environment before a run spends anything (AC-914).

    Returns every result so the caller can report advisories. Raises
    PreflightBlocked only for failures the checks are CERTAIN about -- an
    unreachable endpoint, or a model the server does not serve. Anything that
    could not be determined is returned as a failed-but-non-blocking result: a
    probe that cannot answer is not evidence of a problem, and preflight must
    not become the reason a working setup stops running.

    Until now PreflightChecker existed but was never constructed anywhere, so
    no run has ever been checked. That is the gap this closes; the endpoint
    probes are what make it worth calling.
    """
    # Mirrors cli._apply_preset_env rather than importing it: the cli imports
    # this module, so reaching back would be circular.
    if preset is not None:
        os.environ["AUTOCONTEXT_PRESET"] = preset
    settings = load_settings()

    # AC-917: offline mode and engine-initiated egress are incompatible by
    # design, not by precedence. Reported as blocking CHECK RESULTS rather than
    # raised directly, so an operator sees every conflict at once instead of
    # fixing them one run at a time.
    conflicts = [
        CheckResult(name="offline_configuration", passed=False, detail=conflict, blocking=True)
        for conflict in check_offline_configuration(settings)
    ]

    checker = PreflightChecker(scenario, knowledge_root=Path(settings.knowledge_root), settings=settings)
    results = conflicts + (checker.run_all() if check_scenario else checker.run_without_scenario_check())

    blocking = PreflightChecker.blocking_failures(results)
    if blocking:
        raise PreflightBlocked(blocking)
    return results
