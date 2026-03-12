/**
 * AgentTaskCreator — orchestrates the full agent task creation pipeline.
 * Port of autocontext/src/autocontext/scenarios/custom/agent_task_creator.py
 *
 * Pipeline: NL description → LLM designs spec → validate → factory → save
 */

import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import { join } from "node:path";
import type { AgentTaskInterface } from "../types/index.js";
import type { LLMProvider } from "../types/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { designAgentTask } from "./agent-task-designer.js";
import { validateSpec } from "./agent-task-validator.js";
import { createAgentTask } from "./agent-task-factory.js";

export interface AgentTaskCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export class AgentTaskCreator {
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: AgentTaskCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? "claude-sonnet-4-20250514";
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  /** Stop words excluded from derived names.
   * NOTE: Keep in sync with mts/src/mts/scenarios/custom/agent_task_creator.py STOP_WORDS */
  static readonly STOP_WORDS = new Set([
    "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
    "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
    "that", "can", "should", "could", "would", "will", "must",
    "agent", "tool", "system",
    "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
    "it", "we", "they", "is", "are", "was", "be", "do", "does",
    "to", "in", "on", "at", "by", "which", "what", "how",
  ]);

  /**
   * Derive a snake_case name from a description.
   * Prefers longer, domain-specific words over short common words.
   */
  deriveName(description: string): string {
    const words = description
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w && !AgentTaskCreator.STOP_WORDS.has(w));
    // Prefer longer words (>3 chars) as they are more likely domain-specific nouns
    const sorted = [...words].sort((a, b) => b.length - a.length);
    const seen = new Set<string>();
    const unique: string[] = [];
    for (const w of sorted) {
      if (!seen.has(w)) {
        seen.add(w);
        unique.push(w);
      }
    }
    const nameWords = unique.length >= 3 ? unique.slice(0, 3) : unique.length > 0 ? unique.slice(0, 2) : ["custom"];
    return nameWords.join("_");
  }

  /**
   * Run the full pipeline: design → validate → create → save.
   */
  async create(description: string): Promise<AgentTaskInterface & { readonly name: string; readonly spec: AgentTaskSpec }> {
    // 1. Design spec via LLM
    const llmFn = async (system: string, user: string): Promise<string> => {
      const result = await this.provider.complete({
        systemPrompt: system,
        userPrompt: user,
        model: this.model,
      });
      return result.text;
    };

    const spec = await designAgentTask(description, llmFn);

    // 2. Validate spec
    const errors = validateSpec(spec);
    if (errors.length > 0) {
      throw new Error(`spec validation failed: ${errors.join("; ")}`);
    }

    // 3. Derive name and create task
    const name = this.deriveName(description);
    const task = createAgentTask({ spec, name, provider: this.provider });

    // 4. Save to disk
    const customDir = join(this.knowledgeRoot, "_custom_scenarios");
    const scenarioDir = join(customDir, name);
    if (!existsSync(scenarioDir)) {
      mkdirSync(scenarioDir, { recursive: true });
    }

    const specData: Record<string, unknown> = {
      task_prompt: spec.taskPrompt,
      judge_rubric: spec.judgeRubric,
      output_format: spec.outputFormat,
      judge_model: spec.judgeModel,
    };
    if (spec.difficultyTiers) specData.difficulty_tiers = spec.difficultyTiers;
    if (spec.referenceContext) specData.reference_context = spec.referenceContext;
    if (spec.referenceSources) specData.reference_sources = spec.referenceSources;
    if (spec.requiredConcepts) specData.required_concepts = spec.requiredConcepts;
    if (spec.calibrationExamples) specData.calibration_examples = spec.calibrationExamples;
    if (spec.contextPreparation) specData.context_preparation = spec.contextPreparation;
    if (spec.requiredContextKeys) specData.required_context_keys = spec.requiredContextKeys;
    if (spec.maxRounds !== 1) specData.max_rounds = spec.maxRounds;
    if (spec.qualityThreshold !== 0.9) specData.quality_threshold = spec.qualityThreshold;
    if (spec.revisionPrompt) specData.revision_prompt = spec.revisionPrompt;
    if (spec.sampleInput) specData.sample_input = spec.sampleInput;

    writeFileSync(
      join(scenarioDir, "agent_task_spec.json"),
      JSON.stringify(specData, null, 2),
      "utf-8",
    );
    writeFileSync(join(scenarioDir, "scenario_type.txt"), "agent_task", "utf-8");

    return task;
  }
}
