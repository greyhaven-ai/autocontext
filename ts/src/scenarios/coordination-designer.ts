import type { CoordinationSpec } from "./coordination-spec.js";
import { parseRawCoordinationSpec } from "./coordination-spec.js";
import { healSpec } from "./spec-auto-heal.js";

export const COORDINATION_SPEC_START = "<!-- COORDINATION_SPEC_START -->";
export const COORDINATION_SPEC_END = "<!-- COORDINATION_SPEC_END -->";

const EXAMPLE_SPEC = {
  description: "Multi-agent research report writing.",
  environment_description: "Research team with partial information.",
  initial_state_description: "Task partitioned across workers.",
  workers: [
    { worker_id: "researcher", role: "data gatherer" },
    { worker_id: "writer", role: "report writer" },
  ],
  success_criteria: [
    "coherent merged report",
    "minimal duplication across sections",
  ],
  failure_modes: [
    "duplicate content across workers",
    "lost information during handoff",
  ],
  max_steps: 10,
  actions: [
    {
      name: "research",
      description: "Gather data on assigned topic.",
      parameters: { topic: "string" },
      preconditions: [],
      effects: ["data_gathered"],
    },
    {
      name: "write_section",
      description: "Write a report section.",
      parameters: { section: "string" },
      preconditions: ["research"],
      effects: ["section_written"],
    },
  ],
};

export const COORDINATION_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a multi-agent coordination scenario, produce a CoordinationSpec JSON.

Wrap the output in delimiters:
${COORDINATION_SPEC_START}
{ ... }
${COORDINATION_SPEC_END}

Schema:
{
  "description": "scenario summary",
  "environment_description": "team context",
  "initial_state_description": "starting state",
  "workers": [{"worker_id": "name", "role": "role"}],
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
- include at least two workers with distinct roles
- workers do not share full context by default
- include at least two actions

Example:
${COORDINATION_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${COORDINATION_SPEC_END}
`;

export function parseCoordinationSpec(text: string): CoordinationSpec {
  const startIdx = text.indexOf(COORDINATION_SPEC_START);
  const endIdx = text.indexOf(COORDINATION_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain COORDINATION_SPEC delimiters");
  }
  const raw = text.slice(startIdx + COORDINATION_SPEC_START.length, endIdx).trim();
  return parseRawCoordinationSpec(
    healSpec(JSON.parse(raw) as Record<string, unknown>, "coordination"),
  );
}

export async function designCoordination(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<CoordinationSpec> {
  return parseCoordinationSpec(
    await llmFn(COORDINATION_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
