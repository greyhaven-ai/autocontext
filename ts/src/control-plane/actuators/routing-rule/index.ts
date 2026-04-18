// routing-rule actuator — registers on module import.

import { registerActuator, type ActuatorRegistration } from "../registry.js";
import { routingRuleActuator } from "./applicator.js";
import type { RoutingRulePayload } from "./schema.js";

export const routingRuleRegistration: ActuatorRegistration<RoutingRulePayload> = {
  type: "routing-rule",
  rollback: { kind: "cascade-set", dependsOn: ["tool-policy"] },
  allowedTargetPattern: "**/routing/*.json",
  actuator: routingRuleActuator,
};

registerActuator(routingRuleRegistration);

export { routingRuleActuator } from "./applicator.js";
export {
  RoutingRulePayloadSchema,
  RoutingRuleEntrySchema,
  ROUTING_RULE_FILENAME,
} from "./schema.js";
export type { RoutingRulePayload, RoutingRuleEntry } from "./schema.js";
