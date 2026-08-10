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
import {
  ANALYST_SCHEMA,
  COACH_SCHEMA,
  parseAnalystConstrained,
  parseCoachConstrained,
  wasConstrained,
} from "./role-schemas.js";
import type { OutputSchema } from "../types/index.js";
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

  // AC-929: the schema a role asks its backend to constrain generation to.
  // Roles without one (competitor emits a strategy object, not a role contract)
  // are simply absent from the map and keep today's behavior.
  //
  // architect is deliberately absent. Its Python payload carries nine channels
  // and renders them back into a legacy wire format, re-running AST validation
  // over proposed harness code; porting that faithfully is its own piece of
  // work, and a half-port would silently drop seven channels. It keeps the
  // scrape path until then.
  static readonly #ROLE_SCHEMAS: Partial<Record<GenerationRole, OutputSchema>> = {
    analyst: ANALYST_SCHEMA,
    coach: COACH_SCHEMA,
  };

  #completeRole(role: GenerationRole, userPrompt: string) {
    return this.#providerForRole(role).complete({
      systemPrompt: "",
      userPrompt,
      model: this.#roleModels[role],
      outputSchema: AgentOrchestrator.#ROLE_SCHEMAS[role],
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

    // Parse by what the backend ACTUALLY did, not what was requested. A
    // backend that ignored the schema reports constrained !== true and keeps
    // the markdown scrape, unchanged.
    return {
      competitorOutput,
      analystOutput: wasConstrained(analystResult)
        ? parseAnalystConstrained(analystResult.text)
        : parseAnalystOutput(analystResult.text),
      coachOutput: wasConstrained(coachResult)
        ? parseCoachConstrained(coachResult.text)
        : parseCoachOutput(coachResult.text),
      architectOutput: parseArchitectOutput(architectResult.text),
    };
  }
}
