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

interface SandboxToolManager {
  create(scenarioName: string, userId?: string): object;
  run(sandboxId: string, generations?: number): Promise<object>;
  getStatus(sandboxId: string): object | null;
  readPlaybook(sandboxId: string): string;
  list(): object[];
  destroy(sandboxId: string): boolean;
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

export function buildSandboxNotFoundPayload(sandboxId: string): { error: string } {
  return { error: `Sandbox '${sandboxId}' not found` };
}

export function registerSandboxTools(
  server: McpToolRegistrar,
  opts: {
    sandboxManager: SandboxToolManager;
  },
): void {
  server.tool(
    "sandbox_create",
    "Create an isolated sandbox for scenario execution",
    { scenario: z.string(), userId: z.string().default("anonymous") },
    async (args: Record<string, unknown>) =>
      jsonText(
        opts.sandboxManager.create(
          args.scenario as string,
          args.userId as string | undefined,
        ),
        2,
      ),
  );

  server.tool(
    "sandbox_run",
    "Run generation(s) in a sandbox",
    { sandboxId: z.string(), generations: z.number().int().default(1) },
    async (args: Record<string, unknown>) =>
      jsonText(
        await opts.sandboxManager.run(
          args.sandboxId as string,
          args.generations as number | undefined,
        ),
        2,
      ),
  );

  server.tool(
    "sandbox_status",
    "Get sandbox status",
    { sandboxId: z.string() },
    async (args: Record<string, unknown>) => {
      const sandboxId = args.sandboxId as string;
      const sandbox = opts.sandboxManager.getStatus(sandboxId);
      return jsonText(sandbox ?? buildSandboxNotFoundPayload(sandboxId), 2);
    },
  );

  server.tool(
    "sandbox_playbook",
    "Read the current playbook for a sandbox",
    { sandboxId: z.string() },
    async (args: Record<string, unknown>) => ({
      content: [{
        type: "text",
        text: opts.sandboxManager.readPlaybook(args.sandboxId as string),
      }],
    }),
  );

  server.tool(
    "sandbox_list",
    "List active sandboxes",
    {},
    async () => jsonText(opts.sandboxManager.list(), 2),
  );

  server.tool(
    "sandbox_destroy",
    "Destroy a sandbox and clean up its data",
    { sandboxId: z.string() },
    async (args: Record<string, unknown>) =>
      jsonText(
        {
          destroyed: opts.sandboxManager.destroy(args.sandboxId as string),
          sandboxId: args.sandboxId,
        },
        2,
      ),
  );
}
