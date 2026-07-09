/**
 * Word Count task — deterministic agent_task scenario (AC-402).
 *
 * Asks the agent to produce exactly N words on a topic. Evaluation is
 * purely deterministic: score = 1 - |actual - target| / target, clamped
 * to [0, 1]. No API key required.
 */

import type { JudgeResult } from "../types/index.js";

const TARGET_WORDS = 50;
const TOPIC = "the benefits of automated software testing";

export class WordCountTask {
  getTaskPrompt(): string {
    return `Write exactly ${TARGET_WORDS} words about ${TOPIC}. Your response should contain precisely ${TARGET_WORDS} words — no more, no fewer.`;
  }

  getRubric(): string {
    return `Score based on how close the word count is to exactly ${TARGET_WORDS} words. Perfect score for exactly ${TARGET_WORDS} words. Deduct proportionally for each word over or under.`;
  }

  describeTask(): string {
    return `Deterministic word-count task: produce exactly ${TARGET_WORDS} words about ${TOPIC}.`;
  }

  initialState(): Record<string, unknown> {
    return {};
  }

  async evaluateOutput(output: string): Promise<JudgeResult> {
    const words = output.trim().split(/\s+/).filter(Boolean);
    const actual = words.length;
    const error = Math.abs(actual - TARGET_WORDS) / TARGET_WORDS;
    const score = Math.round(Math.max(0, 1 - error) * 10000) / 10000;

    const onTopic = output.toLowerCase().includes("test");
    const topicBonus = onTopic ? 0 : -0.1;
    const finalScore = Math.round(Math.max(0, Math.min(1, score + topicBonus)) * 10000) / 10000;

    return {
      score: finalScore,
      reasoning: `Word count: ${actual}/${TARGET_WORDS} (error: ${Math.round(error * 100)}%).${onTopic ? "" : " Off-topic penalty applied."}`,
      dimensionScores: {
        word_count_accuracy: score,
        topic_relevance: onTopic ? 1 : 0.5,
      },
      rawResponses: [],
      parseMethod: "deterministic" as "raw_json",
      internalRetries: 0,
      dimensionsWereGenerated: false,
      evaluatorEpoch: null,
    };
  }
}
