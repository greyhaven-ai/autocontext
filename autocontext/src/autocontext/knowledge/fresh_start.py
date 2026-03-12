from __future__ import annotations

import json
import logging
from typing import Any

from autocontext.storage.artifacts import ArtifactStore

LOGGER = logging.getLogger(__name__)


def execute_fresh_start(
    artifacts: ArtifactStore,
    scenario_name: str,
    current_strategy: dict[str, Any],
    lessons: list[str],
    top_n: int = 5,
) -> str:
    """Execute a fresh start: archive playbook, write distilled version, clear hints.

    Returns the fresh-start competitor hint text.
    """
    # 1. Read top lessons
    top_lessons = lessons[:top_n]
    lessons_block = (
        "\n".join(f"- {line.lstrip('- ')}" for line in top_lessons)
        if top_lessons
        else "- No prior lessons"
    )

    # 2. Build distilled playbook
    full_json = json.dumps(current_strategy, indent=2, sort_keys=True)
    strategy_summary = full_json[:500] + ("..." if len(full_json) > 500 else "")
    distilled = (
        "# Fresh Start Playbook\n\n"
        "Previous approach stagnated. Starting fresh with distilled knowledge.\n\n"
        "## Retained Lessons\n\n"
        f"{lessons_block}\n\n"
        "## Best Strategy Reference\n\n"
        f"```json\n{strategy_summary}\n```\n\n"
        "## Directive\n\n"
        "Explore fundamentally different approaches. Do not repeat rolled-back strategies.\n"
    )

    # 3. Archive current playbook and write distilled (write_playbook auto-archives)
    artifacts.write_playbook(scenario_name, distilled)

    # 4. Clear hints
    artifacts.write_hints(scenario_name, "")

    # 5. Build fresh-start competitor hint
    hint = (
        "FRESH START: Previous strategy evolution has stagnated. "
        "You must explore a fundamentally different approach. "
        "Do not repeat parameter combinations from rolled-back strategies. "
        "Focus on the retained lessons above and try novel parameter ranges."
    )

    LOGGER.info("fresh start executed for scenario %s", scenario_name)
    return hint
