import type {
  LegalAction,
  Observation,
  Result,
  ScenarioInterface,
  ScoringDimension,
} from "./game-interface.js";
import { ResultSchema } from "./game-interface.js";

interface ParametricStrategyParam {
  name: string;
  description: string;
  minValue: number;
  maxValue: number;
  defaultValue: number;
}

interface ParametricConstraint {
  expression: string;
  operator: "<=" | ">=" | "<" | ">" | "==";
  threshold: number;
  description: string;
}

interface ParametricEnvironmentVariable {
  name: string;
  description: string;
  low: number;
  high: number;
}

interface ParametricScoringComponent {
  name: string;
  description: string;
  formulaTerms: Record<string, number>;
  noiseRange: [number, number];
}

export interface PersistedParametricScenarioSpec {
  name: string;
  displayName: string;
  description: string;
  strategyInterfaceDescription: string;
  evaluationCriteria: string;
  strategyParams: ParametricStrategyParam[];
  constraints: ParametricConstraint[];
  environmentVariables: ParametricEnvironmentVariable[];
  scoringComponents: ParametricScoringComponent[];
  finalScoreWeights: Record<string, number>;
  winThreshold: number;
  observationConstraints: string[];
}

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

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}

function readString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function readNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function normalizeConstraintOperator(value: unknown): ParametricConstraint["operator"] {
  return value === ">=" || value === "<" || value === ">" || value === "==" ? value : "<=";
}

function normalizeStrategyParams(raw: Record<string, unknown>): ParametricStrategyParam[] {
  const items = raw.strategy_params ?? raw.strategyParams;
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      name: readString(item.name),
      description: readString(item.description),
      minValue: readNumber(item.min_value ?? item.minValue, 0),
      maxValue: readNumber(item.max_value ?? item.maxValue, 1),
      defaultValue: readNumber(item.default ?? item.defaultValue, 0.5),
    }))
    .filter((item) => item.name.length > 0);
}

function normalizeConstraints(raw: Record<string, unknown>): ParametricConstraint[] {
  const items = raw.constraints;
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      expression: readString(item.expression),
      operator: normalizeConstraintOperator(item.operator),
      threshold: readNumber(item.threshold, 0),
      description: readString(item.description),
    }))
    .filter((item) => item.expression.length > 0);
}

function normalizeEnvironmentVariables(
  raw: Record<string, unknown>,
): ParametricEnvironmentVariable[] {
  const items = raw.environment_variables ?? raw.environmentVariables;
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => ({
      name: readString(item.name),
      description: readString(item.description),
      low: readNumber(item.low, 0),
      high: readNumber(item.high, 1),
    }))
    .filter((item) => item.name.length > 0);
}

function normalizeScoringComponents(raw: Record<string, unknown>): ParametricScoringComponent[] {
  const items = raw.scoring_components ?? raw.scoringComponents;
  if (!Array.isArray(items)) {
    return [];
  }
  return items
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .map((item) => {
      const rawNoise = item.noise_range ?? item.noiseRange;
      const noiseRange: [number, number] =
        Array.isArray(rawNoise) && rawNoise.length >= 2
          ? [readNumber(rawNoise[0], 0), readNumber(rawNoise[1], 0)]
          : [0, 0];
      const rawFormulaTerms = item.formula_terms ?? item.formulaTerms;
      const formulaTerms =
        rawFormulaTerms && typeof rawFormulaTerms === "object"
          ? Object.fromEntries(
              Object.entries(rawFormulaTerms as Record<string, unknown>).map(([key, value]) => [
                key,
                readNumber(value, 0),
              ]),
            )
          : {};
      return {
        name: readString(item.name),
        description: readString(item.description),
        formulaTerms,
        noiseRange,
      };
    })
    .filter((item) => item.name.length > 0);
}

function normalizeObservationConstraints(raw: Record<string, unknown>): string[] {
  const items = raw.observation_constraints ?? raw.observationConstraints;
  if (!Array.isArray(items)) {
    return [];
  }
  return items.filter((item): item is string => typeof item === "string");
}

