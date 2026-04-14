export type ActivationState = "candidate" | "shadow" | "active" | "disabled" | "deprecated";

export const ACTIVATION_STATES: readonly ActivationState[] = [
  "candidate",
  "shadow",
  "active",
  "disabled",
  "deprecated",
];

export interface PromotionEvent {
  from: ActivationState;
  to: ActivationState;
  reason: string;
  evidence?: Record<string, unknown>;
  timestamp: string;
}

export interface ModelRecord {
  artifactId: string;
  scenario: string;
  family: string;
  backend: string;
  checkpointDir: string;
  activationState: ActivationState;
  promotionHistory: PromotionEvent[];
  registeredAt: string;
}

export interface PromotionCheck {
  currentState: ActivationState;
  heldOutScore: number;
  incumbentScore: number;
  shadowRunScore?: number;
  parseFailureRate: number;
  validationFailureRate: number;
}

export interface PromotionDecision {
  promote: boolean;
  rollback: boolean;
  targetState: ActivationState;
  reasoning: string;
}

export interface ShadowRunOpts {
  incumbentScore: number;
  heldOutScore: number;
}

export interface PromotionThresholds {
  heldOutMinRatio: number;
  shadowMinRatio: number;
  maxParseFailureRate: number;
  maxValidationFailureRate: number;
  regressionThreshold: number;
}

export type ShadowExecutor = (artifactId: string, scenario: string) => Promise<{
  score: number;
  parseFailureRate: number;
  validationFailureRate: number;
  samplesRun: number;
}>;
