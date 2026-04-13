/**
 * AgentTaskCreator — orchestrates the full agent task creation pipeline.
 * Port of autocontext/src/autocontext/scenarios/custom/agent_task_creator.py
 *
 * Pipeline: NL description → LLM designs spec → validate → factory → save
 */

import type { AgentTaskInterface } from "../types/index.js";
import type { LLMProvider } from "../types/index.js";
import type { AgentTaskSpec } from "./agent-task-spec.js";
import { validateIntent } from "./agent-task-validator.js";
import { createAgentTask } from "./agent-task-factory.js";
import { designAgentTaskWithProvider } from "./agent-task-design-workflow.js";
import {
  type RoutedAgentTaskScenario,
  classifyAgentTaskFamily,
  routeAgentTaskScenarioCreation,
} from "./agent-task-family-routing.js";
import {
  deriveAgentTaskName,
  AGENT_TASK_NAME_STOP_WORDS,
  scoreAgentTaskNameWord,
} from "./agent-task-name-workflow.js";
import { persistAgentTaskScenario } from "./agent-task-persistence-workflow.js";
import { validateForFamily } from "./family-pipeline.js";

export interface AgentTaskCreatorOpts {
  provider: LLMProvider;
  model?: string;
  knowledgeRoot: string;
}

export type CreatedScenario =
  | (AgentTaskInterface & { readonly name: string; readonly spec: AgentTaskSpec; readonly family?: "agent_task" })
  | RoutedAgentTaskScenario;

export class AgentTaskCreator {
  #provider: LLMProvider;
  #model: string;
  #knowledgeRoot: string;

  constructor(opts: AgentTaskCreatorOpts) {
    this.#provider = opts.provider;
    this.#model = opts.model ?? "";
    this.#knowledgeRoot = opts.knowledgeRoot;
  }

  static readonly STOP_WORDS = AGENT_TASK_NAME_STOP_WORDS;

  static wordScore(word: string, position: number, totalWords: number): number {
    return scoreAgentTaskNameWord(word, position, totalWords);
  }

  /**
   * Derive a snake_case name from a description.
   * Prefers longer, domain-specific words over short common words.
   */
  deriveName(description: string): string {
    return deriveAgentTaskName(description);
  }

  /**
   * Run the full pipeline: design → validate → create → save.
   */
  async create(description: string): Promise<CreatedScenario> {
    const name = this.deriveName(description);
    const family = classifyAgentTaskFamily(description);
    const routedScenario = await routeAgentTaskScenarioCreation({
      family,
      description,
      name,
      provider: this.#provider,
      model: this.#model,
      knowledgeRoot: this.#knowledgeRoot,
    });
    if (routedScenario) {
      return routedScenario;
    }

    const spec = await designAgentTaskWithProvider({
      description,
      provider: this.#provider,
      model: this.#model,
    });

    // 2. Validate spec
    const errors = validateForFamily("agent_task", spec);
    if (errors.length > 0) {
      throw new Error(`spec validation failed: ${errors.join("; ")}`);
    }

    const intentErrors = validateIntent(description, spec);
    if (intentErrors.length > 0) {
      throw new Error(`intent validation failed: ${intentErrors.join("; ")}`);
    }

    const task = createAgentTask({ spec, name, provider: this.#provider });
    persistAgentTaskScenario({
      knowledgeRoot: this.#knowledgeRoot,
      name,
      spec,
    });
    return task;
  }
}
