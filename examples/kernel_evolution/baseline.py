"""Synthetic baseline for the kernel-evolution orchestration demo."""

# fake-kernel-correct: true
# fake-kernel-latency-ms: 0.100


class ModelNew:
    def __call__(self, left: list[float], right: list[float]) -> list[float]:
        return [a + b for a, b in zip(left, right, strict=True)]
