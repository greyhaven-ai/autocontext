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

export function registerSolveTools(
  server: McpToolRegistrar,
  opts: {
    solveManager: SolveToolManager;
  },
): void {
  server.tool(
    "solve_scenario",
    "Submit a problem for on-demand solving. Returns a job_id for polling.",
    { description: z.string(), generations: z.number().int().default(5) },
    async (args: Record<string, unknown>) => {
      const jobId = opts.solveManager.submit(
        String(args.description),
        Number(args.generations ?? 5),
      );
      return jsonText({ jobId, status: "pending" });
    },
  );

  server.tool(
    "solve_status",
    "Check status of a solve-on-demand job",
    { jobId: z.string() },
    async (args: Record<string, unknown>) =>
      jsonText(opts.solveManager.getStatus(String(args.jobId)), 2),
  );

  server.tool(
    "solve_result",
    "Get the exported skill package result of a completed solve-on-demand job",
    { jobId: z.string() },
    async (args: Record<string, unknown>) => {
      const jobId = String(args.jobId);
      const result = opts.solveManager.getResult(jobId);
      return jsonText(result ?? buildSolveResultNotFoundPayload(jobId), 2);
    },
  );
}
