import type { RoundResult } from "../types/index.js";

export const PARSE_FAILURE_MARKERS = [
  "no parseable score found",
  "missing JUDGE_RESULT markers",
  "invalid JSON",
  "Failed to parse judge response",
] as const;

export function isParseFailure(score: number, reasoning: string): boolean {
  if (score > 0) {
    return false;
  }
  return PARSE_FAILURE_MARKERS.some((marker) => reasoning.includes(marker));
}

export function isImproved(rounds: RoundResult[]): boolean {
  const validRounds = rounds.filter((round) => !round.judgeFailed);
  if (validRounds.length < 2) {
    return false;
  }
  return validRounds[validRounds.length - 1].score > validRounds[0].score;
}
