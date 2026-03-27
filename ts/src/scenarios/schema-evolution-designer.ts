import type { SchemaEvolutionSpec } from "./schema-evolution-spec.js";
import { parseRawSchemaEvolutionSpec } from "./schema-evolution-spec.js";
import { healSpec } from "./spec-auto-heal.js";

export const SCHEMA_EVOLUTION_SPEC_START = "<!-- SCHEMA_EVOLUTION_SPEC_START -->";
export const SCHEMA_EVOLUTION_SPEC_END = "<!-- SCHEMA_EVOLUTION_SPEC_END -->";

const EXAMPLE_SPEC = {
  description: "API schema evolves from v1 to v3 during a data migration task.",
  environment_description: "REST API backend with versioned schemas.",
  initial_state_description: "v1 schema is active; all endpoints respond with v1 format.",
  mutations: [
    {
      version: 2,
      description: "Add 'priority' field to task objects.",
      breaking: false,
      fields_added: ["priority"],
      fields_removed: [],
      fields_modified: {},
    },
    {
      version: 3,
      description: "Rename 'status' to 'state' and remove 'legacy_id'.",
      breaking: true,
      fields_added: ["state"],
      fields_removed: ["status", "legacy_id"],
      fields_modified: {},
    },
  ],
  success_criteria: [
    "detect each schema version change",
    "discard stale assumptions about removed fields",
  ],
  failure_modes: ["using removed fields after mutation", "caching stale schema"],
  max_steps: 8,
  actions: [
    {
      name: "query_api",
      description: "Query an API endpoint and observe the response schema.",
      parameters: { endpoint: "string" },
      preconditions: [],
      effects: ["schema_observed"],
    },
    {
      name: "validate_schema",
      description: "Check whether the current schema matches expectations.",
      parameters: {},
      preconditions: ["query_api"],
      effects: ["schema_validated"],
    },
  ],
};

export const SCHEMA_EVOLUTION_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a schema-evolution or stale-context scenario, produce a SchemaEvolutionSpec JSON.

Wrap the output in delimiters:
${SCHEMA_EVOLUTION_SPEC_START}
{ ... }
${SCHEMA_EVOLUTION_SPEC_END}

Schema:
{
  "description": "scenario summary",
  "environment_description": "what system has evolving schemas",
  "initial_state_description": "starting state with initial schema version",
  "mutations": [
    {
      "version": 2,
      "description": "what changed",
      "breaking": true,
      "fields_added": ["field"],
      "fields_removed": ["field"],
      "fields_modified": {"field": "old_type -> new_type"}
    }
  ],
  "success_criteria": ["criterion"],
  "failure_modes": ["failure mode"],
  "max_steps": 8,
  "actions": [
    {
      "name": "snake_case",
      "description": "what the action does",
      "parameters": {"param": "type"},
      "preconditions": [],
      "effects": ["effect"]
    }
  ]
}

Rules:
- include at least one breaking mutation
- model the scenario around detecting and adapting to schema changes
- include at least two actions and two mutations

Example:
${SCHEMA_EVOLUTION_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${SCHEMA_EVOLUTION_SPEC_END}
`;

export function parseSchemaEvolutionSpec(text: string): SchemaEvolutionSpec {
  const startIdx = text.indexOf(SCHEMA_EVOLUTION_SPEC_START);
  const endIdx = text.indexOf(SCHEMA_EVOLUTION_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain SCHEMA_EVOLUTION_SPEC delimiters");
  }
  const raw = text.slice(startIdx + SCHEMA_EVOLUTION_SPEC_START.length, endIdx).trim();
  return parseRawSchemaEvolutionSpec(
    healSpec(JSON.parse(raw) as Record<string, unknown>, "schema_evolution"),
  );
}

export async function designSchemaEvolution(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<SchemaEvolutionSpec> {
  return parseSchemaEvolutionSpec(
    await llmFn(SCHEMA_EVOLUTION_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
