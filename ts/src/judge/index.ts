/**
 * LLM-based judge for evaluating agent task outputs.
 * Port of mts/src/mts/execution/judge.py
 */

import type { LLMProvider, JudgeResult } from "../types/index.js";
import { parseJudgeResponse } from "./parse.js";

export { parseJudgeResponse } from "./parse.js";
export type { ParsedJudge } from "./parse.js";

export interface LLMJudgeOpts {
  provider: LLMProvider;
  model: string;
  rubric: string;
  samples?: number;
  temperature?: number;
}

export class LLMJudge {
  private provider: LLMProvider;
  readonly model: string;
  readonly rubric: string;
  private samples: number;
  private temperature: number;

  constructor(opts: LLMJudgeOpts) {
    this.provider = opts.provider;
    this.model = opts.model;
    this.rubric = opts.rubric;
    this.samples = Math.max(1, opts.samples ?? 1);
    this.temperature = opts.temperature ?? 0;
  }

  async evaluate(opts: {
    taskPrompt: string;
    agentOutput: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): Promise<JudgeResult> {
    let systemPrompt =
      "You are an expert judge evaluating an AI agent's output. " +
      "Evaluate the output against the provided rubric. ";

    if (opts.referenceContext) {
      systemPrompt +=
        "You have been provided with authoritative reference context. " +
        "You MUST evaluate factual accuracy against this reference. " +
        "Any claims that contradict the reference context should be penalized heavily. " +
        'Include a \'factual_accuracy\' dimension in your scoring. ';
    }

    systemPrompt +=
      "Output your evaluation between <!-- JUDGE_RESULT_START --> and <!-- JUDGE_RESULT_END --> markers " +
      'containing JSON: {"score": 0.0-1.0, "reasoning": "...", "dimensions": {"dim1": 0.0-1.0, ...}}';

    const userPrompt = this.buildJudgePrompt(opts);

    const scores: number[] = [];
    const reasonings: string[] = [];
    const allDims: Array<Record<string, number>> = [];
    const rawResponses: string[] = [];

    for (let s = 0; s < this.samples; s++) {
      let score = 0;
      let reasoning = "";
      let dims: Record<string, number> = {};

      // Retry up to 2 times on parse failure
      for (let attempt = 0; attempt < 2; attempt++) {
        const result = await this.provider.complete({
          systemPrompt,
          userPrompt,
          model: this.model,
          temperature: this.temperature,
        });
        rawResponses.push(result.text);

        const parsed = parseJudgeResponse(result.text);
        score = parsed.score;
        reasoning = parsed.reasoning;
        dims = parsed.dimensionScores;

        if (score > 0 || !reasoning.includes("Failed to parse")) break;
      }

      scores.push(score);
      reasonings.push(reasoning);
      allDims.push(dims);
    }

    const avgScore = scores.reduce((a, b) => a + b, 0) / scores.length;

    // Average dimension scores
    const avgDims: Record<string, number> = {};
    const allKeys = new Set(allDims.flatMap(d => Object.keys(d)));
    for (const key of allKeys) {
      const vals = allDims.filter(d => key in d).map(d => d[key]);
      avgDims[key] = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
    }

    if (opts.referenceContext && !("factual_accuracy" in avgDims)) {
      avgDims["factual_accuracy"] = avgScore;
    }

    return {
      score: avgScore,
      reasoning: reasonings.join("\n---\n"),
      dimensionScores: avgDims,
      rawResponses,
    };
  }

  private buildJudgePrompt(opts: {
    taskPrompt: string;
    agentOutput: string;
    referenceContext?: string;
    requiredConcepts?: string[];
    calibrationExamples?: Array<Record<string, unknown>>;
  }): string {
    const parts: string[] = [`## Rubric\n${this.rubric}\n`];

    if (opts.referenceContext) {
      parts.push(`\n## Reference Context (Authoritative)\n${opts.referenceContext}\n`);
    }

    if (opts.requiredConcepts?.length) {
      parts.push(
        `\n## Required Concepts\nThe output MUST correctly address these concepts: ${opts.requiredConcepts.join(", ")}\n`,
      );
    }

    if (opts.calibrationExamples?.length) {
      const lines = [
        "\n## Calibration Examples (Human-Scored)\n",
        "The following are real outputs scored by a human reviewer. " +
          "Use these to calibrate your scoring — match the human's standards.\n",
      ];
      for (let i = 0; i < opts.calibrationExamples.length; i++) {
        const ex = opts.calibrationExamples[i];
        const score = ex.human_score ?? "N/A";
        const notes = ex.human_notes ?? "";
        const snippet = String(ex.agent_output ?? "").slice(0, 200);
        lines.push(
          `**Example ${i + 1}** — Score: ${score}\n` +
            `Human notes: ${notes}\n` +
            `Output snippet: ${snippet}...\n`,
        );
      }
      parts.push(lines.join("\n"));
    }

    parts.push(`\n## Task Prompt\n${opts.taskPrompt}\n`);
    parts.push(`\n## Agent Output\n${opts.agentOutput}\n`);
    parts.push(
      "\nEvaluate the agent's output against the rubric. " +
        "Provide your evaluation between <!-- JUDGE_RESULT_START --> and <!-- JUDGE_RESULT_END --> markers.\n\n" +
        "You MUST use exactly this format:\n" +
        "<!-- JUDGE_RESULT_START -->\n" +
        '{"score": 0.85, "reasoning": "Your detailed reasoning here", ' +
        '"dimensions": {"dimension_name": 0.9, "other_dimension": 0.8}}\n' +
        "<!-- JUDGE_RESULT_END -->\n\n" +
        "The score and all dimension values must be between 0.0 and 1.0. " +
        "Include dimension scores that match the rubric criteria.",
    );

    return parts.join("\n");
  }
}
