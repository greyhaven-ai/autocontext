import type {
  HarnessChangeDecision,
  HarnessChangeProposal,
  HarnessChangeSurface,
  HarnessValidationMode,
} from "./types.js";

export const HARNESS_CHANGE_SURFACES = [
  "prompt",
  "tool-schema",
  "tool-affordance-policy",
  "compaction-policy",
  "verifier-rubric",
  "retry-policy",
  "playbook",
] as const;

export const HARNESS_VALIDATION_MODES = ["dev", "heldout", "fresh"] as const;

export function isHarnessChangeSurface(value: string): value is HarnessChangeSurface {
  return HARNESS_CHANGE_SURFACES.some((surface) => surface === value);
}

export function isHarnessValidationMode(value: string): value is HarnessValidationMode {
  return HARNESS_VALIDATION_MODES.some((mode) => mode === value);
}

export function withHarnessChangeDecision(
  proposal: HarnessChangeProposal,
  decision: HarnessChangeDecision,
): HarnessChangeProposal {
  return {
    ...proposal,
    status: decision.status,
    decision,
  };
}
