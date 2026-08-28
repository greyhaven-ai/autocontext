from __future__ import annotations

import ast
import logging
import math
import re
from typing import TYPE_CHECKING

from autocontext.scenarios.custom.codegen_security import validate_generated_scenario_name
from autocontext.scenarios.custom.spec import ScenarioSpec

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.scenarios.base import ScenarioInterface


class SpecValidationError(Exception):
    pass


class CodeValidationError(Exception):
    pass


class ExecutionValidationError(Exception):
    pass


def _is_safe_identifier(value: str) -> bool:
    try:
        validate_generated_scenario_name(value)
    except ValueError:
        return False
    return True


def _is_finite_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(float(value))


def validate_spec(spec: ScenarioSpec) -> list[str]:
    errors: list[str] = []

    if not _is_safe_identifier(spec.name):
        errors.append("name must be a non-empty ASCII Python identifier")

    if spec.family and not _is_safe_identifier(spec.family):
        errors.append("family must be an ASCII Python identifier when provided")

    if not spec.display_name:
        errors.append("display_name must not be empty")

    if not spec.strategy_params:
        errors.append("at least one strategy_param is required")

    param_names = [p.name for p in spec.strategy_params]
    if len(param_names) != len(set(param_names)):
        errors.append("strategy_param names must be unique")

    for p in spec.strategy_params:
        if not _is_safe_identifier(p.name):
            errors.append(f"strategy_param name {p.name!r} must be an ASCII Python identifier")
        if not all(_is_finite_number(value) for value in (p.min_value, p.max_value, p.default)):
            errors.append(f"strategy_param '{p.name}': bounds and default must be finite numbers")
            continue
        if p.min_value >= p.max_value:
            errors.append(f"strategy_param '{p.name}': min_value must be less than max_value")
        if p.default < p.min_value or p.default > p.max_value:
            errors.append(f"strategy_param '{p.name}': default must be within [min_value, max_value]")

    env_names = [e.name for e in spec.environment_variables]
    if len(env_names) != len(set(env_names)):
        errors.append("environment_variable names must be unique")
    for environment in spec.environment_variables:
        if not _is_safe_identifier(environment.name):
            errors.append(f"environment_variable name {environment.name!r} must be an ASCII Python identifier")
        if not all(_is_finite_number(value) for value in (environment.low, environment.high)):
            errors.append(f"environment_variable '{environment.name}': bounds must be finite numbers")
        elif environment.low > environment.high:
            errors.append(f"environment_variable '{environment.name}': low must not exceed high")

    valid_constraint_ops = {"<=", ">=", "<", ">", "=="}
    param_name_set = set(param_names)
    for c in spec.constraints:
        if c.operator not in valid_constraint_ops:
            errors.append(f"constraint operator '{c.operator}' not in {valid_constraint_ops}")
        if not _is_finite_number(c.threshold):
            errors.append("constraint threshold must be a finite number")
        compact_expression = re.sub(r"\s+", "", c.expression)
        expression_parts = re.split(r"([+-])", compact_expression)
        if not compact_expression or len(expression_parts) % 2 == 0 or any(not part for part in expression_parts):
            errors.append("constraint expression must alternate parameter names and +/- operators")
            continue
        tokens = expression_parts[::2]
        for token in tokens:
            if token not in param_name_set:
                errors.append(f"constraint references unknown param '{token}'")

    comp_names = [s.name for s in spec.scoring_components]
    if len(comp_names) != len(set(comp_names)):
        errors.append("scoring_component names must be unique")

    for sc in spec.scoring_components:
        if not _is_safe_identifier(sc.name):
            errors.append(f"scoring_component name {sc.name!r} must be an ASCII Python identifier")
        if not all(_is_finite_number(value) for value in sc.noise_range):
            errors.append(f"scoring_component '{sc.name}': noise_range must contain finite numbers")
        elif sc.noise_range[0] > sc.noise_range[1]:
            errors.append(f"scoring_component '{sc.name}': noise_range low must not exceed high")
        for term_ref, coefficient in sc.formula_terms.items():
            if term_ref not in param_name_set:
                errors.append(f"scoring_component '{sc.name}' references unknown param '{term_ref}'")
            if not _is_finite_number(coefficient):
                errors.append(f"scoring_component '{sc.name}' coefficient for '{term_ref}' must be finite")

    if spec.final_score_weights:
        if not all(_is_finite_number(weight) for weight in spec.final_score_weights.values()):
            errors.append("final_score_weights must contain only finite numbers")
        else:
            weight_sum = sum(spec.final_score_weights.values())
            if abs(weight_sum - 1.0) > 0.01:
                errors.append(f"final_score_weights must sum to ~1.0 (got {weight_sum:.4f})")
        for wk in spec.final_score_weights:
            if wk not in set(comp_names):
                errors.append(f"final_score_weights references unknown component '{wk}'")

    if not _is_finite_number(spec.win_threshold):
        errors.append("win_threshold must be a finite number")

    return errors


