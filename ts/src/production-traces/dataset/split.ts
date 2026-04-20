/**
 * Deterministic train/eval/holdout splitting for dataset generation
 * (spec §8.2 split rule + spec §10.1 property test P2).
 *
 * Given the same seed, the same input ordering, and the same ratios, the
 * partition is identical across runs. This is the foundation of P1's
 * byte-identity guarantee.
 *
 * The partitioner uses a small seeded PRNG (mulberry32 on a 32-bit seed).
 * The spec does not require a specific algorithm — only determinism — but
 * mulberry32 is 20 lines, well-tested, and avoids a dependency.
 */
import type { SplitRule } from "./types.js";

export interface SplitRatios {
  readonly train: number;
  readonly eval: number;
  readonly holdout: number;
}

export interface SplitPartitions<T> {
  readonly train: readonly T[];
  readonly eval: readonly T[];
  readonly holdout: readonly T[];
}

export function partitionByRatios<T>(
  items: readonly T[],
  ratios: SplitRatios,
  seed: number,
  shuffle: boolean,
): SplitPartitions<T> {
  validateRatios(ratios);
  const sequence = shuffle ? seededShuffle(items, seed) : items.slice();

  const total = sequence.length;
  // Use floor for train + eval; the remainder goes to holdout to guarantee
  // exact partition (no dropped items).
  const trainCount = Math.floor(total * ratios.train);
  const evalCount = Math.floor(total * ratios.eval);
  const holdoutCount = total - trainCount - evalCount;

  const train = sequence.slice(0, trainCount);
  const evalSet = sequence.slice(trainCount, trainCount + evalCount);
  const holdout = sequence.slice(trainCount + evalCount, trainCount + evalCount + holdoutCount);

  return { train, eval: evalSet, holdout };
}

export function partitionByRule<T>(
  items: readonly T[],
  rule: SplitRule,
): SplitPartitions<T> {
  return partitionByRatios(items, {
    train: rule.train,
    eval: rule.eval,
    holdout: rule.holdout,
  }, rule.seed ?? 0, rule.shuffle ?? true);
}

function validateRatios(r: SplitRatios): void {
  if (r.train < 0 || r.eval < 0 || r.holdout < 0) {
    throw new Error(`split ratios must be non-negative (got ${JSON.stringify(r)})`);
  }
  const sum = r.train + r.eval + r.holdout;
  if (Math.abs(sum - 1.0) > 1e-9) {
    throw new Error(`split ratios must sum to 1.0 (got ${sum})`);
  }
}

// ---- Seeded shuffle --------------------------------------------------------

/**
 * Fisher–Yates shuffle driven by a seeded mulberry32 PRNG.
 *
 * Given the same seed and the same input ordering, the output is identical
 * across runs (property-tested as P2). Pure; does not mutate the input.
 */
export function seededShuffle<T>(items: readonly T[], seed: number): T[] {
  const copy = items.slice();
  const rng = mulberry32(seed | 0);
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    const tmp = copy[i];
    copy[i] = copy[j];
    copy[j] = tmp;
  }
  return copy;
}

function mulberry32(seed: number): () => number {
  let t = seed >>> 0;
  return () => {
    t = (t + 0x6d2b79f5) >>> 0;
    let x = t;
    x = Math.imul(x ^ (x >>> 15), x | 1);
    x ^= x + Math.imul(x ^ (x >>> 7), x | 61);
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}
