import type { TournamentResult } from "../execution/tournament.js";
import { buildTournamentCompletedPayload } from "./generation-event-coordinator.js";

export interface GenerationLoopEventSequenceItem {
  event: string;
  payload: Record<string, unknown>;
}

export interface TournamentStartedPayload {
  [key: string]: unknown;
  run_id: string;
  generation: number;
  matches: number;
}

export function buildGenerationTournamentEventSequence(opts: {
  runId: string;
  generation: number;
  scheduledMatches: number;
  tournamentResult: TournamentResult;
  attempt?: number;
}): GenerationLoopEventSequenceItem[] {
  return [
    buildGenerationTournamentStartedEvent(
      opts.runId,
      opts.generation,
      opts.scheduledMatches,
      opts.attempt,
    ),
    ...opts.tournamentResult.matches.map((match, matchIndex) =>
      buildMatchCompletedEvent(opts.runId, opts.generation, matchIndex, match.score, match.winner),
    ),
    buildTournamentCompletedEvent(opts.runId, opts.generation, opts.tournamentResult, opts.attempt),
  ];
}

export function buildGenerationTournamentStartedEvent(
  runId: string,
  generation: number,
  scheduledMatches: number,
  attempt?: number,
): GenerationLoopEventSequenceItem {
  return {
    event: "tournament_started",
    payload: {
      run_id: runId,
      generation,
      matches: scheduledMatches,
      ...(attempt === undefined ? {} : { attempt }),
    } as TournamentStartedPayload,
  };
}

function buildMatchCompletedEvent(
  runId: string,
  generation: number,
  matchIndex: number,
  score: number,
  winner: string | null,
): GenerationLoopEventSequenceItem {
  return {
    event: "match_completed",
    payload: {
      run_id: runId,
      generation,
      match_index: matchIndex,
      score,
      winner: winner ?? "",
    },
  };
}

function buildTournamentCompletedEvent(
  runId: string,
  generation: number,
  tournamentResult: TournamentResult,
  attempt?: number,
): GenerationLoopEventSequenceItem {
  return {
    event: "tournament_completed",
    payload: {
      ...buildTournamentCompletedPayload(runId, generation, tournamentResult),
      ...(attempt === undefined ? {} : { attempt }),
    },
  };
}
