/**
 * Mission-simulation bridge — missions invoke simulations as planning tools (AC-455).
 *
 * SimulationAwarePlanner extends the MissionPlanner to detect when a step
 * plan requests a simulation ("what if" analysis) before committing to
 * an action. The simulation runs, results feed back into the planning
 * context, and simulation steps count toward mission budget.
 */

import type { LLMProvider } from "../types/index.js";
import { SimulationEngine, type SimulationResult } from "../simulation/engine.js";
import { MissionPlanner, type PlanNextStepOpts, type StepPlan, type SubgoalPlan } from "./planner.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SimulationRequest {
  description: string;
  variables?: Record<string, unknown>;
  maxSteps?: number;
}

export interface SimulationStepPlan extends StepPlan {
  /** If present, the planner wants a simulation run before this step */
  simulateFirst?: SimulationRequest;
  /** Populated after simulation is executed */
  simulationResult?: SimulationResult;
}

// ---------------------------------------------------------------------------
// SimulationAwarePlanner
// ---------------------------------------------------------------------------

const PLAN_STEP_WITH_SIM_SYSTEM = `You are an adaptive mission executor. Given the mission goal, completed steps, remaining subgoals, and verifier feedback, plan the next action.

If the next decision would benefit from "what if" analysis, you can request a simulation by including a "simulateFirst" field.

Output a JSON object:
{
  "nextStep": "What to do next",
  "reasoning": "Why this is the right next step",
  "shouldRevise": false,
  "simulateFirst": {
    "description": "Plain-language description of what to simulate",
    "variables": {"optional": "variable overrides"}
  }
}

If no simulation is needed, omit "simulateFirst" entirely.
Output ONLY the JSON object.`;

export class SimulationAwarePlanner extends MissionPlanner {
  private simEngine: SimulationEngine;
  private knowledgeRoot: string;

  constructor(provider: LLMProvider, knowledgeRoot: string) {
    super(provider);
    this.knowledgeRoot = knowledgeRoot;
    this.simEngine = new SimulationEngine(provider, knowledgeRoot);
  }

  /**
   * Plan next step with simulation awareness.
   * Detects simulateFirst in the LLM response.
   */
  override async planNextStep(opts: PlanNextStepOpts): Promise<SimulationStepPlan> {
    const userPrompt = this.buildStepPromptWithSimContext(opts);

    try {
      const result = await this.provider.complete({
        systemPrompt: PLAN_STEP_WITH_SIM_SYSTEM,
        userPrompt,
      });

      const parsed = this.parseJSONSafe(result.text);
      if (!parsed || typeof parsed.nextStep !== "string") {
        return this.fallbackStepPlan(opts);
      }

      const plan: SimulationStepPlan = {
        description: String(parsed.nextStep).trim(),
        reasoning: typeof parsed.reasoning === "string" ? String(parsed.reasoning) : "Continuing mission",
        shouldRevise: parsed.shouldRevise === true,
      };

      if (parsed.simulateFirst && typeof parsed.simulateFirst === "object") {
        const simReq = parsed.simulateFirst as Record<string, unknown>;
        plan.simulateFirst = {
          description: String(simReq.description ?? ""),
          variables: (simReq.variables as Record<string, unknown>) ?? undefined,
          maxSteps: typeof simReq.maxSteps === "number" ? simReq.maxSteps : undefined,
        };
      }

      if (parsed.shouldRevise && Array.isArray(parsed.revisedSubgoals)) {
        plan.revisedSubgoals = (parsed.revisedSubgoals as Array<Record<string, unknown>>)
          .filter((s) => typeof s.description === "string")
          .map((s, i) => ({
            description: String(s.description).trim(),
            priority: typeof s.priority === "number" ? s.priority : i + 1,
          }));
      }

      return plan;
    } catch {
      return this.fallbackStepPlan(opts);
    }
  }

  /**
   * Plan next step AND execute any requested simulation.
   * Returns the step plan enriched with simulation results.
   */
  async planAndSimulate(opts: PlanNextStepOpts): Promise<SimulationStepPlan> {
    const step = await this.planNextStep(opts);

    if (step.simulateFirst?.description) {
      try {
        const simResult = await this.simEngine.run({
          description: step.simulateFirst.description,
          variables: step.simulateFirst.variables,
          maxSteps: step.simulateFirst.maxSteps,
        });
        step.simulationResult = simResult;
      } catch {
        // Simulation failure is not fatal to the mission step
        step.simulationResult = undefined;
      }
    }

    return step;
  }

  private buildStepPromptWithSimContext(opts: PlanNextStepOpts): string {
    const sections: string[] = [];
    sections.push(`## Mission Goal\n${opts.goal}`);

    if (opts.completedSteps.length > 0) {
      sections.push(`## Completed Steps\n${opts.completedSteps.map((s, i) => `${i + 1}. ${s}`).join("\n")}`);
    }

    if (opts.remainingSubgoals.length > 0) {
      sections.push(`## Remaining Subgoals\n${opts.remainingSubgoals.map((s) => `- ${s}`).join("\n")}`);
    }

    if (opts.verifierFeedback) {
      sections.push(
        `## Verifier Feedback\nPassed: ${opts.verifierFeedback.passed}\nReason: ${opts.verifierFeedback.reason}`,
      );
      if (opts.verifierFeedback.suggestions.length > 0) {
        sections.push(`Suggestions:\n${opts.verifierFeedback.suggestions.map((s) => `- ${s}`).join("\n")}`);
      }
    }

    sections.push(
      "\n## Simulation Option",
      "If this step would benefit from 'what if' analysis before committing,",
      "include simulateFirst with a description of what to simulate.",
    );

    return sections.join("\n\n");
  }

  private parseJSONSafe(text: string): Record<string, unknown> | null {
    const trimmed = text.trim();
    try { return JSON.parse(trimmed); } catch { /* continue */ }
    const start = trimmed.indexOf("{");
    const end = trimmed.lastIndexOf("}");
    if (start !== -1 && end > start) {
      try { return JSON.parse(trimmed.slice(start, end + 1)); } catch { /* continue */ }
    }
    return null;
  }

  private fallbackStepPlan(opts: PlanNextStepOpts): SimulationStepPlan {
    const next = opts.remainingSubgoals[0];
    return {
      description: next ? `Work on: ${next}` : `Continue: ${opts.goal}`,
      reasoning: "Fallback: could not plan step via LLM",
      shouldRevise: false,
    };
  }
}
