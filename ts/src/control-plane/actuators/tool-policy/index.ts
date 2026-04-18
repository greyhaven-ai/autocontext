// tool-policy actuator — registers on module import.

import { registerActuator, type ActuatorRegistration } from "../registry.js";
import { toolPolicyActuator } from "./applicator.js";
import type { ToolPolicyPayload } from "./schema.js";

export const toolPolicyRegistration: ActuatorRegistration<ToolPolicyPayload> = {
  type: "tool-policy",
  rollback: { kind: "content-revert" },
  allowedTargetPattern: "**/policies/tools/*.json",
  actuator: toolPolicyActuator,
};

registerActuator(toolPolicyRegistration);

export { toolPolicyActuator } from "./applicator.js";
export {
  ToolPolicyPayloadSchema,
  ToolPolicyEntrySchema,
  TOOL_POLICY_FILENAME,
} from "./schema.js";
export type { ToolPolicyPayload, ToolPolicyEntry } from "./schema.js";
