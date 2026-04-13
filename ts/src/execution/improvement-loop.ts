/**
 * Multi-step improvement loop for agent tasks.
 * Port of autocontext/src/autocontext/execution/improvement_loop.py
 */

import type { AgentTaskInterface, AgentTaskResult, ImprovementResult } from "../types/index.js";
import { cleanRevisionOutput } from "./output-cleaner.js";
import { isImproved, isParseFailure } from "./improvement-loop-detection.js";
import {
  applyScoreDeltaPolicy,
  buildRevisionFeedbackResult,
  evaluatePlateauState,
  evaluateThresholdState,
  updateDimensionTrajectory,
} from "./improvement-loop-policy.js";
import { buildImprovementResult, buildRoundResult } from "./improvement-loop-result.js";

export { isImproved, isParseFailure } from "./improvement-loop-detection.js";

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
  #task: AgentTaskInterface;
  #maxRounds: number;
  #qualityThreshold: number;
  #minRounds: number;
  #maxScoreDelta: number;
  #capScoreJumps: boolean;
  #dimensionThreshold: number | null;

  constructor(opts: ImprovementLoopOpts) {
    this.#task = opts.task;
    this.#maxRounds = Math.max(1, opts.maxRounds ?? 5);
    this.#qualityThreshold = opts.qualityThreshold ?? 0.9;
    this.#minRounds = Math.max(1, opts.minRounds ?? 1);
    this.#maxScoreDelta = opts.maxScoreDelta ?? 0.5;
    this.#capScoreJumps = opts.capScoreJumps ?? false;
    this.#dimensionThreshold = opts.dimensionThreshold ?? null;
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
    const rounds = [] as ReturnType<typeof buildRoundResult>[];
    let currentOutput = opts.initialOutput;
    let bestOutput = opts.initialOutput;
    let bestScore = 0;
    let bestRound = 1;
    let judgeFailures = 0;
    let lastGoodResult: ReturnType<typeof buildRoundResult> | null = null;
    let consecutiveFailures = 0;
    const maxConsecutiveFailures = 3;
    let totalInternalRetries = 0;
    let terminationReason: ImprovementResult["terminationReason"] = "max_rounds";
    const dimensionTrajectory: Record<string, number[]> = {};
    let thresholdMetRound: number | null = null;
    let pinnedDimensions: string[] | undefined;
    let prevValidScore: number | null = null;
    let plateauCount = 0;

    for (let roundNum = 1; roundNum <= this.#maxRounds; roundNum++) {
      const roundStart = performance.now();
      const result = await this.#task.evaluateOutput(currentOutput, opts.state, {
        referenceContext: opts.referenceContext,
        requiredConcepts: opts.requiredConcepts,
        calibrationExamples: opts.calibrationExamples,
        pinnedDimensions,
      });
      judgeCalls += 1;
      const roundMs = Math.round(performance.now() - roundStart);
      totalInternalRetries += result.internalRetries ?? 0;

      const failed = isParseFailure(result.score, result.reasoning);
      const roundResult = buildRoundResult({
        roundNumber: roundNum,
        output: currentOutput,
        result,
        judgeFailed: failed,
        roundDurationMs: roundMs,
      });
      rounds.push(roundResult);

      if (failed) {
        judgeFailures += 1;
        consecutiveFailures += 1;
        thresholdMetRound = null;

        if (consecutiveFailures >= maxConsecutiveFailures) {
          terminationReason = "consecutive_failures";
          break;
        }

        if (roundNum < this.#maxRounds && lastGoodResult && this.#task.reviseOutput) {
          const feedbackResult: AgentTaskResult = {
            score: lastGoodResult.score,
            reasoning: lastGoodResult.reasoning,
            dimensionScores: lastGoodResult.dimensionScores,
            internalRetries: 0,
          };
          const revised = await this.#task.reviseOutput(currentOutput, feedbackResult, opts.state);
          const cleaned = cleanRevisionOutput(revised);
          if (cleaned !== currentOutput) {
            currentOutput = cleaned;
          }
        }
        continue;
      }

      consecutiveFailures = 0;
      const previousValidRound = lastGoodResult;
      lastGoodResult = roundResult;

      if (pinnedDimensions === undefined && Object.keys(result.dimensionScores).length > 0) {
        pinnedDimensions = Object.keys(result.dimensionScores).sort();
      }

      updateDimensionTrajectory(dimensionTrajectory, result.dimensionScores);

      const scoreDeltaPolicy = applyScoreDeltaPolicy({
        score: result.score,
        prevValidScore,
        maxScoreDelta: this.#maxScoreDelta,
        capScoreJumps: this.#capScoreJumps,
        roundNum,
      });
      if (scoreDeltaPolicy.warning) {
        console.warn(scoreDeltaPolicy.warning);
      }
      let effectiveScore = scoreDeltaPolicy.effectiveScore;

      if (effectiveScore > 0 && this.#task.verifyFacts) {
        const verifyResult = await this.#task.verifyFacts(currentOutput, opts.state);
        if (verifyResult && !verifyResult.verified) {
          const issues = verifyResult.issues ?? [];
          if (issues.length > 0) {
            roundResult.reasoning += ` | Fact-check issues: ${issues.join("; ")}`;
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

      const plateauState = evaluatePlateauState({
        prevValidScore,
        score: result.score,
        plateauCount,
        roundNum,
        minRounds: this.#minRounds,
      });
      plateauCount = plateauState.plateauCount;
      if (plateauState.shouldStop) {
        terminationReason = "plateau_stall";
        break;
      }
      prevValidScore = result.score;

      const thresholdState = evaluateThresholdState({
        effectiveScore,
        qualityThreshold: this.#qualityThreshold,
        roundNum,
        minRounds: this.#minRounds,
        maxRounds: this.#maxRounds,
        thresholdMetRound,
        dimensionScores: result.dimensionScores,
        dimensionThreshold: this.#dimensionThreshold,
      });
      thresholdMetRound = thresholdState.thresholdMetRound;
      if (thresholdState.shouldStop) {
        terminationReason = "threshold_met";
        return buildImprovementResult({
          rounds,
          bestOutput,
          bestScore,
          bestRound,
          totalRounds: roundNum,
          metThreshold: thresholdState.metThreshold,
          judgeFailures,
          terminationReason,
          dimensionTrajectory,
          totalInternalRetries,
          durationMs: Math.round(performance.now() - loopStart),
          judgeCalls,
        });
      }

      if (roundNum < this.#maxRounds && this.#task.reviseOutput) {
        const revisionFeedback =
          roundNum > 1
            ? buildRevisionFeedbackResult({
                result,
                previousValidRound: previousValidRound ?? undefined,
              })
            : result;
        const revised = await this.#task.reviseOutput(currentOutput, revisionFeedback, opts.state);
        const cleaned = cleanRevisionOutput(revised);
        if (cleaned === currentOutput) {
          terminationReason = "unchanged_output";
          break;
        }
        currentOutput = cleaned;
      }
    }

    return buildImprovementResult({
      rounds,
      bestOutput,
      bestScore,
      bestRound,
      metThreshold: false,
      judgeFailures,
      terminationReason,
      dimensionTrajectory,
      totalInternalRetries,
      durationMs: Math.round(performance.now() - loopStart),
      judgeCalls,
    });
  }
}
