from __future__ import annotations

import re

from autocontext.scenarios.custom.spec import ScenarioSpec

I1 = "    "  # 1 level indent (class body)
I2 = "        "  # 2 levels (method body)
I3 = "            "  # 3 levels


def _safe_identifier(name: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", name)


def _class_name(spec_name: str) -> str:
    parts = spec_name.split("_")
    return "".join(p.capitalize() for p in parts) + "Scenario"


def _gen_initial_state(spec: ScenarioSpec) -> list[str]:
    lines = [
        f"{I1}def initial_state(self, seed: int | None = None) -> dict[str, Any]:",
        f"{I2}rng = random.Random(seed)",
        f"{I2}return {{",
        f'{I3}"seed": seed or 0,',
    ]
    for env in spec.environment_variables:
        safe = _safe_identifier(env.name)
        lines.append(f'{I3}"{safe}": round(rng.uniform({env.low}, {env.high}), 3),')
    lines.extend(
        [
            f'{I3}"terminal": False,',
            f'{I3}"timeline": [],',
            f"{I2}}}",
        ]
    )
    return lines


def _gen_get_observation(spec: ScenarioSpec) -> list[str]:
    state_keys = [_safe_identifier(e.name) for e in spec.environment_variables]
    lines = [
        f"{I1}def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:",
        f"{I2}return Observation(",
        f"{I2}    narrative=(",
        f'{I2}        f"{{player_id}} observes: " + ", ".join(',
        f"{I2}            f\"{{k}}={{state.get(k, 'N/A')}}\" for k in {state_keys!r}",
        f"{I2}        )",
        f"{I2}    ),",
        f"{I2}    state={{",
    ]
    for k in state_keys:
        lines.append(f'{I2}        "{k}": state["{k}"],')
    lines.append(f"{I2}    }},")
    lines.append(f"{I2}    constraints=[")
    for c in spec.observation_constraints:
        lines.append(f'{I2}        "{c}",')
    lines.extend(
        [
            f"{I2}    ],",
            f"{I2})",
        ]
    )
    return lines


def _gen_validate_actions(spec: ScenarioSpec) -> list[str]:
    param_names = [_safe_identifier(p.name) for p in spec.strategy_params]
    required_tuple = ", ".join(f'"{n}"' for n in param_names)

    lines = [
        f"{I1}def validate_actions(",
        f"{I2}self,",
        f"{I2}state: Mapping[str, Any],",
        f"{I2}player_id: str,",
        f"{I2}actions: Mapping[str, Any],",
        f"{I1}) -> tuple[bool, str]:",
        f"{I2}del state, player_id",
        f"{I2}required = ({required_tuple},)",
        f"{I2}parsed: dict[str, float] = {{}}",
        f"{I2}for key in required:",
        f"{I3}value = actions.get(key)",
        f"{I3}if not isinstance(value, (int, float)):",
        f'{I3}    return False, f"missing or invalid field: {{key}}"',
        f"{I3}parsed[key] = float(value)",
    ]
    for p in spec.strategy_params:
        safe = _safe_identifier(p.name)
        lines.append(f'{I2}if parsed["{safe}"] < {p.min_value} or parsed["{safe}"] > {p.max_value}:')
        lines.append(f'{I3}return False, "{safe} must be in [{p.min_value},{p.max_value}]"')

    for c in spec.constraints:
        expr_parts = re.split(r"(\+|-)", c.expression)
        tokens = []
        for part in expr_parts:
            stripped = part.strip()
            if stripped in ("+", "-"):
                tokens.append(stripped)
            elif stripped:
                safe = _safe_identifier(stripped)
                tokens.append(f'parsed["{safe}"]')
        expr = " ".join(tokens)
        lines.append(f"{I2}if not ({expr} {c.operator} {c.threshold}):")
        lines.append(f'{I3}return False, "{c.description}"')

    lines.append(f'{I2}return True, "ok"')
    return lines


def _gen_step(spec: ScenarioSpec) -> list[str]:
    param_names = [_safe_identifier(p.name) for p in spec.strategy_params]
    comp_names = [_safe_identifier(c.name) for c in spec.scoring_components]

    lines = [
        f"{I1}def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:",
    ]
    for n in param_names:
        lines.append(f'{I2}{n} = float(actions["{n}"])')
    lines.append(f'{I2}rng = random.Random(int(state["seed"]))')

    for comp in spec.scoring_components:
        safe = _safe_identifier(comp.name)
        terms = []
        for param_ref, coeff in comp.formula_terms.items():
            safe_ref = _safe_identifier(param_ref)
            terms.append(f"{coeff} * {safe_ref}")
        noise_lo, noise_hi = comp.noise_range
        formula = " + ".join(terms) if terms else "0.0"
        lines.append(f"{I2}{safe} = max(0.0, min(1.0, {formula} + rng.uniform({noise_lo}, {noise_hi})))")

    score_terms = []
    for cn in comp_names:
        w = spec.final_score_weights.get(cn, 0.0)
        score_terms.append(f"{w} * {cn}")
    score_expr = " + ".join(score_terms) if score_terms else "0.0"
    lines.append(f"{I2}score = max(0.0, min(1.0, {score_expr}))")

    lines.extend(
        [
            f'{I2}timeline = list(state["timeline"])',
            f"{I2}timeline.append({{",
            f'{I3}"event": "turn_complete",',
        ]
    )
    for cn in comp_names:
        lines.append(f'{I3}"{cn}": round({cn}, 4),')
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
    for cn in comp_names:
        lines.append(f'{I3}    "{cn}": round({cn}, 4),')
    lines.extend(
        [
            f"{I3}}},",
            f'{I3}"timeline": timeline,',
            f"{I2}}}",
        ]
    )
    return lines


def _gen_get_result(spec: ScenarioSpec) -> list[str]:
    display = spec.display_name
    threshold = spec.win_threshold
    return [
        f"{I1}def get_result(self, state: Mapping[str, Any]) -> Result:",
        f'{I2}replay = list(state.get("timeline", []))',
        f'{I2}score = float(state.get("score", 0.0))',
        f"{I2}return Result(",
        f"{I3}score=score,",
        f'{I3}winner="challenger" if score >= {threshold} else "incumbent",',
        f'{I3}summary=f"{display} score {{score:.4f}}",',
        f"{I3}replay=replay,",
        f'{I3}metrics={{k: float(v) for k, v in dict(state.get("metrics", {{}})).items()}},',
        f"{I2})",
    ]


def _gen_replay_to_narrative(spec: ScenarioSpec) -> list[str]:
    comp_names = [_safe_identifier(c.name) for c in spec.scoring_components]
    display = spec.display_name
    # Build f-string parts using single quotes for inner dict access to avoid nesting issues
    parts = []
    for cn in comp_names:
        parts.append(f"{cn} {{event.get('{cn}', 0.0):.2f}}")
    narrative_parts = ", ".join(parts)
    return [
        f"{I1}def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:",
        f"{I2}if not replay:",
        f'{I2}    return "No replay events were captured."',
        f"{I2}event = replay[-1]",
        f'{I2}return f"{display}: {narrative_parts}"',
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
        f"{I2}return {spec.description!r}",
    ]
    describe_strategy = [
        f"{I1}def describe_strategy_interface(self) -> str:",
        f"{I2}return {spec.strategy_interface_description!r}",
    ]
    describe_eval = [
        f"{I1}def describe_evaluation_criteria(self) -> str:",
        f"{I2}return {spec.evaluation_criteria!r}",
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
        f'    name = "{spec.name}"\n' + (f'    family = "{spec.family}"\n' if spec.family else "") + "\n"
        f"{body}\n"
    )
