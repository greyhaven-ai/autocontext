import { roundToDecimals } from "./number-utils.js";

export interface ComponentChangeMagnitude {
  component: string;
  magnitude: number;
  description: string;
}

export function textChangeMagnitude(oldValue: string, newValue: string): number {
  if (oldValue === newValue) {
    return 0;
  }
  if (!oldValue && !newValue) {
    return 0;
  }
  if (!oldValue || !newValue) {
    return 1;
  }

  const maxLen = Math.max(oldValue.length, newValue.length);
  let common = 0;
  const overlap = Math.min(oldValue.length, newValue.length);
  for (let index = 0; index < overlap; index += 1) {
    if (oldValue[index] === newValue[index]) {
      common += 1;
    }
  }
  return roundToDecimals(1 - common / maxLen, 4);
}

export function listChangeMagnitude(oldValues: unknown[], newValues: unknown[]): number {
  const oldSet = new Set(oldValues.map((value) => String(value)));
  const newSet = new Set(newValues.map((value) => String(value)));
  if (oldSet.size === newSet.size && [...oldSet].every((value) => newSet.has(value))) {
    return 0;
  }

  const union = new Set([...oldSet, ...newSet]);
  if (union.size === 0) {
    return 0;
  }

  let diff = 0;
  for (const value of union) {
    if (oldSet.has(value) !== newSet.has(value)) {
      diff += 1;
    }
  }
  return roundToDecimals(diff / union.size, 4);
}

export function buildComponentChangeMagnitudes(
  previousState: Record<string, unknown>,
  currentState: Record<string, unknown>,
): ComponentChangeMagnitude[] {
  const changes: ComponentChangeMagnitude[] = [];

  const oldPlaybook = String(previousState.playbook ?? "");
  const newPlaybook = String(currentState.playbook ?? "");
  const playbookMagnitude = textChangeMagnitude(oldPlaybook, newPlaybook);
  if (playbookMagnitude > 0) {
    changes.push({ component: "playbook", magnitude: playbookMagnitude, description: `Playbook changed (${Math.round(playbookMagnitude * 100)}%)` });
  }

  const oldTools = Array.isArray(previousState.tools) ? previousState.tools : [];
  const newTools = Array.isArray(currentState.tools) ? currentState.tools : [];
  const toolsMagnitude = listChangeMagnitude(oldTools, newTools);
  if (toolsMagnitude > 0) {
    const oldSet = new Set(oldTools.map((value) => String(value)));
    const newSet = new Set(newTools.map((value) => String(value)));
    const added = [...newSet].filter((value) => !oldSet.has(value)).length;
    const removed = [...oldSet].filter((value) => !newSet.has(value)).length;
    changes.push({ component: "tools", magnitude: toolsMagnitude, description: `+${added}/-${removed} tools` });
  }

  const oldHints = String(previousState.hints ?? "");
  const newHints = String(currentState.hints ?? "");
  const hintsMagnitude = textChangeMagnitude(oldHints, newHints);
  if (hintsMagnitude > 0) {
    changes.push({ component: "hints", magnitude: hintsMagnitude, description: `Hints changed (${Math.round(hintsMagnitude * 100)}%)` });
  }

  const oldAnalysis = String(previousState.analysis ?? "");
  const newAnalysis = String(currentState.analysis ?? "");
  const analysisMagnitude = textChangeMagnitude(oldAnalysis, newAnalysis);
  if (analysisMagnitude > 0) {
    changes.push({ component: "analysis", magnitude: analysisMagnitude, description: `Analysis changed (${Math.round(analysisMagnitude * 100)}%)` });
  }

  return changes;
}
