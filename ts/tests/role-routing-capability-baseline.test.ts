/**
 * AC-911: capability/hosting baseline, recorded before the split.
 *
 * ../../docs/role-routing-capability-baseline.json holds one input per case and
 * the output each language produces for it. This file replays the TypeScript
 * half; autocontext/tests/test_role_routing_capability_baseline.py replays the
 * Python half against the same inputs. Neither asserts the two agree -- they
 * currently do not, and the file records that honestly rather than hiding it.
 *
 * Why this is separate from role-routing-parity.test.ts: that fixture supplies
 * all settings fields non-empty in every case and reports every field as
 * configured, so it cannot reach the unset/default-resolution layer at all. Each
 * case here carries `set_fields`, so a case can say "the user set nothing but the
 * provider" -- the normal way somebody points the loop at a local server, and the
 * layer AC-911 changes.
 *
 * To re-record after an intentional behavior change:
 *
 *   AC911_BASELINE_WRITE=1 npx vitest run tests/role-routing-capability-baseline.test.ts
 *   npx prettier --write ../docs/role-routing-capability-baseline.json
 *
 * Each language writes only its own key, so re-recording one cannot silently
 * overwrite the other's measurement. The prettier pass only re-collapses short
 * arrays that JSON.stringify expands; skipping it changes no values and no CI
 * gate checks it, but it keeps the recorded diff to the lines that actually moved.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  SETTINGS_KEY_MAP,
  estimateRoleRoutingCost,
  routeRoleProvider,
  type RoleRoutingSettings,
} from "../src/providers/index.js";

type Assignment = {
  cost_per_1k_tokens: number;
  model: string | null;
  provider_class: string;
  provider_type: string;
};

type CostTotals = {
  all_frontier_per_1k_tokens: number;
  savings_vs_all_frontier: number;
  total_per_1k_tokens: number;
};

type BaselineCase<T> = {
  context: { availableLocalModels?: string[] };
  python: T | null;
  role?: string;
  set_fields: string[];
  settings: Record<string, string>;
  typescript: T | null;
};

type Baseline = {
  cases: Record<string, BaselineCase<Assignment>>;
  cost_cases: Record<string, BaselineCase<CostTotals>>;
  roles: string[];
  settings_defaults: Record<string, string>;
};

const BASELINE_PATH = join(
  import.meta.dirname,
  "..",
  "..",
  "docs",
  "role-routing-capability-baseline.json",
);

function load(): Baseline {
  return JSON.parse(readFileSync(BASELINE_PATH, "utf-8")) as Baseline;
}

function settingsFor(baseline: Baseline, testCase: BaselineCase<unknown>): RoleRoutingSettings {
  const unknown = Object.keys(testCase.settings).filter(
    (key) => !(key in baseline.settings_defaults),
  );
  if (unknown.length > 0) {
    throw new Error(`case sets unknown settings keys: ${unknown.sort().join(", ")}`);
  }
  const settings = {} as RoleRoutingSettings;
  for (const [pythonKey, value] of Object.entries(baseline.settings_defaults)) {
    settings[SETTINGS_KEY_MAP[pythonKey]] = testCase.settings[pythonKey] ?? value;
  }
  // The case's `set_fields` is what Python feeds to model_fields_set; the same
  // list in TypeScript spelling is what feeds configuredFields. Without it every
  // field would look deliberately chosen, because the values above include the
  // schema defaults -- which is exactly the confusion AC-911 removes.
  return {
    ...settings,
    configuredFields: testCase.set_fields.map((key) => SETTINGS_KEY_MAP[key]).sort(),
  };
}

function route(baseline: Baseline, testCase: BaselineCase<Assignment>): Assignment {
  const cfg = routeRoleProvider(settingsFor(baseline, testCase), testCase.role ?? "", {
    availableLocalModels: testCase.context.availableLocalModels ?? [],
  });
  return {
    provider_type: cfg.providerType,
    model: cfg.model,
    provider_class: cfg.providerClass,
    cost_per_1k_tokens: cfg.estimatedCostPer1kTokens,
  };
}

function estimate(baseline: Baseline, testCase: BaselineCase<CostTotals>): CostTotals {
  const est = estimateRoleRoutingCost(settingsFor(baseline, testCase), {
    availableLocalModels: testCase.context.availableLocalModels ?? [],
  });
  return {
    all_frontier_per_1k_tokens: est.allFrontierPer1kTokens,
    savings_vs_all_frontier: est.savingsVsAllFrontier,
    total_per_1k_tokens: est.totalPer1kTokens,
  };
}

/**
 * routeRoleProvider() reads AUTOCONTEXT_AGENT_PROVIDER / AUTOCONTEXT_PROVIDER live
 * from process.env and lets them outrank the passed settings. Python does not read
 * env at all. Without this isolation the baseline records the runner's environment.
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

if (process.env.AC911_BASELINE_WRITE === "1") {
  const savedAgent = process.env.AUTOCONTEXT_AGENT_PROVIDER;
  const savedGeneric = process.env.AUTOCONTEXT_PROVIDER;
  delete process.env.AUTOCONTEXT_AGENT_PROVIDER;
  delete process.env.AUTOCONTEXT_PROVIDER;
  const baseline = load();
  for (const testCase of Object.values(baseline.cases)) {
    testCase.typescript = route(baseline, testCase);
  }
  for (const testCase of Object.values(baseline.cost_cases)) {
    testCase.typescript = estimate(baseline, testCase);
  }
  writeFileSync(BASELINE_PATH, `${JSON.stringify(baseline, null, 2)}\n`, "utf-8");
  if (savedAgent !== undefined) process.env.AUTOCONTEXT_AGENT_PROVIDER = savedAgent;
  if (savedGeneric !== undefined) process.env.AUTOCONTEXT_PROVIDER = savedGeneric;
}

const BASELINE = load();

describe("capability/hosting baseline", () => {
  it.each(Object.keys(BASELINE.cases).sort())("routes %s as recorded", (caseId) => {
    const testCase = BASELINE.cases[caseId];
    expect(testCase.typescript).not.toBeNull();
    expect(route(BASELINE, testCase)).toEqual(testCase.typescript);
  });

  it.each(Object.keys(BASELINE.cost_cases).sort())("totals %s as recorded", (caseId) => {
    const testCase = BASELINE.cost_cases[caseId];
    expect(testCase.typescript).not.toBeNull();
    expect(estimate(BASELINE, testCase)).toEqual(testCase.typescript);
  });

  // AC-911's third criterion needs a guard that cannot be quietly emptied: every
  // role under both cloud routing modes, so narrowing the regression surface
  // fails here instead of looking green.
  it("covers cloud-only routing for every role in both modes", () => {
    const expected = ["auto", "off"]
      .flatMap((mode) => BASELINE.roles.map((role) => `cloud_regression.${mode}.anthropic.${role}`))
      .sort();
    const present = Object.keys(BASELINE.cases);
    expect(expected.filter((id) => !present.includes(id))).toEqual([]);
  });

  // The unknown-key guard is otherwise never executed, and if it rotted into a
  // no-op a misspelled settings key would silently record default behavior.
  it("rejects an unknown settings key", () => {
    expect(() =>
      settingsFor(BASELINE, {
        context: {},
        python: null,
        set_fields: ["not_a_real_setting"],
        settings: { not_a_real_setting: "x" },
        typescript: null,
      }),
    ).toThrow(/unknown settings keys/);
  });
});
