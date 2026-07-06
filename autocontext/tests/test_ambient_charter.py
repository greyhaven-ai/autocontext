from __future__ import annotations

import pytest
from pydantic import ValidationError

from autocontext.ambient.charter import (
    Charter,
    CharterAnchor,
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


def test_charter_has_default_anchor() -> None:
    charter = _minimal_charter()
    assert charter.anchor.model  # a sensible frontier default, never empty
    assert charter.anchor.provider == "anthropic"
    assert charter.anchor.rubric


def test_anchor_empty_model_rejected() -> None:
    with pytest.raises(ValidationError):
        CharterAnchor(model="")


def test_anchor_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        CharterAnchor(temperature=0.5)  # type: ignore[call-arg]


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


def test_duplicate_target_names_rejected() -> None:
    target = CharterTarget(
        name="dup",
        kind="role",
        selector="competitor@grid_ctf",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        min_dataset_records=1,
        eval_suite="s",
    )
    with pytest.raises(ValidationError, match="duplicate target names"):
        _minimal_charter(targets=[target, target.model_copy()])


@pytest.mark.parametrize("unsafe_name", ["../../etc/foo", "sub/evil"])
def test_target_name_path_traversal_rejected(unsafe_name: str) -> None:
    with pytest.raises(ValidationError):
        CharterTarget(
            name=unsafe_name,
            kind="role",
            selector="competitor@grid_ctf",
            base_model="Qwen/Qwen2.5-3B-Instruct",
            min_dataset_records=1,
            eval_suite="s",
        )


@pytest.mark.parametrize("safe_name", ["grid_ctf-auto", "competitor-local"])
def test_target_name_slug_accepted(safe_name: str) -> None:
    target = CharterTarget(
        name=safe_name,
        kind="role",
        selector="competitor@grid_ctf",
        base_model="Qwen/Qwen2.5-3B-Instruct",
        min_dataset_records=1,
        eval_suite="s",
    )
    assert target.name == safe_name


def test_unknown_charter_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        _minimal_charter(autonmy="full")  # typo'd key must fail loudly
    with pytest.raises(ValidationError):
        CharterTarget(
            name="t",
            kind="role",
            selector="s",
            base_model="m",
            min_dataset_records=1,
            eval_suite="e",
            autonmy="full",
        )


def test_non_default_redaction_profile_rejected() -> None:
    with pytest.raises(ValidationError, match="reserved"):
        CharterSource(name="native", kind="autocontext", redaction_profile="strict")


def test_duplicate_source_names_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate source names"):
        _minimal_charter(
            sources=[
                CharterSource(name="main", kind="autocontext"),
                CharterSource(name="main", kind="otel"),
            ]
        )
