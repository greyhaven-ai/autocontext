import type { OperatorLoopSpec } from "./operator-loop-spec.js";
import { parseRawOperatorLoopSpec } from "./operator-loop-spec.js";
import {
  designFamilySpec,
  parseFamilyDesignerSpec,
  type FamilyDesignerDescriptor,
} from "./family-designer.js";

export const OPERATOR_LOOP_SPEC_START = "<!-- OPERATOR_LOOP_SPEC_START -->";
export const OPERATOR_LOOP_SPEC_END = "<!-- OPERATOR_LOOP_SPEC_END -->";

const OPERATOR_LOOP_DESCRIPTOR: FamilyDesignerDescriptor<OperatorLoopSpec> = {
  family: "operator_loop",
  startDelimiter: OPERATOR_LOOP_SPEC_START,
  endDelimiter: OPERATOR_LOOP_SPEC_END,
  missingDelimiterLabel: "OPERATOR_LOOP_SPEC",
  parseRaw: parseRawOperatorLoopSpec,
};

export const OPERATOR_LOOP_DESIGNER_SYSTEM = `You are describing operator-in-the-loop capabilities for autocontext.
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
- keep the scenario neutral and capability-oriented
- do not anchor the scenario to a canned domain, action set, or scoring pattern
- avoid prescriptive examples that imply a preferred escalation workflow
`;

export function parseOperatorLoopSpec(text: string): OperatorLoopSpec {
  return parseFamilyDesignerSpec(text, OPERATOR_LOOP_DESCRIPTOR);
}

export async function designOperatorLoop(
  description: string,
  llmFn: (system: string, user: string) => Promise<string>,
): Promise<OperatorLoopSpec> {
  return designFamilySpec(
    description,
    OPERATOR_LOOP_DESIGNER_SYSTEM,
    OPERATOR_LOOP_DESCRIPTOR,
    llmFn,
  );
}
