from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from autocontext.agents.subagent_runtime import SubagentRuntime, SubagentTask
from autocontext.scenarios.base import ScenarioInterface
from autocontext.scenarios.custom.codegen import generate_scenario_class
from autocontext.scenarios.custom.designer import SCENARIO_DESIGNER_SYSTEM, parse_spec_from_response
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.registry import CUSTOM_SCENARIOS_DIR
from autocontext.scenarios.custom.spec import ScenarioSpec
from autocontext.scenarios.custom.validator import validate_by_execution, validate_generated_code, validate_spec


@dataclass(slots=True)
class BuildResult:
    scenario_class: type[ScenarioInterface]
    test_scores: list[float] = field(default_factory=list)


class ScenarioCreator:
    def __init__(self, runtime: SubagentRuntime, model: str, knowledge_root: Path) -> None:
        self.runtime = runtime
        self.model = model
        self.knowledge_root = knowledge_root

    # NOTE: Keep in sync with AgentTaskCreator.STOP_WORDS and ts/src/scenarios/agent-task-creator.ts
    STOP_WORDS = frozenset({
        "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
        "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
        "that", "can", "should", "could", "would", "will", "must",
        "agent", "tool", "system",
        "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
        "it", "we", "they", "is", "are", "was", "be", "do", "does",
        "to", "in", "on", "at", "by", "which", "what", "how",
        "game",
    })

    def derive_name(self, description: str) -> str:
        words = re.sub(r"[^a-z0-9\s]", " ", description.lower()).split()
        meaningful = [w for w in words if w not in self.STOP_WORDS]
        # Prefer longer words (>3 chars) as they are more likely domain-specific nouns
        sorted_words = sorted(meaningful, key=len, reverse=True)
        seen: set[str] = set()
        unique: list[str] = []
        for w in sorted_words:
            if w not in seen:
                seen.add(w)
                unique.append(w)
        name_words = unique[:3] if len(unique) >= 3 else unique[:2] if unique else ["custom"]
        return "_".join(name_words)

    def generate_spec(self, description: str) -> ScenarioSpec:
        prompt = SCENARIO_DESIGNER_SYSTEM + f"\n\nUser description:\n{description}"
        result = self.runtime.run_task(SubagentTask(
            role="scenario_designer",
            model=self.model,
            prompt=prompt,
            max_tokens=3000,
            temperature=0.3,
        ))
        spec = parse_spec_from_response(result.content)
        errors = validate_spec(spec)
        if errors:
            raise ValueError(f"generated spec has validation errors: {'; '.join(errors)}")
        return spec

    def revise_spec(self, current_spec: ScenarioSpec, feedback: str) -> ScenarioSpec:
        prompt = (
            SCENARIO_DESIGNER_SYSTEM
            + f"\n\nCurrent spec:\n```json\n{json.dumps(current_spec.to_dict(), indent=2)}\n```"
            + f"\n\nUser feedback:\n{feedback}"
            + "\n\nRevise the spec based on the feedback. Output the complete revised spec."
        )
        result = self.runtime.run_task(SubagentTask(
            role="scenario_designer",
            model=self.model,
            prompt=prompt,
            max_tokens=3000,
            temperature=0.3,
        ))
        spec = parse_spec_from_response(result.content)
        errors = validate_spec(spec)
        if errors:
            raise ValueError(f"revised spec has validation errors: {'; '.join(errors)}")
        return spec

    def build_and_validate(self, spec: ScenarioSpec) -> BuildResult:
        source = generate_scenario_class(spec)

        code_errors = validate_generated_code(source)
        if code_errors:
            raise ValueError(f"generated code has syntax errors: {'; '.join(code_errors)}")

        custom_dir = self.knowledge_root / CUSTOM_SCENARIOS_DIR
        scenario_dir = custom_dir / spec.name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        scenario_file = scenario_dir / "scenario.py"
        scenario_file.write_text(source, encoding="utf-8")
        spec.save(scenario_dir)

        scenario_class = load_custom_scenario(custom_dir, spec.name, ScenarioInterface)

        exec_errors = validate_by_execution(scenario_class, spec, seeds=3)
        if exec_errors:
            raise ValueError(f"execution validation failed: {'; '.join(exec_errors)}")

        test_scores = []
        instance = scenario_class()
        for seed in range(3):
            default_strategy = {p.name: p.default for p in spec.strategy_params}
            result = instance.execute_match(strategy=default_strategy, seed=seed)
            test_scores.append(round(result.score, 3))

        return BuildResult(scenario_class=scenario_class, test_scores=test_scores)
