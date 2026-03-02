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

const PARSE_FAILURE_MARKERS = [
  "no parseable score found",
  "missing JUDGE_RESULT markers",
  "invalid JSON",
  "Failed to parse judge response",
] as const;

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
}

export class ImprovementLoop {
  private task: AgentTaskInterface;
  private maxRounds: number;
  private qualityThreshold: number;

  constructor(opts: ImprovementLoopOpts) {
    this.task = opts.task;
    this.maxRounds = Math.max(1, opts.maxRounds ?? 5);
    this.qualityThreshold = opts.qualityThreshold ?? 0.9;
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

        if (consecutiveFailures >= maxConsecutiveFailures) break;

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
            if (revised !== currentOutput) currentOutput = revised;
          }
          // else: no prior feedback, just re-judge next round
        }
        continue;
      }

      // Successful evaluation
      consecutiveFailures = 0;
      lastGoodResult = roundResult;

      if (result.score > bestScore) {
        bestScore = result.score;
        bestOutput = currentOutput;
        bestRound = roundNum;
      }

      if (result.score >= this.qualityThreshold) {
        return {
          rounds,
          bestOutput,
          bestScore,
          bestRound,
          totalRounds: roundNum,
          metThreshold: true,
          judgeFailures,
        };
      }

      if (roundNum < this.maxRounds && this.task.reviseOutput) {
        const revised = await this.task.reviseOutput(
          currentOutput,
          result,
          opts.state,
        );
        if (revised === currentOutput) break;
        currentOutput = revised;
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
    };
  }
}
