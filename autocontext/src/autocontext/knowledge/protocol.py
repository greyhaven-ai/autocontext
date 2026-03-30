from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from autocontext.config.tuning_bounds import protocol_bounds

logger = logging.getLogger(__name__)

# Protocol-tier bounds: wider ranges for deliberate experimental exploration.
# Derived from the canonical definition in config/tuning_bounds.py.
TUNING_ALLOWED_KEYS: dict[str, tuple[type, float | int, float | int]] = protocol_bounds()


@dataclass(slots=True)
class ResearchProtocol:
    exploration_mode: str = "linear"
    current_focus: str = ""
    constraints: list[str] = field(default_factory=list)
    tuning_overrides: dict[str, float | int] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Serialize protocol to markdown format."""
        lines = [
            "## Exploration Mode",
            self.exploration_mode,
            "",
            "## Current Focus",
            self.current_focus or "(none)",
            "",
            "## Constraints",
        ]
        if self.constraints:
            for c in self.constraints:
                lines.append(f"- {c}")
        else:
            lines.append("(none)")
        lines.append("")
        lines.append("## Tuning Overrides")
        if self.tuning_overrides:
            lines.append("```json")
            lines.append(json.dumps(self.tuning_overrides, indent=2))
            lines.append("```")
        else:
            lines.append("(none)")
        lines.append("")
        return "\n".join(lines)


def parse_research_protocol(markdown: str) -> ResearchProtocol:
    """Parse a research protocol from its markdown representation."""
    protocol = ResearchProtocol()

    # Extract exploration mode
    mode_match = re.search(r"## Exploration Mode\s*\n(.+)", markdown)
    if mode_match:
        mode = mode_match.group(1).strip()
        if mode in ("linear", "rapid", "tree"):
            protocol.exploration_mode = mode

    # Extract current focus
    focus_match = re.search(r"## Current Focus\s*\n(.+?)(?=\n##|\Z)", markdown, re.DOTALL)
    if focus_match:
        focus = focus_match.group(1).strip()
        if focus != "(none)":
            protocol.current_focus = focus

    # Extract constraints
    constraints_match = re.search(r"## Constraints\s*\n(.+?)(?=\n##|\Z)", markdown, re.DOTALL)
    if constraints_match:
        block = constraints_match.group(1).strip()
        if block != "(none)":
            protocol.constraints = [
                line.lstrip("- ").strip()
                for line in block.splitlines()
                if line.strip().startswith("-")
            ]

    # Extract tuning overrides
    tuning_match = re.search(r"## Tuning Overrides\s*\n```json\s*\n(.+?)```", markdown, re.DOTALL)
    if tuning_match:
        try:
            raw = json.loads(tuning_match.group(1))
            protocol.tuning_overrides = validate_tuning_overrides(raw)
        except (json.JSONDecodeError, ValueError):
            logger.debug("knowledge.protocol: suppressed json.JSONDecodeError), ValueError", exc_info=True)

    return protocol


def validate_tuning_overrides(raw: dict[str, object]) -> dict[str, float | int]:
    """Validate and filter tuning overrides against allowed keys and ranges."""
    result: dict[str, float | int] = {}
    for key, value in raw.items():
        if key not in TUNING_ALLOWED_KEYS:
            continue
        expected_type, min_val, max_val = TUNING_ALLOWED_KEYS[key]
        try:
            if expected_type is int:
                val = int(value)  # type: ignore[call-overload]
            else:
                val = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if min_val <= val <= max_val:
            result[key] = val
    return result


def parse_protocol_from_architect(output: str) -> ResearchProtocol | None:
    """Extract protocol proposal from architect output using PROTOCOL markers."""
    match = re.search(
        r"<!-- PROTOCOL_START -->\s*\n(.+?)\n\s*<!-- PROTOCOL_END -->",
        output,
        re.DOTALL,
    )
    if not match:
        return None
    return parse_research_protocol(match.group(1))


def default_protocol() -> ResearchProtocol:
    """Create a default research protocol."""
    return ResearchProtocol()
