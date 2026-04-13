import type { AgentTaskSpec } from "./agent-task-spec.js";
import {
  getNumberValue,
  getRecordArrayValue,
  getStringArrayValue,
  getStringValue,
} from "./spec-auto-heal-readers.js";

const ALWAYS_EXTERNAL_PATTERNS = ["you will be provided with"];

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

  for (const marker of INLINE_DATA_MARKERS) {
    if (after.includes(marker)) return true;
  }

  const lines = after.split("\n").filter((line) => line.trim());
  const kvLines = lines.filter((line) =>
    /^[A-Za-z0-9 _()/.-]{1,40}:\s+\S/.test(line.trim()),
  );
  if (kvLines.length >= 2) return true;

  return false;
}

export function needsSampleInput(spec: AgentTaskSpec): boolean {
  if (spec.sampleInput != null && spec.sampleInput.trim().length > 0) {
    return false;
  }

  const promptLower = spec.taskPrompt.toLowerCase();

  for (const pattern of ALWAYS_EXTERNAL_PATTERNS) {
    if (promptLower.includes(pattern)) return true;
  }

  for (const pattern of CONTEXTUAL_DATA_PATTERNS) {
    if (
      promptLower.includes(pattern) &&
      !hasInlineDataAfter(spec.taskPrompt, pattern)
    ) {
      return true;
    }
  }

  return false;
}

const STOP_WORDS = new Set([
  "the",
  "a",
  "an",
  "and",
  "or",
  "of",
  "for",
  "to",
  "in",
  "on",
  "with",
  "is",
  "are",
  "will",
  "be",
  "you",
  "your",
  "this",
  "that",
  "from",
  "have",
  "has",
  "been",
  "should",
  "could",
  "would",
  "can",
  "may",
]);

function extractDomainHints(taskPrompt: string, description: string): string[] {
  const text = `${taskPrompt} ${description}`.toLowerCase();
  const words = text.replace(/[^a-z0-9\s]/g, " ").split(/\s+/);
  return words.filter((word) => word.length > 3 && !STOP_WORDS.has(word)).slice(0, 10);
}

const COLLECTION_WORDS = new Set([
  "data",
  "records",
  "items",
  "list",
  "entries",
  "results",
]);
const ENTITY_WORDS = new Set([
  "patient",
  "customer",
  "user",
  "client",
  "employee",
  "student",
]);
const ITEM_WORDS = new Set([
  "drug",
  "medication",
  "interaction",
  "product",
  "order",
  "transaction",
]);

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
      sample[hint] = {
        name: `Sample ${hint.charAt(0).toUpperCase() + hint.slice(1)}`,
        id: `${hint}-001`,
      };
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

export function normalizeAgentTaskHealSpec(
  spec: Record<string, unknown>,
): AgentTaskSpec {
  const outputFormat = getStringValue(spec, "outputFormat", "output_format");
  return {
    taskPrompt: getStringValue(spec, "taskPrompt", "task_prompt") ?? "",
    judgeRubric:
      getStringValue(spec, "judgeRubric", "judge_rubric", "rubric") ??
      "Evaluate the response.",
    outputFormat:
      outputFormat === "json_schema" || outputFormat === "code"
        ? outputFormat
        : "free_text",
    judgeModel: getStringValue(spec, "judgeModel", "judge_model") ?? "",
    difficultyTiers: getRecordArrayValue(
      spec,
      "difficultyTiers",
      "difficulty_tiers",
    ),
    referenceContext: getStringValue(
      spec,
      "referenceContext",
      "reference_context",
    ),
    referenceSources: getStringArrayValue(
      spec,
      "referenceSources",
      "reference_sources",
    ),
    requiredConcepts: getStringArrayValue(
      spec,
      "requiredConcepts",
      "required_concepts",
    ),
    calibrationExamples: getRecordArrayValue(
      spec,
      "calibrationExamples",
      "calibration_examples",
    ),
    contextPreparation: getStringValue(
      spec,
      "contextPreparation",
      "context_preparation",
    ),
    requiredContextKeys: getStringArrayValue(
      spec,
      "requiredContextKeys",
      "required_context_keys",
    ),
    maxRounds: getNumberValue(spec, "maxRounds", "max_rounds") ?? 1,
    qualityThreshold:
      getNumberValue(spec, "qualityThreshold", "quality_threshold") ?? 0.9,
    revisionPrompt: getStringValue(spec, "revisionPrompt", "revision_prompt"),
    sampleInput: getStringValue(spec, "sampleInput", "sample_input"),
  };
}

export function applyHealedAgentTaskSpec(
  original: Record<string, unknown>,
  healedTask: AgentTaskSpec,
): Record<string, unknown> {
  const healed = { ...original };
  const usesSnakeCase =
    "task_prompt" in healed ||
    "judge_rubric" in healed ||
    "output_format" in healed ||
    "max_rounds" in healed ||
    "quality_threshold" in healed ||
    "sample_input" in healed;

  if (usesSnakeCase) {
    healed.task_prompt = healedTask.taskPrompt;
    healed.judge_rubric = healedTask.judgeRubric;
    healed.output_format = healedTask.outputFormat;
    healed.judge_model = healedTask.judgeModel;
    healed.max_rounds = healedTask.maxRounds;
    healed.quality_threshold = healedTask.qualityThreshold;
    healed.sample_input = healedTask.sampleInput ?? null;
    healed.context_preparation = healedTask.contextPreparation ?? null;
    healed.reference_context = healedTask.referenceContext ?? null;
    healed.reference_sources = healedTask.referenceSources ?? null;
    healed.required_concepts = healedTask.requiredConcepts ?? null;
    healed.calibration_examples = healedTask.calibrationExamples ?? null;
    healed.required_context_keys = healedTask.requiredContextKeys ?? null;
    healed.revision_prompt = healedTask.revisionPrompt ?? null;
    healed.difficulty_tiers = healedTask.difficultyTiers ?? null;
    return healed;
  }

  healed.taskPrompt = healedTask.taskPrompt;
  healed.judgeRubric = healedTask.judgeRubric;
  healed.outputFormat = healedTask.outputFormat;
  healed.judgeModel = healedTask.judgeModel;
  healed.maxRounds = healedTask.maxRounds;
  healed.qualityThreshold = healedTask.qualityThreshold;
  healed.sampleInput = healedTask.sampleInput ?? null;
  healed.contextPreparation = healedTask.contextPreparation ?? null;
  healed.referenceContext = healedTask.referenceContext ?? null;
  healed.referenceSources = healedTask.referenceSources ?? null;
  healed.requiredConcepts = healedTask.requiredConcepts ?? null;
  healed.calibrationExamples = healedTask.calibrationExamples ?? null;
  healed.requiredContextKeys = healedTask.requiredContextKeys ?? null;
  healed.revisionPrompt = healedTask.revisionPrompt ?? null;
  healed.difficultyTiers = healedTask.difficultyTiers ?? null;
  if (!getStringValue(healed, "rubric")) {
    healed.rubric = healedTask.judgeRubric;
  }
  return healed;
}

export function healAgentTaskSpec(
  spec: AgentTaskSpec,
  description = "",
): AgentTaskSpec {
  if (!needsSampleInput(spec)) return spec;
  const synthetic = generateSyntheticSampleInput(spec.taskPrompt, description);
  return { ...spec, sampleInput: synthetic };
}
