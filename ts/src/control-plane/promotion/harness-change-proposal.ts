import type {
  Artifact,
  EvalRun,
  HarnessChangeDecision,
  HarnessChangeProposal,
  HarnessValidationEvidence,
  PromotionThresholds,
} from "../contract/types.js";
import { decidePromotion } from "./decide.js";

export interface DecideHarnessChangeProposalInputs {
  readonly proposal: HarnessChangeProposal;
  readonly candidate: { readonly artifact: Artifact; readonly evalRun: EvalRun };
  readonly baseline: { readonly artifact: Artifact; readonly evalRun: EvalRun } | null;
  readonly thresholds: PromotionThresholds;
  readonly validation: HarnessValidationEvidence;
  readonly decidedAt: string;
}

export function decideHarnessChangeProposal(
  inputs: DecideHarnessChangeProposalInputs,
): HarnessChangeDecision {
  const promotionDecision = decidePromotion({
    candidate: inputs.candidate,
    baseline: inputs.baseline,
    thresholds: inputs.thresholds,
    evaluatedAt: inputs.decidedAt,
  });

  const status = classifyHarnessDecision(inputs.validation.mode, promotionDecision.pass, inputs.baseline !== null);
  return {
    status,
    reason: reasonForHarnessDecision(status, inputs.validation.mode, promotionDecision.reasoning),
    validation: inputs.validation,
    promotionDecision,
    candidateArtifactId: inputs.candidate.artifact.id,
    candidateEvalRunId: inputs.candidate.evalRun.runId,
    ...(inputs.baseline !== null
      ? {
          baselineArtifactId: inputs.baseline.artifact.id,
          baselineEvalRunId: inputs.baseline.evalRun.runId,
        }
      : {}),
    decidedAt: inputs.decidedAt,
  };
}

function classifyHarnessDecision(
  mode: HarnessValidationEvidence["mode"],
  promotionPassed: boolean,
  hasBaseline: boolean,
): HarnessChangeDecision["status"] {
  if (mode === "dev" || !hasBaseline) return "inconclusive";
  return promotionPassed ? "accepted" : "rejected";
}

function reasonForHarnessDecision(
  status: HarnessChangeDecision["status"],
  mode: HarnessValidationEvidence["mode"],
  promotionReasoning: string,
): string {
  if (status === "inconclusive") {
    return mode === "dev"
      ? `Dev-only validation is not enough for promotion; rerun on heldout or fresh traces. ${promotionReasoning}`
      : `Baseline comparison is required for evidence-gated harness promotion. ${promotionReasoning}`;
  }
  if (status === "accepted") {
    return `Accepted on ${mode} validation. ${promotionReasoning}`;
  }
  return `Rejected on ${mode} validation. ${promotionReasoning}`;
}
