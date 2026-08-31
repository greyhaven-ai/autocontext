"""Builders that translate runtime objects into interactive protocol messages."""

from __future__ import annotations

from typing import Any

from autocontext.server.protocol import (
    EnvironmentsMsg,
    ScenarioPreviewMsg,
    ScoringComponent,
    StrategyParam,
)


def build_environments_msg(env_info: dict[str, Any]) -> EnvironmentsMsg:
    """Convert RunManager environment metadata into a typed message."""
    return EnvironmentsMsg(**env_info)  # type: ignore[arg-type]


def build_scenario_preview_msg(spec: Any) -> ScenarioPreviewMsg:
    """Build a scenario preview message from a scenario specification."""
    params = [StrategyParam(name=p.name, description=p.description) for p in spec.strategy_params]
    scoring = [
        ScoringComponent(
            name=s.name,
            description=s.description,
            weight=spec.final_score_weights.get(s.name, 0.0),
        )
        for s in spec.scoring_components
    ]
    constraints = [f"{c.expression} {c.operator} {c.threshold}" for c in spec.constraints]
    return ScenarioPreviewMsg(
        name=spec.name,
        display_name=spec.display_name,
        description=spec.description,
        strategy_params=params,
        scoring_components=scoring,
        constraints=constraints,
        win_threshold=spec.win_threshold,
    )