export function normalizePersistedParametricScenarioSpec(
  name: string,
  raw: Record<string, unknown>,
): PersistedParametricScenarioSpec {
  return {
    name,
    displayName: readString(raw.display_name ?? raw.displayName, name),
    description: readString(raw.description),
    strategyInterfaceDescription: readString(
      raw.strategy_interface_description ?? raw.strategyInterfaceDescription,
    ),
    evaluationCriteria: readString(raw.evaluation_criteria ?? raw.evaluationCriteria),
    strategyParams: normalizeStrategyParams(raw),
    constraints: normalizeConstraints(raw),
    environmentVariables: normalizeEnvironmentVariables(raw),
    scoringComponents: normalizeScoringComponents(raw),
    finalScoreWeights:
      raw.final_score_weights && typeof raw.final_score_weights === "object"
        ? Object.fromEntries(
            Object.entries(raw.final_score_weights as Record<string, unknown>).map(
              ([key, value]) => [key, readNumber(value, 0)],
            ),
          )
        : {},
    winThreshold: readNumber(raw.win_threshold ?? raw.winThreshold, 0.55),
    observationConstraints: normalizeObservationConstraints(raw),
  };
}

function evaluateConstraintExpression(expression: string, parsed: Record<string, number>): number {
  const parts = expression.split(/(\+|-)/);
  let total = 0;
  let sign = 1;
  for (const part of parts) {
    const token = part.trim();
    if (!token) {
      continue;
    }
    if (token === "+") {
      sign = 1;
      continue;
    }
    if (token === "-") {
      sign = -1;
      continue;
    }
    total += sign * (parsed[token] ?? 0);
  }
  return total;
}

function evaluateConstraint(
  operator: ParametricConstraint["operator"],
  value: number,
  threshold: number,
): boolean {
  switch (operator) {
    case ">=":
      return value >= threshold;
    case "<":
      return value < threshold;
    case ">":
      return value > threshold;
    case "==":
      return value === threshold;
    default:
      return value <= threshold;
  }
}

export class PersistedParametricScenario implements ScenarioInterface {
  readonly name: string;
  readonly #spec: PersistedParametricScenarioSpec;

  constructor(name: string, rawSpec: Record<string, unknown>) {
    this.name = name;
    this.#spec = normalizePersistedParametricScenarioSpec(name, rawSpec);
  }

  describeRules(): string {
    return this.#spec.description;
  }

  describeStrategyInterface(): string {
    return this.#spec.strategyInterfaceDescription;
  }

  describeEvaluationCriteria(): string {
    return this.#spec.evaluationCriteria;
  }

  initialState(seed = 0): Record<string, unknown> {
    const rng = createRng(seed);
    const state: Record<string, unknown> = {
      seed,
      terminal: false,
      timeline: [],
    };
    for (const envVar of this.#spec.environmentVariables) {
      state[envVar.name] = round3(rngUniform(rng, envVar.low, envVar.high));
    }
    return state;
  }

