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
import { validateIntent } from "./agent-task-validator.js";
import { createAgentTask } from "./agent-task-factory.js";
import {
  type ArtifactEditingScenarioHandle,
  ArtifactEditingCreator,
} from "./artifact-editing-creator.js";
import {
  type CoordinationScenarioHandle,
  CoordinationCreator,
} from "./coordination-creator.js";
import { classifyScenarioFamily, routeToFamily } from "./family-classifier.js";
import { validateForFamily } from "./family-pipeline.js";
import { getScenarioTypeMarker } from "./families.js";
import {
  type InvestigationScenarioHandle,
  InvestigationCreator,
} from "./investigation-creator.js";
import {
  type NegotiationScenarioHandle,
  NegotiationCreator,
} from "./negotiation-creator.js";
import {
  OperatorLoopCreator,
  type OperatorLoopScenarioHandle,
} from "./operator-loop-creator.js";
import {
  type SchemaEvolutionScenarioHandle,
  SchemaEvolutionCreator,
} from "./schema-evolution-creator.js";
import {
  type SimulationScenarioHandle,
  SimulationCreator,
} from "./simulation-creator.js";
import {
  type ToolFragilityScenarioHandle,
  ToolFragilityCreator,
} from "./tool-fragility-creator.js";
import {
  type WorkflowScenarioHandle,
  WorkflowCreator,
} from "./workflow-creator.js";

export interface AgentTaskCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export type CreatedScenario =
  | (AgentTaskInterface & { readonly name: string; readonly spec: AgentTaskSpec; readonly family?: "agent_task" })
  | ArtifactEditingScenarioHandle
  | CoordinationScenarioHandle
  | InvestigationScenarioHandle
  | NegotiationScenarioHandle
  | OperatorLoopScenarioHandle
  | SchemaEvolutionScenarioHandle
  | SimulationScenarioHandle
  | ToolFragilityScenarioHandle
  | WorkflowScenarioHandle;

export class AgentTaskCreator {
  private static readonly ABSTRACT_SUFFIXES = [
    "ness", "tion", "sion", "ment", "ity", "ous", "ive", "able",
    "ible", "ful", "less", "ence", "ance", "ical", "ally",
  ];
  private provider: LLMProvider;
  private model: string;
  private knowledgeRoot: string;

  constructor(opts: AgentTaskCreatorOpts) {
    this.provider = opts.provider;
    this.model = opts.model ?? "";
    this.knowledgeRoot = opts.knowledgeRoot;
  }

  /** Stop words excluded from derived names.
   * NOTE: Keep in sync with autocontext/src/autocontext/scenarios/custom/agent_task_creator.py STOP_WORDS */
  static readonly STOP_WORDS = new Set([
    "a", "an", "the", "task", "where", "you", "with", "and", "or", "of", "for",
    "i", "want", "need", "make", "create", "build", "write", "develop", "implement",
    "that", "can", "should", "could", "would", "will", "must",
    "agent", "tool", "system",
    "clear", "well", "good", "great", "very", "really", "also", "just", "structured",
    "it", "we", "they", "is", "are", "was", "be", "do", "does",
    "to", "in", "on", "at", "by", "which", "what", "how",
    "about", "from", "into", "after", "before", "below", "above", "under", "over",
    "using", "via",
    "design", "generate", "generates", "generated", "edit", "analyze", "analyse",
    "find", "add", "remove", "update", "improve",
    "file", "section", "scenario",
    "simple", "complex", "advanced", "word", "multi", "partial", "hidden",
  ]);

  private static wordScore(word: string, position: number, totalWords: number): number {
    let score = 0;

    if (AgentTaskCreator.ABSTRACT_SUFFIXES.some((suffix) => word.endsWith(suffix))) {
      score -= 2;
    }

    if (word.length >= 4 && word.length <= 12) {
      score += 2;
    } else if (word.length > 2) {
      score += 1;
    }

    if (totalWords > 0) {
      score += 1 - (position / totalWords) * 0.5;
    }

    return score;
  }

  /**
   * Derive a snake_case name from a description.
   * Prefers longer, domain-specific words over short common words.
   */
  deriveName(description: string): string {
    const words = description
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, " ")
      .split(/\s+/)
      .filter((w) => w && !AgentTaskCreator.STOP_WORDS.has(w) && w.length > 1);
    const sorted = words
      .map((word, index) => ({
        word,
        index,
        score: AgentTaskCreator.wordScore(word, index, words.length),
      }))
      .sort((a, b) => (b.score - a.score) || (a.index - b.index));
    const seen = new Set<string>();
    const unique: string[] = [];
    for (const { word } of sorted) {
      const w = word;
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
  async create(description: string): Promise<CreatedScenario> {
    const name = this.deriveName(description);
    const family = routeToFamily(classifyScenarioFamily(description));
    if (family === "simulation") {
      return new SimulationCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "artifact_editing") {
      return new ArtifactEditingCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "investigation") {
      return new InvestigationCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "workflow") {
      return new WorkflowCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "schema_evolution") {
      return new SchemaEvolutionCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "tool_fragility") {
      return new ToolFragilityCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "negotiation") {
      return new NegotiationCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "operator_loop") {
      return new OperatorLoopCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family === "coordination") {
      return new CoordinationCreator({
        provider: this.provider,
        model: this.model,
        knowledgeRoot: this.knowledgeRoot,
      }).create(description, name);
    }
    if (family !== "agent_task") {
      throw new Error(`Scenario family '${family}' is not yet supported for custom scaffolding`);
    }

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
    const errors = validateForFamily("agent_task", spec);
    if (errors.length > 0) {
      throw new Error(`spec validation failed: ${errors.join("; ")}`);
    }

    const intentErrors = validateIntent(description, spec);
    if (intentErrors.length > 0) {
      throw new Error(`intent validation failed: ${intentErrors.join("; ")}`);
    }

    // 3. Derive name and create task
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
    writeFileSync(join(scenarioDir, "scenario_type.txt"), getScenarioTypeMarker("agent_task"), "utf-8");

    return task;
  }
}
