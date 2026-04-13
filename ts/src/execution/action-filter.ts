/**
 * ActionFilterHarness — constrains LLM action selection to valid moves.
 *
 * Wraps match execution to enumerate legal actions from the scenario or
 * loaded harness, format them as numbered prompts, and parse LLM responses.
 * Supports filter mode (LLM selects by index) and verify mode (LLM proposes,
 * harness validates).
 */

export {
  ActionDictSchema,
  type ActionDict,
  type HarnessLoaderLike,
  type ScenarioLike,
} from "./action-filter-contracts.js";
import type { ActionDict, HarnessLoaderLike, ScenarioLike } from "./action-filter-contracts.js";
import { getLegalActions } from "./action-filter-discovery-workflow.js";
import { formatActionPrompt } from "./action-filter-prompt-workflow.js";
import { parseActionSelection } from "./action-filter-selection-workflow.js";
import { getVerifyFeedback, verifyAction } from "./action-filter-verification-workflow.js";

export class ActionFilterHarness {
  readonly #scenario: ScenarioLike;
  readonly #harnessLoader: HarnessLoaderLike | null;

  constructor(scenario: ScenarioLike, harnessLoader: HarnessLoaderLike | null = null) {
    this.#scenario = scenario;
    this.#harnessLoader = harnessLoader;
  }

  getLegalActions(state: Record<string, unknown>): ActionDict[] | null {
    return getLegalActions(this.#scenario, state, this.#harnessLoader);
  }

  formatActionPrompt(actions: ActionDict[]): string {
    return formatActionPrompt(actions);
  }

  parseActionSelection(
    response: string,
    actions: ActionDict[],
  ): Record<string, unknown> | ActionDict | null {
    return parseActionSelection(response, actions);
  }

  verifyAction(
    state: Record<string, unknown>,
    playerId: string,
    proposed: Record<string, unknown>,
  ): [boolean, string] {
    return verifyAction(this.#scenario, state, playerId, proposed);
  }

  getVerifyFeedback(reason: string, state: Record<string, unknown>): string {
    return getVerifyFeedback(reason, this.getLegalActions(state));
  }
}
