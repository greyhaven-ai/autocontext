"""Skill export — portable knowledge packages for external agents."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from mts.mcp.tools import MtsToolContext
from mts.scenarios import SCENARIO_REGISTRY

LOGGER = logging.getLogger(__name__)

# Patterns for cleaning noisy lesson bullets
_ROLLBACK_RE = re.compile(r"^-\s*Generation\s+\d+\s+ROLLBACK\b", re.IGNORECASE)
_RAW_JSON_RE = re.compile(r'\{"[a-z_]+"\s*:\s*[\d.]+')
_SCORE_PARENS_RE = re.compile(r"\(score=[0-9.]+,\s*delta=[0-9.+-]+,\s*threshold=[0-9.]+\)")


@dataclass(slots=True)
class SkillPackage:
    scenario_name: str
    display_name: str
    description: str
    playbook: str
    lessons: list[str]
    best_strategy: dict[str, Any] | None
    best_score: float
    best_elo: float
    hints: str
    metadata: dict[str, Any] = field(default_factory=dict)
    task_prompt: str | None = None
    judge_rubric: str | None = None
    example_outputs: list[dict] | None = None
    output_format: str | None = None
    reference_context: str | None = None
    context_preparation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "scenario_name": self.scenario_name,
            "display_name": self.display_name,
            "description": self.description,
            "playbook": self.playbook,
            "lessons": self.lessons,
            "best_strategy": self.best_strategy,
            "best_score": self.best_score,
            "best_elo": self.best_elo,
            "hints": self.hints,
            "metadata": self.metadata,
        }
        if self.task_prompt is not None:
            d["task_prompt"] = self.task_prompt
        if self.judge_rubric is not None:
            d["judge_rubric"] = self.judge_rubric
        if self.example_outputs is not None:
            d["example_outputs"] = self.example_outputs
        if self.output_format is not None:
            d["output_format"] = self.output_format
        if self.reference_context is not None:
            d["reference_context"] = self.reference_context
        if self.context_preparation is not None:
            d["context_preparation"] = self.context_preparation
        return d

    def to_skill_markdown(self) -> str:
        """Render as a portable SKILL.md suitable for any agent's skill directory."""
        lessons_block = "\n".join(f"- {ln}" for ln in self.lessons) if self.lessons else "No lessons yet."
        strategy_block = ""
        if self.best_strategy:
            strategy_block = (
                "\n## Best Known Strategy\n\n"
                f"```json\n{json.dumps(self.best_strategy, indent=2)}\n```\n"
                f"\nBest score: {self.best_score:.4f} | Best Elo: {self.best_elo:.1f}\n"
            )
        # Agent task rendering path
        if self.task_prompt is not None:
            return self._render_agent_task_markdown(lessons_block)

        return (
            f"---\nname: {self.scenario_name.replace('_', '-')}-knowledge\n"
            f"description: {self.description[:200]}\n---\n\n"
            f"# {self.display_name}\n\n"
            f"{self.description}\n\n"
            "## Operational Lessons\n\n"
            f"{lessons_block}\n"
            f"{strategy_block}\n"
            "## Playbook\n\n"
            f"{self.playbook}\n"
        )

    def _render_agent_task_markdown(self, lessons_block: str) -> str:
        """Render markdown for agent task skill packages."""
        parts: list[str] = [
            f"---\nname: {self.scenario_name.replace('_', '-')}-knowledge\n"
            f"description: {self.description[:200]}\n---\n\n"
            f"# {self.display_name}\n\n"
            f"{self.description}\n\n"
            f"## Task\n\n"
            f"{self.task_prompt}\n",
        ]

        if self.judge_rubric:
            parts.append(
                f"\n## Evaluation Criteria\n\n"
                f"{self.judge_rubric}\n"
            )

        if self.context_preparation:
            parts.append(
                f"\n## Context Preparation\n\n"
                f"{self.context_preparation}\n"
            )

        if self.reference_context:
            parts.append(
                f"\n## Reference Context\n\n"
                f"{self.reference_context}\n"
            )

        if self.example_outputs:
            parts.append("\n## Example Outputs\n")
            for i, ex in enumerate(self.example_outputs[:3], 1):
                score = ex.get("score", 0.0)
                reasoning = ex.get("reasoning", "")
                output = ex.get("output", "")
                parts.append(
                    f"\n<details>\n<summary>Example {i} (score: {score:.2f})</summary>\n\n"
                    f"**Output:**\n\n{output}\n\n"
                    f"**Reasoning:** {reasoning}\n\n"
                    f"</details>\n"
                )

        parts.append(
            f"\n## Operational Lessons\n\n"
            f"{lessons_block}\n"
        )

        if self.best_strategy:
            strategy_text = json.dumps(self.best_strategy, indent=2)
            parts.append(
                f"\n## Best Known Strategy\n\n"
                f"```\n{strategy_text}\n```\n"
                f"\nBest score: {self.best_score:.4f} | Best Elo: {self.best_elo:.1f}\n"
            )

        parts.append(
            f"\n## Playbook\n\n"
            f"{self.playbook}\n"
        )

        return "".join(parts)


