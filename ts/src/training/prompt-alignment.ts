/**
 * Prompt alignment — training ↔ runtime contract (AC-457).
 *
 * Ensures distilled local models are trained on the same prompt surface
 * they'll encounter at runtime. Closes the gap between training-time
 * evaluation and runtime invocation.
 *
 * Three components:
 * 1. PromptContract — defines canonical prompt shape for local models
 * 2. RuntimePromptAdapter — converts runtime bundles to contract shape
 * 3. TrainingPromptAdapter — converts training records to contract shape
 * 4. validatePromptAlignment — checks training vs runtime alignment
 */

import {
  buildPromptContractShape,
  validatePromptContract,
} from "./prompt-contract-workflow.js";
import { validatePromptAlignmentReport } from "./prompt-alignment-validation.js";
import { adaptRuntimePromptBundle } from "./runtime-prompt-adapter-workflow.js";
import {
  adaptTrainingPromptRecord,
  buildTrainingShareGptExample,
} from "./training-prompt-adapter-workflow.js";
import type {
  AlignmentReport,
  PromptPair,
  PromptShape,
  ShareGPTExample,
  TrainingPromptRecord,
  ValidationResult,
} from "./prompt-alignment-types.js";

export type {
  AlignmentReport,
  PromptPair,
  PromptShape,
  ShareGPTExample,
  ValidationResult,
} from "./prompt-alignment-types.js";

export class PromptContract {
  shape(): PromptShape {
    return buildPromptContractShape();
  }

  validate(prompt: PromptPair): ValidationResult {
    return validatePromptContract(prompt);
  }
}

export class RuntimePromptAdapter {
  fromBundle(bundle: { competitor: string }): PromptPair {
    return adaptRuntimePromptBundle(bundle);
  }
}

export class TrainingPromptAdapter {
  fromTrainingRecord(record: TrainingPromptRecord): PromptPair {
    return adaptTrainingPromptRecord(record);
  }

  toTrainingExample(record: TrainingPromptRecord): ShareGPTExample {
    return buildTrainingShareGptExample(record);
  }
}

export function validatePromptAlignment(opts: {
  trainingPrompt: PromptPair;
  runtimePrompt: PromptPair;
}): AlignmentReport {
  return validatePromptAlignmentReport(opts);
}
