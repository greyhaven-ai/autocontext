// model-routing actuator (AC-545) — registers on module import.
//
// Target pattern: **/routing/models/*.json (sibling of routing-rule's
// **/routing/*.json — the two actuators own distinct subtrees).

import { registerActuator, type ActuatorRegistration } from "../registry.js";
import { modelRoutingActuator } from "./applicator.js";
import type { ModelRoutingPayload } from "./schema.js";

export const modelRoutingRegistration: ActuatorRegistration<ModelRoutingPayload> = {
  type: "model-routing",
  rollback: { kind: "content-revert" },
  allowedTargetPattern: "**/routing/models/*.json",
  actuator: modelRoutingActuator,
};

registerActuator(modelRoutingRegistration);

export { modelRoutingActuator } from "./applicator.js";
export {
  ModelRoutingPayloadSchema,
  RouteSchema,
  MatchExpressionSchema,
  MatchOperatorSchema,
  ModelTargetSchema,
  RolloutSchema,
  BudgetGuardrailSchema,
  LatencyGuardrailSchema,
  ConfidenceGuardrailSchema,
  FallbackEntrySchema,
  FallbackReasonSchema,
  MODEL_ROUTING_FILENAME,
} from "./schema.js";
export type {
  ModelRoutingPayload,
  ModelTarget,
  MatchOperator,
  MatchExpression,
  Rollout,
  BudgetGuardrail,
  LatencyGuardrail,
  ConfidenceGuardrail,
  Route,
  FallbackEntry,
  FallbackReason,
} from "./schema.js";
