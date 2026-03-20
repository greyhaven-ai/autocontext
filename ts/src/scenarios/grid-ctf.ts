/**
 * Grid CTF scenario — 20x20 capture-the-flag game (AC-343 Task 6).
 * Mirrors Python's autocontext/scenarios/grid_ctf/scenario.py.
 */

import type {
  LegalAction,
  Observation,
  Result,
  ScenarioInterface,
  ScoringDimension,
} from "./game-interface.js";
import { ResultSchema } from "./game-interface.js";

// ---------------------------------------------------------------------------
// Seedable PRNG (matches Python's random.Random)
// ---------------------------------------------------------------------------

/**
 * Simple seedable PRNG using mulberry32 algorithm.
 * Not cryptographic, but deterministic and fast.
 */
function createRng(seed: number): () => number {
  let s = seed | 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function rngUniform(rng: () => number, lo: number, hi: number): number {
  return lo + rng() * (hi - lo);
}

// ---------------------------------------------------------------------------
// GridCtfScenario
// ---------------------------------------------------------------------------

export class GridCtfScenario implements ScenarioInterface {
  readonly name = "grid_ctf";

  scoringDimensions(): ScoringDimension[] {
    return [
      {
        name: "capture_progress",
        weight: 0.6,
        description: "How effectively the strategy advances toward capturing the flag.",
      },
      {
        name: "defender_survival",
        weight: 0.25,
        description: "How well the strategy preserves defenders and base integrity.",
      },
      {
        name: "energy_efficiency",
        weight: 0.15,
        description: "How efficiently the strategy converts aggression into progress without waste.",
      },
    ];
  }

  describeRules(): string {
    return (
      "20x20 capture-the-flag map with fog of war and three unit archetypes " +
      "(Scout, Soldier, Commander). Preserve at least one defender near base."
    );
  }

  describeStrategyInterface(): string {
    return (
      "Return JSON object with keys `aggression`, `defense`, and `path_bias`, " +
      "all floats in [0,1]. Constraint: aggression + defense <= 1.4."
    );
  }

  describeEvaluationCriteria(): string {
    return (
      "Primary objective is capture progress. Secondary objectives are defender " +
      "survivability and resource efficiency."
    );
  }

  initialState(seed?: number): Record<string, unknown> {
    const s = seed ?? 0;
    const rng = createRng(s);
    return {
      seed: s,
      enemy_spawn_bias: Number(rngUniform(rng, 0.25, 0.75).toFixed(3)),
      resource_density: Number(rngUniform(rng, 0.1, 0.9).toFixed(3)),
      terminal: false,
      turn: 0,
      timeline: [] as Array<Record<string, unknown>>,
    };
  }

  getObservation(state: Record<string, unknown>, playerId: string): Observation {
    return {
      narrative:
        `${playerId} sees mirrored lanes, enemy spawn bias ` +
        `${state.enemy_spawn_bias}, and resource density ${state.resource_density}.`,
      state: {
        enemy_spawn_bias: state.enemy_spawn_bias,
        resource_density: state.resource_density,
      },
      constraints: [
        "Maintain at least one defender near base.",
        "Avoid aggression spikes above sustainable energy budget.",
      ],
    };
  }

  validateActions(
    _state: Record<string, unknown>,
    _playerId: string,
    actions: Record<string, unknown>,
  ): [boolean, string] {
    const required = ["aggression", "defense", "path_bias"] as const;
    const parsed: Record<string, number> = {};

    for (const key of required) {
      const value = actions[key];
      if (typeof value !== "number") {
        return [false, `missing or invalid field: ${key}`];
      }
      if (value < 0 || value > 1) {
        return [false, `${key} must be in [0,1]`];
      }
      parsed[key] = value;
    }

    if (parsed.aggression + parsed.defense > 1.4) {
      return [false, "combined aggression + defense must be <= 1.4"];
    }

    return [true, "ok"];
  }

  step(state: Record<string, unknown>, actions: Record<string, unknown>): Record<string, unknown> {
    const aggression = actions.aggression as number;
    const defense = actions.defense as number;
    const pathBias = actions.path_bias as number;

    const rng = createRng(state.seed as number);
    const stochastic = rngUniform(rng, -0.07, 0.07);

    const captureProgress = Math.max(0.0, Math.min(1.0, 0.55 * aggression + 0.45 * pathBias + stochastic));
    const defenderSurvival = Math.max(0.0, Math.min(1.0, 1.0 - aggression * 0.4 + defense * 0.4));
    const energyEfficiency = Math.max(0.0, Math.min(1.0, 1.0 - aggression * 0.3 + defense * 0.1));
    const score = Math.max(
      0.0,
      Math.min(1.0, captureProgress * 0.6 + defenderSurvival * 0.25 + energyEfficiency * 0.15),
    );

    const timeline = [...(state.timeline as Array<Record<string, unknown>>)];
    timeline.push({
      event: "turn_complete",
      turn: (state.turn as number) + 1,
      capture_progress: Number(captureProgress.toFixed(4)),
      defender_survival: Number(defenderSurvival.toFixed(4)),
      energy_efficiency: Number(energyEfficiency.toFixed(4)),
    });

    return {
      ...state,
      terminal: true,
      turn: (state.turn as number) + 1,
      score: Number(score.toFixed(4)),
      metrics: {
        capture_progress: Number(captureProgress.toFixed(4)),
        defender_survival: Number(defenderSurvival.toFixed(4)),
        energy_efficiency: Number(energyEfficiency.toFixed(4)),
      },
      timeline,
    };
  }

  isTerminal(state: Record<string, unknown>): boolean {
    return Boolean(state.terminal);
  }

  getResult(state: Record<string, unknown>): Result {
    const replay = [...((state.timeline as Array<Record<string, unknown>>) ?? [])];
    const score = Number(state.score ?? 0);
    const metrics = (state.metrics ?? {}) as Record<string, number>;

    return ResultSchema.parse({
      score,
      winner: score >= 0.55 ? "challenger" : "incumbent",
      summary: `GridCTF score ${score.toFixed(4)}`,
      replay,
      metrics: Object.fromEntries(Object.entries(metrics).map(([k, v]) => [k, Number(v)])),
    });
  }

  replayToNarrative(replay: Array<Record<string, unknown>>): string {
    if (!replay.length) return "No replay events were captured.";
    const event = replay[replay.length - 1];
    return (
      `Capture phase ended with progress ${Number(event.capture_progress ?? 0).toFixed(2)}, ` +
      `defender survival ${Number(event.defender_survival ?? 0).toFixed(2)}, ` +
      `and energy efficiency ${Number(event.energy_efficiency ?? 0).toFixed(2)}.`
    );
  }

  enumerateLegalActions(state: Record<string, unknown>): LegalAction[] | null {
    if (this.isTerminal(state)) return [];
    return [
      {
        action: "aggression",
        description: "Attack intensity; higher values push harder toward the flag",
        type: "continuous",
        range: [0.0, 1.0],
      },
      {
        action: "defense",
        description: "Defensive allocation; constraint: aggression + defense <= 1.4",
        type: "continuous",
        range: [0.0, 1.0],
      },
      {
        action: "path_bias",
        description: "Pathfinding preference; influences capture route selection",
        type: "continuous",
        range: [0.0, 1.0],
      },
    ];
  }

  renderFrame(state: Record<string, unknown>): Record<string, unknown> {
    return {
      scenario: this.name,
      turn: Number(state.turn ?? 0),
      score: Number(state.score ?? 0),
      metrics: state.metrics ?? {},
    };
  }

  executeMatch(strategy: Record<string, unknown>, seed: number): Result {
    const state = this.initialState(seed);
    const [valid, reason] = this.validateActions(state, "challenger", strategy);
    if (!valid) {
      return ResultSchema.parse({
        score: 0.0,
        winner: "incumbent",
        summary: "strategy rejected during validation",
        replay: [{ event: "validation_failed", reason }],
        metrics: { valid: 0.0 },
        validationErrors: [reason],
      });
    }
    const nextState = this.step(state, strategy);
    return this.getResult(nextState);
  }
}
