import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchCatalogFromModuleMap,
  createAgentAppFetchHandler,
  createAgentAppFetchLazyRuntime,
  createAgentAppFetchRuntimeFactoryFromModuleMap,
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchEntrypointTemplate,
} from "../src/control-plane/agent-app-fetch/index.js";

import type { AgentRuntime } from "../src/runtimes/base.js";

declare global {
  var __AUTOCTX_FETCH_ENTRYPOINT_SMOKE__: Map<string, GeneratedEntrypointSmokeState> | undefined;
}

interface GeneratedEntrypointSmokeState {
  agentModuleLoads: number;
  runtimeModuleLoads: number;
  runtimeFactoryCalls: number;
}

interface EvaluatedGeneratedEntrypoint {
  agentAppFetchHostCapabilityManifest: {
    acceptedHostCapabilities: string[];
  };
  createAgentAppFetchEntrypoint(
    hostCapabilities?: Record<string, unknown>,
  ): (request: Request) => Promise<Response>;
}

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("generated Fetch entrypoint runtime factory integration", () => {
  it("selects a bundled named runtime factory lazily through generated entrypoints", async () => {
    const { generated, state } = createGeneratedEntrypointSmoke("named-lazy");
    const handler = generated.createAgentAppFetchEntrypoint({ runtimeFactoryName: "bundled" });

    expect(state).toMatchObject({
      agentModuleLoads: 0,
      runtimeModuleLoads: 0,
      runtimeFactoryCalls: 0,
    });

    await expect(invokePrompt(handler, "named-run", "hello")).resolves.toEqual({
      ok: true,
      agent: "support",
      id: "named-run",
      result: {
        text: "bundled:hello",
        sessionId: "agent:support:default",
      },
    });
    expect(state).toEqual({
      agentModuleLoads: 1,
      runtimeModuleLoads: 1,
      runtimeFactoryCalls: 1,
    });
  });

  it("prefers a direct runtime over bundled named runtime factories", async () => {
    const { generated, state } = createGeneratedEntrypointSmoke("direct-runtime");
    const handler = generated.createAgentAppFetchEntrypoint({
      runtime: createRuntime("direct"),
      runtimeFactoryName: "bundled",
    });

    await expect(invokePrompt(handler, "direct-runtime-run", "hello")).resolves.toMatchObject({
      result: { text: "direct:hello" },
    });
    expect(state.runtimeModuleLoads).toBe(0);
    expect(state.runtimeFactoryCalls).toBe(0);
  });

  it("prefers a direct runtimeFactory over bundled named runtime factories", async () => {
    const { generated, state } = createGeneratedEntrypointSmoke("direct-factory");
    const calls = { directFactory: 0 };
    const handler = generated.createAgentAppFetchEntrypoint({
      runtimeFactory: () => {
        calls.directFactory += 1;
        return createRuntime("direct-factory");
      },
      runtimeFactoryName: "bundled",
    });

    expect(calls.directFactory).toBe(0);
    await expect(invokePrompt(handler, "direct-factory-run", "hello")).resolves.toMatchObject({
      result: { text: "direct-factory:hello" },
    });
    expect(calls.directFactory).toBe(1);
    expect(state.runtimeModuleLoads).toBe(0);
    expect(state.runtimeFactoryCalls).toBe(0);
  });

  it("exports runtime factory host capability keys from generated manifests", () => {
    const { generated } = createGeneratedEntrypointSmoke("manifest");

    expect(generated.agentAppFetchHostCapabilityManifest.acceptedHostCapabilities).toEqual(
      expect.arrayContaining([
        "runtimeFactory",
        "runtimeFactoryName",
        "runtimeFactoryPlan",
        "runtimeFactoryModuleMap",
      ]),
    );
  });

  it("keeps generated entrypoint smoke generic Fetch/ESM only", () => {
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
    const generatedSource = createGeneratedEntrypointSmoke("neutrality").source;

    for (const source of [templateSource, generatedSource]) {
      expect(source).not.toContain("process.env");
      expect(source).not.toContain("discoverAutoctxAgents");
      expect(source).not.toContain("fs.readdir");
      expect(source).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
    }
  });
});

