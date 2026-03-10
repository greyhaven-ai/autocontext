"""Canonical tuning bounds for meta-parameter validation.

Both the AR-3 research protocol (protocol.py) and AR-6 architect tuning
proposals (tuning.py) reference these bounds.  Two tiers exist:

* **architect** — tighter bounds for automated architect proposals that
  are applied without human review.
* **protocol** — wider bounds for research protocol overrides that
  represent deliberate experimental exploration.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParamBounds:
    """Bounds for a single tunable parameter."""

    param_type: type  # int or float
    architect_min: float
    architect_max: float
    protocol_min: float
    protocol_max: float


# Canonical definition — single source of truth
TUNING_PARAMS: dict[str, ParamBounds] = {
    "backpressure_min_delta": ParamBounds(
        param_type=float,
        architect_min=0.0, architect_max=0.05,
        protocol_min=0.0, protocol_max=1.0,
    ),
    "matches_per_generation": ParamBounds(
        param_type=int,
        architect_min=1, architect_max=10,
        protocol_min=1, protocol_max=20,
    ),
    "rlm_max_turns": ParamBounds(
        param_type=int,
        architect_min=3, architect_max=50,
        protocol_min=1, protocol_max=50,
    ),
    "architect_every_n_gens": ParamBounds(
        param_type=int,
        architect_min=1, architect_max=10,
        protocol_min=1, protocol_max=10,
    ),
    "probe_matches": ParamBounds(
        param_type=int,
        architect_min=0, architect_max=5,
        protocol_min=0, protocol_max=10,
    ),
}


def architect_bounds() -> dict[str, tuple[float, float]]:
    """Return (min, max) tuples for architect-tier validation."""
    return {k: (p.architect_min, p.architect_max) for k, p in TUNING_PARAMS.items()}


def protocol_bounds() -> dict[str, tuple[type, float, float]]:
    """Return (type, min, max) tuples for protocol-tier validation."""
    return {k: (p.param_type, p.protocol_min, p.protocol_max) for k, p in TUNING_PARAMS.items()}
