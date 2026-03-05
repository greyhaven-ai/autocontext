/**
 * Multi-step improvement loop for agent tasks.
 * Port of mts/src/mts/execution/improvement_loop.py
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
const PLATEAU_PATIENCE = 2;

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
}

export class ImprovementLoop {
  private task: AgentTaskInterface;
  private maxRounds: number;
  private qualityThreshold: number;
  private minRounds: number;
  private maxScoreDelta: number;
  private capScoreJumps: boolean;

  constructor(opts: ImprovementLoopOpts) {
    this.task = opts.task;
    this.maxRounds = Math.max(1, opts.maxRounds ?? 5);
    this.qualityThreshold = opts.qualityThreshold ?? 0.9;
    this.minRounds = Math.max(1, opts.minRounds ?? 1);
    this.maxScoreDelta = opts.maxScoreDelta ?? 0.5;
    this.capScoreJumps = opts.capScoreJumps ?? false;
  }

  async run(opts: {
    initialOutput: string;
    state: Record<string, unknown>;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): Promise<ImprovementResult> {
    const rounds: RoundResult[] = [];
    let currentOutput = opts.initialOutput;
    let bestOutput = opts.initialOutput;
    let bestScore = 0;
    let bestRound = 1;
    let judgeFailures = 0;
    let lastGoodResult: RoundResult | null = null;
    let consecutiveFailures = 0;
    const maxConsecutiveFailures = 3;
    let terminationReason: ImprovementResult["terminationReason"] = "max_rounds";
    const dimensionTrajectory: Record<string, number[]> = {};

    // Plateau detection state
    let prevValidScore: number | null = null;
    let plateauCount = 0;

    for (let roundNum = 1; roundNum <= this.maxRounds; roundNum++) {
      const result = await this.task.evaluateOutput(currentOutput, opts.state, {
        referenceContext: opts.referenceContext,
        requiredConcepts: opts.requiredConcepts,
        calibrationExamples: opts.calibrationExamples,
      });

      const failed = isParseFailure(result.score, result.reasoning);

      const roundResult: RoundResult = {
        roundNumber: roundNum,
        output: currentOutput,
        score: result.score,
        reasoning: result.reasoning,
        dimensionScores: result.dimensionScores,
        isRevision: roundNum > 1,
        judgeFailed: failed,
      };
      rounds.push(roundResult);

      if (failed) {
        judgeFailures++;
        consecutiveFailures++;

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

      if (effectiveScore >= this.qualityThreshold && roundNum >= this.minRounds) {
        terminationReason = "threshold_met";
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
        };
      }

      if (roundNum < this.maxRounds && this.task.reviseOutput) {
        const revised = await this.task.reviseOutput(
          currentOutput,
          result,
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
    };
  }
}
