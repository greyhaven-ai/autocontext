import type { ScenarioFamilyName } from "./families.js";
import { SCENARIO_TYPE_MARKERS } from "./families.js";

export interface FamilyCandidate {
  familyName: ScenarioFamilyName;
  confidence: number;
  rationale: string;
}

export interface FamilyClassification {
  familyName: ScenarioFamilyName;
  confidence: number;
  rationale: string;
  alternatives: FamilyCandidate[];
}

export class LowConfidenceError extends Error {
  classification: FamilyClassification;
  minConfidence: number;

  constructor(classification: FamilyClassification, minConfidence: number) {
    super(
      `Family classification confidence ${classification.confidence.toFixed(2)} is below threshold ${minConfidence.toFixed(2)} for family '${classification.familyName}'`,
    );
    this.classification = classification;
    this.minConfidence = minConfidence;
  }
}

const SIMULATION_SIGNALS: Record<string, number> = {
  orchestrat: 2.0,
  rollback: 2.0,
  deploy: 1.5,
  pipeline: 1.5,
  workflow: 1.5,
  incident: 1.5,
  remediat: 1.5,
  triage: 1.5,
  "state machine": 2.0,
  "mock api": 2.0,
  "mock environment": 2.0,
  "api call": 1.5,
  endpoint: 1.0,
  microservice: 1.5,
  "service health": 1.5,
  monitor: 1.0,
  dashboard: 1.0,
  recovery: 1.5,
  failover: 2.0,
  "circuit breaker": 2.0,
  retry: 1.0,
  "dependency order": 2.0,
  "correct order": 1.5,
  "action trace": 2.0,
  "side effect": 1.5,
  transact: 1.5,
  simulat: 1.0,
  trace: 1.0,
  "step by step": 1.0,
  "health endpoint": 1.5,
  "server log": 1.0,
  "root cause": 1.0,
  investigat: 1.0,
};

const AGENT_TASK_SIGNALS: Record<string, number> = {
  essay: 2.0,
  article: 1.5,
  blog: 1.5,
  "blog post": 2.0,
  "write about": 1.5,
  persuasive: 1.5,
  narrative: 1.0,
  poem: 1.5,
  haiku: 1.5,
  story: 1.0,
  fiction: 1.5,
  prose: 1.5,
  recipe: 1.5,
  summariz: 1.5,
  abstract: 1.0,
  generat: 1.0,
  translat: 1.5,
  classify: 1.0,
  sentiment: 1.5,
  report: 1.0,
  review: 1.0,
  evaluat: 1.0,
  "code quality": 1.5,
  "python function": 1.5,
  sort: 0.5,
  "data analysis": 1.0,
  "customer review": 1.0,
};

const GAME_SIGNALS: Record<string, number> = {
  tournament: 2.0,
  "board game": 2.0,
  compet: 1.5,
  "two-player": 2.0,
  "two player": 2.0,
  "head-to-head": 2.0,
  "head to head": 2.0,
  opponent: 1.5,
  territory: 1.5,
  "capture the flag": 2.0,
  "grid game": 2.0,
  maze: 1.0,
  "strategy game": 2.0,
  "resource management": 1.5,
  scoring: 1.0,
  elo: 2.0,
  ranking: 1.0,
  win: 0.5,
  lose: 0.5,
  match: 0.5,
  player: 1.0,
};

const ARTIFACT_EDITING_SIGNALS: Record<string, number> = {
  "edit file": 2.0,
  "modify file": 2.0,
  "update config": 2.0,
  configuration: 1.5,
  "config file": 1.5,
  yaml: 1.5,
  json: 1.0,
  schema: 1.5,
  migration: 1.5,
  manifest: 1.5,
  patch: 1.0,
  "refactor config": 2.0,
  "fix config": 2.0,
  artifact: 1.5,
  "file edit": 2.0,
  rewrite: 1.0,
  "update policy": 1.5,
  "change file": 1.5,
  "modify yaml": 2.0,
  "modify json": 2.0,
  "config repair": 2.0,
  "repair schema": 2.0,
  "sql migration": 2.0,
  dockerfile: 1.5,
};

const FAMILY_SIGNAL_GROUPS: Record<ScenarioFamilyName, Record<string, number>> = {
  game: GAME_SIGNALS,
  agent_task: AGENT_TASK_SIGNALS,
  simulation: SIMULATION_SIGNALS,
  artifact_editing: ARTIFACT_EDITING_SIGNALS,
};

const DEFAULT_FAMILY_NAME: ScenarioFamilyName = "agent_task";

function buildRationale(matched: string[], familyName: ScenarioFamilyName): string {
  if (matched.length === 0) {
    return `No strong signals for ${familyName}`;
  }
  return `Matched ${familyName} signals: ${matched.slice(0, 3).join(", ")}`;
}

function scoreSignals(textLower: string, signals: Record<string, number>): [number, string[]] {
  let score = 0;
  const matched: string[] = [];
  for (const [signal, weight] of Object.entries(signals)) {
    if (textLower.includes(signal)) {
      score += weight;
      matched.push(signal);
    }
  }
  return [score, matched];
}

export function classifyScenarioFamily(description: string): FamilyClassification {
  if (!description.trim()) {
    throw new Error("description must be non-empty");
  }

  const families = Object.keys(SCENARIO_TYPE_MARKERS) as ScenarioFamilyName[];
  const textLower = description.toLowerCase();
  const rawScores = new Map<ScenarioFamilyName, number>();
  const matchedSignals = new Map<ScenarioFamilyName, string[]>();

  for (const familyName of families) {
    const [score, matched] = scoreSignals(textLower, FAMILY_SIGNAL_GROUPS[familyName] ?? {});
    rawScores.set(familyName, score);
    matchedSignals.set(familyName, matched);
  }

  const total = [...rawScores.values()].reduce((sum, score) => sum + score, 0);
  if (total === 0) {
    const defaultFamily = families.includes(DEFAULT_FAMILY_NAME) ? DEFAULT_FAMILY_NAME : families[0];
    return {
      familyName: defaultFamily,
      confidence: 0.2,
      rationale: `No strong signals detected; defaulting to ${defaultFamily}`,
      alternatives: families
        .filter((familyName) => familyName !== defaultFamily)
        .map((familyName) => ({
          familyName,
          confidence: 0.1,
          rationale: `No ${familyName} signals`,
        })),
    };
  }

  const ranked = families
    .map((familyName) => ({
      familyName,
      confidence: rawScores.get(familyName)! / total,
    }))
    .sort((a, b) => b.confidence - a.confidence);

  const [top, ...rest] = ranked;
  return {
    familyName: top.familyName,
    confidence: Number(top.confidence.toFixed(4)),
    rationale: buildRationale(matchedSignals.get(top.familyName) ?? [], top.familyName),
    alternatives: rest.map(({ familyName, confidence }) => ({
      familyName,
      confidence: Number(confidence.toFixed(4)),
      rationale: buildRationale(matchedSignals.get(familyName) ?? [], familyName),
    })),
  };
}

export function routeToFamily(
  classification: FamilyClassification,
  minConfidence = 0.3,
): ScenarioFamilyName {
  if (classification.confidence < minConfidence) {
    throw new LowConfidenceError(classification, minConfidence);
  }
  return classification.familyName;
}
