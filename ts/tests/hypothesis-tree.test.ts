/**
 * Tests for HypothesisTree — mirrors Python test_hypothesis_tree.py
 */

import { describe, it, expect } from "vitest";
import { HypothesisTree, HypothesisNodeSchema } from "../src/loop/hypothesis-tree.js";

// Seedable PRNG (xorshift32) for deterministic tests
function seededRng(seed: number): () => number {
  let state = seed;
  return () => {
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return (state >>> 0) / 0x100000000;
  };
}

describe("HypothesisTree", () => {
  describe("add", () => {
    it("should add a single hypothesis", () => {
      const tree = new HypothesisTree({ maxHypotheses: 4 });
      const node = tree.add({ flag_x: 3, flag_y: 4 });
      expect(tree.nodes.has(node.id)).toBe(true);
      expect(node.strategy).toEqual({ flag_x: 3, flag_y: 4 });
      expect(node.elo).toBe(1500.0);
      expect(node.parentId).toBeNull();
    });

    it("should add with parent", () => {
      const tree = new HypothesisTree();
      const parent = tree.add({ flag_x: 1 });
      const child = tree.add({ flag_x: 2 }, { parentId: parent.id, generation: 1 });
      expect(child.parentId).toBe(parent.id);
      expect(child.generation).toBe(1);
      expect(tree.size()).toBe(2);
    });

    it("should auto-prune past max", () => {
      const tree = new HypothesisTree({ maxHypotheses: 3 });
      const nodes = [];
      for (let i = 0; i < 3; i++) {
        const n = tree.add({ v: i });
        tree.update(n.id, [i * 0.1], 1500.0 + i * 10);
        nodes.push(n);
      }
      // Adding a 4th should prune the lowest-Elo node
      tree.add({ v: 99 });
      expect(tree.size()).toBe(3);
      // Lowest Elo (nodes[0]) should be pruned
      expect(tree.nodes.has(nodes[0]!.id)).toBe(false);
    });

    it("should preserve newly added node when existing elos are higher", () => {
      const tree = new HypothesisTree({ maxHypotheses: 3 });
      const nodes = [];
      for (let i = 0; i < 3; i++) {
        const n = tree.add({ v: i });
        tree.update(n.id, [0.8], 1600.0 + i * 50);
        nodes.push(n);
      }

      const newNode = tree.add({ v: 99 });
      expect(tree.size()).toBe(3);
      expect(tree.nodes.has(newNode.id)).toBe(true);
      expect(tree.nodes.has(nodes[0]!.id)).toBe(false);
    });
  });

  describe("select", () => {
    it("should select single node", () => {
      const tree = new HypothesisTree();
      const node = tree.add({ v: 1 });
      expect(tree.select()).toBe(node);
    });

    it("should throw on empty tree", () => {
      const tree = new HypothesisTree();
      expect(() => tree.select()).toThrow("empty");
    });

    it("should be deterministic with seed", () => {
      const tree = new HypothesisTree();
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      tree.update(n1.id, [0.9, 0.8, 0.85], 1600.0);
      tree.update(n2.id, [0.1, 0.2, 0.15], 1400.0);
      // Same seed should produce same selection
      const sel1 = tree.select(seededRng(42));
      const sel2 = tree.select(seededRng(42));
      expect(sel1.id).toBe(sel2.id);
    });

    it("should favour higher scoring node", () => {
      const tree = new HypothesisTree({ temperature: 0.01 });
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      tree.update(n1.id, Array(20).fill(0.9), 1700.0);
      tree.update(n2.id, Array(20).fill(0.1), 1300.0);
      // With very low temperature, should almost always pick n1
      const rng = seededRng(123);
      let n1Count = 0;
      for (let i = 0; i < 50; i++) {
        if (tree.select(rng).id === n1.id) n1Count++;
      }
      expect(n1Count).toBeGreaterThan(40);
    });

    it("should select with no scores (uniform)", () => {
      const tree = new HypothesisTree();
      tree.add({ v: 1 });
      tree.add({ v: 2 });
      tree.add({ v: 3 });
      // No scores -> uninformative prior Beta(1,1) -> uniform
      const rng = seededRng(99);
      const ids = new Set<string>();
      for (let i = 0; i < 30; i++) {
        ids.add(tree.select(rng).id);
      }
      // Should select at least 2 different nodes with uniform prior
      expect(ids.size).toBeGreaterThanOrEqual(2);
    });
  });

  describe("update", () => {
    it("should update scores and elo", () => {
      const tree = new HypothesisTree();
      const node = tree.add({ v: 1 });
      tree.update(node.id, [0.8, 0.9], 1600.0);
      const updated = tree.nodes.get(node.id)!;
      expect(updated.scores).toEqual([0.8, 0.9]);
      expect(updated.elo).toBe(1600.0);
      expect(updated.refinementCount).toBe(1);
    });

    it("should accumulate scores", () => {
      const tree = new HypothesisTree();
      const node = tree.add({ v: 1 });
      tree.update(node.id, [0.5], 1500.0);
      tree.update(node.id, [0.7, 0.8], 1550.0);
      const updated = tree.nodes.get(node.id)!;
      expect(updated.scores).toEqual([0.5, 0.7, 0.8]);
      expect(updated.refinementCount).toBe(2);
    });

    it("should throw on nonexistent node", () => {
      const tree = new HypothesisTree();
      expect(() => tree.update("nonexistent", [0.5], 1500.0)).toThrow();
    });
  });

  describe("prune", () => {
    it("should remove lowest elo", () => {
      const tree = new HypothesisTree({ maxHypotheses: 5 });
      const nodes = [];
      for (let i = 0; i < 4; i++) {
        const n = tree.add({ v: i });
        tree.update(n.id, [i * 0.25], 1400.0 + i * 50);
        nodes.push(n);
      }
      // Manually reduce max and prune
      (tree as { maxHypotheses: number }).maxHypotheses = 2;
      const removed = tree.prune();
      expect(removed.length).toBe(2);
      expect(tree.size()).toBe(2);
      // The two lowest-Elo should be removed
      const remainingElos = [...tree.nodes.values()].map((n) => n.elo);
      expect(Math.min(...remainingElos)).toBeGreaterThanOrEqual(1500.0);
    });

    it("should be noop under limit", () => {
      const tree = new HypothesisTree({ maxHypotheses: 5 });
      tree.add({ v: 1 });
      tree.add({ v: 2 });
      const removed = tree.prune();
      expect(removed).toEqual([]);
      expect(tree.size()).toBe(2);
    });

    it("should throw when protected ids block pruning", () => {
      const tree = new HypothesisTree({ maxHypotheses: 2 });
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      (tree as { maxHypotheses: number }).maxHypotheses = 1;
      expect(() => tree.prune(new Set([n1.id, n2.id]))).toThrow("Not enough non-protected nodes");
    });
  });

  describe("best", () => {
    it("should return highest elo", () => {
      const tree = new HypothesisTree();
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      tree.update(n1.id, [0.3], 1450.0);
      tree.update(n2.id, [0.8], 1600.0);
      expect(tree.best()).toBe(n2);
    });

    it("should throw on empty tree", () => {
      const tree = new HypothesisTree();
      expect(() => tree.best()).toThrow("empty");
    });
  });

  describe("converged", () => {
    it("should be true for single node", () => {
      const tree = new HypothesisTree();
      tree.add({ v: 1 });
      expect(tree.converged()).toBe(true);
    });

    it("should be true for similar elos", () => {
      const tree = new HypothesisTree();
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      tree.update(n1.id, [0.5], 1500.0);
      tree.update(n2.id, [0.5], 1501.0);
      expect(tree.converged(0.01)).toBe(true);
    });

    it("should be false for divergent elos", () => {
      const tree = new HypothesisTree();
      const n1 = tree.add({ v: 1 });
      const n2 = tree.add({ v: 2 });
      tree.update(n1.id, [0.1], 1200.0);
      tree.update(n2.id, [0.9], 1800.0);
      expect(tree.converged(0.01)).toBe(false);
    });
  });

  describe("init", () => {
    it("should reject max_hypotheses < 1", () => {
      expect(() => new HypothesisTree({ maxHypotheses: 0 })).toThrow();
    });

    it("should reject temperature <= 0", () => {
      expect(() => new HypothesisTree({ temperature: 0 })).toThrow();
    });
  });

  describe("schema", () => {
    it("should validate a hypothesis node", () => {
      const tree = new HypothesisTree();
      const node = tree.add({ v: 1 });
      const result = HypothesisNodeSchema.safeParse(node);
      expect(result.success).toBe(true);
    });
  });
});
