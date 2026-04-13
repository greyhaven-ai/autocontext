import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";

interface SandboxToolResult {
  content: Array<{ type: "text"; text: string }>;
}

interface SandboxToolServer {
  tool(
    name: string,
    description: string,
    schema: Record<string, unknown>,
    handler: (args: Record<string, unknown>) => Promise<SandboxToolResult> | SandboxToolResult,
  ): void;
}

type SandboxToolRegistrar = McpServer | SandboxToolServer;

interface SandboxManagerLike {
  create(scenarioName: string, userId?: string): unknown;
  run(sandboxId: string, generations?: number): Promise<unknown>;
  getStatus(sandboxId: string): unknown | null;
  readPlaybook(sandboxId: string): string;
  list(): unknown[];
  destroy(sandboxId: string): boolean;
}

function jsonContent(payload: unknown): SandboxToolResult {
  return {
    content: [
      {
        type: "text",
        text: JSON.stringify(payload, null, 2),
      },
    ],
  };
}

export function buildSandboxNotFoundPayload(sandboxId: string): Record<string, string> {
  return { error: `Sandbox '${sandboxId}' not found` };
}

export function registerSandboxTools(
  server: SandboxToolRegistrar,
  opts: { sandboxManager: SandboxManagerLike },
): void {
  const { sandboxManager } = opts;
  const toolServer = server as SandboxToolServer;

  toolServer.tool(
    "sandbox_create",
    "Create an isolated sandbox for scenario execution",
    { scenario: z.string(), userId: z.string().default("anonymous") },
    async (args) => jsonContent(
      sandboxManager.create(String(args.scenario), String(args.userId ?? "anonymous")),
    ),
  );

  toolServer.tool(
    "sandbox_run",
    "Run generation(s) in a sandbox",
    { sandboxId: z.string(), generations: z.number().int().default(1) },
    async (args) => jsonContent(
      await sandboxManager.run(String(args.sandboxId), Number(args.generations ?? 1)),
    ),
  );

  toolServer.tool(
    "sandbox_status",
    "Get sandbox status",
    { sandboxId: z.string() },
    async (args) => {
      const sandboxId = String(args.sandboxId);
      return jsonContent(
        sandboxManager.getStatus(sandboxId) ?? buildSandboxNotFoundPayload(sandboxId),
      );
    },
  );

  toolServer.tool(
    "sandbox_playbook",
    "Read the current playbook for a sandbox",
    { sandboxId: z.string() },
    async (args) => ({
      content: [
        {
          type: "text",
          text: sandboxManager.readPlaybook(String(args.sandboxId)),
        },
      ],
    }),
  );

  toolServer.tool(
    "sandbox_list",
    "List active sandboxes",
    {},
    async () => jsonContent(sandboxManager.list()),
  );

  toolServer.tool(
    "sandbox_destroy",
    "Destroy a sandbox and clean up its data",
    { sandboxId: z.string() },
    async (args) => {
      const sandboxId = String(args.sandboxId);
      return jsonContent({
        destroyed: sandboxManager.destroy(sandboxId),
        sandboxId,
      });
    },
  );
}
