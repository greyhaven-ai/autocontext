import { describe, expect, it } from "vitest";

import { buildGenerationTournamentEventSequence } from "../src/loop/generation-tournament-event-sequencing.js";

describe("generation tournament event sequencing", () => {
  it("builds tournament lifecycle events in emission order", () => {
    const events = buildGenerationTournamentEventSequence({
      runId: "run-1",
      generation: 2,
      scheduledMatches: 3,
      tournamentResult: {
        matches: [
          {
            seed: 100,
            score: 0.4,
            winner: "challenger",
            passedValidation: true,
            validationErrors: [],
            replay: [],
          },
          {
            seed: 101,
            score: 0.7,
            winner: null,
            passedValidation: true,
            validationErrors: [],
            replay: [],
          },
        ],
        meanScore: 0.55,
        bestScore: 0.7,
        wins: 1,
        losses: 1,
        elo: 1042,
      },
    });

    expect(events).toEqual([
      {
        event: "tournament_started",
        payload: {
          run_id: "run-1",
          generation: 2,
          matches: 3,
        },
      },
      {
        event: "match_completed",
        payload: {
          run_id: "run-1",
          generation: 2,
          match_index: 0,
          score: 0.4,
          winner: "challenger",
        },
      },
      {
        event: "match_completed",
        payload: {
          run_id: "run-1",
          generation: 2,
          match_index: 1,
          score: 0.7,
          winner: "",
        },
      },
      {
        event: "tournament_completed",
        payload: {
          run_id: "run-1",
          generation: 2,
          mean_score: 0.55,
          best_score: 0.7,
          wins: 1,
          losses: 1,
        },
      },
    ]);
  });

  it("still emits start and completion events when no matches are present", () => {
    const events = buildGenerationTournamentEventSequence({
      runId: "run-2",
      generation: 1,
      scheduledMatches: 0,
      tournamentResult: {
        matches: [],
        meanScore: 0,
        bestScore: 0,
        wins: 0,
        losses: 0,
        elo: 1000,
      },
    });

    expect(events).toEqual([
      {
        event: "tournament_started",
        payload: {
          run_id: "run-2",
          generation: 1,
          matches: 0,
        },
      },
      {
        event: "tournament_completed",
        payload: {
          run_id: "run-2",
          generation: 1,
          mean_score: 0,
          best_score: 0,
          wins: 0,
          losses: 0,
        },
      },
    ]);
  });
});
