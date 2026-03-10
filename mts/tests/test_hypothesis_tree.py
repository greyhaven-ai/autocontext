"""Tests for HypothesisTree (MTS-78)."""

from __future__ import annotations

import random

import pytest

from mts.loop.hypothesis_tree import HypothesisTree


class TestHypothesisTreeAdd:
    def test_add_single_hypothesis(self) -> None:
        tree = HypothesisTree(max_hypotheses=4)
        node = tree.add({"flag_x": 3, "flag_y": 4})
        assert node.id in tree.nodes
        assert node.strategy == {"flag_x": 3, "flag_y": 4}
        assert node.elo == 1500.0
        assert node.parent_id is None

    def test_add_with_parent(self) -> None:
        tree = HypothesisTree()
        parent = tree.add({"flag_x": 1})
        child = tree.add({"flag_x": 2}, parent_id=parent.id, generation=1)
        assert child.parent_id == parent.id
        assert child.generation == 1
        assert tree.size() == 2

    def test_add_auto_prunes_past_max(self) -> None:
        tree = HypothesisTree(max_hypotheses=3)
        nodes = []
        for i in range(3):
            n = tree.add({"v": i})
            tree.update(n.id, [float(i) * 0.1], elo=1500.0 + i * 10)
            nodes.append(n)
        # Adding a 4th should prune the lowest-Elo node
        tree.add({"v": 99})
        assert tree.size() == 3
        # Lowest Elo (nodes[0]) should be pruned
        assert nodes[0].id not in tree.nodes

    def test_add_preserves_new_node_when_existing_elos_are_higher(self) -> None:
        tree = HypothesisTree(max_hypotheses=3)
        nodes = []
        for i, elo in enumerate([1600.0, 1650.0, 1700.0]):
            n = tree.add({"v": i})
            tree.update(n.id, [0.8], elo=elo)
            nodes.append(n)

        new_node = tree.add({"v": 99})
        assert tree.size() == 3
        assert new_node.id in tree.nodes
        assert nodes[0].id not in tree.nodes


class TestHypothesisTreeSelect:
    def test_select_single_node(self) -> None:
        tree = HypothesisTree()
        node = tree.add({"v": 1})
        assert tree.select() is node

    def test_select_from_empty_raises(self) -> None:
        tree = HypothesisTree()
        with pytest.raises(ValueError, match="empty"):
            tree.select()

    def test_select_deterministic_with_seed(self) -> None:
        tree = HypothesisTree()
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.update(n1.id, [0.9, 0.8, 0.85], elo=1600.0)
        tree.update(n2.id, [0.1, 0.2, 0.15], elo=1400.0)
        # Same seed should produce same selection
        rng1 = random.Random(42)
        rng2 = random.Random(42)
        sel1 = tree.select(rng=rng1)
        sel2 = tree.select(rng=rng2)
        assert sel1.id == sel2.id

    def test_select_favours_higher_scoring_node(self) -> None:
        tree = HypothesisTree(temperature=0.01)  # Low temp = exploit
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.update(n1.id, [0.9] * 20, elo=1700.0)
        tree.update(n2.id, [0.1] * 20, elo=1300.0)
        # With very low temperature, should almost always pick n1
        rng = random.Random(123)
        selections = [tree.select(rng=rng).id for _ in range(50)]
        assert selections.count(n1.id) > 40  # Strong majority

    def test_select_with_no_scores_uniform(self) -> None:
        tree = HypothesisTree()
        tree.add({"v": 1})
        tree.add({"v": 2})
        tree.add({"v": 3})
        # No scores -> uninformative prior Beta(1,1) -> uniform
        rng = random.Random(99)
        ids = {tree.select(rng=rng).id for _ in range(30)}
        # Should select at least 2 different nodes with uniform prior
        assert len(ids) >= 2


class TestHypothesisTreeUpdate:
    def test_update_scores_and_elo(self) -> None:
        tree = HypothesisTree()
        node = tree.add({"v": 1})
        tree.update(node.id, [0.8, 0.9], elo=1600.0)
        assert tree.nodes[node.id].scores == [0.8, 0.9]
        assert tree.nodes[node.id].elo == 1600.0
        assert tree.nodes[node.id].refinement_count == 1

    def test_update_accumulates_scores(self) -> None:
        tree = HypothesisTree()
        node = tree.add({"v": 1})
        tree.update(node.id, [0.5], elo=1500.0)
        tree.update(node.id, [0.7, 0.8], elo=1550.0)
        assert tree.nodes[node.id].scores == [0.5, 0.7, 0.8]
        assert tree.nodes[node.id].refinement_count == 2

    def test_update_nonexistent_raises(self) -> None:
        tree = HypothesisTree()
        with pytest.raises(KeyError):
            tree.update("nonexistent", [0.5], elo=1500.0)


class TestHypothesisTreePrune:
    def test_prune_removes_lowest_elo(self) -> None:
        tree = HypothesisTree(max_hypotheses=5)
        nodes = [tree.add({"v": i}) for i in range(4)]
        for i, n in enumerate(nodes):
            tree.update(n.id, [i * 0.25], elo=1400.0 + i * 50)
        tree.max_hypotheses = 2
        removed = tree.prune()
        assert len(removed) == 2
        assert tree.size() == 2
        # The two lowest-Elo should be removed
        remaining_elos = [n.elo for n in tree.nodes.values()]
        assert min(remaining_elos) >= 1500.0

    def test_prune_noop_under_limit(self) -> None:
        tree = HypothesisTree(max_hypotheses=5)
        tree.add({"v": 1})
        tree.add({"v": 2})
        removed = tree.prune()
        assert removed == []
        assert tree.size() == 2

    def test_prune_raises_when_protected_ids_block_removal(self) -> None:
        tree = HypothesisTree(max_hypotheses=2)
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.max_hypotheses = 1
        with pytest.raises(ValueError, match="Not enough non-protected nodes"):
            tree.prune(protected_ids={n1.id, n2.id})


class TestHypothesisTreeBest:
    def test_best_returns_highest_elo(self) -> None:
        tree = HypothesisTree()
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.update(n1.id, [0.3], elo=1450.0)
        tree.update(n2.id, [0.8], elo=1600.0)
        assert tree.best() is n2

    def test_best_on_empty_raises(self) -> None:
        tree = HypothesisTree()
        with pytest.raises(ValueError, match="empty"):
            tree.best()


class TestHypothesisTreeConverged:
    def test_converged_single_node(self) -> None:
        tree = HypothesisTree()
        tree.add({"v": 1})
        assert tree.converged() is True

    def test_converged_similar_elos(self) -> None:
        tree = HypothesisTree()
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.update(n1.id, [0.5], elo=1500.0)
        tree.update(n2.id, [0.5], elo=1501.0)
        assert tree.converged(threshold=0.01) is True

    def test_not_converged_divergent_elos(self) -> None:
        tree = HypothesisTree()
        n1 = tree.add({"v": 1})
        n2 = tree.add({"v": 2})
        tree.update(n1.id, [0.1], elo=1200.0)
        tree.update(n2.id, [0.9], elo=1800.0)
        assert tree.converged(threshold=0.01) is False


class TestHypothesisTreeInit:
    def test_max_hypotheses_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            HypothesisTree(max_hypotheses=0)

    def test_temperature_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            HypothesisTree(temperature=0.0)
