/**
 * Agent orchestrator — dispatches roles in sequence (AC-345 Task 18).
 * Mirrors Python's autocontext/agents/orchestrator.py (simplified).
 *
 * Competitor → Translator (implicit) → [Analyst, Coach, Architect] in parallel.
 */

import type { LLMProvider } from "../types/index.js";
import type { GenerationRole } from "../providers/index.js";
import {
  parseAnalystOutput,
  parseArchitectOutput,
  parseCoachOutput,
  parseCompetitorOutput,
} from "./roles.js";
import type { AnalystOutput, ArchitectOutput, CoachOutput, CompetitorOutput } from "./roles.js";

export interface GenerationPrompts {
  competitorPrompt: string;
  analystPrompt: string;
  coachPrompt: string;
  architectPrompt?: string;
}

export interface GenerationResult {
  competitorOutput: CompetitorOutput;
  analystOutput: AnalystOutput;
  coachOutput: CoachOutput;
  architectOutput: ArchitectOutput;
}

export interface AgentOrchestratorOpts {
  roleProviders?: Partial<Record<GenerationRole, LLMProvider>>;
  roleModels?: Partial<Record<GenerationRole, string>>;
}

export class AgentOrchestrator {
  #provider: LLMProvider;
  #roleProviders: Partial<Record<GenerationRole, LLMProvider>>;
  #roleModels: Partial<Record<GenerationRole, string>>;

  constructor(provider: LLMProvider, opts: AgentOrchestratorOpts = {}) {
    this.#provider = provider;
    this.#roleProviders = opts.roleProviders ?? {};
    this.#roleModels = opts.roleModels ?? {};
  }

  #providerForRole(role: GenerationRole): LLMProvider {
    return this.#roleProviders[role] ?? this.#provider;
  }

  #completeRole(role: GenerationRole, userPrompt: string) {
    return this.#providerForRole(role).complete({
      systemPrompt: "",
      userPrompt,
      model: this.#roleModels[role],
    });
  }

  async runGeneration(prompts: GenerationPrompts): Promise<GenerationResult> {
    // Phase 1: Competitor
    const competitorResult = await this.#completeRole("competitor", prompts.competitorPrompt);
    let strategy: Record<string, unknown> = {};
    try {
      strategy = JSON.parse(competitorResult.text);
    } catch {
      strategy = { raw: competitorResult.text };
    }
    const competitorOutput = parseCompetitorOutput(competitorResult.text, strategy);

    // Phase 2: Analyst, Coach, Architect in parallel
    const [analystResult, coachResult, architectResult] = await Promise.all([
      this.#completeRole("analyst", prompts.analystPrompt),
      this.#completeRole("coach", prompts.coachPrompt),
      prompts.architectPrompt
        ? this.#completeRole("architect", prompts.architectPrompt)
        : Promise.resolve({ text: "", usage: {} }),
    ]);

    return {
      competitorOutput,
      analystOutput: parseAnalystOutput(analystResult.text),
      coachOutput: parseCoachOutput(coachResult.text),
      architectOutput: parseArchitectOutput(architectResult.text),
    };
  }
}
