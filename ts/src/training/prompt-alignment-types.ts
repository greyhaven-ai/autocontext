export interface PromptShape {
  systemFields: string[];
  userFields: string[];
  responseFormat: string;
}

export interface PromptPair {
  system: string;
  user: string;
  expectedOutput?: string;
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export interface AlignmentReport {
  aligned: boolean;
  mismatches: string[];
  trainingSections: string[];
  runtimeSections: string[];
}

export interface ShareGPTExample {
  conversations: Array<{ from: string; value: string }>;
  metadata?: Record<string, unknown>;
}

export type PromptContextLike = Record<string, unknown>;

export interface TrainingPromptRecord {
  scenario: string;
  strategy: string;
  score: number;
  context: Record<string, unknown>;
}
