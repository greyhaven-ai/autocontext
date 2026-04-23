import type { ToolFragilitySpec } from "./tool-fragility-spec.js";
import { parseRawToolFragilitySpec } from "./tool-fragility-spec.js";
import {
  designFamilySpec,
  parseFamilyDesignerSpec,
  type FamilyDesignerDescriptor,
} from "./family-designer.js";

export const TOOL_FRAGILITY_SPEC_START = "<!-- TOOL_FRAGILITY_SPEC_START -->";
export const TOOL_FRAGILITY_SPEC_END = "<!-- TOOL_FRAGILITY_SPEC_END -->";

const TOOL_FRAGILITY_DESCRIPTOR: FamilyDesignerDescriptor<ToolFragilitySpec> = {
  family: "tool_fragility",
  startDelimiter: TOOL_FRAGILITY_SPEC_START,
  endDelimiter: TOOL_FRAGILITY_SPEC_END,
  missingDelimiterLabel: "TOOL_FRAGILITY_SPEC",
  parseRaw: parseRawToolFragilitySpec,
};

const EXAMPLE_SPEC = {
  description: "API contracts drift during a data processing pipeline.",
  environment_description: "Microservice architecture with versioned API contracts.",
  initial_state_description: "All tools at v1; pipeline runs successfully.",
  tool_contracts: [
    { tool_name: "search_api", version: 1, description: "Search endpoint returning flat list." },
    { tool_name: "transform_api", version: 1, description: "Data transformation endpoint." },
  ],
  success_criteria: [
    "complete the pipeline despite tool changes",
    "detect and adapt to changed response formats",
  ],
  failure_modes: ["using stale response format", "selecting wrong tool"],
  max_steps: 10,
  actions: [
    {
      name: "call_search",
      description: "Call the search API with a query.",
      parameters: { query: "string" },
      preconditions: [],
      effects: ["search_results_obtained"],
    },
    {
      name: "call_transform",
      description: "Transform data using the transformation API.",
      parameters: { data: "string" },
      preconditions: ["call_search"],
      effects: ["data_transformed"],
    },
  ],
};

export const TOOL_FRAGILITY_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a tool-fragility or environment-drift scenario, produce a ToolFragilitySpec JSON.

Wrap the output in delimiters:
${TOOL_FRAGILITY_SPEC_START}
{ ... }
${TOOL_FRAGILITY_SPEC_END}

Schema:
{
  "description": "scenario summary",
  "environment_description": "what system has drifting tools",
  "initial_state_description": "starting state with stable tools",
  "tool_contracts": [
    {
      "tool_name": "api_name",
      "version": 1,
      "description": "what this tool does"
    }
  ],
  "success_criteria": ["criterion"],
  "failure_modes": ["failure mode"],
  "max_steps": 10,
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
- include at least two tool contracts
- model the scenario around adapting to changed tool behavior
- include at least two actions

Example:
${TOOL_FRAGILITY_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${TOOL_FRAGILITY_SPEC_END}
`;

export function parseToolFragilitySpec(text: string): ToolFragilitySpec {
  return parseFamilyDesignerSpec(text, TOOL_FRAGILITY_DESCRIPTOR);
}

export async function designToolFragility(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<ToolFragilitySpec> {
  return designFamilySpec(
    description,
    TOOL_FRAGILITY_DESIGNER_SYSTEM,
    TOOL_FRAGILITY_DESCRIPTOR,
    llmFn,
  );
}
