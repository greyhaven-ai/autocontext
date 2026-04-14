import { z } from "zod";

import type { LLMProvider } from "../types/index.js";
import {
  reviseSpec,
  type RevisionResult,
} from "../scenarios/scenario-revision.js";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

interface ScenarioRevisionInternals {
  reviseSpec(opts: {
    currentSpec: Record<string, unknown>;
    feedback: string;
    family: string;
    provider: LLMProvider;
  }): Promise<RevisionResult>;
}

const defaultInternals: ScenarioRevisionInternals = {
  reviseSpec,
};

function jsonText(payload: unknown, indent?: number): JsonToolResponse {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, indent),
      },
    ],
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function registerScenarioRevisionTools(
  server: McpToolRegistrar,
  opts: {
    provider: LLMProvider;
    internals?: Partial<ScenarioRevisionInternals>;
  },
): void {
  const internals: ScenarioRevisionInternals = {
    ...defaultInternals,
    ...opts.internals,
  };

  server.tool(
    "revise_scenario",
    "Revise a scenario spec based on user feedback. Takes the current spec and feedback, returns an updated spec via LLM.",
    {
      currentSpec: z.record(z.unknown()).describe("The current scenario spec to revise"),
      feedback: z.string().describe("User feedback describing what to change"),
      family: z.string().default("agent_task").describe("Scenario family (agent_task, simulation, etc.)"),
    },
    async (args: Record<string, unknown>) => {
      const result = await internals.reviseSpec({
        currentSpec: isRecord(args.currentSpec) ? args.currentSpec : {},
        feedback: String(args.feedback),
        family: String(args.family ?? "agent_task"),
        provider: opts.provider,
      });

      return jsonText(
        {
          changesApplied: result.changesApplied,
          revised: result.revised,
          error: result.error ?? null,
        },
        2,
      );
    },
  );
}
