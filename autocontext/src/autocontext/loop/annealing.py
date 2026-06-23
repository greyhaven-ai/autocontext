from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AnnealingSchedule:
    enabled: bool = False
    start_temperature: float = 0.05
    end_temperature: float = 0.001
    generations: int = 20


@dataclass(frozen=True, slots=True)
class AnnealingOutcome:
    accepted: bool
    temperature: float
    acceptance_probability: float
    random_value: float
    delta: float

    def model_dump(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "temperature": self.temperature,
            "acceptance_probability": self.acceptance_probability,
            "random_value": self.random_value,
            "delta": self.delta,
        }


def annealing_temperature(schedule: AnnealingSchedule, generation: int) -> float:
    span = max(1, schedule.generations - 1)
    progress = min(1.0, max(0.0, (generation - 1) / span))
    return max(0.0, schedule.start_temperature + (schedule.end_temperature - schedule.start_temperature) * progress)


def annealing_random_value(seed_base: int, generation: int, attempt: int) -> float:
    value = 2_166_136_261
    for byte in f"{seed_base}:{generation}:{attempt}".encode():
        value = ((value ^ byte) * 16_777_619) & 0xFFFFFFFF
    return value / 0x1_0000_0000


def evaluate_annealing(delta: float, schedule: AnnealingSchedule, generation: int, random_value: float) -> AnnealingOutcome:
    temperature = annealing_temperature(schedule, generation)
    probability = math.exp(delta / temperature) if schedule.enabled and delta < 0 and temperature > 0 else 0.0
    probability = min(1.0, max(0.0, probability))
    return AnnealingOutcome(
        accepted=schedule.enabled and delta < 0 and random_value < probability,
        temperature=temperature,
        acceptance_probability=probability,
        random_value=random_value,
        delta=delta,
    )
