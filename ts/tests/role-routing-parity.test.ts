/**
 * AC-910 step 1: cross-language routing parity.
 *
 * Replays docs/role-routing-parity-fixtures.json through routeRoleProvider().
 * autocontext/tests/test_role_routing_parity.py replays the identical file
 * through the Python RoleRouter. Both must agree exactly.
 *
 * To add a scenario group: add it to the fixture, then add its name to
 * ROUTE_GROUPS here and to ROUTE_GROUPS in the Python replay.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  estimateRoleRoutingCost,
  routeRoleProvider,
  type RoleRoutingSettings,
} from "../src/providers/index.js";

type ExpectedAssignment = {
  cost_per_1k_tokens: number;
  model: string;
  provider_class: string;
  provider_type: string;
};

type ParityCase = {
  context?: { availableLocalModels?: string[] };
  expected: ExpectedAssignment;
  role: string;
  settings?: Record<string, string>;
};

type ParityFixtures = {
  fixtures: Record<string, Record<string, ParityCase>>;
  known_divergences: unknown[];
  schema_version: number;
};

const FIXTURES = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "role-routing-parity-fixtures.json"),
    "utf-8",
  ),
) as ParityFixtures;

// Fixture groups whose cases are single routeRoleProvider() calls compared field by
// field. Must stay in sync with ROUTE_GROUPS in the Python replay.
const ROUTE_GROUPS = ["auto_mode", "explicit_override", "routing_off", "local_artifacts"] as const;

// The fixture uses Python snake_case settings keys. Map them to the camelCase
// fields RoleRoutingSettings expects, so one fixture drives both languages.
const SETTINGS_KEY_MAP: Record<string, keyof RoleRoutingSettings> = {
  role_routing: "roleRouting",
  agent_provider: "agentProvider",
  competitor_provider: "competitorProvider",
  analyst_provider: "analystProvider",
  coach_provider: "coachProvider",
  architect_provider: "architectProvider",
  model_competitor: "modelCompetitor",
  model_analyst: "modelAnalyst",
  model_coach: "modelCoach",
  model_architect: "modelArchitect",
  model_curator: "modelCurator",
  model_translator: "modelTranslator",
  tier_opus_model: "tierOpusModel",
  tier_sonnet_model: "tierSonnetModel",
  tier_haiku_model: "tierHaikuModel",
  mlx_model_path: "mlxModelPath",
};

// Must match _SETTINGS_DEFAULTS in the Python replay exactly.
function baseSettings(): RoleRoutingSettings {
  return {
    agentProvider: "anthropic",
    roleRouting: "auto",
    competitorProvider: "",
    analystProvider: "",
    coachProvider: "",
    architectProvider: "",
    modelCompetitor: "competitor-role-model",
    modelAnalyst: "analyst-role-model",
    modelCoach: "coach-role-model",
    modelArchitect: "architect-role-model",
    modelCurator: "curator-role-model",
    modelTranslator: "translator-role-model",
    tierOpusModel: "opus-tier-model",
    tierSonnetModel: "sonnet-tier-model",
    tierHaikuModel: "haiku-tier-model",
    mlxModelPath: "/models/default-local",
  };
}

function settingsFromFixture(overrides: Record<string, string> = {}): RoleRoutingSettings {
  const settings = baseSettings();
  for (const [pythonKey, value] of Object.entries(overrides)) {
    const camelKey = SETTINGS_KEY_MAP[pythonKey];
    if (!camelKey) {
      throw new Error(`fixture sets unknown settings key: ${pythonKey}`);
    }
    settings[camelKey] = value;
  }
  return settings;
}

function assertRouteMatches(
  result: {
    estimatedCostPer1kTokens: number;
    model: string;
    providerClass: string;
    providerType: string;
  },
  expected: ExpectedAssignment,
): void {
  expect(result.providerType).toBe(expected.provider_type);
  expect(result.model).toBe(expected.model);
  expect(result.providerClass).toBe(expected.provider_class);
  expect(result.estimatedCostPer1kTokens).toBeCloseTo(expected.cost_per_1k_tokens);
}

/**
 * routeRoleProvider() reads AUTOCONTEXT_AGENT_PROVIDER / AUTOCONTEXT_PROVIDER live from
 * process.env and lets them outrank the passed settings. Python does not read env at all.
 * Without this isolation the suite passes or fails based on the runner's environment.
 */
