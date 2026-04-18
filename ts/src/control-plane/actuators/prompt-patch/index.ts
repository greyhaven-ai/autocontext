// prompt-patch actuator — registers on module import.
//
// Allowed target pattern: **/prompts/**/*.{txt,md}
// The actuator itself always writes .txt; .md is permitted by the pattern so
// human-edited prompt files may also be targeted by future override flows.

import { registerActuator, type ActuatorRegistration } from "../registry.js";
import { promptPatchActuator } from "./applicator.js";
import type { PromptPatchPayload } from "./schema.js";

export const promptPatchRegistration: ActuatorRegistration<PromptPatchPayload> = {
  type: "prompt-patch",
  rollback: { kind: "content-revert" },
  allowedTargetPattern: "**/prompts/**/*.{txt,md}",
  actuator: promptPatchActuator,
};

registerActuator(promptPatchRegistration);

export { promptPatchActuator } from "./applicator.js";
export { PromptPatchPayloadSchema, PROMPT_PATCH_FILENAME } from "./schema.js";
export type { PromptPatchPayload } from "./schema.js";
