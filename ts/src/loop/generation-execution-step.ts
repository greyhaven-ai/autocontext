import type { GenerationAttempt } from "./generation-attempt-state.js";

export const COMPETITOR_REPAIR_MAX_OUTPUT_TOKENS = 1_024;
const COMPETITOR_REPAIR_CONTEXT_MAX_CHARS = 8_000;
const COMPETITOR_REPAIR_OUTPUT_MAX_CHARS = 4_000;

export class CompetitorStrategyParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CompetitorStrategyParseError";
  }
}

export function parseCompetitorStrategyResult(
  competitorResultText: string,
): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(competitorResultText);
  } catch {
    throw new CompetitorStrategyParseError("Competitor returned invalid strategy JSON");
  }
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new CompetitorStrategyParseError("Competitor strategy must be a JSON object");
  }
  return parsed as Record<string, unknown>;
}

export function buildCompetitorStrategyRepairPrompt(opts: {
  competitorPrompt: string;
  strategyInterface: string;
  invalidOutput: string;
}): string {
  // Preserve the strategy contract before spending the remaining bounded
  // context budget on scenario prose. Scenario rules and extension rewrites
  // can be arbitrarily long and otherwise push the interface past a simple
  // prefix slice.
  const boundedInterface = opts.strategyInterface.slice(
    0,
    COMPETITOR_REPAIR_CONTEXT_MAX_CHARS,
  );
  const remainingContextCharacters = Math.max(
    0,
    COMPETITOR_REPAIR_CONTEXT_MAX_CHARS - boundedInterface.length,
  );
  const boundedContext = opts.competitorPrompt.slice(0, remainingContextCharacters);
  const boundedOutput = opts.invalidOutput.slice(0, COMPETITOR_REPAIR_OUTPUT_MAX_CHARS);
  return [
    "Repair the invalid competitor strategy below.",
    "Return one valid JSON object only: no Markdown fences, commentary, or extra text.",
    "The object must use the exact fields and constraints from the strategy interface.",
    "This is the only repair attempt; if the output is not valid JSON, the generation will fail.",
    "",
    "## Strategy interface (authoritative)",
    boundedInterface,
    "",
    "## Additional bounded strategy context",
    boundedContext,
    "",
    "## Invalid output",
    boundedOutput,
  ].join("\n");
}

export interface TournamentExecutionPlan {
  seedForGeneration: number;
  tournamentOptions: {
    matchCount: number;
    seedBase: number;
    initialElo: number;
  };
}

export function createTournamentExecutionPlan(opts: {
  generation: number;
  seedBase: number;
  matchesPerGeneration: number;
  currentElo: number;
}): TournamentExecutionPlan {
  const seedForGeneration = opts.seedBase + (opts.generation - 1) * opts.matchesPerGeneration;

  return {
    seedForGeneration,
    tournamentOptions: {
      matchCount: opts.matchesPerGeneration,
      seedBase: seedForGeneration,
      initialElo: opts.currentElo,
    },
  };
}

export function buildGenerationAttemptCandidate(attempt: GenerationAttempt): GenerationAttempt {
  return attempt;
}
