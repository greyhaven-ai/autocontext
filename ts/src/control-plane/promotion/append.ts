import type { Artifact, PromotionEvent } from "../contract/types.js";
import { isAllowedTransition } from "./transitions.js";

/**
 * Append a PromotionEvent to an Artifact's history, returning a new Artifact.
 * Rejects:
 *   - event.from !== artifact.activationState (local precondition)
 *   - (from, to) not in the transition allow-list (P5 enforcement at the constructor)
 *
 * Immutable: the input Artifact is not mutated.
 */
export function appendPromotionEvent(artifact: Artifact, event: PromotionEvent): Artifact {
  if (event.from !== artifact.activationState) {
    throw new Error(
      `appendPromotionEvent: event.from=${event.from} does not match artifact.activationState=${artifact.activationState}`,
    );
  }
  if (!isAllowedTransition(event.from, event.to)) {
    throw new Error(
      `appendPromotionEvent: transition ${event.from} → ${event.to} is not in the allow-list`,
    );
  }
  return {
    ...artifact,
    activationState: event.to,
    promotionHistory: [...artifact.promotionHistory, event],
  };
}
