/**
 * AC-910 step 1: cross-language routing parity.
 *
 * Replays docs/role-routing-parity-fixtures.json through routeRoleProvider().
 * autocontext/tests/test_role_routing_parity.py replays the identical file
 * through the Python RoleRouter. Both must agree exactly.
 *
 * To add a scenario: add it to the fixture, then add its case id to
 * EXPECTED_CASE_IDS here and _EXPECTED_CASE_IDS in the Python replay.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SETTINGS_KEY_MAP,
  estimateRoleRoutingCost,
  routeRoleProvider,
  type RoleRoutingSettings,
} from "../src/providers/index.js";

type ExpectedAssignment = {
  cost_per_1k_tokens: number;
  model: string | null;
  provider_class: string;
  provider_type: string;
};

type ParityCase = {
  context?: { availableLocalModels?: string[] };
  expected: ExpectedAssignment;
  role: string;
  settings?: Record<string, string>;
};

type DivergenceCase = {
  case: string;
  context?: { availableLocalModels?: string[] };
  python: ExpectedAssignment;
  reason: string;
  resolution: string;
  role: string;
  settings?: Record<string, string>;
  typescript: ExpectedAssignment;
};

type ParityFixtures = {
  fixtures: Record<string, Record<string, ParityCase>>;
  known_divergences: DivergenceCase[];
  schema_version: number;
};

const FIXTURES = JSON.parse(
  readFileSync(
    join(import.meta.dirname, "..", "..", "docs", "role-routing-parity-fixtures.json"),
    "utf-8",
  ),
) as ParityFixtures;

// The fixture is the behavioral contract, but the expected case ids live outside
// it so deleting or replacing a critical scenario cannot silently reduce coverage.
const EXPECTED_CASE_IDS = {
  auto_mode: [
    "competitor_frontier",
    "analyst_mid_tier",
    "coach_mid_tier",
    "architect_frontier_when_no_artifact_is_available",
    "curator_fast",
    "translator_fast",
    "unknown_role_falls_back_to_mid_tier",
    "self_hosted_default_provider_is_frontier_via_table",
  ],
  explicit_override: [
    "competitor_override_to_ollama_is_mid_tier",
    "architect_override_to_vllm_is_mid_tier",
    "unknown_override_provider_defaults_to_frontier",
    "override_to_mlx_uses_mlx_model_path",
    "override_wins_over_role_routing_off",
  ],
  routing_off: [
    "off_uses_default_provider_and_role_model",
    "off_with_ollama_default_is_mid_tier",
    "off_with_unknown_default_provider_is_mid_tier",
    "off_uses_coach_role_model",
    "off_uses_curator_role_model",
    "off_uses_translator_role_model",
    "off_with_mlx_default_uses_mlx_model_path",
  ],
  local_artifacts: [
    "eligible_role_prefers_local_artifact",
    "architect_uses_local_artifact",
    "curator_uses_local_artifact",
    "local_artifact_ignored_when_routing_off",
  ],
  cost_estimation: ["default_auto_mode_totals", "with_local_artifacts_totals"],
} as const satisfies Record<string, readonly string[]>;

// Compared with .sort(), so this list must stay in sorted order.
const EXPECTED_DIVERGENCE_CASE_IDS = [
  "explicit_override.mixed_case_provider_name",
  "explicit_override.whitespace_only_provider_name",
  "routing_off.unknown_role_model",
  // AC-919 item 2. Every other fixture case supplies all 16 settings fields
  // non-empty, so nothing reached the unset/empty layer -- the exact layer
  // AC-912 rewrites. These five pin its before-state so that rewrite can be
  // diffed rather than trusted.
  "unset_settings.agent_provider_empty",
  "unset_settings.blank_local_artifact",
  "unset_settings.mlx_model_path_empty",
  "unset_settings.role_model_empty_routing_off",
  "unset_settings.tier_model_empty",
];

const EXPECTED_ASSIGNMENT_KEYS = ["cost_per_1k_tokens", "model", "provider_class", "provider_type"];

const EXPECTED_GROUPS = Object.keys(EXPECTED_CASE_IDS).sort();

// Fixture groups whose cases are single routeRoleProvider() calls compared field by field.
const ROUTE_GROUPS = Object.keys(EXPECTED_CASE_IDS).filter((group) => group !== "cost_estimation");

// Must match _SETTINGS_DEFAULTS in the Python replay exactly. Keyed by the Python
// spelling and translated through the shared SETTINGS_KEY_MAP, so the two halves
// of a settings field cannot drift apart: adding one here without adding it to the
// contract fails the completeness test below, and adding it to the contract with a
// field name TypeScript lacks fails to compile in the generated module.
const BASE_SETTINGS_BY_PYTHON_KEY: Record<string, string> = {
  role_routing: "auto",
  agent_provider: "anthropic",
  competitor_provider: "",
  analyst_provider: "",
  coach_provider: "",
  architect_provider: "",
  model_competitor: "competitor-role-model",
  model_analyst: "analyst-role-model",
  model_coach: "coach-role-model",
  model_architect: "architect-role-model",
  model_curator: "curator-role-model",
  model_translator: "translator-role-model",
  tier_opus_model: "opus-tier-model",
  tier_sonnet_model: "sonnet-tier-model",
  tier_haiku_model: "haiku-tier-model",
  mlx_model_path: "/models/default-local",
  // AC-912. Empty is the shipped default; a non-empty value would fill every unset
  // role/tier slot for non-anthropic providers, a different contract than this
  // fixture pins.
  local_model: "",
  // AC-911. Empty means capability is inferred from the transport, which is the
  // state every case in this fixture was recorded under.
  provider_capability: "",
  provider_hosting: "",
  competitor_provider_capability: "",
  analyst_provider_capability: "",
  coach_provider_capability: "",
  architect_provider_capability: "",
  competitor_provider_hosting: "",
  analyst_provider_hosting: "",
  coach_provider_hosting: "",
  architect_provider_hosting: "",
};

function baseSettings(): RoleRoutingSettings {
  const settings = {} as RoleRoutingSettings;
  for (const [pythonKey, value] of Object.entries(BASE_SETTINGS_BY_PYTHON_KEY)) {
    settings[SETTINGS_KEY_MAP[pythonKey]] = value;
  }
  // Mirrors the Python replay's `model_fields_set = every field`: within this
  // fixture's contract every setting is configured, so per-provider default
  // resolution (AC-912) must not treat any of them as untouched.
  return {
    ...settings,
    configuredFields: Object.values(SETTINGS_KEY_MAP).slice().sort(),
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

// toBeCloseTo defaults to precision 2 (tolerance 0.005); the cost classes here are
// 0.015 / 0.003 / 0.001 / 0.0, so a default-precision check cannot distinguish
// most of them from each other. Pin to precision 6 so this assertion is at least
// as strict as Python's pytest.approx (relative tolerance 1e-6) on the same fields.
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
  expect(result.estimatedCostPer1kTokens).toBeCloseTo(expected.cost_per_1k_tokens, 6);
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

describe("known divergences", () => {
  it("contains every expected divergence exactly once", () => {
    const caseIds = FIXTURES.known_divergences.map((testCase) => testCase.case);
    expect(new Set(caseIds).size).toBe(caseIds.length);
    expect(caseIds.sort()).toEqual(EXPECTED_DIVERGENCE_CASE_IDS);
  });

  for (const testCase of FIXTURES.known_divergences) {
    it(`pins the TypeScript output for ${testCase.case}`, () => {
      expect(Object.keys(testCase.python).sort()).toEqual(EXPECTED_ASSIGNMENT_KEYS);
      expect(Object.keys(testCase.typescript).sort()).toEqual(EXPECTED_ASSIGNMENT_KEYS);
      expect(testCase.typescript).not.toEqual(testCase.python);
      expect(testCase.reason.trim()).not.toBe("");
      expect(testCase.resolution.trim()).not.toBe("");

      const result = routeRoleProvider(
        settingsFromFixture(testCase.settings),
        testCase.role,
        testCase.context ?? {},
      );
      assertRouteMatches(result, testCase.typescript);
    });
  }
});

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
      expect(estimate.totalPer1kTokens).toBeCloseTo(testCase.expected.total_per_1k_tokens, 6);
      expect(estimate.allFrontierPer1kTokens).toBeCloseTo(
        testCase.expected.all_frontier_per_1k_tokens,
        6,
      );
      expect(estimate.savingsVsAllFrontier).toBeCloseTo(
        testCase.expected.savings_vs_all_frontier,
        6,
      );
    });
  }
});

describe("fixture completeness", () => {
  it("replays every expected fixture group", () => {
    expect(Object.keys(FIXTURES.fixtures).sort()).toEqual(EXPECTED_GROUPS);
    expect([...ROUTE_GROUPS, "cost_estimation"].sort()).toEqual(EXPECTED_GROUPS);
  });

  it("replays every expected fixture case", () => {
    const actual = Object.fromEntries(
      Object.entries(FIXTURES.fixtures).map(([group, cases]) => [group, Object.keys(cases).sort()]),
    );
    const expected = Object.fromEntries(
      Object.entries(EXPECTED_CASE_IDS).map(([group, caseIds]) => [group, [...caseIds].sort()]),
    );
    expect(actual).toEqual(expected);
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

  // AC-911. Exact-set equality, not a subset check: a settings field this replay
  // stops supplying is as much a coverage hole as one it never supplied. AC-912
  // added `local_model` to the Python replay and not here, and nothing failed --
  // this is the assertion that would have caught it.
  it("supplies every settings field the shared contract declares", () => {
    expect(Object.keys(BASE_SETTINGS_BY_PYTHON_KEY).sort()).toEqual(
      Object.keys(SETTINGS_KEY_MAP).sort(),
    );
  });
});
