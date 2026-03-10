/**
 * ActionFilterHarness — constrains LLM action selection to valid moves.
 *
 * Wraps match execution to enumerate legal actions from the scenario or
 * loaded harness, format them as numbered prompts, and parse LLM responses.
 * Supports filter mode (LLM selects by index) and verify mode (LLM proposes,
 * harness validates).
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export const ActionDictSchema = z
  .object({
    action: z.string(),
    description: z.string(),
    type: z.string().optional(),
    range: z.tuple([z.number(), z.number()]).optional(),
    row: z.number().optional(),
    col: z.number().optional(),
  })
  .passthrough();

export type ActionDict = z.infer<typeof ActionDictSchema>;

/**
 * Minimal scenario interface for the ActionFilterHarness.
 * Mirrors the Python ScenarioInterface methods used by the harness.
 */
export interface ScenarioLike {
  enumerateLegalActions(state: Record<string, unknown>): ActionDict[] | null;
  validateActions(
    state: Record<string, unknown>,
    playerId: string,
    actions: Record<string, unknown>,
  ): [boolean, string];
}

/**
 * Optional harness loader interface — used as fallback when the scenario
 * does not support enumerate_legal_actions.
 */
export interface HarnessLoaderLike {
  validators: Array<{
    enumerate_legal_actions?: (state: Record<string, unknown>) => ActionDict[];
  }>;
}

// ---------------------------------------------------------------------------
// ActionFilterHarness
// ---------------------------------------------------------------------------

export class ActionFilterHarness {
  private readonly scenario: ScenarioLike;
  private readonly harnessLoader: HarnessLoaderLike | null;

  constructor(
    scenario: ScenarioLike,
    harnessLoader: HarnessLoaderLike | null = null,
  ) {
    this.scenario = scenario;
    this.harnessLoader = harnessLoader;
  }

  /**
   * Get legal actions, preferring the scenario method over harness loader.
   * Returns null if enumeration is not supported by either source.
   */
  getLegalActions(state: Record<string, unknown>): ActionDict[] | null {
    const result = this.scenario.enumerateLegalActions(state);
    if (result !== null) {
      return result;
    }
    if (this.harnessLoader) {
      return this.getHarnessActions(state);
    }
    return null;
  }

  /**
   * Format actions as a numbered list for LLM selection.
   */
  formatActionPrompt(actions: ActionDict[]): string {
    if (actions.length === 0) {
      return "No actions available.";
    }
    if (this.isContinuousParamSpace(actions)) {
      const lines: string[] = ["Provide a JSON object with all strategy parameters:"];
      const example: Record<string, number> = {};
      for (const action of actions) {
        const name = action.action;
        const desc = action.description ?? "";
        const [low, high] = action.range!;
        lines.push(`- ${name}: ${desc} (range [${low}, ${high}])`);
        example[name] = Number(((low + high) / 2).toFixed(3));
      }
      lines.push(`Example: ${JSON.stringify(example)}`);
      lines.push("Respond with JSON only.");
      return lines.join("\n");
    }
    const lines: string[] = ["Available actions:"];
    for (let i = 0; i < actions.length; i++) {
      const action = actions[i];
      const name = action.action ?? `action_${i + 1}`;
      const desc = action.description ?? "";
      let extra = "";
      if (action.type === "continuous" && action.range) {
        extra = ` (continuous [${action.range[0]}, ${action.range[1]}])`;
      } else if (action.row !== undefined && action.col !== undefined) {
        extra = ` (row ${action.row}, col ${action.col})`;
      }
      let line = `${i + 1}. ${name}`;
      if (desc) {
        line += ` — ${desc}`;
      }
      line += extra;
      lines.push(line);
    }
    lines.push("Select an action by number:");
    return lines.join("\n");
  }

  /**
   * Parse LLM response to extract the selected action.
   * Handles numeric index and action name matching.
   * Returns null if no match found.
   */
  parseActionSelection(
    response: string,
    actions: ActionDict[],
  ): Record<string, unknown> | ActionDict | null {
    if (actions.length === 0) {
      return null;
    }

    if (this.isContinuousParamSpace(actions)) {
      return this.parseContinuousSelection(response, actions);
    }

    // Try numeric index first
    const match = response.trim().match(/\b(\d+)\b/);
    if (match) {
      const idx = parseInt(match[1], 10);
      if (idx >= 1 && idx <= actions.length) {
        return actions[idx - 1];
      }
    }

    // Try action name match
    const lower = response.trim().toLowerCase();
    for (const action of actions) {
      if (action.action && lower.includes(action.action.toLowerCase())) {
        return action;
      }
    }

    return null;
  }

  /**
   * Verify a proposed action using validateActions.
   * In verify mode, the LLM proposes freely and we check validity.
   */
  verifyAction(
    state: Record<string, unknown>,
    playerId: string,
    proposed: Record<string, unknown>,
  ): [boolean, string] {
    return this.scenario.validateActions(state, playerId, proposed);
  }

  /**
   * Build feedback string for verify mode retries.
   * Includes the rejection reason and available legal actions if enumerable.
   */
  getVerifyFeedback(
    reason: string,
    state: Record<string, unknown>,
  ): string {
    const parts: string[] = [`Invalid action: ${reason}`];
    const legal = this.getLegalActions(state);
    if (legal) {
      parts.push(this.formatActionPrompt(legal));
    }
    parts.push("Please try again.");
    return parts.join("\n");
  }

  private getHarnessActions(
    state: Record<string, unknown>,
  ): ActionDict[] | null {
    if (!this.harnessLoader) return null;
    for (const v of this.harnessLoader.validators) {
      if (typeof v.enumerate_legal_actions === "function") {
        try {
          const result = v.enumerate_legal_actions(state);
          if (Array.isArray(result)) {
            return result;
          }
        } catch {
          // harness enumerate_legal_actions failed, try next
        }
      }
    }
    return null;
  }

  private isContinuousParamSpace(actions: ActionDict[]): boolean {
    if (actions.length === 0) return false;
    return actions.every((action) => {
      if (action.type !== "continuous") return false;
      if (!action.range || action.range.length !== 2) return false;
      const [low, high] = action.range;
      return typeof low === "number" && typeof high === "number";
    });
  }

  private parseContinuousSelection(
    response: string,
    actions: ActionDict[],
  ): Record<string, number> | null {
    const payload = this.extractJsonObject(response);
    if (!payload) return null;

    const strategy: Record<string, number> = {};
    for (const action of actions) {
      const key = action.action;
      if (!(key in payload)) return null;
      const raw = payload[key];
      if (typeof raw !== "number" || Number.isNaN(raw)) return null;
      const [low, high] = action.range!;
      if (raw < low || raw > high) return null;
      strategy[key] = raw;
    }
    return strategy;
  }

  private extractJsonObject(response: string): Record<string, unknown> | null {
    const candidates: string[] = [];
    const fenced = response.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/i);
    if (fenced?.[1]) candidates.push(fenced[1]);
    const start = response.indexOf("{");
    const end = response.lastIndexOf("}");
    if (start !== -1 && end > start) candidates.push(response.slice(start, end + 1));
    for (const candidate of candidates) {
      try {
        const parsed = JSON.parse(candidate);
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          return parsed as Record<string, unknown>;
        }
      } catch {
        // continue
      }
    }
    return null;
  }
}
