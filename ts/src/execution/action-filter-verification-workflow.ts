import type {
  ActionDict,
  ScenarioLike,
} from "./action-filter-contracts.js";
import { formatActionPrompt } from "./action-filter-prompt-workflow.js";

export function verifyAction(
  scenario: ScenarioLike,
  state: Record<string, unknown>,
  playerId: string,
  proposed: Record<string, unknown>,
): [boolean, string] {
  return scenario.validateActions(state, playerId, proposed);
}

export function getVerifyFeedback(
  reason: string,
  legalActions: ActionDict[] | null,
): string {
  const parts: string[] = [`Invalid action: ${reason}`];
  if (legalActions) {
    parts.push(formatActionPrompt(legalActions));
  }
  parts.push("Please try again.");
  return parts.join("\n");
}
