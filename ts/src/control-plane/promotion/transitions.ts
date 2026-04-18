import type { ActivationState } from "../contract/types.js";

export const ACTIVATION_STATES: readonly ActivationState[] = [
  "candidate",
  "shadow",
  "canary",
  "active",
  "disabled",
  "deprecated",
];

// Allow-list of valid (from, to) state transitions. Any (from, to) not in this
// map is rejected by the state machine. Self-loops are not allowed.
const ALLOWED: Readonly<Record<ActivationState, readonly ActivationState[]>> = {
  candidate:  ["shadow", "canary", "active", "disabled"],
  shadow:     ["canary", "active", "disabled", "candidate"],
  canary:     ["active", "disabled", "candidate", "shadow"],
  active:     ["deprecated", "disabled", "candidate", "canary", "shadow"],
  disabled:   ["candidate"],
  deprecated: ["candidate"],
};

export function isAllowedTransition(from: ActivationState, to: ActivationState): boolean {
  const allowed = ALLOWED[from];
  return allowed.includes(to);
}

export function nextStatesFrom(state: ActivationState): readonly ActivationState[] {
  return ALLOWED[state];
}
