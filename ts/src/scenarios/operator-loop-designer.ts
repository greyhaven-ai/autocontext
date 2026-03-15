import type { OperatorLoopSpec } from "./operator-loop-spec.js";
import { parseRawOperatorLoopSpec } from "./operator-loop-spec.js";

export const OPERATOR_LOOP_SPEC_START = "<!-- OPERATOR_LOOP_SPEC_START -->";
export const OPERATOR_LOOP_SPEC_END = "<!-- OPERATOR_LOOP_SPEC_END -->";

const EXAMPLE_SPEC = {
  description: "Customer support triage with escalation policy.",
  environment_description: "Help desk system with tiered support.",
  initial_state_description: "Ticket received, agent begins triage.",
  escalation_policy: {
    escalation_threshold: "high",
    max_escalations: 3,
  },
  success_criteria: [
    "resolve issue or correctly escalate",
    "minimize unnecessary escalations",
  ],
  failure_modes: [
    "over-escalation (escalating trivial issues)",
    "under-escalation (failing to escalate critical issues)",
  ],
  max_steps: 10,
  actions: [
    {
      name: "respond",
      description: "Reply to the customer directly.",
      parameters: { message: "string" },
      preconditions: [],
      effects: ["response_sent"],
    },
    {
      name: "escalate_ticket",
      description: "Escalate to a human operator.",
      parameters: { reason: "string" },
      preconditions: [],
      effects: ["escalated"],
    },
  ],
};

export const OPERATOR_LOOP_DESIGNER_SYSTEM = `You are a scenario designer for autocontext.
Given a natural-language request for an operator-in-the-loop scenario, produce an OperatorLoopSpec JSON.

Wrap the output in delimiters:
${OPERATOR_LOOP_SPEC_START}
{ ... }
${OPERATOR_LOOP_SPEC_END}

Schema:
{
  "description": "scenario summary",
  "environment_description": "system context",
  "initial_state_description": "starting state",
  "escalation_policy": {"escalation_threshold": "level", "max_escalations": 3},
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
- escalation_policy must include escalation_threshold and max_escalations
- include at least one action that acts and one that escalates
- failure_modes should include both over-escalation and under-escalation

Example:
${OPERATOR_LOOP_SPEC_START}
${JSON.stringify(EXAMPLE_SPEC, null, 2)}
${OPERATOR_LOOP_SPEC_END}
`;

export function parseOperatorLoopSpec(text: string): OperatorLoopSpec {
  const startIdx = text.indexOf(OPERATOR_LOOP_SPEC_START);
  const endIdx = text.indexOf(OPERATOR_LOOP_SPEC_END);
  if (startIdx === -1 || endIdx === -1 || endIdx <= startIdx) {
    throw new Error("response does not contain OPERATOR_LOOP_SPEC delimiters");
  }
  const raw = text.slice(startIdx + OPERATOR_LOOP_SPEC_START.length, endIdx).trim();
  return parseRawOperatorLoopSpec(JSON.parse(raw) as Record<string, unknown>);
}

export async function designOperatorLoop(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<OperatorLoopSpec> {
  return parseOperatorLoopSpec(
    await llmFn(OPERATOR_LOOP_DESIGNER_SYSTEM, `User description:\n${description}`),
  );
}
