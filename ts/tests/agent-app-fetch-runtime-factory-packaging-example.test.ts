import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchCatalogFromModuleMap,
  createAgentAppFetchHandler,
  createAgentAppFetchLazyRuntime,
  createAgentAppFetchRuntimeFactoryFromModuleMap,
} from "../src/control-plane/agent-app-fetch/index.js";
import {
  buildGeneratedFetchRuntimeFactoryPackageArtifacts,
} from "../examples/generated-fetch-runtime-factory-packaging.ts";

import type { AgentRuntime } from "../src/runtimes/base.js";

declare global {
  var __AUTOCTX_FETCH_RUNTIME_FACTORY_PACKAGING__:
    | Map<string, RuntimeFactoryPackagingState>
    | undefined;
}

interface RuntimeFactoryPackagingState {
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

const repoRoot = join(import.meta.dirname, "..", "..");
const apiReferencePath = join(repoRoot, "docs", "fetch-api-reference.md");
const manifestExamplesDocPath = join(repoRoot, "docs", "fetch-host-capability-manifest.md");
const packagingDocPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");
const examplePath = join(
  import.meta.dirname,
  "..",
  "examples",
  "generated-fetch-runtime-factory-packaging.ts",
);

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("generated Fetch runtime-factory packaging example", () => {
  it("emits provider-neutral entrypoint, manifest, schema, and runtime factory plan artifacts", () => {
    const artifacts = buildGeneratedFetchRuntimeFactoryPackageArtifacts();

    expect(artifacts.files.map((file) => file.path).sort()).toEqual([
      "agent-app-fetch-entrypoint.mjs",
      "agent-app-fetch-host-capability-manifest.json",
      "agent-app-fetch-host-capability-manifest.schema.json",
      "agent-app-fetch-runtime-factory-plan.json",
    ]);
    expect(artifacts.runtimeFactoryNames).toEqual(["standard"]);
    expect(artifacts.hostCapabilities).toEqual(
      expect.arrayContaining([
        "runtimeFactory",
        "runtimeFactoryName",
        "runtimeFactoryPlan",
        "runtimeFactoryModuleMap",
      ]),
    );
    expect(artifacts.entrypointSource).toContain("agentAppFetchRuntimeFactoryPlan");
    expect(artifacts.entrypointSource).toContain("agentAppFetchRuntimeFactoryModuleMap");
    expect(artifacts.entrypointSource).toContain("runtimeFactoryPlan");
    expect(artifacts.entrypointSource).toContain("runtimeFactoryModuleMap");
  });

  it("selects bundled runtimeFactoryName lazily through the generated entrypoint", async () => {
    const { generated, state } = createRuntimeFactoryPackagingSmoke("named-lazy");
    const handler = generated.createAgentAppFetchEntrypoint({ runtimeFactoryName: "standard" });

    expect(state).toEqual({ agentModuleLoads: 0, runtimeModuleLoads: 0, runtimeFactoryCalls: 0 });
    await expect(invokePrompt(handler, "named-run", "hello")).resolves.toMatchObject({
      ok: true,
      agent: "support",
      id: "named-run",
      result: { text: "standard:hello" },
    });
    expect(state).toEqual({ agentModuleLoads: 1, runtimeModuleLoads: 1, runtimeFactoryCalls: 1 });
  });

  it("keeps direct runtime and runtimeFactory precedence over named bundled factories", async () => {
    const directRuntimeSmoke = createRuntimeFactoryPackagingSmoke("direct-runtime");
    const directRuntimeHandler = directRuntimeSmoke.generated.createAgentAppFetchEntrypoint({
      runtime: createRuntime("direct"),
      runtimeFactoryName: "standard",
    });

    await expect(
      invokePrompt(directRuntimeHandler, "direct-runtime-run", "hello"),
    ).resolves.toMatchObject({
      result: { text: "direct:hello" },
    });
    expect(directRuntimeSmoke.state.runtimeModuleLoads).toBe(0);
    expect(directRuntimeSmoke.state.runtimeFactoryCalls).toBe(0);

    const directFactorySmoke = createRuntimeFactoryPackagingSmoke("direct-factory");
    const calls = { directFactory: 0 };
    const directFactoryHandler = directFactorySmoke.generated.createAgentAppFetchEntrypoint({
      runtimeFactory: () => {
        calls.directFactory += 1;
        return createRuntime("direct-factory");
      },
      runtimeFactoryName: "standard",
    });

    expect(calls.directFactory).toBe(0);
    await expect(
      invokePrompt(directFactoryHandler, "direct-factory-run", "hello"),
    ).resolves.toMatchObject({
      result: { text: "direct-factory:hello" },
    });
    expect(calls.directFactory).toBe(1);
    expect(directFactorySmoke.state.runtimeModuleLoads).toBe(0);
    expect(directFactorySmoke.state.runtimeFactoryCalls).toBe(0);
  });

  it("links the runtime-factory packaging example from related Fetch docs", () => {
    for (const path of [
      apiReferencePath,
      manifestExamplesDocPath,
      packagingDocPath,
      tsReadmePath,
    ]) {
      expect(readFileSync(path, "utf-8")).toContain(
        "generated-fetch-runtime-factory-packaging.ts",
      );
    }
  });

  it("keeps the runtime-factory packaging example generic Fetch/ESM only", () => {
    const example = readFileSync(examplePath, "utf-8");
    const artifactText = buildGeneratedFetchRuntimeFactoryPackageArtifacts()
      .files.map((file) => file.contents)
      .join("\n");

    for (const source of [example, artifactText]) {
      expect(source).not.toContain("process.env");
      expect(source).not.toContain("discoverAutoctxAgents");
      expect(source).not.toContain("fs.readdir");
      expect(source).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
    }
  });
});

function createRuntimeFactoryPackagingSmoke(tag: string): {
  generated: EvaluatedGeneratedEntrypoint;
  state: RuntimeFactoryPackagingState;
} {
  const state = createSmokeState(tag);
  const artifacts = buildGeneratedFetchRuntimeFactoryPackageArtifacts({
    agentModuleSpecifier: () => dataModule(agentModuleSource(tag)),
    runtimeFactoryModuleSpecifier: () => dataModule(runtimeFactoryModuleSource(tag)),
  });
  return {
    generated: evaluateGeneratedEntrypoint(artifacts.entrypointSource),
    state,
  };
}

function createSmokeState(tag: string): RuntimeFactoryPackagingState {
  const registry = (globalThis.__AUTOCTX_FETCH_RUNTIME_FACTORY_PACKAGING__ ??= new Map());
  const state: RuntimeFactoryPackagingState = {
    agentModuleLoads: 0,
    runtimeModuleLoads: 0,
    runtimeFactoryCalls: 0,
  };
  registry.set(tag, state);
  return state;
}

function agentModuleSource(tag: string): string {
  return `
const state = globalThis.__AUTOCTX_FETCH_RUNTIME_FACTORY_PACKAGING__.get(${JSON.stringify(tag)});
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
const state = globalThis.__AUTOCTX_FETCH_RUNTIME_FACTORY_PACKAGING__.get(${JSON.stringify(tag)});
state.runtimeModuleLoads += 1;
export default function createRuntime() {
  state.runtimeFactoryCalls += 1;
  return {
    name: "standard-runtime",
    generate: async ({ prompt }) => ({ text: "standard:" + prompt }),
    revise: async ({ prompt }) => ({ text: "standard:revise:" + prompt }),
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
    new Request("https://runtime-factory-packaging.example/agents/support/invoke", {
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