let savedAgentProvider: string | undefined;
let savedProvider: string | undefined;

beforeEach(() => {
  savedAgentProvider = process.env.AUTOCONTEXT_AGENT_PROVIDER;
  savedProvider = process.env.AUTOCONTEXT_PROVIDER;
  delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
  delete process.env.AUTOCONTEXT_PROVIDER;
});

afterEach(() => {
  if (savedAgentProvider === undefined) delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
  else process.env.AUTOCONTEXT_AGENT_PROVIDER = savedAgentProvider;
  if (savedProvider === undefined) delete process.env.AUTOCONTEXT_PROVIDER;
  else process.env.AUTOCONTEXT_PROVIDER = savedProvider;
});

for (const group of ROUTE_GROUPS) {
  describe(`${group} parity`, () => {
    const cases = Object.entries(FIXTURES.fixtures[group] ?? {});

    it("has cases to replay", () => {
      expect(cases.length).toBeGreaterThan(0);
    });

    for (const [name, testCase] of cases) {
      it(`matches Python for ${name}`, () => {
        const result = routeRoleProvider(
          settingsFromFixture(testCase.settings),
          testCase.role,
          testCase.context ?? {},
        );
        assertRouteMatches(result, testCase.expected);
      });
    }
  });
}

type CostCase = {
  context?: { availableLocalModels?: string[] };
  expected: {
    all_frontier_per_1k_tokens: number;
    savings_vs_all_frontier: number;
    total_per_1k_tokens: number;
  };
  settings?: Record<string, string>;
};

describe("cost_estimation parity", () => {
  const cases = Object.entries(
    (FIXTURES.fixtures.cost_estimation ?? {}) as unknown as Record<string, CostCase>,
  );

  it("has cases to replay", () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  for (const [name, testCase] of cases) {
    it(`matches Python for ${name}`, () => {
      const estimate = estimateRoleRoutingCost(
        settingsFromFixture(testCase.settings),
        testCase.context ?? {},
      );
      expect(estimate.totalPer1kTokens).toBeCloseTo(testCase.expected.total_per_1k_tokens);
      expect(estimate.allFrontierPer1kTokens).toBeCloseTo(
        testCase.expected.all_frontier_per_1k_tokens,
      );
      expect(estimate.savingsVsAllFrontier).toBeCloseTo(testCase.expected.savings_vs_all_frontier);
    });
  }
});

describe("fixture completeness", () => {
  // Must match _EXPECTED_GROUPS in the Python replay.
  const EXPECTED_GROUPS = [
    "auto_mode",
    "cost_estimation",
    "explicit_override",
    "local_artifacts",
    "routing_off",
  ];

  it("replays every expected fixture group", () => {
    expect(Object.keys(FIXTURES.fixtures).sort()).toEqual(EXPECTED_GROUPS);
  });

  it("has no empty group", () => {
    const empty = EXPECTED_GROUPS.filter(
      (g) => Object.keys(FIXTURES.fixtures[g] ?? {}).length === 0,
    );
    expect(empty).toEqual([]);
  });

  // The unknown-key guard exists so a misspelled fixture key fails loudly instead
  // of silently falling back to defaults. Untested, it could rot into a no-op.
  it("rejects an unknown settings key", () => {
    expect(() => settingsFromFixture({ not_a_real_setting: "x" })).toThrow(/unknown settings key/);
  });
});