def export_skill_package(ctx: MtsToolContext, scenario_name: str) -> SkillPackage:
    """Assemble a portable skill package from accumulated scenario knowledge."""
    if scenario_name not in SCENARIO_REGISTRY:
        supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
        raise ValueError(f"Unknown scenario '{scenario_name}'. Available: {supported}")

    scenario = SCENARIO_REGISTRY[scenario_name]()

    playbook = ctx.artifacts.read_playbook(scenario_name)
    raw_lessons = ctx.artifacts.read_skill_lessons_raw(scenario_name)
    lessons = _clean_lessons(raw_lessons)
    hints = ctx.artifacts.read_hints(scenario_name)

    snapshot = ctx.sqlite.get_best_knowledge_snapshot(scenario_name)
    best_score = snapshot["best_score"] if snapshot else 0.0
    best_elo = snapshot["best_elo"] if snapshot else 1500.0

    best_strategy_raw = ctx.sqlite.get_best_competitor_output(scenario_name)
    best_strategy: dict[str, Any] | None = None
    if best_strategy_raw:
        try:
            best_strategy = json.loads(best_strategy_raw)
        except (json.JSONDecodeError, TypeError):
            best_strategy = None

    completed_runs = ctx.sqlite.count_completed_runs(scenario_name)

    describe_fn = getattr(scenario, "describe_rules", None) or getattr(scenario, "describe_task", None)
    description = describe_fn() if describe_fn else ""
    display_name = scenario_name.replace("_", " ").title()

    # Populate agent task fields if applicable
    task_prompt: str | None = None
    judge_rubric: str | None = None
    output_format: str | None = None
    reference_context: str | None = None
    context_preparation: str | None = None
    if hasattr(scenario, "get_task_prompt") and hasattr(scenario, "get_rubric"):
        try:
            task_prompt = scenario.get_task_prompt(scenario.initial_state())
            judge_rubric = scenario.get_rubric()
            output_format = getattr(scenario, "_output_format", None)
            reference_context = getattr(scenario, "_reference_context", None)
            context_preparation = getattr(scenario, "_context_preparation", None)
        except Exception:
            pass

    return SkillPackage(
        scenario_name=scenario_name,
        display_name=display_name,
        description=description,
        playbook=playbook,
        lessons=lessons,
        best_strategy=best_strategy,
        best_score=best_score,
        best_elo=best_elo,
        hints=hints,
        metadata={
            "completed_runs": completed_runs,
            "has_snapshot": snapshot is not None,
        },
        task_prompt=task_prompt,
        judge_rubric=judge_rubric,
        output_format=output_format,
        reference_context=reference_context,
        context_preparation=context_preparation,
    )


def list_solved_scenarios(ctx: MtsToolContext) -> list[dict[str, Any]]:
    """Return metadata for scenarios that have at least one completed run."""
    results: list[dict[str, Any]] = []
    for name in sorted(SCENARIO_REGISTRY.keys()):
        completed = ctx.sqlite.count_completed_runs(name)
        if completed == 0:
            continue
        scenario = SCENARIO_REGISTRY[name]()
        snapshot = ctx.sqlite.get_best_knowledge_snapshot(name)
        results.append({
            "name": name,
            "display_name": name.replace("_", " ").title(),
            "description": _scenario_description(scenario)[:200],
            "best_score": snapshot["best_score"] if snapshot else 0.0,
            "best_elo": snapshot["best_elo"] if snapshot else 1500.0,
            "completed_runs": completed,
        })
    return results


def export_agent_task_skill(
    scenario_name: str,
    task_prompt: str,
    judge_rubric: str,
    output_format: str,
    playbook: str,
    lessons: list[str],
    best_outputs: list[dict],
    hints: str | None = None,
    reference_context: str | None = None,
    context_preparation: str | None = None,
) -> SkillPackage:
    """Convenience builder for agent-task skill packages."""
    display_name = scenario_name.replace("_", " ").title()
    return SkillPackage(
        scenario_name=scenario_name,
        display_name=display_name,
        description=f"Agent task: {display_name}",
        playbook=playbook,
        lessons=lessons,
        best_strategy=None,
        best_score=best_outputs[0]["score"] if best_outputs else 0.0,
        best_elo=1500.0,
        hints=hints or "",
        task_prompt=task_prompt,
        judge_rubric=judge_rubric,
        example_outputs=best_outputs or None,
        output_format=output_format,
        reference_context=reference_context,
        context_preparation=context_preparation,
    )


def _scenario_description(scenario: object) -> str:
    """Get description from either ScenarioInterface or AgentTaskInterface."""
    fn = getattr(scenario, "describe_rules", None) or getattr(scenario, "describe_task", None)
    return fn() if fn else ""


def _clean_lessons(raw_bullets: list[str]) -> list[str]:
    """Strip MTS-internal noise from lesson bullets, keeping prescriptive rules."""
    cleaned: list[str] = []
    for bullet in raw_bullets:
        text = bullet.strip()
        if not text:
            continue
        # Remove leading "- " for processing, re-add later
        content = text[2:] if text.startswith("- ") else text
        # Skip noisy rollback log lines
        if _ROLLBACK_RE.match(text):
            continue
        # Skip lines that are mostly raw JSON strategy blobs
        if _RAW_JSON_RE.search(content) and content.strip().startswith("{"):
            continue
        # Strip score parentheticals inline
        content = _SCORE_PARENS_RE.sub("", content).strip()
        if content:
            cleaned.append(content)
    return cleaned
