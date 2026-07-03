from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.ambient.charter import (
    Charter,
    CharterBudgets,
    CharterSource,
    CharterTarget,
    GuardrailConfig,
)


def _minimal_charter(**overrides: object) -> Charter:
    base: dict[str, object] = dict(
        tier="oss",
        control_surface="local",
        autonomy="propose",
        sources=[CharterSource(name="native", kind="autocontext", enabled=True)],
        targets=[
            CharterTarget(
                name="competitor-grid",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                method="sft-distill",
                min_dataset_records=500,
                eval_suite="grid_ctf_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=200.0),
    )
    base.update(overrides)
    return Charter(**base)  # type: ignore[arg-type]


def test_minimal_charter_validates() -> None:
    charter = _minimal_charter()
    assert charter.autonomy == "propose"
    assert charter.guardrails.frozen_anchor is True
    assert charter.guardrails.min_frontier_fraction == pytest.approx(0.2)


def test_guardrail_booleans_cannot_be_disabled() -> None:
    with pytest.raises(ValidationError, match="guardrail floor"):
        GuardrailConfig(frozen_anchor=False)
    with pytest.raises(ValidationError, match="guardrail floor"):
        GuardrailConfig(provenance_quarantine=False)


def test_min_frontier_fraction_floor() -> None:
    with pytest.raises(ValidationError):
        GuardrailConfig(min_frontier_fraction=0.01)
    assert GuardrailConfig(min_frontier_fraction=0.05).min_frontier_fraction == pytest.approx(0.05)


def test_full_box_source_requires_hosted_tier() -> None:
    with pytest.raises(ValidationError, match="full-box"):
        _minimal_charter(
            sources=[CharterSource(name="box", kind="full-box", enabled=True)],
        )


def test_full_box_source_allowed_on_hosted_tier() -> None:
    charter = _minimal_charter(
        tier="hosted-box",
        sources=[CharterSource(name="box", kind="full-box", enabled=True)],
    )
    assert charter.sources[0].kind == "full-box"


def test_target_autonomy_override_optional() -> None:
    charter = _minimal_charter()
    assert charter.targets[0].autonomy is None


def test_guardrail_floor_holds_on_assignment() -> None:
    config = GuardrailConfig()
    with pytest.raises(ValidationError, match="guardrail floor"):
        config.frozen_anchor = False
    with pytest.raises(ValidationError):
        config.min_frontier_fraction = 0.0
