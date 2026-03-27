/**
 * Spec auto-heal — graceful recovery from malformed specs (AC-440).
 *
 * Ports Python's spec_auto_heal.py and adds broader healing:
 * - Missing sampleInput when prompt references external data
 * - Type coercion (string "10" → number 10)
 * - Missing field inference (empty description → derived from taskPrompt)
 * - Per-family healing applied before codegen
 *
 * The goal: NL descriptions are messy. Auto-heal turns "your description
 * had a minor issue" into "we fixed it and created the scenario."
 */

import type { AgentTaskSpec } from "./agent-task-spec.js";

// ---------------------------------------------------------------------------
// External data detection patterns (ported from Python)
// ---------------------------------------------------------------------------

const ALWAYS_EXTERNAL_PATTERNS = [
  "you will be provided with",
];

const CONTEXTUAL_DATA_PATTERNS = [
  "given the following data",
  "analyze the following",
  "using the provided",
  "based on the data below",
  "review the following",
  "examine the data",
];

const INLINE_DATA_MARKERS = ["{", "[", "|", "- ", "* ", "##", "```"];
const INLINE_DATA_MIN_CHARS = 20;

function hasInlineDataAfter(prompt: string, pattern: string): boolean {
  const idx = prompt.toLowerCase().indexOf(pattern);
  if (idx < 0) return false;
  const after = prompt.slice(idx + pattern.length).trim();
  if (!after || after.length < INLINE_DATA_MIN_CHARS) return false;

  // Check for structured data markers
  for (const marker of INLINE_DATA_MARKERS) {
    if (after.includes(marker)) return true;
  }

  // Check for key-value lines
  const lines = after.split("\n").filter((l) => l.trim());
  const kvLines = lines.filter((l) => /^[A-Za-z0-9 _()/.-]{1,40}:\s+\S/.test(l.trim()));
  if (kvLines.length >= 2) return true;

  return false;
}

// ---------------------------------------------------------------------------
// Sample input detection
// ---------------------------------------------------------------------------

/**
 * Detect when a spec needs auto-generated sampleInput.
 *
 * Returns true when:
 * - sampleInput is undefined/null/empty
 * - taskPrompt references external data
 * - No substantial inline data follows the reference
 */
export function needsSampleInput(spec: AgentTaskSpec): boolean {
  if (spec.sampleInput != null && spec.sampleInput.trim().length > 0) {
    return false;
  }

  const promptLower = spec.taskPrompt.toLowerCase();

  for (const pattern of ALWAYS_EXTERNAL_PATTERNS) {
    if (promptLower.includes(pattern)) return true;
  }

  for (const pattern of CONTEXTUAL_DATA_PATTERNS) {
    if (promptLower.includes(pattern) && !hasInlineDataAfter(spec.taskPrompt, pattern)) {
      return true;
    }
  }

  return false;
}

// ---------------------------------------------------------------------------
// Synthetic sample input generation
// ---------------------------------------------------------------------------

const STOP_WORDS = new Set([
  "the", "a", "an", "and", "or", "of", "for", "to", "in", "on", "with",
  "is", "are", "will", "be", "you", "your", "this", "that", "from",
  "have", "has", "been", "should", "could", "would", "can", "may",
]);

function extractDomainHints(taskPrompt: string, description: string): string[] {
  const text = `${taskPrompt} ${description}`.toLowerCase();
  const words = text.replace(/[^a-z0-9\s]/g, " ").split(/\s+/);
  return words
    .filter((w) => w.length > 3 && !STOP_WORDS.has(w))
    .slice(0, 10);
}

const COLLECTION_WORDS = new Set(["data", "records", "items", "list", "entries", "results"]);
const ENTITY_WORDS = new Set(["patient", "customer", "user", "client", "employee", "student"]);
const ITEM_WORDS = new Set(["drug", "medication", "interaction", "product", "order", "transaction"]);

/**
 * Generate a synthetic placeholder sampleInput from task context.
 * Deterministic heuristic, not an LLM call.
 */
export function generateSyntheticSampleInput(
  taskPrompt: string,
  description = "",
): string {
  const hints = extractDomainHints(taskPrompt, description);
  const sample: Record<string, unknown> = {};

  for (let i = 0; i < Math.min(hints.length, 5); i++) {
    const hint = hints[i];
    if (COLLECTION_WORDS.has(hint)) {
      sample[hint] = [`sample_${hint}_1`, `sample_${hint}_2`];
    } else if (ENTITY_WORDS.has(hint)) {
      sample[hint] = { name: `Sample ${hint.charAt(0).toUpperCase() + hint.slice(1)}`, id: `${hint}-001` };
    } else if (ITEM_WORDS.has(hint)) {
      sample[hint] = [`sample_${hint}_A`, `sample_${hint}_B`];
    } else {
      sample[`field_${i + 1}_${hint}`] = `sample_${hint}_value`;
    }
  }

  if (Object.keys(sample).length === 0) {
    sample.input_data = [
      { id: "sample-1", value: "placeholder data point 1" },
      { id: "sample-2", value: "placeholder data point 2" },
    ];
  }

  return JSON.stringify(sample, null, 2);
}

