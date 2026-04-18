// fine-tuned-model actuator — registers on module import.

import { registerActuator, type ActuatorRegistration } from "../registry.js";
import { fineTunedModelActuator } from "./applicator.js";
import type { FineTunedModelPayload } from "./schema.js";

export const fineTunedModelRegistration: ActuatorRegistration<FineTunedModelPayload> = {
  type: "fine-tuned-model",
  rollback: { kind: "pointer-flip" },
  allowedTargetPattern: "**/models/active/*.json",
  actuator: fineTunedModelActuator,
};

registerActuator(fineTunedModelRegistration);

export { fineTunedModelActuator } from "./applicator.js";
export { FineTunedModelPayloadSchema, FINE_TUNED_MODEL_FILENAME } from "./schema.js";
export type { FineTunedModelPayload } from "./schema.js";
export { importLegacyModelRecords } from "./legacy-adapter.js";
