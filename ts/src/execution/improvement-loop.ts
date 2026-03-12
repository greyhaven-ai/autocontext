/**
 * Multi-step improvement loop for agent tasks.
 * Port of autocontext/src/autocontext/execution/improvement_loop.py
 */

import type {
  AgentTaskInterface,
  AgentTaskResult,
  RoundResult,
  ImprovementResult,
} from "../types/index.js";
import { cleanRevisionOutput } from "./output-cleaner.js";

const PARSE_FAILURE_MARKERS = [
  "no parseable score found",
  "missing JUDGE_RESULT markers",
  "invalid JSON",
  "Failed to parse judge response",
] as const;

const PLATEAU_EPSILON = 0.01;
const NEAR_THRESHOLD_MARGIN = 0.02;
const PLATEAU_PATIENCE = 2;
const DIMENSION_DELTA_THRESHOLD = 0.05;

export function isParseFailure(score: number, reasoning: string): boolean {
  if (score > 0) return false;
  return PARSE_FAILURE_MARKERS.some((m) => reasoning.includes(m));
}

export function isImproved(rounds: RoundResult[]): boolean {
  const valid = rounds.filter((r) => !r.judgeFailed);
  if (valid.length < 2) return false;
  return valid[valid.length - 1].score > valid[0].score;
}

export interface ImprovementLoopOpts {
  task: AgentTaskInterface;
  maxRounds?: number;
  qualityThreshold?: number;
  minRounds?: number;
  maxScoreDelta?: number;
  capScoreJumps?: boolean;
  dimensionThreshold?: number;
}

export class ImprovementLoop {
  private task: AgentTaskInterface;
  private maxRounds: number;
  private qualityThreshold: number;
  private minRounds: number;
  private maxScoreDelta: number;
  private capScoreJumps: boolean;
  private dimensionThreshold: number | null;

  constructor(opts: ImprovementLoopOpts) {
    this.task = opts.task;
    this.maxRounds = Math.max(1, opts.maxRounds ?? 5);
    this.qualityThreshold = opts.qualityThreshold ?? 0.9;
    this.minRounds = Math.max(1, opts.minRounds ?? 1);
    this.maxScoreDelta = opts.maxScoreDelta ?? 0.5;
    this.capScoreJumps = opts.capScoreJumps ?? false;
    this.dimensionThreshold = opts.dimensionThreshold ?? null;
  }

