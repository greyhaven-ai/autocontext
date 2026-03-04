from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class StagnationReport:
    is_stagnated: bool
    trigger: str  # "none", "consecutive_rollbacks", "score_plateau"
    detail: str

    @staticmethod
    def no_stagnation() -> StagnationReport:
        return StagnationReport(is_stagnated=False, trigger="none", detail="")


class StagnationDetector:
    def __init__(
        self,
        rollback_threshold: int = 5,
        plateau_window: int = 5,
        plateau_epsilon: float = 0.01,
    ) -> None:
        self.rollback_threshold = rollback_threshold
        self.plateau_window = plateau_window
        self.plateau_epsilon = plateau_epsilon

    def detect(
        self,
        gate_history: list[str],
        score_history: list[float],
    ) -> StagnationReport:
        # Count trailing 'rollback' only (retries excluded — they may still succeed)
        consecutive_rollbacks = 0
        for decision in reversed(gate_history):
            if decision == "rollback":
                consecutive_rollbacks += 1
            else:
                break

        if consecutive_rollbacks >= self.rollback_threshold:
            return StagnationReport(
                is_stagnated=True,
                trigger="consecutive_rollbacks",
                detail=f"{consecutive_rollbacks} consecutive rollbacks",
            )

        # Check score plateau
        if len(score_history) >= self.plateau_window:
            window = score_history[-self.plateau_window:]
            mean = sum(window) / len(window)
            variance = sum((s - mean) ** 2 for s in window) / len(window)
            if variance < self.plateau_epsilon:
                return StagnationReport(
                    is_stagnated=True,
                    trigger="score_plateau",
                    detail=(
                        f"score variance {variance:.6f} < epsilon {self.plateau_epsilon}"
                        f" over last {self.plateau_window} gens"
                    ),
                )

        return StagnationReport.no_stagnation()
