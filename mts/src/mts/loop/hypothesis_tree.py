"""HypothesisTree — multi-hypothesis strategy search with Thompson sampling."""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass


@dataclass(slots=True)
class HypothesisNode:
    """A single strategy hypothesis in the search tree."""

    id: str
    strategy: dict  # The strategy (JSON or code)
    parent_id: str | None
    scores: list[float]
    elo: float
    generation: int
    refinement_count: int


class HypothesisTree:
    """Maintains multiple strategy candidates, selecting via Thompson sampling."""

    def __init__(self, max_hypotheses: int = 8, temperature: float = 1.0) -> None:
        if max_hypotheses < 1:
            raise ValueError("max_hypotheses must be >= 1")
        if temperature <= 0:
            raise ValueError("temperature must be > 0")
        self.max_hypotheses = max_hypotheses
        self.temperature = temperature
        self.nodes: dict[str, HypothesisNode] = {}

    def add(
        self,
        strategy: dict,
        parent_id: str | None = None,
        generation: int = 0,
    ) -> HypothesisNode:
        """Add a new hypothesis. Auto-prunes if exceeding max_hypotheses."""
        node_id = uuid.uuid4().hex[:12]
        node = HypothesisNode(
            id=node_id,
            strategy=strategy,
            parent_id=parent_id,
            scores=[],
            elo=1500.0,
            generation=generation,
            refinement_count=0,
        )
        self.nodes[node_id] = node

        if len(self.nodes) > self.max_hypotheses:
            self.prune()

        return node

    def select(self, rng: random.Random | None = None) -> HypothesisNode:
        """Select next hypothesis to refine via Thompson sampling.

        Fits Beta(alpha, beta) per node from score history relative to the
        median. Samples from each distribution and returns the highest sample.
        """
        if not self.nodes:
            raise ValueError("Cannot select from empty tree")
        if len(self.nodes) == 1:
            return next(iter(self.nodes.values()))

        r = rng or random.Random()
        median = self._median_score()

        best_sample = -math.inf
        best_node: HypothesisNode | None = None

        for node in self.nodes.values():
            alpha, beta = self._fit_beta(node, median)
            # Temperature scales variance: higher temp = more exploration
            scaled_alpha = max(1.0, alpha / self.temperature)
            scaled_beta = max(1.0, beta / self.temperature)
            sample = r.betavariate(scaled_alpha, scaled_beta)

            if sample > best_sample:
                best_sample = sample
                best_node = node

        assert best_node is not None
        return best_node

    def update(self, node_id: str, scores: list[float], elo: float) -> None:
        """Update a node with new match results."""
        if node_id not in self.nodes:
            raise KeyError(f"Node {node_id} not found")
        node = self.nodes[node_id]
        node.scores.extend(scores)
        node.elo = elo
        node.refinement_count += 1

    def prune(self) -> list[HypothesisNode]:
        """Remove lowest-Elo nodes to stay within max_hypotheses. Returns removed nodes."""
        if len(self.nodes) <= self.max_hypotheses:
            return []
        sorted_nodes = sorted(self.nodes.values(), key=lambda n: n.elo)
        to_remove = len(self.nodes) - self.max_hypotheses
        removed = sorted_nodes[:to_remove]
        for node in removed:
            del self.nodes[node.id]
        return removed

    def best(self) -> HypothesisNode:
        """Return the highest-Elo hypothesis."""
        if not self.nodes:
            raise ValueError("Cannot get best from empty tree")
        return max(self.nodes.values(), key=lambda n: n.elo)

    def converged(self, threshold: float = 0.01) -> bool:
        """Check if all hypotheses have similar Elo (within threshold ratio of mean)."""
        if len(self.nodes) < 2:
            return True
        elos = [n.elo for n in self.nodes.values()]
        mean_elo = sum(elos) / len(elos)
        if mean_elo == 0:
            return True
        max_deviation = max(abs(e - mean_elo) for e in elos)
        return max_deviation / mean_elo < threshold

    def size(self) -> int:
        """Number of hypotheses in the tree."""
        return len(self.nodes)

    # ---- internal helpers ----

    def _median_score(self) -> float:
        """Compute overall median score across all nodes."""
        all_scores: list[float] = []
        for node in self.nodes.values():
            all_scores.extend(node.scores)
        if not all_scores:
            return 0.5
        sorted_scores = sorted(all_scores)
        n = len(sorted_scores)
        if n % 2 == 1:
            return sorted_scores[n // 2]
        return (sorted_scores[n // 2 - 1] + sorted_scores[n // 2]) / 2

    @staticmethod
    def _fit_beta(node: HypothesisNode, median: float) -> tuple[float, float]:
        """Fit Beta(alpha, beta) from a node's score history relative to median."""
        if not node.scores:
            # Uninformative prior
            return 1.0, 1.0
        wins = sum(1 for s in node.scores if s >= median)
        losses = len(node.scores) - wins
        alpha = 1.0 + wins
        beta = 1.0 + losses
        return alpha, beta
