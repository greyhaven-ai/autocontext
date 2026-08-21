"""Authoritative readiness policy for protected accelerator evaluation."""

from __future__ import annotations

from autocontext.kernel_evolution.docker_watchdog import crash_safe_container_creation_policy

_ROLE_ISOLATION_AVAILABLE = False
_MUTATION_OBSERVATION_AVAILABLE = False
_COMPARABLE_TIMING_AVAILABLE = False


def protected_evaluator_boundary_requirements() -> dict[str, dict[str, bool | str]]:
    """Return every boundary that must be ready before protected Docker dispatch."""

    return {
        "accelerator_role_isolation": {
            "required": "independently-attested-evaluator-candidate-incumbent-grants/v1",
            "available": _ROLE_ISOLATION_AVAILABLE,
            "reason": (
                "protected accelerator evidence requires independently attested evaluator, candidate, and incumbent "
                "grants; the v1 shared-grant topology is unsupported"
            ),
        },
        "trusted_out_of_process_mutation_observation": {
            "required": "trusted-out-of-process-input-mutation-observation/v1",
            "available": _MUTATION_OBSERVATION_AVAILABLE,
            "reason": (
                "protected accelerator evidence requires trusted out-of-process input-mutation observation; "
                "the v1 same-interpreter authority is unsupported"
            ),
        },
        "comparable_timing_boundaries": {
            "required": "comparable-candidate-incumbent-reference-timing/v1",
            "available": _COMPARABLE_TIMING_AVAILABLE,
            "reason": (
                "protected accelerator evidence requires comparable candidate/incumbent/reference timing boundaries; "
                "the v1 RPC/local timing topology is unsupported"
            ),
        },
        "crash_safe_container_creation": dict(crash_safe_container_creation_policy()),
    }


def protected_evaluator_boundary_error() -> str | None:
    """Return the first unavailable boundary reason, or ``None`` when dispatch is safe."""

    for requirement in protected_evaluator_boundary_requirements().values():
        if requirement.get("available") is not True:
            reason = requirement.get("reason")
            return str(reason) if reason else "protected accelerator evidence boundary is unavailable"
    return None


__all__ = ["protected_evaluator_boundary_error", "protected_evaluator_boundary_requirements"]
