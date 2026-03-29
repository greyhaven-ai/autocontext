/**
 * Mission planner — LLM-driven goal decomposition and adaptive step planning (AC-435).
 *
 * Turns plain-language mission goals into executable plans:
 * 1. decompose() — breaks a goal into prioritized subgoals
 * 2. planNextStep() — plans the next action based on goal + history + feedback
 *
 * Replaces the old generic "Advance mission toward goal" placeholder
 * with real adaptive planning.
 */

import type { LLMProvider } from "../types/index.js";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SubgoalPlan {
  description: string;
  priority: number;
}

export interface PlanResult {
  subgoals: SubgoalPlan[];
  reasoning?: string;
}

export interface StepPlan {
  description: string;
  reasoning: string;
  shouldRevise: boolean;
  targetSubgoal?: string;
  revisedSubgoals?: SubgoalPlan[];
}

export interface PlanNextStepOpts {
  goal: string;
  completedSteps: string[];
  remainingSubgoals: string[];
  verifierFeedback?: {
    passed: boolean;
    reason: string;
    suggestions: string[];
  };
}

// ---------------------------------------------------------------------------
// Prompts
// ---------------------------------------------------------------------------

const DECOMPOSE_SYSTEM = `You are a mission planner. Given a plain-language goal, decompose it into concrete, prioritized subgoals.

Output a JSON object with this shape:
{
  "subgoals": [
    { "description": "Concrete step description", "priority": 1 },
    { "description": "Next step", "priority": 2 }
  ],
  "reasoning": "Why this decomposition"
}

Rules:
- Priority 1 is highest (do first)
- Each subgoal should be specific and actionable
- Order by dependency: if B depends on A, A gets lower priority number
- 2-7 subgoals is ideal; avoid over-decomposition
- Output ONLY the JSON object, no markdown fences`;

const PLAN_STEP_SYSTEM = `You are an adaptive mission executor. Given the mission goal, completed steps, remaining subgoals, and verifier feedback, plan the next action.

Output a JSON object with this shape:
{
  "nextStep": "What to do next",
  "reasoning": "Why this is the right next step",
  "shouldRevise": false,
  "targetSubgoal": "Exact string from Remaining Subgoals"
}

If verifier feedback suggests the current plan is wrong, set shouldRevise: true and include revised subgoals:
{
  "nextStep": "What to do next",
  "reasoning": "Why we need to change approach",
  "shouldRevise": true,
  "revisedSubgoals": [
    { "description": "New step", "priority": 1 }
  ]
}

Rules:
- Base your decision on verifier feedback and completed work
- If feedback has suggestions, incorporate them
- Don't repeat already-completed steps
- When the next step advances an existing remaining subgoal, set targetSubgoal to the exact subgoal text from Remaining Subgoals
- If you are revising the plan instead of completing a current subgoal, omit targetSubgoal
- Be specific about what to do, not generic
- Output ONLY the JSON object`;

// ---------------------------------------------------------------------------
// Planner
// ---------------------------------------------------------------------------

function parseJSON(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  try { return JSON.parse(trimmed); } catch { /* continue */ }
  const start = trimmed.indexOf("{");
  const end = trimmed.lastIndexOf("}");
  if (start !== -1 && end > start) {
    try { return JSON.parse(trimmed.slice(start, end + 1)); } catch { /* continue */ }
  }
  return null;
}

export class MissionPlanner {
  protected provider: LLMProvider;

  constructor(provider: LLMProvider) {
    this.provider = provider;
  }

  /**
   * Decompose a plain-language goal into prioritized subgoals.
   */
  async decompose(goal: string): Promise<PlanResult> {
    try {
      const result = await this.provider.complete({
        systemPrompt: DECOMPOSE_SYSTEM,
        userPrompt: `Mission goal: ${goal}`,
      });

      const parsed = parseJSON(result.text);
      if (!parsed || !Array.isArray(parsed.subgoals)) {
        return this.fallbackPlan(goal);
      }

      const subgoals = (parsed.subgoals as Array<Record<string, unknown>>)
        .filter((s) => typeof s.description === "string" && s.description.trim())
        .map((s, i) => ({
          description: String(s.description).trim(),
          priority: typeof s.priority === "number" ? s.priority : i + 1,
        }));

      if (subgoals.length === 0) return this.fallbackPlan(goal);

      return {
        subgoals: subgoals.sort((a, b) => a.priority - b.priority),
        reasoning: typeof parsed.reasoning === "string" ? parsed.reasoning : undefined,
      };
    } catch {
      return this.fallbackPlan(goal);
    }
  }

  /**
   * Plan the next step based on goal, history, and verifier feedback.
   */
  async planNextStep(opts: PlanNextStepOpts): Promise<StepPlan> {
    const userPrompt = this.buildStepPrompt(opts);

    try {
      const result = await this.provider.complete({
        systemPrompt: PLAN_STEP_SYSTEM,
        userPrompt,
      });

      const parsed = parseJSON(result.text);
      if (!parsed || typeof parsed.nextStep !== "string") {
        return this.fallbackStep(opts);
      }

      const plan: StepPlan = {
        description: String(parsed.nextStep).trim(),
        reasoning: typeof parsed.reasoning === "string" ? String(parsed.reasoning) : "Continuing mission",
        shouldRevise: parsed.shouldRevise === true,
      };

      if (
        typeof parsed.targetSubgoal === "string"
        && opts.remainingSubgoals.includes(parsed.targetSubgoal)
      ) {
        plan.targetSubgoal = parsed.targetSubgoal;
      } else if (!plan.shouldRevise && opts.remainingSubgoals.length === 1) {
        plan.targetSubgoal = opts.remainingSubgoals[0];
      }

      if (plan.shouldRevise && Array.isArray(parsed.revisedSubgoals)) {
        plan.revisedSubgoals = (parsed.revisedSubgoals as Array<Record<string, unknown>>)
          .filter((s) => typeof s.description === "string")
          .map((s, i) => ({
            description: String(s.description).trim(),
            priority: typeof s.priority === "number" ? s.priority : i + 1,
          }));
      }

      return plan;
    } catch {
      return this.fallbackStep(opts);
    }
  }

  private buildStepPrompt(opts: PlanNextStepOpts): string {
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

    return sections.join("\n\n");
  }

  private fallbackPlan(goal: string): PlanResult {
    return {
      subgoals: [{ description: `Work toward: ${goal}`, priority: 1 }],
      reasoning: "Fallback: could not decompose goal via LLM",
    };
  }

  private fallbackStep(opts: PlanNextStepOpts): StepPlan {
    const next = opts.remainingSubgoals[0];
    return {
      description: next ? `Work on: ${next}` : `Continue: ${opts.goal}`,
      reasoning: "Fallback: could not plan step via LLM",
      shouldRevise: false,
      ...(next ? { targetSubgoal: next } : {}),
    };
  }
}
