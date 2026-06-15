import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  planAgentAppFetchCatalog,
  renderAgentAppFetchEntrypointTemplate,
} from "../src/control-plane/agent-app-fetch/index.js";

describe("agent app Fetch entrypoint template", () => {
  it("renders a generated Fetch entrypoint with explicit host hooks", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
        {
          name: "audit.worker",
          relativePath: ".autoctx/agents/audit.worker.ts",
          extension: ".ts",
        },
      ],
      moduleSpecifier: (entry) => `./${entry.relativePath}`,
    });

    const source = renderAgentAppFetchEntrypointTemplate(plan);

    expect(source).toContain("autoctx/control-plane/agent-app-fetch");
    expect(source).toContain("createAgentAppFetchCatalogFromModuleMap");
    expect(source).toContain("createAgentAppFetchHandler");
    expect(source).toContain('support: () => import("./.autoctx/agents/support.mjs")');
    expect(source).toContain(
      '"audit.worker": () => import("./.autoctx/agents/audit.worker.ts")',
    );
    expect(source).toContain("export function createAgentAppFetchEntrypoint");
    expect(source).toContain("hostCapabilities.env");
    expect(source).toContain("hostCapabilities.runtime");
    expect(source).toContain("runtimeFactoryPlan: hostCapabilities.runtimeFactoryPlan");
    expect(source).toContain("runtimeFactoryModuleMap: hostCapabilities.runtimeFactoryModuleMap");
    expect(source).toContain("hostCapabilities.workspaceStore");
    expect(source).toContain("hostCapabilities.sessionEventStore");
    expect(source).toContain("hostCapabilities.commands");
    expect(source).toContain("hostCapabilities.tools");
    expect(source).toContain("hostCapabilities.eventSink");
    expect(source).toContain("export const fetch = createAgentAppFetchEntrypoint();");
    expect(source).toContain("export default { fetch };");
  });

  it("allows package specifier customization without changing generated capability names", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });

    const source = renderAgentAppFetchEntrypointTemplate(plan, {
      packageSpecifier: "@autocontext/control-plane/agent-app-fetch",
    });

    expect(source).toContain('from "@autocontext/control-plane/agent-app-fetch"');
    expect(source).toContain("workspaceStore: hostCapabilities.workspaceStore");
    expect(source).toContain("sessionEventStore: hostCapabilities.sessionEventStore");
  });

  it("keeps template and generated output provider-neutral", () => {
    const templateSource = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "entrypoint-template.ts",
      ),
      "utf-8",
    );
    const generatedSource = renderAgentAppFetchEntrypointTemplate(
      planAgentAppFetchCatalog({
        entries: [
          {
            name: "support",
            relativePath: ".autoctx/agents/support.mjs",
            extension: ".mjs",
          },
        ],
      }),
    );

    for (const source of [templateSource, generatedSource]) {
      expect(source).not.toContain('"node:');
      expect(source).not.toContain("'node:");
      expect(source).not.toContain("process.env");
      expect(source).not.toContain("discoverAutoctxAgents");
      expect(source).not.toContain("fs.readdir");
      expect(source).not.toMatch(
        /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3/i,
      );
    }
  });
});
