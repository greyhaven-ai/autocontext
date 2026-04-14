import { buildComponentChangeMagnitudes } from "./credit-assignment-magnitude.js";
import { ComponentChange, GenerationChangeVector } from "./credit-assignment-models.js";

export function computeGenerationChangeVector(
  generation: number,
  scoreDelta: number,
  previousState: Record<string, unknown>,
  currentState: Record<string, unknown>,
): GenerationChangeVector {
  return new GenerationChangeVector(
    generation,
    scoreDelta,
    buildComponentChangeMagnitudes(previousState, currentState).map(
      (change) => new ComponentChange(change.component, change.magnitude, change.description),
    ),
  );
}
