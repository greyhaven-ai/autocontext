import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import type { AutoctxAgentHandler } from "../src/agent-runtime/index.js";
import {
  createAgentAppFetchCatalogFromModuleMap,
  createAgentAppFetchHandler,
  planAgentAppFetchCatalog,
  renderAgentAppFetchModuleMapEntrypoint,
} from "../src/control-plane/agent-app-fetch/index.js";

async function jsonBody(response: Response): Promise<unknown> {
  return await response.json();
}

function request(path: string, init?: RequestInit): Request {
  return new Request(`https://agent-app.test${path}`, init);
}

describe("agent app Fetch catalog planner", () => {
  it("plans a deterministic static catalog and bundler-visible module map from explicit handler entries", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
        {
          name: "audit",
          relativePath: ".autoctx/agents/audit.ts",
          extension: ".ts",
        },
      ],
      moduleSpecifier: (entry) => `../${entry.relativePath}`,
    });

    expect(plan).toEqual({
      target: "fetch",
      handlerDir: ".autoctx/agents",
      routes: ["GET /manifest", "POST /agents/:agent/invoke"],
      entries: [
        {
          name: "audit",
          relativePath: ".autoctx/agents/audit.ts",
          extension: ".ts",
          importSpecifier: "../.autoctx/agents/audit.ts",
        },
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          importSpecifier: "../.autoctx/agents/support.mjs",
          triggers: { webhook: true },
        },
      ],
    });
  });

  it("turns a planned module map into a Fetch catalog without loading handlers for the manifest", async () => {
    let loadCalls = 0;
    const supportHandler: AutoctxAgentHandler<{ message: string }> = async (ctx) => ({
      id: ctx.id,
      message: ctx.payload.message,
      token: ctx.env.SUPPORT_TOKEN,
    });
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
      ],
    });
    const catalog = createAgentAppFetchCatalogFromModuleMap(plan, {
      support: async () => {
        loadCalls += 1;
        return { default: supportHandler };
      },
    });
    const handler = createAgentAppFetchHandler({
      env: { SUPPORT_TOKEN: "explicit-token" },
      catalog,
    });

    const manifest = await handler(request("/manifest"));

    expect(manifest.status).toBe(200);
    expect(await jsonBody(manifest)).toEqual({
      ok: true,
      target: "fetch",
      agents: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
      ],
    });
    expect(loadCalls).toBe(0);

    const invocation = await handler(
      request("/agents/support/invoke", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ id: "ticket-123", payload: { message: "please triage" } }),
      }),
    );

    expect(invocation.status).toBe(200);
    expect(await jsonBody(invocation)).toEqual({
      ok: true,
      agent: "support",
      id: "ticket-123",
      result: {
        id: "ticket-123",
        message: "please triage",
        token: "explicit-token",
      },
    });
    expect(loadCalls).toBe(1);
  });

  it("renders a provider-neutral ESM module-map entrypoint for generated Fetch hosts", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
      moduleSpecifier: (entry) => `./${entry.relativePath}`,
    });

    const source = renderAgentAppFetchModuleMapEntrypoint(plan);

    expect(source).toContain("autoctx/control-plane/agent-app-fetch");
    expect(source).toContain("createAgentAppFetchCatalogFromModuleMap");
    expect(source).toContain("createAgentAppFetchHandler");
    expect(source).toContain('support: () => import("./.autoctx/agents/support.mjs")');
    expect(source).not.toContain("node:");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(/wrangler|cloudflare|vercel|deno deploy|durable object/i);
  });

  it("rejects duplicate, non-agent, and declaration-file entries before rendering module maps", () => {
    expect(() =>
      planAgentAppFetchCatalog({
        entries: [
          { name: "support", relativePath: ".autoctx/agents/support.mjs", extension: ".mjs" },
          { name: "support", relativePath: ".autoctx/agents/support.ts", extension: ".ts" },
        ],
      }),
    ).toThrow("Duplicate AutoContext agent name: support");

    expect(() =>
      planAgentAppFetchCatalog({
        entries: [{ name: "skill", relativePath: ".autoctx/skills/skill.mjs", extension: ".mjs" }],
      }),
    ).toThrow("Agent app Fetch catalog entries must be under .autoctx/agents");

    expect(() =>
      planAgentAppFetchCatalog({
        entries: [{ name: "types", relativePath: ".autoctx/agents/types.d.ts", extension: ".ts" }],
      }),
    ).toThrow("Declaration files cannot be agent app handlers");
  });

  it("keeps the catalog planner source free of runtime filesystem discovery and provider deployment imports", () => {
    const source = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "catalog-planner.ts",
      ),
      "utf-8",
    );

    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain("discoverAutoctxAgents");
    expect(source).not.toContain("fs.readdir");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(/wrangler|cloudflare build|durable object namespace/i);
  });
});
