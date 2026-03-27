import type { NegotiationSpec } from "./negotiation-spec.js";
import { parseRawNegotiationSpec } from "./negotiation-spec.js";
import { healSpec } from "./spec-auto-heal.js";

export const NEGOTIATION_SPEC_START = "<!-- NEGOTIATION_SPEC_START -->";
export const NEGOTIATION_SPEC_END = "<!-- NEGOTIATION_SPEC_END -->";

const EXAMPLE_SPEC = {
  description: "Contract price negotiation with hidden BATNA.",
  environment_description: "Buyer-seller negotiation over contract terms.",
  initial_state_description: "Both parties have opening positions; hidden preferences unknown.",
  hidden_preferences: {
    priorities: { price: 0.6, delivery_time: 0.3, warranty: 0.1 },
    reservation_value: 50.0,
    aspiration_value: 85.0,
    batna_description: "Switch to alternative vendor with longer lead time.",
  },
  max_rounds: 5,
  success_criteria: [
    "reach agreement above reservation value",
    "accurately model opponent priorities by final round",
  ],
  failure_modes: ["deadlock without agreement", "accept below BATNA"],
  actions: [
    {
      name: "make_offer",
      description: "Propose contract terms to the opponent.",
      parameters: { terms: "dict" },
      preconditions: [],
      effects: ["offer_on_table"],
    },
    {
      name: "counter_offer",
      description: "Respond with modified terms.",
      parameters: { terms: "dict" },
      preconditions: ["make_offer"],
      effects: ["counter_on_table"],
    },
    {
      name: "accept",
      description: "Accept the current terms on the table.",
      parameters: {},
      preconditions: ["make_offer"],
      effects: ["deal_closed"],
    },
  ],
};

export const NEGOTIATION_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for a negotiation or adversarial hidden-state scenario, produce a NegotiationSpec JSON.

Wrap the output in delimiters:
${NEGOTIATION_SPEC_START}
{ ... }
${NEGOTIATION_SPEC_END}

Schema:
{
  "description": "scenario summary",
  "environment_description": "negotiation context",
  "initial_state_description": "starting positions",
  "hidden_preferences": {
    "priorities": {"dimension": weight},
    "reservation_value": 50.0,
    "aspiration_value": 85.0,
    "batna_description": "string"
  },
  "max_rounds": 5,
  "success_criteria": ["criterion"],
  "failure_modes": ["failure mode"],
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
- hidden_preferences must include priorities, reservation_value, aspiration_value, batna_description
- include at least two actions
- max_rounds should be between 2 and 10

Example:
${NEGOTIATION_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${NEGOTIATION_SPEC_END}
`;

export function parseNegotiationSpec(text: string): NegotiationSpec {
  const startIdx = text.indexOf(NEGOTIATION_SPEC_START);
  const endIdx = text.indexOf(NEGOTIATION_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain NEGOTIATION_SPEC delimiters");
  }
  const raw = text.slice(startIdx + NEGOTIATION_SPEC_START.length, endIdx).trim();
  return parseRawNegotiationSpec(
    healSpec(JSON.parse(raw) as Record<string, unknown>, "negotiation"),
  );
}

export async function designNegotiation(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<NegotiationSpec> {
  return parseNegotiationSpec(
    await llmFn(NEGOTIATION_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
