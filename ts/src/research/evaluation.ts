/**
 * Research A/B evaluation (AC-502 TS parity).
 */

import type { ResearchBrief } from "./consultation.js";

export type ScoreFn = (text: string) => number;

export class EvalResult {
  readonly baselineScore: number;
  readonly augmentedScore: number;
  readonly improvement: number;
  readonly citationCoverage: number;
  readonly sampleSize: number;

  constructor(opts: {
    baselineScore?: number; augmentedScore?: number; improvement?: number;
    citationCoverage?: number; sampleSize?: number;
  }) {
    this.baselineScore = opts.baselineScore ?? 0;
    this.augmentedScore = opts.augmentedScore ?? 0;
    this.improvement = opts.improvement ?? 0;
    this.citationCoverage = opts.citationCoverage ?? 0;
    this.sampleSize = opts.sampleSize ?? 1;
  }

  get isImprovement(): boolean { return this.improvement > 0; }
  get relativeGain(): number {
    if (this.baselineScore === 0) return this.improvement > 0 ? Infinity : 0;
    return this.improvement / this.baselineScore;
  }
}

export class BatchSummary {
  readonly sampleSize: number;
  readonly avgBaseline: number;
  readonly avgAugmented: number;
  readonly avgImprovement: number;
  readonly winRate: number;

  constructor(opts?: { sampleSize?: number; avgBaseline?: number; avgAugmented?: number; avgImprovement?: number; winRate?: number }) {
    this.sampleSize = opts?.sampleSize ?? 0;
    this.avgBaseline = opts?.avgBaseline ?? 0;
    this.avgAugmented = opts?.avgAugmented ?? 0;
    this.avgImprovement = opts?.avgImprovement ?? 0;
    this.winRate = opts?.winRate ?? 0;
  }
}

function citationCoverage(brief: ResearchBrief, text: string): number {
  if (!brief.uniqueCitations.length) return 0;
  const mentioned = brief.uniqueCitations.filter((c) => text.includes(c.source)).length;
  return mentioned / brief.uniqueCitations.length;
}

interface EvalPairInput {
  brief: ResearchBrief;
  baseline: string;
  augmented: string;
  scoreFn: ScoreFn;
}

export class ResearchEvaluator {
  evaluatePair(opts: EvalPairInput): EvalResult {
    const bs = opts.scoreFn(opts.baseline);
    const as_ = opts.scoreFn(opts.augmented);
    return new EvalResult({
      baselineScore: bs,
      augmentedScore: as_,
      improvement: as_ - bs,
      citationCoverage: citationCoverage(opts.brief, opts.augmented),
    });
  }

  evaluateBatch(opts: { pairs: Array<{ brief: ResearchBrief; baseline: string; augmented: string }>; scoreFn: ScoreFn }): BatchSummary {
    if (!opts.pairs.length) return new BatchSummary();
    const results = opts.pairs.map((p) => this.evaluatePair({ ...p, scoreFn: opts.scoreFn }));
    const n = results.length;
    return new BatchSummary({
      sampleSize: n,
      avgBaseline: results.reduce((s, r) => s + r.baselineScore, 0) / n,
      avgAugmented: results.reduce((s, r) => s + r.augmentedScore, 0) / n,
      avgImprovement: results.reduce((s, r) => s + r.improvement, 0) / n,
      winRate: results.filter((r) => r.isImprovement).length / n,
    });
  }
}
