import { readFileSync } from "node:fs";
import { join } from "node:path";

import Ajv, { type ValidateFunction } from "ajv";
import { describe, expect, it } from "vitest";

import {
  agentAppFetchHostCapabilityManifestSchema,
  createAgentAppFetchHostCapabilityManifest,
  planAgentAppFetchCatalog,
  renderAgentAppFetchEntrypointTemplate,
  renderAgentAppFetchHostCapabilityManifest,
  renderAgentAppFetchHostCapabilityManifestSchema,
} from "../src/control-plane/agent-app-fetch/index.js";

describe("agent app Fetch host capability manifest", () => {
  it("builds a deterministic provider-neutral manifest from a catalog plan", () => {
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
    });

    expect(createAgentAppFetchHostCapabilityManifest(plan)).toEqual({
      target: "fetch",
      routes: ["GET /manifest", "GET /agents", "POST /agents/:agent/invoke"],
      agents: [
        {
          name: "audit",
          relativePath: ".autoctx/agents/audit.ts",
          extension: ".ts",
        },
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
          triggers: { webhook: true },
        },
      ],
      acceptedHostCapabilities: [
        "env",
        "runtime",
        "runtimeFactory",
        "runtimeFactoryName",
        "workspace",
        "workspaceStore",
        "commands",
        "tools",
        "eventStore",
        "sessionEventStore",
        "eventSink",
        "maxBodyBytes",
      ],
      requiredHostCapabilities: [],
      unsupportedDefaults: [
        "runtime_filesystem_discovery",
        "ambient_environment_capture",
        "local_shell_execution",
        "provider_deployment_configuration",
        "hosted_orchestration",
      ],
    });
  });

  it("renders stable JSON for external hosts", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });

    const rendered = renderAgentAppFetchHostCapabilityManifest(plan);

    expect(rendered).toBe(
      `${JSON.stringify(createAgentAppFetchHostCapabilityManifest(plan), null, 2)}\n`,
    );
    expect(JSON.parse(rendered)).toMatchObject({
      target: "fetch",
      acceptedHostCapabilities: expect.arrayContaining([
        "env",
        "runtime",
        "runtimeFactory",
        "runtimeFactoryName",
        "workspaceStore",
        "sessionEventStore",
        "commands",
        "tools",
        "eventSink",
      ]),
      unsupportedDefaults: expect.arrayContaining([
        "runtime_filesystem_discovery",
        "ambient_environment_capture",
        "local_shell_execution",
      ]),
    });
  });

  it("validates rendered manifests against a provider-neutral JSON schema", () => {
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
    const validate = compileManifestSchema();
    const renderedManifest = JSON.parse(renderAgentAppFetchHostCapabilityManifest(plan));
    const renderedSchema = JSON.parse(renderAgentAppFetchHostCapabilityManifestSchema());

    expect(validate(renderedManifest)).toBe(true);
    expect(renderedSchema).toEqual(agentAppFetchHostCapabilityManifestSchema);
  });

  it("rejects Fetch manifest drift through the JSON schema", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });
    const manifest = createAgentAppFetchHostCapabilityManifest(plan);
    const validate = compileManifestSchema();

    expect(
      validate({
        ...manifest,
        acceptedHostCapabilities: [...manifest.acceptedHostCapabilities, "providerBinding"],
      }),
    ).toBe(false);
    expect(
      validate({
        ...manifest,
        routes: [...manifest.routes, "POST /deploy"],
      }),
    ).toBe(false);
    expect(
      validate({
        ...manifest,
        routes: manifest.routes.slice(1),
      }),
    ).toBe(false);
    expect(
      validate({
        ...manifest,
        agents: [{ ...manifest.agents[0], relativePath: "/absolute/agent.mjs" }],
      }),
    ).toBe(false);
    const { unsupportedDefaults, ...missingRequiredSection } = manifest;
    void unsupportedDefaults;
    expect(validate(missingRequiredSection)).toBe(false);
    expect(
      validate({
        ...manifest,
        hostedDeployment: true,
      }),
    ).toBe(false);
  });

  it("exports the manifest from generated Fetch entrypoints", () => {
    const plan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });

    const source = renderAgentAppFetchEntrypointTemplate(plan);

    expect(source).toContain("export const agentAppFetchHostCapabilityManifest = {");
    expect(source).toContain('"GET /agents"');
    expect(source).toContain('"acceptedHostCapabilities"');
    expect(source).toContain('"unsupportedDefaults"');
    expect(source).toContain('"runtime_filesystem_discovery"');
    expect(source).toContain('"ambient_environment_capture"');
    expect(source).toContain('"local_shell_execution"');
  });

  it("keeps manifest helpers and generated output free of provider deployment code", () => {
    const manifestSource = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "capability-manifest.ts",
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

    for (const source of [manifestSource, generatedSource]) {
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

function compileManifestSchema(): ValidateFunction {
  const ajv = new Ajv({ allErrors: true, strict: true });
  return ajv.compile(agentAppFetchHostCapabilityManifestSchema);
}