  async run(opts: {
    initialOutput: string;
    state: Record<string, unknown>;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): Promise<ImprovementResult> {
    const loopStart = performance.now();
    let judgeCalls = 0;
    const rounds: RoundResult[] = [];
    let currentOutput = opts.initialOutput;
    let bestOutput = opts.initialOutput;
    let bestScore = 0;
    let bestRound = 1;
    let judgeFailures = 0;
    let lastGoodResult: RoundResult | null = null;
    let consecutiveFailures = 0;
    const maxConsecutiveFailures = 3;
    let totalInternalRetries = 0;
    let terminationReason: ImprovementResult["terminationReason"] = "max_rounds";
    const dimensionTrajectory: Record<string, number[]> = {};
    let thresholdMetRound: number | null = null;

    // Dimension pinning: lock dimension names after first successful evaluation
    let pinnedDimensions: string[] | undefined;

    // Plateau detection state
    let prevValidScore: number | null = null;
    let plateauCount = 0;

    for (let roundNum = 1; roundNum <= this.maxRounds; roundNum++) {
      const roundStart = performance.now();
      const result = await this.task.evaluateOutput(currentOutput, opts.state, {
        referenceContext: opts.referenceContext,
        requiredConcepts: opts.requiredConcepts,
        calibrationExamples: opts.calibrationExamples,
        pinnedDimensions,
      });
      judgeCalls++;
      const roundMs = Math.round(performance.now() - roundStart);
      totalInternalRetries += result.internalRetries ?? 0;

      const failed = isParseFailure(result.score, result.reasoning);

      const roundResult: RoundResult = {
        roundNumber: roundNum,
        output: currentOutput,
        score: result.score,
        reasoning: result.reasoning,
        dimensionScores: result.dimensionScores,
        isRevision: roundNum > 1,
        judgeFailed: failed,
        worstDimension: undefined,
        worstDimensionScore: undefined,
        roundDurationMs: roundMs,
      };
      rounds.push(roundResult);

      if (failed) {
        judgeFailures++;
        consecutiveFailures++;
        thresholdMetRound = null; // Reset stability tracking on parse failure

        if (consecutiveFailures >= maxConsecutiveFailures) {
          terminationReason = "consecutive_failures";
          break;
        }

        if (roundNum < this.maxRounds) {
          if (lastGoodResult && this.task.reviseOutput) {
            const feedbackResult: AgentTaskResult = {
              score: lastGoodResult.score,
              reasoning: lastGoodResult.reasoning,
              dimensionScores: lastGoodResult.dimensionScores,
              internalRetries: 0,
            };
            const revised = await this.task.reviseOutput(
              currentOutput,
              feedbackResult,
              opts.state,
            );
            const cleaned = cleanRevisionOutput(revised);
            if (cleaned !== currentOutput) currentOutput = cleaned;
          }
          // else: no prior feedback, just re-judge next round
        }
        continue;
      }

      // Successful evaluation
      consecutiveFailures = 0;
      lastGoodResult = roundResult;

      // Compute worst dimension for this round
      const dimEntries = Object.entries(result.dimensionScores);
      if (dimEntries.length > 0) {
        let worstDim = dimEntries[0][0];
        let worstScore = dimEntries[0][1];
        for (let i = 1; i < dimEntries.length; i++) {
          const [dim, dimScore] = dimEntries[i];
          if (dimScore < worstScore) {
            worstDim = dim;
            worstScore = dimScore;
          }
        }
        roundResult.worstDimension = worstDim;
        roundResult.worstDimensionScore = worstScore;
      }

      // Pin dimension names after first successful evaluation
      if (pinnedDimensions === undefined && Object.keys(result.dimensionScores).length > 0) {
        pinnedDimensions = Object.keys(result.dimensionScores).sort();
      }

      // Build dimension trajectory from valid rounds
      for (const [dim, dimScore] of Object.entries(result.dimensionScores)) {
        if (!(dim in dimensionTrajectory)) {
          dimensionTrajectory[dim] = [];
        }
        dimensionTrajectory[dim].push(dimScore);
      }

      let effectiveScore = result.score;

      // Max score delta warning + optional cap
      if (prevValidScore !== null) {
        const delta = Math.abs(result.score - prevValidScore);
        if (delta > this.maxScoreDelta) {
          console.warn(
            `Score jump of ${delta.toFixed(3)} exceeds maxScoreDelta ${this.maxScoreDelta} ` +
            `(round ${roundNum}: ${prevValidScore.toFixed(3)} -> ${result.score.toFixed(3)})`,
          );
          if (this.capScoreJumps) {
            effectiveScore = Math.max(0, result.score > prevValidScore
              ? prevValidScore + this.maxScoreDelta
              : prevValidScore - this.maxScoreDelta);
          }
        }
      }

      // Reference verification hook — apply score penalty if facts unverified
      if (effectiveScore > 0 && this.task.verifyFacts) {
        const verifyResult = await this.task.verifyFacts(currentOutput, opts.state);
        if (verifyResult && !verifyResult.verified) {
          const issues = verifyResult.issues ?? [];
          if (issues.length > 0) {
            roundResult.reasoning += " | Fact-check issues: " + issues.join("; ");
          }
          effectiveScore = Math.max(0, effectiveScore * 0.9);
          roundResult.score = effectiveScore;
        }
      }

      if (effectiveScore > bestScore) {
        bestScore = effectiveScore;
        bestOutput = currentOutput;
        bestRound = roundNum;
      }

      // Plateau detection (only after minRounds satisfied)
      if (prevValidScore !== null && Math.abs(result.score - prevValidScore) < PLATEAU_EPSILON) {
        plateauCount++;
        if (plateauCount >= PLATEAU_PATIENCE && roundNum >= this.minRounds) {
          terminationReason = "plateau_stall";
          break;
        }
      } else {
        plateauCount = 0;
      }
      prevValidScore = result.score;

      // Dimension threshold gate: all dimensions must meet minimum
      let dimsOk = true;
      if (this.dimensionThreshold !== null && Object.keys(result.dimensionScores).length > 0) {
        dimsOk = Object.values(result.dimensionScores).every(
          (v) => v >= this.dimensionThreshold!,
        );
      }

      if (effectiveScore >= this.qualityThreshold && roundNum >= this.minRounds && dimsOk) {
        const nearThreshold =
          effectiveScore < this.qualityThreshold + NEAR_THRESHOLD_MARGIN;

        if (thresholdMetRound !== null) {
          // Threshold was met on a previous round too — confirmed stable
          terminationReason = "threshold_met";
          const durationMs = Math.round(performance.now() - loopStart);
          return {
            rounds,
            bestOutput,
            bestScore,
            bestRound,
            totalRounds: roundNum,
            metThreshold: true,
            judgeFailures,
            terminationReason,
            dimensionTrajectory,
            totalInternalRetries,
            durationMs,
            judgeCalls,
          };
        }

        if (nearThreshold && roundNum < this.maxRounds) {
          // Score barely meets threshold — continue to confirm stability
          thresholdMetRound = roundNum;
        } else {
          // Clearly above threshold — stop immediately
          terminationReason = "threshold_met";
          const durationMs = Math.round(performance.now() - loopStart);
          return {
            rounds,
            bestOutput,
            bestScore,
            bestRound,
            totalRounds: roundNum,
            metThreshold: true,
            judgeFailures,
            terminationReason,
            dimensionTrajectory,
            totalInternalRetries,
            durationMs,
            judgeCalls,
          };
        }
      } else {
        // Score dropped below threshold after previously meeting it
        thresholdMetRound = null;
      }

      if (roundNum < this.maxRounds && this.task.reviseOutput) {
        // Enrich feedback with dimension scores + regression warnings (MTS-41)
        let revisionResult: AgentTaskResult = result;
        if (Object.keys(result.dimensionScores).length > 0 && roundNum > 1) {
          const prevValid = rounds.slice(0, -1).filter((r) => !r.judgeFailed);
          const prevDims = prevValid.length > 0 ? prevValid[prevValid.length - 1].dimensionScores : {};
          const dimLines: string[] = [];
          for (const [dim, dscore] of Object.entries(result.dimensionScores).sort()) {
            let line = `  - ${dim}: ${dscore.toFixed(2)}`;
            if (dim in prevDims) {
              const delta = dscore - prevDims[dim];
              if (delta < -DIMENSION_DELTA_THRESHOLD) {
                line += ` (REGRESSION from ${prevDims[dim].toFixed(2)} -- preserve this dimension)`;
              } else if (delta > DIMENSION_DELTA_THRESHOLD) {
                line += ` (improved from ${prevDims[dim].toFixed(2)})`;
              }
            }
            dimLines.push(line);
          }
          const dimAnnotation = "\n\nDimension Scores:\n" + dimLines.join("\n");
          revisionResult = {
            score: result.score,
            reasoning: result.reasoning + dimAnnotation,
            dimensionScores: result.dimensionScores,
            internalRetries: result.internalRetries,
          };
        }
        const revised = await this.task.reviseOutput(
          currentOutput,
          revisionResult,
          opts.state,
        );
        const cleaned = cleanRevisionOutput(revised);
        if (cleaned === currentOutput) {
          terminationReason = "unchanged_output";
          break;
        }
        currentOutput = cleaned;
      }
    }

    const durationMs = Math.round(performance.now() - loopStart);
    return {
      rounds,
      bestOutput,
      bestScore,
      bestRound,
      totalRounds: rounds.length,
      metThreshold: false,
      judgeFailures,
      terminationReason,
      dimensionTrajectory,
      totalInternalRetries,
      durationMs,
      judgeCalls,
    };
  }
}
