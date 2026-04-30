import { describe, expect, it } from "vitest";

import {
  parseSchemaEvolutionSpec,
  SCHEMA_EVOLUTION_SPEC_END,
  SCHEMA_EVOLUTION_SPEC_START,
} from "../src/scenarios/schema-evolution-designer.js";

describe("schema-evolution designer parsing", () => {
  it("recovers long Pi-style fenced JSON with comments, trailing commas, and camelCase fields", () => {
    const raw = `
Pi explored a few variants and selected the schema migration harness below.

${SCHEMA_EVOLUTION_SPEC_START}
\`\`\`json
{
  // Pi sometimes echoes a JS-style annotation in long runs.
  "description": "Portfolio schema evolution under macro regime changes",
  "environmentDescription": "A portfolio API changes allocation and risk schemas over time.",
  "initialStateDescription": "The v1 schema contains equities, bonds, cash, commodities, and hedges.",
  "mutations": [
    {
      "version": 2,
      "description": "Add drawdown limits and regime confidence.",
      "breaking": false,
      "fieldsAdded": ["drawdown_limit", "regime_confidence"],
      "fieldsRemoved": [],
      "fieldsModified": {},
    },
    {
      "version": 3,
      "description": "Rename equities to risk_assets and remove legacy hedge weight.",
      "breaking": true,
      "fieldsAdded": ["risk_assets"],
      "fieldsRemoved": ["equities", "legacy_hedge_weight"],
      "fieldsModified": {"cash": "number -> object"},
    },
  ],
  "successCriteria": ["detect schema mutations", "avoid stale removed fields"],
  "failureModes": ["using equities after v3", "ignoring cash type changes"],
  "maxSteps": 8,
  "actions": [
    {
      "name": "inspect_schema",
      "description": "Inspect the active schema.",
      "parameters": {"endpoint": "string"},
      "preconditions": [],
      "effects": ["schema_observed"],
    },
    {
      "name": "rebalance_portfolio",
      "description": "Submit allocation under the active schema.",
      "parameters": {"allocation": "object"},
      "preconditions": ["inspect_schema"],
      "effects": ["allocation_submitted"],
    },
  ],
}
\`\`\`
${SCHEMA_EVOLUTION_SPEC_END}

That is the final spec.`;

    const spec = parseSchemaEvolutionSpec(raw);

    expect(spec.description).toContain("Portfolio schema evolution");
    expect(spec.environmentDescription).toContain("portfolio API");
    expect(spec.mutations[1]?.fieldsRemoved).toContain("equities");
    expect(spec.actions.map((action) => action.name)).toEqual([
      "inspect_schema",
      "rebalance_portfolio",
    ]);
  });

  it("still fails explicitly when no JSON object is recoverable", () => {
    expect(() =>
      parseSchemaEvolutionSpec(
        `${SCHEMA_EVOLUTION_SPEC_START}\nnot json at all\n${SCHEMA_EVOLUTION_SPEC_END}`,
      ),
    ).toThrow(/invalid SCHEMA_EVOLUTION_SPEC JSON/);
  });
});
