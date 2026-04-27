import type { SimulationSpec } from "./simulation-spec.js";
import { parseRawSimulationSpec } from "./simulation-spec.js";
import {
  designFamilySpec,
  parseFamilyDesignerSpec,
  type FamilyDesignerDescriptor,
} from "./family-designer.js";

export const SIM_SPEC_START = "<!-- SIMULATION_SPEC_START -->";
export const SIM_SPEC_END = "<!-- SIMULATION_SPEC_END -->";

const SIMULATION_DESCRIPTOR: FamilyDesignerDescriptor<SimulationSpec> = {
  family: "simulation",
  startDelimiter: SIM_SPEC_START,
  endDelimiter: SIM_SPEC_END,
  missingDelimiterLabel: "SIMULATION_SPEC",
  parseRaw: parseRawSimulationSpec,
};

const EXAMPLE_SPEC = {
  description: "Recover a multi-step API workflow after a mid-flow cancellation.",
  environment_description: "Mock booking system with dependent flight, hotel, and transport steps.",
  initial_state_description: "No bookings exist yet. A flight cancellation may occur mid-flow.",
  success_criteria: [
    "all required bookings are completed consistently",
    "partial side effects are rolled back or compensated cleanly",
  ],
  failure_modes: ["flight cancellation", "dependency mismatch", "partial side effects"],
  max_steps: 8,
  actions: [
    {
      name: "book_flight",
      description: "Reserve a flight matching the request.",
      parameters: { flight_id: "string" },
      preconditions: [],
      effects: ["flight_reserved"],
    },
    {
      name: "book_hotel",
      description: "Reserve a hotel after the flight exists.",
      parameters: { hotel_id: "string" },
      preconditions: ["book_flight"],
      effects: ["hotel_reserved"],
    },
  ],
};

export const SIMULATION_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a stateful or action-trace task, produce a SimulationSpec JSON.

Wrap the output in delimiters:
${SIM_SPEC_START}
{ ... }
${SIM_SPEC_END}

Schema:
{
  "description": "human readable scenario summary",
  "environment_description": "what the mock environment models",
  "initial_state_description": "starting state narrative",
  "success_criteria": ["criterion 1", "criterion 2"],
  "failure_modes": ["failure mode"],
  "max_steps": 8,
  "actions": [
    {
      "name": "snake_case_action",
      "description": "what the action does",
      "parameters": {"param": "type"},
      "preconditions": ["prior_action"],
      "effects": ["effect"]
    }
  ]
}

Rules:
- model the task as a mock environment with explicit actions
- use preconditions to encode dependency ordering
- include at least two success criteria
- keep the action set minimal but sufficient to complete and recover the workflow

Example:
${SIM_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${SIM_SPEC_END}
`;

export function parseSimulationSpec(text: string): SimulationSpec {
  return parseFamilyDesignerSpec(text, SIMULATION_DESCRIPTOR);
}

export async function designSimulation(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<SimulationSpec> {
  return designFamilySpec(
    description,
    SIMULATION_DESIGNER_SYSTEM,
    SIMULATION_DESCRIPTOR,
    llmFn,
  );
}
