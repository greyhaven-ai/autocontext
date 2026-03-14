"""Stage 3b: Archivist gate — evaluate librarian escalations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autocontext.agents.contracts import ArchivistOutput


def evaluate_archivist_gate(
    archivist_output: ArchivistOutput | None,
    backpressure_decision: str,
) -> dict:
    """Evaluate archivist decisions and determine gate action.

    Returns dict with:
      action: "proceed", "retry", or "skip"
      soft_flags: list of ArchivistDecision with verdict "soft_flag"
      constraint: str reasoning for retry (if action == "retry")
    """
    if backpressure_decision == "rollback":
        return {"action": "skip", "soft_flags": [], "constraint": ""}

    if archivist_output is None or not archivist_output.decisions:
        return {"action": "proceed", "soft_flags": [], "constraint": ""}

    soft_flags = [d for d in archivist_output.decisions if d.verdict == "soft_flag"]
    hard_gates = [d for d in archivist_output.decisions if d.verdict == "hard_gate"]

    if hard_gates:
        constraints = []
        for gate in hard_gates:
            constraints.append(
                f"[{gate.book_name}] {gate.reasoning} (Source: {gate.cited_passage})"
            )
        return {
            "action": "retry",
            "soft_flags": soft_flags,
            "constraint": "\n".join(constraints),
        }

    return {"action": "proceed", "soft_flags": soft_flags, "constraint": ""}
