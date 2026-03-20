/**
 * Tournament runner — run N matches, aggregate scores, compute Elo (AC-343 Task 9).
 * Mirrors Python's tournament logic from loop/tournament_helpers.py.
 */

import type { Result, ScenarioInterface } from "../scenarios/game-interface.js";
import { updateElo } from "./elo.js";

export interface TournamentOpts {
  matchCount: number;
  seedBase: number;
  initialElo?: number;
  opponentElo?: number;
}

export interface MatchResult {
  seed: number;
  score: number;
  winner: string | null;
  passedValidation: boolean;
  validationErrors: string[];
  replay: Array<Record<string, unknown>>;
}

export interface TournamentResult {
  matches: MatchResult[];
  meanScore: number;
  bestScore: number;
  wins: number;
  losses: number;
  elo: number;
}

export class TournamentRunner {
  private scenario: ScenarioInterface;
  private opts: Required<TournamentOpts>;

  constructor(scenario: ScenarioInterface, opts: TournamentOpts) {
    this.scenario = scenario;
    this.opts = {
      matchCount: opts.matchCount,
      seedBase: opts.seedBase,
      initialElo: opts.initialElo ?? 1000.0,
      opponentElo: opts.opponentElo ?? 1000.0,
    };
  }

  run(strategy: Record<string, unknown>): TournamentResult {
    const matches: MatchResult[] = [];
    let elo = this.opts.initialElo;
    let totalScore = 0;
    let bestScore = -Infinity;
    let wins = 0;
    let losses = 0;

    for (let i = 0; i < this.opts.matchCount; i++) {
      const seed = this.opts.seedBase + i;
      const result: Result = this.scenario.executeMatch(strategy, seed);

      const matchResult: MatchResult = {
        seed,
        score: result.score,
        winner: result.winner,
        passedValidation: result.passedValidation,
        validationErrors: result.validationErrors,
        replay: result.replay,
      };
      matches.push(matchResult);

      totalScore += result.score;
      if (result.score > bestScore) bestScore = result.score;

      if (result.winner === "challenger") {
        wins++;
        elo = updateElo(elo, this.opts.opponentElo, 1.0);
      } else {
        losses++;
        elo = updateElo(elo, this.opts.opponentElo, 0.0);
      }
    }

    const meanScore = this.opts.matchCount > 0 ? totalScore / this.opts.matchCount : 0;

    return {
      matches,
      meanScore,
      bestScore: bestScore === -Infinity ? 0 : bestScore,
      wins,
      losses,
      elo,
    };
  }
}
