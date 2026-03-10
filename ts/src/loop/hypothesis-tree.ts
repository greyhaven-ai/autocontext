/**
 * HypothesisTree — multi-hypothesis strategy search with Thompson sampling.
 *
 * Port of mts/src/mts/loop/hypothesis_tree.py
 */

import { z } from "zod";
import { randomBytes } from "node:crypto";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export const HypothesisNodeSchema = z.object({
  id: z.string(),
  strategy: z.record(z.unknown()),
  parentId: z.string().nullable(),
  scores: z.array(z.number()),
  elo: z.number(),
  generation: z.number(),
  refinementCount: z.number(),
});

export type HypothesisNode = z.infer<typeof HypothesisNodeSchema>;

// ---------------------------------------------------------------------------
// Beta distribution sampling (using Jöhnk's algorithm)
// ---------------------------------------------------------------------------

/**
 * Sample from a Gamma(alpha, 1) distribution using the Marsaglia–Tsang method.
 * For alpha >= 1, uses the standard algorithm.
 * For alpha < 1, uses the Ahrens–Dieter boost.
 */
function gammaSample(alpha: number, rng: () => number): number {
  if (alpha < 1) {
    // Boost: Gamma(alpha) = Gamma(alpha+1) * U^(1/alpha)
    return gammaSample(alpha + 1, rng) * Math.pow(rng(), 1 / alpha);
  }

  // Marsaglia–Tsang method for alpha >= 1
  const d = alpha - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);

  for (;;) {
    let x: number;
    let v: number;

    do {
      // Generate standard normal via Box-Muller
      const u1 = rng();
      const u2 = rng();
      x = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
      v = 1 + c * x;
    } while (v <= 0);

    v = v * v * v;
    const u = rng();

    if (u < 1 - 0.0331 * (x * x) * (x * x)) {
      return d * v;
    }
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) {
      return d * v;
    }
  }
}

/**
 * Sample from Beta(alpha, beta) distribution.
 */
function betaSample(alpha: number, beta: number, rng: () => number): number {
  const x = gammaSample(alpha, rng);
  const y = gammaSample(beta, rng);
  return x / (x + y);
}

// ---------------------------------------------------------------------------
// HypothesisTree
// ---------------------------------------------------------------------------

export class HypothesisTree {
  readonly maxHypotheses: number;
  readonly temperature: number;
  readonly nodes: Map<string, HypothesisNode>;

  constructor(opts?: { maxHypotheses?: number; temperature?: number }) {
    const maxH = opts?.maxHypotheses ?? 8;
    const temp = opts?.temperature ?? 1.0;

    if (maxH < 1) {
      throw new Error("maxHypotheses must be >= 1");
    }
    if (temp <= 0) {
      throw new Error("temperature must be > 0");
    }

    this.maxHypotheses = maxH;
    this.temperature = temp;
    this.nodes = new Map();
  }

  /** Add a new hypothesis. Auto-prunes if exceeding maxHypotheses. */
  add(
    strategy: Record<string, unknown>,
    opts?: { parentId?: string | null; generation?: number },
  ): HypothesisNode {
    const nodeId = randomBytes(6).toString("hex");
    const node: HypothesisNode = {
      id: nodeId,
      strategy,
      parentId: opts?.parentId ?? null,
      scores: [],
      elo: 1500.0,
      generation: opts?.generation ?? 0,
      refinementCount: 0,
    };
    this.nodes.set(nodeId, node);

    if (this.nodes.size > this.maxHypotheses) {
      this.prune();
    }

    return node;
  }

  /**
   * Select next hypothesis to refine via Thompson sampling.
   *
   * Fits Beta(alpha, beta) per node from score history relative to the
   * median. Samples from each distribution and returns the highest sample.
   */
  select(rng?: () => number): HypothesisNode {
    if (this.nodes.size === 0) {
      throw new Error("Cannot select from empty tree");
    }
    if (this.nodes.size === 1) {
      return this.nodes.values().next().value!;
    }

    const r = rng ?? Math.random;
    const median = this.medianScore();

    let bestSample = -Infinity;
    let bestNode: HypothesisNode | null = null;

    for (const node of this.nodes.values()) {
      const [alpha, beta] = this.fitBeta(node, median);
      const scaledAlpha = Math.max(1.0, alpha / this.temperature);
      const scaledBeta = Math.max(1.0, beta / this.temperature);
      const sample = betaSample(scaledAlpha, scaledBeta, r);

      if (sample > bestSample) {
        bestSample = sample;
        bestNode = node;
      }
    }

    return bestNode!;
  }

  /** Update a node with new match results. */
  update(nodeId: string, scores: number[], elo: number): void {
    const node = this.nodes.get(nodeId);
    if (!node) {
      throw new Error(`Node ${nodeId} not found`);
    }
    node.scores.push(...scores);
    node.elo = elo;
    node.refinementCount += 1;
  }

  /** Remove lowest-Elo nodes to stay within maxHypotheses. Returns removed nodes. */
  prune(): HypothesisNode[] {
    if (this.nodes.size <= this.maxHypotheses) {
      return [];
    }
    const sorted = [...this.nodes.values()].sort((a, b) => a.elo - b.elo);
    const toRemove = this.nodes.size - this.maxHypotheses;
    const removed = sorted.slice(0, toRemove);
    for (const node of removed) {
      this.nodes.delete(node.id);
    }
    return removed;
  }

  /** Return the highest-Elo hypothesis. */
  best(): HypothesisNode {
    if (this.nodes.size === 0) {
      throw new Error("Cannot get best from empty tree");
    }
    let bestNode: HypothesisNode | null = null;
    for (const node of this.nodes.values()) {
      if (!bestNode || node.elo > bestNode.elo) {
        bestNode = node;
      }
    }
    return bestNode!;
  }

  /** Check if all hypotheses have similar Elo (within threshold ratio of mean). */
  converged(threshold = 0.01): boolean {
    if (this.nodes.size < 2) {
      return true;
    }
    const elos = [...this.nodes.values()].map((n) => n.elo);
    const meanElo = elos.reduce((a, b) => a + b, 0) / elos.length;
    if (meanElo === 0) {
      return true;
    }
    const maxDeviation = Math.max(...elos.map((e) => Math.abs(e - meanElo)));
    return maxDeviation / meanElo < threshold;
  }

  /** Number of hypotheses in the tree. */
  size(): number {
    return this.nodes.size;
  }

  // ---- Internal helpers ----

  private medianScore(): number {
    const allScores: number[] = [];
    for (const node of this.nodes.values()) {
      allScores.push(...node.scores);
    }
    if (allScores.length === 0) {
      return 0.5;
    }
    allScores.sort((a, b) => a - b);
    const n = allScores.length;
    if (n % 2 === 1) {
      return allScores[Math.floor(n / 2)]!;
    }
    return (allScores[n / 2 - 1]! + allScores[n / 2]!) / 2;
  }

  private fitBeta(node: HypothesisNode, median: number): [number, number] {
    if (node.scores.length === 0) {
      // Uninformative prior
      return [1.0, 1.0];
    }
    const wins = node.scores.filter((s) => s >= median).length;
    const losses = node.scores.length - wins;
    return [1.0 + wins, 1.0 + losses];
  }
}
