from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from autocontext.scenarios.custom.spec import ScenarioSpec

if TYPE_CHECKING:
    from autocontext.scenarios.base import ScenarioInterface


class SpecValidationError(Exception):
    pass


class CodeValidationError(Exception):
    pass


class ExecutionValidationError(Exception):
    pass


def validate_spec(spec: ScenarioSpec) -> list[str]:
    errors: list[str] = []

    if not spec.name or not spec.name.replace("_", "").isalnum():
        errors.append("name must be a non-empty alphanumeric+underscore identifier")

    if not spec.display_name:
        errors.append("display_name must not be empty")

    if not spec.strategy_params:
        errors.append("at least one strategy_param is required")

    param_names = [p.name for p in spec.strategy_params]
    if len(param_names) != len(set(param_names)):
        errors.append("strategy_param names must be unique")

    for p in spec.strategy_params:
        if p.min_value >= p.max_value:
            errors.append(f"strategy_param '{p.name}': min_value must be less than max_value")
        if p.default < p.min_value or p.default > p.max_value:
            errors.append(f"strategy_param '{p.name}': default must be within [min_value, max_value]")

    env_names = [e.name for e in spec.environment_variables]
    if len(env_names) != len(set(env_names)):
        errors.append("environment_variable names must be unique")

    valid_constraint_ops = {"<=", ">=", "<", ">", "=="}
    param_name_set = set(param_names)
    for c in spec.constraints:
        if c.operator not in valid_constraint_ops:
            errors.append(f"constraint operator '{c.operator}' not in {valid_constraint_ops}")
        tokens = [t.strip() for t in c.expression.replace("+", " ").replace("-", " ").split() if t.strip()]
        for token in tokens:
            if token not in param_name_set:
                errors.append(f"constraint references unknown param '{token}'")

    comp_names = [s.name for s in spec.scoring_components]
    if len(comp_names) != len(set(comp_names)):
        errors.append("scoring_component names must be unique")

    for sc in spec.scoring_components:
        for term_ref in sc.formula_terms:
            if term_ref not in param_name_set:
                errors.append(f"scoring_component '{sc.name}' references unknown param '{term_ref}'")

    if spec.final_score_weights:
        weight_sum = sum(spec.final_score_weights.values())
        if abs(weight_sum - 1.0) > 0.01:
            errors.append(f"final_score_weights must sum to ~1.0 (got {weight_sum:.4f})")
        for wk in spec.final_score_weights:
            if wk not in set(comp_names):
                errors.append(f"final_score_weights references unknown component '{wk}'")

    return errors


def validate_generated_code(source: str) -> list[str]:
    errors: list[str] = []
    try:
        ast.parse(source)
    except SyntaxError as exc:
        errors.append(f"syntax error at line {exc.lineno}: {exc.msg}")
    return errors


def validate_by_execution(scenario_class: type[ScenarioInterface], spec: ScenarioSpec, seeds: int = 3) -> list[str]:
    errors: list[str] = []
    scenario = scenario_class()

    if scenario.name != spec.name:
        errors.append(f"scenario.name '{scenario.name}' does not match spec.name '{spec.name}'")

    default_strategy = {p.name: p.default for p in spec.strategy_params}

    for seed in range(seeds):
        try:
            state = scenario.initial_state(seed=seed)
        except Exception as exc:
            errors.append(f"initial_state(seed={seed}) raised: {exc}")
            continue

        if "seed" not in state or "terminal" not in state or "timeline" not in state:
            errors.append(f"seed={seed}: state missing required keys (seed, terminal, timeline)")
            continue

        try:
            obs = scenario.get_observation(state, "test_player")
            if not obs.narrative:
                errors.append(f"seed={seed}: observation narrative is empty")
        except Exception as exc:
            errors.append(f"seed={seed}: get_observation raised: {exc}")

        try:
            valid, reason = scenario.validate_actions(state, "test_player", default_strategy)
            if not valid:
                errors.append(f"seed={seed}: default strategy failed validation: {reason}")
                continue
        except Exception as exc:
            errors.append(f"seed={seed}: validate_actions raised: {exc}")
            continue

        try:
            next_state = scenario.step(state, default_strategy)
        except Exception as exc:
            errors.append(f"seed={seed}: step raised: {exc}")
            continue

        if not scenario.is_terminal(next_state):
            errors.append(f"seed={seed}: state not terminal after step")
            continue

        try:
            result = scenario.get_result(next_state)
            if result.score < 0.0 or result.score > 1.0:
                errors.append(f"seed={seed}: score {result.score} out of [0,1] range")
        except Exception as exc:
            errors.append(f"seed={seed}: get_result raised: {exc}")

    return errors
