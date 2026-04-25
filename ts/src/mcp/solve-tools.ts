import { z } from "zod";

interface JsonToolResponse {
  content: Array<{
    type: "text";
    text: string;
  }>;
}

type McpToolRegistrar = {
  tool: (...args: any[]) => unknown;
};

interface SolveToolManager {
  submit(
    description: string,
    generations: number,
    opts?: {
      familyOverride?: string;
      generationTimeBudgetSeconds?: number | null;
    },
  ): string;
  getStatus(jobId: string): Record<string, unknown>;
  getResult(jobId: string): Record<string, unknown> | null;
}

interface SolveToolRegistration {
  name: string;
  description: string;
  schema: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => Promise<JsonToolResponse>;
}

interface SolveToolDefinition extends SolveToolRegistration {
  aliases: SolveToolRegistration[];
}

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

export function buildSolveResultNotFoundPayload(jobId: string): {
  error: string;
  jobId: string;
} {
  return {
    error: "Job not completed or not found",
    jobId,
  };
}

function buildPrefixedSolveResultNotFoundPayload(jobId: string): {
  error: string;
  job_id: string;
} {
  return {
    error: "Job not completed or not found",
    job_id: jobId,
  };
}

function toPrefixedSolveStatusPayload(payload: Record<string, unknown>): Record<string, unknown> {
  const prefixedPayload = { ...payload };
  if ("jobId" in prefixedPayload) {
    prefixedPayload.job_id = prefixedPayload.jobId;
    delete prefixedPayload.jobId;
  }
  if ("scenarioName" in prefixedPayload) {
    prefixedPayload.scenario_name = prefixedPayload.scenarioName;
    delete prefixedPayload.scenarioName;
  }
  return prefixedPayload;
}

function solveSubmitSchema(): Record<string, unknown> {
  return {
    description: z.string(),
    generations: z.number().int().default(5),
    family: z.string().optional(),
    generationTimeBudget: z.number().int().min(0).optional(),
    generationTimeBudgetSeconds: z.number().int().min(0).optional(),
    generation_time_budget: z.number().int().min(0).optional(),
  };
}

function solveSubmitOptions(args: Record<string, unknown>):
  | {
    familyOverride?: string;
    generationTimeBudgetSeconds?: number | null;
  }
  | undefined {
  const familyOverride = typeof args.family === "string" && args.family.trim()
    ? args.family.trim()
    : undefined;
  const generationTimeBudgetSeconds =
    typeof args.generationTimeBudgetSeconds === "number"
      ? args.generationTimeBudgetSeconds
      : typeof args.generationTimeBudget === "number"
        ? args.generationTimeBudget
        : typeof args.generation_time_budget === "number"
          ? args.generation_time_budget
          : undefined;

  if (familyOverride === undefined && generationTimeBudgetSeconds === undefined) {
    return undefined;
  }
  return {
    familyOverride,
    generationTimeBudgetSeconds,
  };
}

function submitSolveJob(
  solveManager: SolveToolManager,
  args: Record<string, unknown>,
): string {
  const description = String(args.description);
  const generations = Number(args.generations ?? 5);
  const options = solveSubmitOptions(args);
  if (options === undefined) {
    return solveManager.submit(description, generations);
  }
  return solveManager.submit(description, generations, options);
}

function buildSolveToolDefinitions(solveManager: SolveToolManager): SolveToolDefinition[] {
  return [
    {
      name: "solve_scenario",
      description: "Submit a problem for on-demand solving. Returns a job_id for polling.",
      schema: solveSubmitSchema(),
      handler: async (args: Record<string, unknown>) => {
        const jobId = submitSolveJob(solveManager, args);
        return jsonText({ jobId, status: "pending" });
      },
      aliases: [
        {
          name: "autocontext_solve_scenario",
          description: "Submit a problem for on-demand solving. Returns a job_id for polling.",
          schema: solveSubmitSchema(),
          handler: async (args: Record<string, unknown>) => {
            const jobId = submitSolveJob(solveManager, args);
            return jsonText({ job_id: jobId, status: "pending" });
          },
        },
      ],
    },
    {
      name: "solve_status",
      description: "Check status of a solve-on-demand job",
      schema: { jobId: z.string() },
      handler: async (args: Record<string, unknown>) =>
        jsonText(solveManager.getStatus(String(args.jobId)), 2),
      aliases: [
        {
          name: "autocontext_solve_status",
          description: "Check status of a solve-on-demand job",
          schema: { job_id: z.string() },
          handler: async (args: Record<string, unknown>) =>
            jsonText(toPrefixedSolveStatusPayload(solveManager.getStatus(String(args.job_id))), 2),
        },
      ],
    },
    {
      name: "solve_result",
      description: "Get the exported skill package result of a completed solve-on-demand job",
      schema: { jobId: z.string() },
      handler: async (args: Record<string, unknown>) => {
        const jobId = String(args.jobId);
        const result = solveManager.getResult(jobId);
        return jsonText(result ?? buildSolveResultNotFoundPayload(jobId), 2);
      },
      aliases: [
        {
          name: "autocontext_solve_result",
          description: "Get the exported skill package result of a completed solve-on-demand job",
          schema: { job_id: z.string() },
          handler: async (args: Record<string, unknown>) => {
            const jobId = String(args.job_id);
            const result = solveManager.getResult(jobId);
            return jsonText(result ?? buildPrefixedSolveResultNotFoundPayload(jobId), 2);
          },
        },
      ],
    },
  ];
}

function registerTool(server: McpToolRegistrar, registration: SolveToolRegistration): void {
  server.tool(registration.name, registration.description, registration.schema, registration.handler);
}

function registerToolDefinition(server: McpToolRegistrar, definition: SolveToolDefinition): void {
  registerTool(server, definition);
  for (const alias of definition.aliases) {
    registerTool(server, alias);
  }
}

export function registerSolveTools(
  server: McpToolRegistrar,
  opts: {
    solveManager: SolveToolManager;
  },
): void {
  for (const definition of buildSolveToolDefinitions(opts.solveManager)) {
    registerToolDefinition(server, definition);
  }
}