  getObservation(state: Record<string, unknown>, playerId: string): Observation {
    const visibleState = Object.fromEntries(
      this.#spec.environmentVariables.map((envVar) => [envVar.name, state[envVar.name]]),
    );
    return {
      narrative: `${playerId} observes: ${Object.entries(visibleState)
        .map(([key, value]) => `${key}=${String(value)}`)
        .join(", ")}`,
      state: visibleState,
      constraints: [...this.#spec.observationConstraints],
    };
  }

  validateActions(
    _state: Record<string, unknown>,
    _playerId: string,
    actions: Record<string, unknown>,
  ): [boolean, string] {
    const parsed: Record<string, number> = {};
    for (const param of this.#spec.strategyParams) {
      const value = actions[param.name];
      if (typeof value !== "number" || !Number.isFinite(value)) {
        return [false, `missing or invalid field: ${param.name}`];
      }
      if (value < param.minValue || value > param.maxValue) {
        return [false, `${param.name} must be in [${param.minValue},${param.maxValue}]`];
      }
      parsed[param.name] = value;
    }

    for (const constraint of this.#spec.constraints) {
      const value = evaluateConstraintExpression(constraint.expression, parsed);
      if (!evaluateConstraint(constraint.operator, value, constraint.threshold)) {
        return [false, constraint.description];
      }
    }

    return [true, "ok"];
  }

  step(state: Record<string, unknown>, actions: Record<string, unknown>): Record<string, unknown> {
    const parsed = Object.fromEntries(
      this.#spec.strategyParams.map((param) => [
        param.name,
        Number(actions[param.name] ?? param.defaultValue),
      ]),
    );
    const rng = createRng(Number(state.seed ?? 0));
    const metrics = Object.fromEntries(
      this.#spec.scoringComponents.map((component) => {
        const base = Object.entries(component.formulaTerms).reduce(
          (sum, [paramName, coefficient]) => sum + coefficient * Number(parsed[paramName] ?? 0),
          0,
        );
        const noise = rngUniform(rng, component.noiseRange[0], component.noiseRange[1]);
        return [component.name, round4(clamp01(base + noise))];
      }),
    ) as Record<string, number>;

    const score = round4(
      clamp01(
        Object.entries(metrics).reduce(
          (sum, [metricName, value]) =>
            sum + (this.#spec.finalScoreWeights[metricName] ?? 0) * value,
          0,
        ),
      ),
    );

    const timeline = [...((state.timeline as Array<Record<string, unknown>> | undefined) ?? [])];
    timeline.push({ event: "turn_complete", ...metrics });

    return {
      ...state,
      terminal: true,
      score,
      metrics,
      timeline,
    };
  }

  isTerminal(state: Record<string, unknown>): boolean {
    return Boolean(state.terminal);
  }

  getResult(state: Record<string, unknown>): Result {
    const score = Number(state.score ?? 0);
    const metrics = (state.metrics ?? {}) as Record<string, number>;
    const replay = [...((state.timeline as Array<Record<string, unknown>>) ?? [])];
    return ResultSchema.parse({
      score,
      winner: score >= this.#spec.winThreshold ? "challenger" : "incumbent",
      summary: `${this.#spec.displayName} score ${score.toFixed(4)}`,
      replay,
      metrics: Object.fromEntries(
        Object.entries(metrics).map(([key, value]) => [key, Number(value)]),
      ),
    });
  }

  replayToNarrative(replay: Array<Record<string, unknown>>): string {
    if (!replay.length) {
      return "No replay events were captured.";
    }
    const event = replay[replay.length - 1];
    const dimensionText = this.#spec.scoringComponents
      .map((component) => `${component.name} ${Number(event[component.name] ?? 0).toFixed(2)}`)
      .join(", ");
    return `${this.#spec.displayName}: ${dimensionText}`;
  }

  renderFrame(state: Record<string, unknown>): Record<string, unknown> {
    return {
      scenario: this.name,
      score: Number(state.score ?? 0),
      metrics: state.metrics ?? {},
    };
  }

  enumerateLegalActions(state: Record<string, unknown>): LegalAction[] | null {
    if (this.isTerminal(state)) {
      return [];
    }
    return this.#spec.strategyParams.map((param) => ({
      action: param.name,
      description: param.description,
      type: "continuous",
      range: [param.minValue, param.maxValue] as [number, number],
    }));
  }

  scoringDimensions(): ScoringDimension[] | null {
    return this.#spec.scoringComponents.map((component) => ({
      name: component.name,
      weight: this.#spec.finalScoreWeights[component.name] ?? 0,
      description: component.description,
    }));
  }

  executeMatch(strategy: Record<string, unknown>, seed: number): Result {
    const state = this.initialState(seed);
    const [valid, reason] = this.validateActions(state, "challenger", strategy);
    if (!valid) {
      return ResultSchema.parse({
        score: 0,
        winner: "incumbent",
        summary: "strategy rejected during validation",
        replay: [{ event: "validation_failed", reason }],
        metrics: { valid: 0 },
        validationErrors: [reason],
      });
    }
    return this.getResult(this.step(state, strategy));
  }
}

export function createPersistedParametricScenarioClass(
  name: string,
  rawSpec: Record<string, unknown>,
): new () => ScenarioInterface {
  return class PersistedCustomParametricScenario extends PersistedParametricScenario {
    constructor() {
      super(name, rawSpec);
    }
  };
}