// ---------------------------------------------------------------------------
// Agent task spec healing
// ---------------------------------------------------------------------------

/**
 * Auto-heal an AgentTaskSpec by generating synthetic sampleInput if needed.
 * Returns a new spec (does not mutate the original).
 */
export function healAgentTaskSpec(
  spec: AgentTaskSpec,
  description = "",
): AgentTaskSpec {
  if (!needsSampleInput(spec)) return spec;
  const synthetic = generateSyntheticSampleInput(spec.taskPrompt, description);
  return { ...spec, sampleInput: synthetic };
}

// ---------------------------------------------------------------------------
// Type coercion
// ---------------------------------------------------------------------------

const NUMERIC_FIELD_PATTERNS = /^(max|min|limit|count|threshold|steps|rounds|quality|size|depth|width|height|port|timeout|retries)/i;
const BOOLEAN_FIELDS = new Set(["retryable", "enabled", "active", "visible", "required", "optional"]);

/**
 * Coerce string values to their likely intended types.
 * Fixes common LLM output issues like maxSteps: "10" → maxSteps: 10.
 */
export function coerceSpecTypes(spec: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {};

  for (const [key, value] of Object.entries(spec)) {
    if (typeof value === "string") {
      // String → number
      if (NUMERIC_FIELD_PATTERNS.test(key) || key.endsWith("_steps") || key.endsWith("Steps")) {
        const num = Number(value);
        if (!isNaN(num) && value.trim() !== "") {
          result[key] = num;
          continue;
        }
      }

      // String → boolean
      if (BOOLEAN_FIELDS.has(key)) {
        if (value.toLowerCase() === "true") { result[key] = true; continue; }
        if (value.toLowerCase() === "false") { result[key] = false; continue; }
      }
    }

    result[key] = value;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Missing field inference
// ---------------------------------------------------------------------------

/**
 * Infer missing fields from available context.
 * Does not overwrite existing non-empty values.
 */
export function inferMissingFields(spec: Record<string, unknown>): Record<string, unknown> {
  const result = { ...spec };
  const taskPrompt = typeof spec.taskPrompt === "string" ? spec.taskPrompt : "";

  // Infer description from taskPrompt
  if (!result.description || (typeof result.description === "string" && !result.description.trim())) {
    if (taskPrompt) {
      // Take first sentence or first 100 chars
      const firstSentence = taskPrompt.split(/[.!?]\s/)[0];
      result.description = firstSentence.length > 100
        ? firstSentence.slice(0, 100) + "..."
        : firstSentence + ".";
    }
  }

  // Infer rubric from taskPrompt
  const hasRubric = (result.rubric && typeof result.rubric === "string" && result.rubric.trim()) ||
    (result.judgeRubric && typeof result.judgeRubric === "string" && (result.judgeRubric as string).trim());
  if (!hasRubric && taskPrompt) {
    const inferredRubric = `Evaluate the quality and completeness of the response to: ${taskPrompt.slice(0, 80)}`;
    result.rubric = inferredRubric;
    result.judgeRubric = inferredRubric;
  }

  return result;
}

// ---------------------------------------------------------------------------
// Generic heal (all families)
// ---------------------------------------------------------------------------

/**
 * Apply all healing passes to a spec before codegen.
 *
 * 1. Type coercion (string → number/boolean)
 * 2. Missing field inference
 * 3. Family-specific healing (e.g., agent_task sampleInput)
 *
 * Returns a new spec object (does not mutate the original).
 */
export function healSpec(
  spec: Record<string, unknown>,
  family: string,
  description?: string,
): Record<string, unknown> {
  let healed = { ...spec };

  // Pass 1: type coercion
  healed = coerceSpecTypes(healed);

  // Pass 2: missing field inference
  healed = inferMissingFields(healed);

  // Pass 3: family-specific healing
  if (family === "agent_task") {
    const taskSpec = healed as unknown as AgentTaskSpec;
    const healedTask = healAgentTaskSpec(taskSpec, description);
    healed = healedTask as unknown as Record<string, unknown>;
  }

  return healed;
}
