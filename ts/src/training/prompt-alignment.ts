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

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

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

type PromptContextLike = Record<string, unknown>;

function readString(ctx: PromptContextLike, ...keys: string[]): string {
  for (const key of keys) {
    const value = ctx[key];
    if (typeof value === "string" && value.trim()) {
      return value.trim();
    }
  }
  return "";
}

function formatTrajectory(value: unknown): string {
  if (typeof value === "string") {
    return value.trim();
  }
  if (!Array.isArray(value)) {
    return "";
  }
  const lines = value
    .map((entry, idx) => {
      if (!entry || typeof entry !== "object") {
        return "";
      }
      const row = entry as Record<string, unknown>;
      const generation = typeof row.generation_index === "number" ? row.generation_index : idx + 1;
      const score = typeof row.best_score === "number" ? row.best_score.toFixed(4) : "unknown";
      const gate = typeof row.gate_decision === "string" ? row.gate_decision : "unknown";
      return `Generation ${generation}: score=${score}, gate=${gate}`;
    })
    .filter(Boolean);
  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Section detection
// ---------------------------------------------------------------------------

const KNOWN_SECTIONS = [
  "Scenario Rules",
  "Strategy Interface",
  "Evaluation Criteria",
  "Current Playbook",
  "Operational Lessons",
  "Available Tools",
  "Competitor Hints",
  "Previous Analysis",
  "Your Task",
  "Playbook",
];

function extractSections(text: string): string[] {
  const sections: string[] = [];
  const textLower = text.toLowerCase();
  for (const section of KNOWN_SECTIONS) {
    const sectionLower = section.toLowerCase();
    // Match ## Section, # Section, ### Section, with flexible whitespace
    const patterns = [
      `## ${sectionLower}`,
      `# ${sectionLower}`,
      `### ${sectionLower}`,
      `**${sectionLower}**`, // bold as header
    ];
    if (patterns.some((p) => textLower.includes(p))) {
      sections.push(section);
    }
  }
  return sections;
}

// ---------------------------------------------------------------------------
// PromptContract
// ---------------------------------------------------------------------------

const REQUIRED_SYSTEM_SECTIONS = ["Scenario Rules", "Evaluation Criteria"];
const REQUIRED_USER_SECTIONS = ["Your Task"];

export class PromptContract {
  shape(): PromptShape {
    return {
      systemFields: ["scenarioRules", "strategyInterface", "evaluationCriteria", "playbook", "trajectory"],
      userFields: ["task"],
      responseFormat: "JSON strategy or structured text matching scenario interface",
    };
  }

  validate(prompt: PromptPair): ValidationResult {
    const errors: string[] = [];
    const systemSections = extractSections(prompt.system);

    for (const required of REQUIRED_SYSTEM_SECTIONS) {
      if (!systemSections.includes(required)) {
        errors.push(`Missing required system section: ${required}`);
      }
    }

    if (!prompt.user || prompt.user.trim().length < 3) {
      errors.push("User prompt is empty or too short");
    }

    return { valid: errors.length === 0, errors };
  }
}

// ---------------------------------------------------------------------------
// RuntimePromptAdapter
// ---------------------------------------------------------------------------

export class RuntimePromptAdapter {
  /**
   * Convert a runtime prompt bundle (from buildPromptBundle) into
   * the contract-compatible {system, user} pair.
   */
  fromBundle(bundle: { competitor: string }): PromptPair {
    const prompt = bundle.competitor;

    // Split on "## Your Task" — everything before is system, after is user
    const taskMarker = "## Your Task";
    const taskIdx = prompt.indexOf(taskMarker);

    if (taskIdx >= 0) {
      return {
        system: prompt.slice(0, taskIdx).trim(),
        user: prompt.slice(taskIdx + taskMarker.length).trim(),
      };
    }

    // Fallback: last paragraph is user, rest is system
    const parts = prompt.split("\n\n");
    if (parts.length >= 2) {
      return {
        system: parts.slice(0, -1).join("\n\n").trim(),
        user: parts[parts.length - 1].trim(),
      };
    }

    return { system: prompt, user: "" };
  }
}

// ---------------------------------------------------------------------------
// TrainingPromptAdapter
// ---------------------------------------------------------------------------

export class TrainingPromptAdapter {
  /**
   * Convert a training record into a contract-compatible prompt pair.
   */
  fromTrainingRecord(record: {
    scenario: string;
    strategy: string;
    score: number;
    context: Record<string, unknown>;
  }): PromptPair {
    const ctx = record.context;
    const systemParts: string[] = [];
    const scenarioRules = readString(ctx, "scenarioRules", "scenario_rules");
    const strategyInterface = readString(ctx, "strategyInterface", "strategy_interface");
    const evaluationCriteria = readString(ctx, "evaluationCriteria", "evaluation_criteria");
    const trajectory = formatTrajectory(ctx.trajectory);
    const playbook = readString(ctx, "playbook");
    const lessons = readString(ctx, "lessons", "operationalLessons", "operational_lessons");
    const tools = readString(ctx, "tools", "availableTools", "available_tools");
    const hints = readString(ctx, "hints", "competitorHints", "competitor_hints");
    const analysis = readString(ctx, "analysis", "previousAnalysis", "previous_analysis");

    if (scenarioRules) {
      systemParts.push(`## Scenario Rules\n${scenarioRules}`);
    }
    if (strategyInterface) {
      systemParts.push(`## Strategy Interface\n${strategyInterface}`);
    }
    if (evaluationCriteria) {
      systemParts.push(`## Evaluation Criteria\n${evaluationCriteria}`);
    }
    if (trajectory) {
      systemParts.push(trajectory);
    }
    if (playbook) {
      systemParts.push(`## Current Playbook\n\n${playbook}`);
    }
    if (lessons) {
      systemParts.push(`## Operational Lessons\n\n${lessons}`);
    }
    if (tools) {
      systemParts.push(`## Available Tools\n\n${tools}`);
    }
    if (hints) {
      systemParts.push(`## Competitor Hints\n\n${hints}`);
    }
    if (analysis) {
      systemParts.push(`## Previous Analysis\n\n${analysis}`);
    }

    return {
      system: systemParts.join("\n\n"),
      user: "Produce a JSON strategy that maximizes the evaluation criteria.",
      expectedOutput: record.strategy,
    };
  }

  /**
   * Convert a training record into a ShareGPT training example
   * that matches the runtime prompt contract.
   */
  toTrainingExample(record: {
    scenario: string;
    strategy: string;
    score: number;
    context: Record<string, unknown>;
  }): ShareGPTExample {
    const pair = this.fromTrainingRecord(record);

    return {
      conversations: [
        { from: "system", value: pair.system },
        { from: "human", value: pair.user },
        { from: "gpt", value: record.strategy },
      ],
      metadata: {
        scenario: record.scenario,
        score: record.score,
        contractVersion: "1.0",
      },
    };
  }
}

// ---------------------------------------------------------------------------
// Alignment validation
// ---------------------------------------------------------------------------

/**
 * Validate that training prompts and runtime prompts use the same
 * structural contract. Reports mismatches.
 */
export function validatePromptAlignment(opts: {
  trainingPrompt: PromptPair;
  runtimePrompt: PromptPair;
}): AlignmentReport {
  const trainingSections = extractSections(opts.trainingPrompt.system);
  const runtimeSections = extractSections(opts.runtimePrompt.system);

  const mismatches: string[] = [];

  // Check sections in runtime but missing from training
  for (const section of runtimeSections) {
    if (!trainingSections.includes(section)) {
      mismatches.push(`Section '${section}' present in runtime but missing from training`);
    }
  }

  // Check sections in training but missing from runtime
  for (const section of trainingSections) {
    if (!runtimeSections.includes(section)) {
      mismatches.push(`Section '${section}' present in training but missing from runtime`);
    }
  }

  // Check user prompt similarity
  if (opts.trainingPrompt.user !== opts.runtimePrompt.user) {
    const trainWords = new Set(opts.trainingPrompt.user.toLowerCase().split(/\s+/));
    const runtimeWords = new Set(opts.runtimePrompt.user.toLowerCase().split(/\s+/));
    const overlap = [...trainWords].filter((w) => runtimeWords.has(w)).length;
    const similarity = overlap / Math.max(trainWords.size, runtimeWords.size);
    if (similarity < 0.5) {
      mismatches.push("User prompts differ significantly between training and runtime");
    }
  }

  return {
    aligned: mismatches.length === 0,
    mismatches,
    trainingSections,
    runtimeSections,
  };
}
