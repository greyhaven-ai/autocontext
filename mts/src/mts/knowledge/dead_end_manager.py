from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DeadEndEntry:
    generation: int
    strategy_summary: str
    score: float
    reason: str

    def to_markdown(self) -> str:
        return (
            f"- **Gen {self.generation}**: {self.strategy_summary} "
            f"(score={self.score:.4f}) — {self.reason}"
        )

    @classmethod
    def from_rollback(cls, generation: int, strategy: str, score: float) -> DeadEndEntry:
        summary = strategy[:80] + "..." if len(strategy) > 80 else strategy
        return cls(
            generation=generation,
            strategy_summary=summary,
            score=score,
            reason="Rolled back due to score regression",
        )


def consolidate_dead_ends(entries_md: str, max_entries: int) -> str:
    """Trim dead-end registry to max_entries, keeping most recent."""
    lines = entries_md.strip().splitlines()
    # Find entries by "- **Gen" prefix
    entry_lines = [line for line in lines if line.startswith("- **Gen")]
    if len(entry_lines) <= max_entries:
        return entries_md
    # Keep most recent entries
    kept = entry_lines[-max_entries:]
    return "# Dead-End Registry\n\n" + "\n".join(kept) + "\n"
