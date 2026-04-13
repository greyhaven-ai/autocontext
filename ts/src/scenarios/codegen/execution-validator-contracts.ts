export interface ExecutionValidationResult {
  /** Whether the generated code passed all execution checks. */
  valid: boolean;
  /** Error descriptions for any failures. */
  errors: string[];
  /** Methods that were successfully called during validation. */
  executedMethods: string[];
  /** Duration of the validation run in milliseconds. */
  durationMs: number;
}

export type ExecutableScenario = Record<string, (...args: unknown[]) => unknown>;

export interface ExecutionValidationContext {
  errors: string[];
  executedMethods: string[];
}
