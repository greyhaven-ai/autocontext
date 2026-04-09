/**
 * Codegen registry — routes family names to codegen functions (AC-436).
 *
 * Each codegen function takes a family-specific spec and produces a JS source
 * string that implements the family's interface methods.
 */

export { ScenarioRuntime, CodegenUnsupportedFamilyError } from "./runtime.js";
export type { ScenarioProxy, ScenarioRuntimeOpts } from "./runtime.js";
export { validateGeneratedScenario } from "./execution-validator.js";
export type { ExecutionValidationResult } from "./execution-validator.js";
export {
  generateScenarioSource,
  hasCodegen,
  generateAndValidateScenarioSource,
} from "./registry.js";
export type { CodegenFn } from "./registry.js";
