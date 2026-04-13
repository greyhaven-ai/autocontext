import type { ActionDict } from "./action-filter-contracts.js";
import { isContinuousParamSpace } from "./action-filter-prompt-workflow.js";

export function extractJsonObject(response: string): Record<string, unknown> | null {
  const candidates: string[] = [];
  const fenced = response.match(/```(?:json)?\s*(\{[\s\S]*?\})\s*```/i);
  if (fenced?.[1]) {
    candidates.push(fenced[1]);
  }
  const start = response.indexOf("{");
  const end = response.lastIndexOf("}");
  if (start !== -1 && end > start) {
    candidates.push(response.slice(start, end + 1));
  }
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

export function parseContinuousSelection(
  response: string,
  actions: ActionDict[],
): Record<string, number> | null {
  const payload = extractJsonObject(response);
  if (!payload) {
    return null;
  }

  const strategy: Record<string, number> = {};
  for (const action of actions) {
    const key = action.action;
    if (!(key in payload)) {
      return null;
    }
    const raw = payload[key];
    if (typeof raw !== "number" || Number.isNaN(raw)) {
      return null;
    }
    const [low, high] = action.range!;
    if (raw < low || raw > high) {
      return null;
    }
    strategy[key] = raw;
  }
  return strategy;
}

export function parseActionSelection(
  response: string,
  actions: ActionDict[],
): Record<string, unknown> | ActionDict | null {
  if (actions.length === 0) {
    return null;
  }

  if (isContinuousParamSpace(actions)) {
    return parseContinuousSelection(response, actions);
  }

  const match = response.trim().match(/\b(\d+)\b/);
  if (match) {
    const index = Number.parseInt(match[1], 10);
    if (index >= 1 && index <= actions.length) {
      return actions[index - 1];
    }
  }

  const normalizedResponse = response.trim().toLowerCase();
  for (const action of actions) {
    if (action.action && normalizedResponse.includes(action.action.toLowerCase())) {
      return action;
    }
  }

  return null;
}
