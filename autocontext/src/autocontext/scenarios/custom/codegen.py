from __future__ import annotations

import re

from autocontext.scenarios.custom.codegen_security import (
    generated_class_name,
    python_literal,
    python_string_literal,
)
from autocontext.scenarios.custom.spec import ScenarioSpec

I1 = "    "  # 1 level indent (class body)
I2 = "        "  # 2 levels (method body)
I3 = "            "  # 3 levels


def _class_name(spec_name: str) -> str:
    return generated_class_name(spec_name, "Scenario")


def _gen_initial_state(spec: ScenarioSpec) -> list[str]:
    lines = [
        f"{I1}def initial_state(self, seed: int | None = None) -> dict[str, Any]:",
        f"{I2}rng = random.Random(seed)",
        f"{I2}return {{",
        f'{I3}"seed": seed or 0,',
    ]
    for env in spec.environment_variables:
        lines.append(
            f"{I3}{python_literal(env.name)}: "
            f"round(rng.uniform({python_literal(env.low)}, {python_literal(env.high)}), 3),"
        )
    lines.extend(
        [
            f'{I3}"terminal": False,',
            f'{I3}"timeline": [],',
            f"{I2}}}",
        ]
    )
    return lines


def _gen_get_observation(spec: ScenarioSpec) -> list[str]:
    state_keys = [e.name for e in spec.environment_variables]
    lines = [
        f"{I1}def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:",
        f"{I2}return Observation(",
        f"{I2}    narrative=(",
        f'{I2}        f"{{player_id}} observes: " + ", ".join(',
        f"{I2}            f\"{{k}}={{state.get(k, 'N/A')}}\" for k in {python_literal(state_keys)}",
        f"{I2}        )",
        f"{I2}    ),",
        f"{I2}    state={{",
    ]
    for k in state_keys:
        key = python_literal(k)
        lines.append(f"{I2}        {key}: state[{key}],")
    lines.append(f"{I2}    }},")
    lines.append(f"{I2}    constraints=[")
    for c in spec.observation_constraints:
        lines.append(f"{I2}        {python_literal(c)},")
    lines.extend(
        [
            f"{I2}    ],",
            f"{I2})",
        ]
    )
    return lines


def _constraint_expression_source(expression: str, param_names: set[str]) -> str:
    parts = re.split(r"([+-])", re.sub(r"\s+", "", expression))
    if not parts or len(parts) % 2 == 0:
        raise ValueError("constraint expression must alternate parameter names and +/- operators")

    emitted: list[str] = []
    for index, part in enumerate(parts):
        if index % 2:
            if part not in {"+", "-"}:
                raise ValueError("constraint expression contains an unsupported operator")
            emitted.append(part)
        else:
            if not part or part not in param_names:
                raise ValueError(f"constraint references unknown parameter: {part!r}")
            emitted.append(f"parsed[{python_literal(part)}]")
    return " ".join(emitted)


def _gen_validate_actions(spec: ScenarioSpec) -> list[str]:
    param_names = [p.name for p in spec.strategy_params]
    required_tuple = python_literal(tuple(param_names))

    lines = [
        f"{I1}def validate_actions(",
        f"{I2}self,",
        f"{I2}state: Mapping[str, Any],",
        f"{I2}player_id: str,",
        f"{I2}actions: Mapping[str, Any],",
        f"{I1}) -> tuple[bool, str]:",
        f"{I2}del state, player_id",
        f"{I2}required = {required_tuple}",
        f"{I2}parsed: dict[str, float] = {{}}",
        f"{I2}for key in required:",
        f"{I3}value = actions.get(key)",
        f"{I3}if not isinstance(value, (int, float)):",
        f'{I3}    return False, f"missing or invalid field: {{key}}"',
        f"{I3}parsed[key] = float(value)",
    ]
    for p in spec.strategy_params:
        name = python_literal(p.name)
        minimum = python_literal(p.min_value)
        maximum = python_literal(p.max_value)
        error = python_literal(f"{p.name} must be in [{p.min_value},{p.max_value}]")
        lines.append(f"{I2}if parsed[{name}] < {minimum} or parsed[{name}] > {maximum}:")
        lines.append(f"{I3}return False, {error}")

    for c in spec.constraints:
        operator = c.operator if c.operator in {"<=", ">=", "<", ">", "=="} else None
        if operator is None:
            raise ValueError(f"unsupported constraint operator: {c.operator!r}")
        expression = _constraint_expression_source(c.expression, set(param_names))
        lines.append(
            f"{I2}if not ({expression} {operator} {python_literal(c.threshold)}):"
        )
        lines.append(f"{I3}return False, {python_literal(c.description)}")

    lines.append(f'{I2}return True, "ok"')
    return lines


