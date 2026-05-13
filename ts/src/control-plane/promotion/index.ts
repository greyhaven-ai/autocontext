export {
  isAllowedTransition,
  nextStatesFrom,
  ACTIVATION_STATES,
} from "./transitions.js";

export { appendPromotionEvent } from "./append.js";

export {
  defaultThresholds,
  computeConfidence,
} from "./thresholds.js";

export { decidePromotion } from "./decide.js";
export type { DecidePromotionInputs } from "./decide.js";
export { decideHarnessChangeProposal } from "./harness-change-proposal.js";
export type { DecideHarnessChangeProposalInputs } from "./harness-change-proposal.js";
