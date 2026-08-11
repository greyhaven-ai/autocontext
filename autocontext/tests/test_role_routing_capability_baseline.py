"""AC-911: capability/hosting baseline, recorded before the split.

``docs/role-routing-capability-baseline.json`` holds one input per case and the
output each language produces for it. This module replays the Python half;
``ts/tests/role-routing-capability-baseline.test.ts`` replays the TypeScript
half against the same inputs. Neither asserts the two languages agree -- they
currently do not, and the file records that honestly rather than hiding it.

Why this exists separately from ``test_role_routing_parity.py``: that fixture
supplies all 16 settings fields non-empty in every case and reports every field
as configured, so it cannot reach the unset/default-resolution layer at all.
This file carries ``set_fields`` per case, so a case can say "the user set
nothing but the provider" -- which is the normal way somebody points the loop at
a local server, and the layer AC-911 changes.

To re-record after an intentional behavior change:

    AC911_BASELINE_WRITE=1 uv run --frozen pytest tests/test_role_routing_capability_baseline.py
    cd ts && npx prettier --write ../docs/role-routing-capability-baseline.json

Each language writes only its own key, so re-recording one cannot silently
overwrite the other's measurement. The prettier pass only re-collapses short
arrays that json.dumps expands; skipping it changes no values and no CI gate
checks it, but it keeps the recorded diff to the lines that actually moved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

BASELINE_PATH = Path(__file__).resolve().parents[2] / "docs" / "role-routing-capability-baseline.json"

_WRITE = os.environ.get("AC911_BASELINE_WRITE") == "1"

_CONTEXT_KEY_MAP = {"availableLocalModels": "available_local_models"}

_ASSIGNMENT_KEYS = {"provider_type", "model", "provider_class", "cost_per_1k_tokens"}


def _load() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _settings(payload: dict[str, Any], case: dict[str, Any]) -> MagicMock:
    """Build a settings double whose ``model_fields_set`` matches the case.

    Specced, so a field a refactor newly reads raises AttributeError naming
    itself rather than resolving to a truthy auto-Mock (AC-919 item 8).
    """
    defaults: dict[str, str] = payload["settings_defaults"]
    overrides: dict[str, str] = case["settings"]
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise AssertionError(f"case sets unknown settings keys: {sorted(unknown)}")
    set_fields = set(case["set_fields"])
    if set_fields != set(overrides):
        raise AssertionError(f"set_fields {sorted(set_fields)} does not match settings keys {sorted(overrides)}")

    settings = MagicMock(spec=[*defaults, "model_fields_set"])
    settings.model_fields_set = frozenset(set_fields)
    for key, default in defaults.items():
        setattr(settings, key, overrides.get(key, default))
    return settings


def _context(raw: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, value in raw.items():
        mapped = _CONTEXT_KEY_MAP.get(key)
        if mapped is None:
            raise AssertionError(f"case sets unknown context key: {key}")
        converted[mapped] = value
    return converted


def _route(payload: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    from autocontext.agents.role_router import RoleRouter, RoutingContext

    router = RoleRouter(_settings(payload, case))
    cfg = router.route(case["role"], context=RoutingContext(**_context(case["context"])))
    return {
        "provider_type": cfg.provider_type,
        "model": cfg.model,
        "provider_class": cfg.provider_class.value,
        "cost_per_1k_tokens": cfg.estimated_cost_per_1k_tokens,
    }


def _rewrite() -> None:
    payload = _load()
    for case in payload["cases"].values():
        case["python"] = _route(payload, case)
    BASELINE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


if _WRITE:  # pragma: no cover - maintenance path, not a behavior under test
    _rewrite()


_PAYLOAD = _load()


@pytest.mark.parametrize("case_id", sorted(_PAYLOAD["cases"]))
def test_route_matches_baseline(case_id: str) -> None:
    """Every recorded route still resolves the way the baseline says it does."""
    case = _PAYLOAD["cases"][case_id]
    assert case["python"] is not None, f"{case_id} has no recorded Python output"
    assert _route(_PAYLOAD, case) == case["python"]


def test_every_case_records_both_languages() -> None:
    """A half-recorded case would pass its own replay while measuring nothing.

    Both keys must be populated and well-shaped, so deleting the TypeScript half
    to make a stubborn diff go away fails here instead of going unnoticed.
    """
    for case_id, case in _PAYLOAD["cases"].items():
        for language in ("python", "typescript"):
            recorded = case[language]
            assert recorded is not None, f"cases.{case_id} missing {language}"
            assert set(recorded) == _ASSIGNMENT_KEYS, f"cases.{case_id}.{language} has keys {sorted(recorded)}"


def test_cloud_only_routing_is_covered() -> None:
    """AC-911's third criterion needs a guard that cannot be quietly emptied.

    Every role must appear under both cloud routing modes, so a future edit
    cannot narrow the regression surface and still look green.
    """
    expected = {f"cloud_regression.{mode}.anthropic.{role}" for mode in ("auto", "off") for role in _PAYLOAD["roles"]}
    assert expected <= set(_PAYLOAD["cases"])


def test_baseline_input_guards_actually_raise() -> None:
    """The harness's own guards are otherwise never executed."""
    with pytest.raises(AssertionError, match="unknown settings keys"):
        _settings(_PAYLOAD, {"settings": {"nope": "x"}, "set_fields": ["nope"]})
    with pytest.raises(AssertionError, match="does not match settings keys"):
        _settings(_PAYLOAD, {"settings": {"agent_provider": "ollama"}, "set_fields": []})
    with pytest.raises(AssertionError, match="unknown context key"):
        _context({"notARealKey": []})
