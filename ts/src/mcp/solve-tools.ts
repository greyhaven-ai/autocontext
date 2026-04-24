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
  submit(description: string, generations: number): string;
  getStatus(jobId: string): Record<string, unknown>;
  getResult(jobId: string): Record<string, unknown> | null;
}

interface SolveToolDefinition {
  name: string;
  aliases: string[];
  description: string;
  schema: Record<string, unknown>;
  handler: (args: Record<string, unknown>) => Promise<JsonToolResponse>;
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

function buildSolveToolDefinitions(solveManager: SolveToolManager): SolveToolDefinition[] {
  return [
    {
      name: "solve_scenario",
      aliases: ["autocontext_solve_scenario"],
      description: "Submit a problem for on-demand solving. Returns a job_id for polling.",
      schema: { description: z.string(), generations: z.number().int().default(5) },
      handler: async (args: Record<string, unknown>) => {
        const jobId = solveManager.submit(
          String(args.description),
          Number(args.generations ?? 5),
        );
        return jsonText({ jobId, status: "pending" });
      },
    },
    {
      name: "solve_status",
      aliases: ["autocontext_solve_status"],
      description: "Check status of a solve-on-demand job",
      schema: { jobId: z.string() },
      handler: async (args: Record<string, unknown>) =>
        jsonText(solveManager.getStatus(String(args.jobId)), 2),
    },
    {
      name: "solve_result",
      aliases: ["autocontext_solve_result"],
      description: "Get the exported skill package result of a completed solve-on-demand job",
      schema: { jobId: z.string() },
      handler: async (args: Record<string, unknown>) => {
        const jobId = String(args.jobId);
        const result = solveManager.getResult(jobId);
        return jsonText(result ?? buildSolveResultNotFoundPayload(jobId), 2);
      },
    },
  ];
}

function registerToolDefinition(server: McpToolRegistrar, definition: SolveToolDefinition): void {
  server.tool(definition.name, definition.description, definition.schema, definition.handler);
  for (const alias of definition.aliases) {
    server.tool(
      alias,
      `${definition.description} Alias for ${definition.name}.`,
      definition.schema,
      definition.handler,
    );
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