def _gen_step(spec: ScenarioSpec) -> list[str]:
    param_vars = {
        param.name: f"parameter_{index}"
        for index, param in enumerate(spec.strategy_params)
    }
    component_vars = [
        (component, f"component_{index}")
        for index, component in enumerate(spec.scoring_components)
    ]

    lines = [
        f"{I1}def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:",
    ]
    for name, variable in param_vars.items():
        lines.append(f"{I2}{variable} = float(actions[{python_literal(name)}])")
    lines.append(f'{I2}rng = random.Random(int(state["seed"]))')

    for component, variable in component_vars:
        terms: list[str] = []
        for param_ref, coefficient in component.formula_terms.items():
            parameter_variable = param_vars.get(param_ref)
            if parameter_variable is None:
                raise ValueError(
                    f"scoring component references unknown parameter: {param_ref!r}"
                )
            terms.append(f"{python_literal(coefficient)} * {parameter_variable}")
        noise_lo, noise_hi = component.noise_range
        formula = " + ".join(terms) if terms else "0.0"
        lines.append(
            f"{I2}{variable} = max(0.0, min(1.0, {formula} + "
            f"rng.uniform({python_literal(noise_lo)}, {python_literal(noise_hi)})))"
        )

    score_terms: list[str] = []
    for component, variable in component_vars:
        weight = spec.final_score_weights.get(component.name, 0.0)
        score_terms.append(f"{python_literal(weight)} * {variable}")
    score_expr = " + ".join(score_terms) if score_terms else "0.0"
    lines.append(f"{I2}score = max(0.0, min(1.0, {score_expr}))")

    lines.extend(
        [
            f'{I2}timeline = list(state["timeline"])',
            f"{I2}timeline.append({{",
            f'{I3}"event": "turn_complete",',
        ]
    )
    for component, variable in component_vars:
        lines.append(f"{I3}{python_literal(component.name)}: round({variable}, 4),")
    lines.extend(
        [
            f"{I2}}})",
            f"{I2}return {{",
            f"{I3}**dict(state),",
            f'{I3}"terminal": True,',
            f'{I3}"score": round(score, 4),',
            f'{I3}"metrics": {{',
        ]
    )
    for component, variable in component_vars:
        lines.append(
            f"{I3}    {python_literal(component.name)}: round({variable}, 4),"
        )
    lines.extend(
        [
            f"{I3}}},",
            f'{I3}"timeline": timeline,',
            f"{I2}}}",
        ]
    )
    return lines


def _gen_get_result(spec: ScenarioSpec) -> list[str]:
    display = python_literal(spec.display_name)
    threshold = python_literal(spec.win_threshold)
    return [
        f"{I1}def get_result(self, state: Mapping[str, Any]) -> Result:",
        f'{I2}replay = list(state.get("timeline", []))',
        f'{I2}score = float(state.get("score", 0.0))',
        f"{I2}return Result(",
        f"{I3}score=score,",
        f'{I3}winner="challenger" if score >= {threshold} else "incumbent",',
        f'{I3}summary={display} + f" score {{score:.4f}}",',
        f"{I3}replay=replay,",
        f'{I3}metrics={{k: float(v) for k, v in dict(state.get("metrics", {{}})).items()}},',
        f"{I2})",
    ]


def _gen_replay_to_narrative(spec: ScenarioSpec) -> list[str]:
    component_names = tuple(component.name for component in spec.scoring_components)
    display = python_literal(spec.display_name)
    return [
        f"{I1}def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:",
        f"{I2}if not replay:",
        f'{I2}    return "No replay events were captured."',
        f"{I2}event = replay[-1]",
        f"{I2}component_names = {python_literal(component_names)}",
        f"{I2}metrics = \", \".join(",
        f'{I3}f"{{name}} {{float(event.get(name, 0.0)):.2f}}"',
        f"{I3}for name in component_names",
        f"{I2})",
        f"{I2}return {display} + \": \" + metrics",
    ]


def _gen_render_frame() -> list[str]:
    return [
        f"{I1}def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:",
        f"{I2}return {{",
        f'{I3}"scenario": self.name,',
        f'{I3}"score": float(state.get("score", 0.0)),',
        f'{I3}"metrics": state.get("metrics", {{}}),',
        f"{I2}}}",
    ]


def _gen_is_terminal() -> list[str]:
    return [
        f"{I1}def is_terminal(self, state: Mapping[str, Any]) -> bool:",
        f'{I2}return bool(state.get("terminal", False))',
    ]


def generate_scenario_class(spec: ScenarioSpec) -> str:
    cls_name = _class_name(spec.name)

    describe_rules = [
        f"{I1}def describe_rules(self) -> str:",
        f"{I2}return {python_literal(spec.description)}",
    ]
    describe_strategy = [
        f"{I1}def describe_strategy_interface(self) -> str:",
        f"{I2}return {python_literal(spec.strategy_interface_description)}",
    ]
    describe_eval = [
        f"{I1}def describe_evaluation_criteria(self) -> str:",
        f"{I2}return {python_literal(spec.evaluation_criteria)}",
    ]

    method_blocks = [
        describe_rules,
        describe_strategy,
        describe_eval,
        _gen_initial_state(spec),
        _gen_get_observation(spec),
        _gen_validate_actions(spec),
        _gen_step(spec),
        _gen_is_terminal(),
        _gen_get_result(spec),
        _gen_replay_to_narrative(spec),
        _gen_render_frame(),
    ]

    body = "\n\n".join("\n".join(block) for block in method_blocks)

    return (
        "from __future__ import annotations\n"
        "\n"
        "import random\n"
        "from collections.abc import Mapping\n"
        "from typing import Any\n"
        "\n"
        "from autocontext.scenarios.base import Observation, Result, ScenarioInterface\n"
        "\n"
        "\n"
        f"class {cls_name}(ScenarioInterface):\n"
        f"    name = {python_string_literal(spec.name)}\n"
        + (f"    family = {python_string_literal(spec.family)}\n" if spec.family else "")
        + "\n"
        f"{body}\n"
    )