function createGeneratedEntrypointSmoke(tag: string): {
  generated: EvaluatedGeneratedEntrypoint;
  source: string;
  state: GeneratedEntrypointSmokeState;
} {
  const state = createSmokeState(tag);
  const catalogPlan = planAgentAppFetchCatalog({
    entries: [
      {
        name: "support",
        relativePath: ".autoctx/agents/support.mjs",
        extension: ".mjs",
      },
    ],
    moduleSpecifier: () => dataModule(agentModuleSource(tag)),
  });
  const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
    entries: [
      {
        name: "bundled",
        relativePath: ".autoctx/runtimes/bundled.mjs",
        extension: ".mjs",
      },
    ],
    moduleSpecifier: () => dataModule(runtimeFactoryModuleSource(tag)),
  });
  const source = renderAgentAppFetchEntrypointTemplate(catalogPlan, { runtimeFactoryPlan });

  return {
    generated: evaluateGeneratedEntrypoint(source),
    source,
    state,
  };
}

function createSmokeState(tag: string): GeneratedEntrypointSmokeState {
  const registry = (globalThis.__AUTOCTX_FETCH_ENTRYPOINT_SMOKE__ ??= new Map());
  const state: GeneratedEntrypointSmokeState = {
    agentModuleLoads: 0,
    runtimeModuleLoads: 0,
    runtimeFactoryCalls: 0,
  };
  registry.set(tag, state);
  return state;
}

function agentModuleSource(tag: string): string {
  return `
const state = globalThis.__AUTOCTX_FETCH_ENTRYPOINT_SMOKE__.get(${JSON.stringify(tag)});
state.agentModuleLoads += 1;
export default async function supportAgent(ctx) {
  const runtime = await ctx.init();
  const session = await runtime.session("default");
  const reply = await session.prompt(ctx.payload.prompt);
  return { text: reply.text, sessionId: reply.sessionId };
}
`;
}

function runtimeFactoryModuleSource(tag: string): string {
  return `
const state = globalThis.__AUTOCTX_FETCH_ENTRYPOINT_SMOKE__.get(${JSON.stringify(tag)});
state.runtimeModuleLoads += 1;
export default function createRuntime() {
  state.runtimeFactoryCalls += 1;
  return {
    name: "bundled-runtime",
    generate: async ({ prompt }) => ({ text: "bundled:" + prompt }),
    revise: async ({ prompt }) => ({ text: "bundled:revise:" + prompt }),
  };
}
`;
}

function dataModule(source: string): string {
  return `data:text/javascript;base64,${Buffer.from(source, "utf-8").toString("base64")}`;
}

function evaluateGeneratedEntrypoint(source: string): EvaluatedGeneratedEntrypoint {
  const rewrittenSource = source
    .replace(/^import \{[^}]+\} from "[^"]+";\n/u, "")
    .replace(
      /export const fetch = createAgentAppFetchEntrypoint\(\);/u,
      "const fetch = createAgentAppFetchEntrypoint();",
    )
    .replace(/export const /gu, "const ")
    .replace(/export function /gu, "function ")
    .replace(/import\(("data:[^"]+")\)/gu, "importModule($1)")
    .replace(/\nexport default \{ fetch \};\n?/u, "\n");
  const moduleFactory = new Function(
    "createAgentAppFetchCatalogFromModuleMap",
    "createAgentAppFetchHandler",
    "createAgentAppFetchLazyRuntime",
    "createAgentAppFetchRuntimeFactoryFromModuleMap",
    "importModule",
    `${rewrittenSource}\nreturn { agentAppFetchHostCapabilityManifest, createAgentAppFetchEntrypoint };`,
  ) as (...args: unknown[]) => EvaluatedGeneratedEntrypoint;

  return moduleFactory(
    createAgentAppFetchCatalogFromModuleMap,
    createAgentAppFetchHandler,
    createAgentAppFetchLazyRuntime,
    createAgentAppFetchRuntimeFactoryFromModuleMap,
    (specifier: string) => import(specifier),
  );
}

async function invokePrompt(
  handler: (request: Request) => Promise<Response>,
  id: string,
  prompt: string,
): Promise<unknown> {
  const response = await handler(
    new Request("https://generated-fetch-smoke.test/agents/support/invoke", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id, payload: { prompt } }),
    }),
  );
  if (response.status !== 200) {
    throw new Error(
      `Expected generated Fetch response 200, received ${response.status}: ${await response.text()}`,
    );
  }
  return await response.json();
}

function createRuntime(name: string): AgentRuntime {
  return {
    name: `${name}-runtime`,
    generate: async ({ prompt }) => ({ text: `${name}:${prompt}` }),
    revise: async ({ prompt }) => ({ text: `${name}:revise:${prompt}` }),
  };
}