def validate_generated_code(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error at line {exc.lineno}: {exc.msg}"]

    errors: list[str] = []
    allowed_imports: set[tuple[str, str | None, tuple[tuple[str, str | None], ...]]] = {
        ("from", "__future__", (("annotations", None),)),
        ("import", None, (("random", None),)),
        ("from", "collections.abc", (("Mapping", None),)),
        ("from", "typing", (("Any", None),)),
        (
            "from",
            "autocontext.scenarios.base",
            (("Observation", None), ("Result", None), ("ScenarioInterface", None)),
        ),
    }
    scenario_classes: list[ast.ClassDef] = []
    signature: tuple[str, str | None, tuple[tuple[str, str | None], ...]]
    for node in tree.body:
        if isinstance(node, ast.Import):
            signature = ("import", None, tuple((alias.name, alias.asname) for alias in node.names))
            if signature not in allowed_imports:
                errors.append(f"unexpected generated import at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            signature = (
                "from",
                node.module,
                tuple((alias.name, alias.asname) for alias in node.names),
            )
            if node.level or signature not in allowed_imports:
                errors.append(f"unexpected generated import at line {node.lineno}")
        elif isinstance(node, ast.ClassDef):
            scenario_classes.append(node)
        else:
            errors.append(f"unexpected top-level {type(node).__name__} at line {getattr(node, 'lineno', '?')}")

    if len(scenario_classes) != 1:
        errors.append("generated module must define exactly one scenario class")
        return errors

    scenario_class = scenario_classes[0]
    if (
        len(scenario_class.bases) != 1
        or not isinstance(scenario_class.bases[0], ast.Name)
        or scenario_class.bases[0].id != "ScenarioInterface"
    ):
        errors.append("generated class must directly subclass ScenarioInterface")

    expected_methods = {
        "describe_rules",
        "describe_strategy_interface",
        "describe_evaluation_criteria",
        "initial_state",
        "get_observation",
        "validate_actions",
        "step",
        "is_terminal",
        "get_result",
        "replay_to_narrative",
        "render_frame",
    }
    actual_methods: set[str] = set()
    class_assignments: set[str] = set()
    for node in scenario_class.body:
        if isinstance(node, ast.FunctionDef):
            if node.name in actual_methods:
                errors.append(f"duplicate generated method '{node.name}'")
            actual_methods.add(node.name)
            if node.decorator_list:
                errors.append(f"generated method '{node.name}' must not have decorators")
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            if target in class_assignments:
                errors.append(f"duplicate generated class attribute '{target}'")
            class_assignments.add(target)
            if target not in {"name", "family"}:
                errors.append(f"unexpected generated class attribute '{target}'")
            if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
                errors.append(f"generated class attribute '{target}' must be a string literal")
            continue
        errors.append(f"unexpected {type(node).__name__} in generated class body at line {getattr(node, 'lineno', '?')}")

    if class_assignments not in ({"name"}, {"name", "family"}):
        errors.append("generated class must define only the name and optional family attributes")
    if actual_methods != expected_methods:
        missing = sorted(expected_methods - actual_methods)
        unexpected = sorted(actual_methods - expected_methods)
        if missing:
            errors.append(f"generated class is missing methods: {', '.join(missing)}")
        if unexpected:
            errors.append(f"generated class has unexpected methods: {', '.join(unexpected)}")

    for descendant in ast.walk(scenario_class):
        if descendant is scenario_class:
            continue
        if isinstance(descendant, ast.Import | ast.ImportFrom | ast.ClassDef | ast.Global | ast.Nonlocal):
            errors.append(
                f"generated methods contain forbidden {type(descendant).__name__} at line {getattr(descendant, 'lineno', '?')}"
            )
        if (
            isinstance(descendant, ast.Call)
            and isinstance(descendant.func, ast.Name)
            and descendant.func.id in {"__import__", "compile", "eval", "exec"}
        ):
            errors.append(f"generated methods contain forbidden call '{descendant.func.id}'")
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
            logger.debug("scenarios.custom.validator: caught Exception", exc_info=True)
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
            logger.debug("scenarios.custom.validator: caught Exception", exc_info=True)
            errors.append(f"seed={seed}: get_observation raised: {exc}")

        try:
            valid, reason = scenario.validate_actions(state, "test_player", default_strategy)
            if not valid:
                errors.append(f"seed={seed}: default strategy failed validation: {reason}")
                continue
        except Exception as exc:
            logger.debug("scenarios.custom.validator: caught Exception", exc_info=True)
            errors.append(f"seed={seed}: validate_actions raised: {exc}")
            continue

        try:
            next_state = scenario.step(state, default_strategy)
        except Exception as exc:
            logger.debug("scenarios.custom.validator: caught Exception", exc_info=True)
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
            logger.debug("scenarios.custom.validator: caught Exception", exc_info=True)
            errors.append(f"seed={seed}: get_result raised: {exc}")

    return errors
