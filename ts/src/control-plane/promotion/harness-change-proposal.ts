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

  const hasEvidenceRefs = inputs.validation.evidenceRefs.length > 0;
  const status = classifyHarnessDecision(
    inputs.validation.mode,
    promotionDecision.pass,
    inputs.baseline !== null,
    hasEvidenceRefs,
  );
  return {
    status,
    reason: reasonForHarnessDecision(
      status,
      inputs.validation.mode,
      promotionDecision.reasoning,
      inputs.baseline !== null,
      hasEvidenceRefs,
    ),
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
  hasEvidenceRefs: boolean,
): HarnessChangeDecision["status"] {
  if (mode === "dev" || !hasBaseline || !hasEvidenceRefs) return "inconclusive";
  return promotionPassed ? "accepted" : "rejected";
}

function reasonForHarnessDecision(
  status: HarnessChangeDecision["status"],
  mode: HarnessValidationEvidence["mode"],
  promotionReasoning: string,
  hasBaseline: boolean,
  hasEvidenceRefs: boolean,
): string {
  if (status === "inconclusive") {
    if (mode === "dev") {
      return `Dev-only validation is not enough for promotion; rerun on heldout or fresh traces. ${promotionReasoning}`;
    }
    if (!hasBaseline) {
      return `Baseline comparison is required for evidence-gated harness promotion. ${promotionReasoning}`;
    }
    if (!hasEvidenceRefs) {
      return `At least one evidence reference is required for ${mode} harness promotion. ${promotionReasoning}`;
    }
    return `Harness proposal validation is inconclusive. ${promotionReasoning}`;
  }
  if (status === "accepted") {
    return `Accepted on ${mode} validation. ${promotionReasoning}`;
  }
  return `Rejected on ${mode} validation. ${promotionReasoning}`;
}
