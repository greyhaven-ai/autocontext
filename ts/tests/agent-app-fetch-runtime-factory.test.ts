import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchLazyRuntime,
  createAgentAppFetchRuntimeFactoryFromModuleMap,
  planAgentAppFetchCatalog,
  planAgentAppFetchRuntimeFactories,
  renderAgentAppFetchEntrypointTemplate,
} from "../src/control-plane/agent-app-fetch/index.js";

import type { AgentRuntime } from "../src/runtimes/base.js";

describe("agent app Fetch runtime factory bundling seam", () => {
  it("plans deterministic bundled runtime factories from explicit runtime entries", () => {
    const plan = planAgentAppFetchRuntimeFactories({
      entries: [
        {
          name: "slow",
          relativePath: ".autoctx/runtimes/slow.ts",
          extension: ".ts",
        },
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
        },
      ],
      moduleSpecifier: (entry) => `../${entry.relativePath}`,
    });

    expect(plan).toEqual({
      target: "fetch",
      runtimeDir: ".autoctx/runtimes",
      entries: [
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
          importSpecifier: "../.autoctx/runtimes/fast.mjs",
        },
        {
          name: "slow",
          relativePath: ".autoctx/runtimes/slow.ts",
          extension: ".ts",
          importSpecifier: "../.autoctx/runtimes/slow.ts",
        },
      ],
    });
  });

  it("loads a bundled runtime factory lazily from a static module map", async () => {
    let loadCalls = 0;
    let factoryCalls = 0;
    const runtime: AgentRuntime = {
      name: "fast-runtime",
      generate: async ({ prompt }) => ({ text: `fast:${prompt}` }),
      revise: async ({ prompt }) => ({ text: `revise:${prompt}` }),
    };
    const plan = planAgentAppFetchRuntimeFactories({
      entries: [
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
        },
      ],
    });
    const runtimeFactory = createAgentAppFetchRuntimeFactoryFromModuleMap(
      plan,
      {
        fast: async () => {
          loadCalls += 1;
          return {
            default: () => {
              factoryCalls += 1;
              return runtime;
            },
          };
        },
      },
      "fast",
    );
    const lazyRuntime = createAgentAppFetchLazyRuntime(runtimeFactory, {
      name: "bundled-fast-runtime",
    });

    expect(loadCalls).toBe(0);
    expect(factoryCalls).toBe(0);
    lazyRuntime.close?.();
    expect(loadCalls).toBe(0);
    await expect(lazyRuntime.generate({ prompt: "hello" })).resolves.toEqual({
      text: "fast:hello",
    });
    await expect(
      lazyRuntime.revise({ prompt: "draft", previousOutput: "", feedback: "" }),
    ).resolves.toEqual({ text: "revise:draft" });
    expect(loadCalls).toBe(1);
    expect(factoryCalls).toBe(1);
  });

  it("uses selected factory names before load and forwards loaded runtime names", async () => {
    const runtime: AgentRuntime = {
      name: "fast-runtime",
      generate: async ({ prompt }) => ({ text: `fast:${prompt}` }),
      revise: async ({ prompt }) => ({ text: `revise:${prompt}` }),
    };
    const plan = planAgentAppFetchRuntimeFactories({
      entries: [
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
        },
      ],
    });
    const runtimeFactory = createAgentAppFetchRuntimeFactoryFromModuleMap(
      plan,
      { fast: () => ({ default: () => runtime }) },
      "fast",
    );
    const lazyRuntime = createAgentAppFetchLazyRuntime(runtimeFactory);

    expect(lazyRuntime.name).toBe("fast");
    await expect(lazyRuntime.generate({ prompt: "hello" })).resolves.toEqual({
      text: "fast:hello",
    });
    expect(lazyRuntime.name).toBe("fast-runtime");
  });

  it("renders generated Fetch entrypoints with explicit runtime factory hooks", () => {
    const catalogPlan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });
    const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
      entries: [
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
        },
      ],
    });

    const source = renderAgentAppFetchEntrypointTemplate(catalogPlan, { runtimeFactoryPlan });

    expect(source).toContain("createAgentAppFetchRuntimeFactoryFromModuleMap");
    expect(source).toContain("createAgentAppFetchLazyRuntime");
    expect(source).toContain("export const agentAppFetchRuntimeFactoryPlan = {");
    expect(source).toContain("export const agentAppFetchRuntimeFactoryModuleMap = {");
    expect(source).toContain('fast: () => import("./.autoctx/runtimes/fast.mjs")');
    expect(source).toContain("hostCapabilities.runtimeFactory");
    expect(source).toContain("hostCapabilities.runtimeFactoryName");
    expect(source).toContain("runtime: hostCapabilities.runtime ??");
  });

  it("does not resolve bundled runtime factories before direct host capabilities", () => {
    const catalogPlan = planAgentAppFetchCatalog({
      entries: [
        {
          name: "support",
          relativePath: ".autoctx/agents/support.mjs",
          extension: ".mjs",
        },
      ],
    });
    const runtimeFactoryPlan = planAgentAppFetchRuntimeFactories({
      entries: [
        {
          name: "fast",
          relativePath: ".autoctx/runtimes/fast.mjs",
          extension: ".mjs",
        },
      ],
    });
    const source = renderAgentAppFetchEntrypointTemplate(catalogPlan, { runtimeFactoryPlan });
    let bundledLookups = 0;
    const directRuntime: AgentRuntime = {
      name: "direct-runtime",
      generate: async ({ prompt }) => ({ text: prompt }),
      revise: async ({ prompt }) => ({ text: prompt }),
    };
    const directRuntimeFactory = () => directRuntime;
    const createEntrypoint = evaluateGeneratedEntrypoint(source, {
      createAgentAppFetchRuntimeFactoryFromModuleMap: () => {
        bundledLookups += 1;
        throw new Error("stale bundled runtime factory should not resolve");
      },
    });

    const directRuntimeOptions = createEntrypoint({
      runtime: directRuntime,
      runtimeFactoryName: "stale",
    });
    const directFactoryOptions = createEntrypoint({
      runtimeFactory: directRuntimeFactory,
      runtimeFactoryName: "stale",
    });

    expect(directRuntimeOptions.runtime).toBe(directRuntime);
    expect(directFactoryOptions.runtime).toMatchObject({ lazyRuntime: true });
    expect(directFactoryOptions.lazyFactories).toEqual([directRuntimeFactory]);
    expect(bundledLookups).toBe(0);
  });

  it("rejects duplicate, non-runtime, and declaration-file runtime factory entries", () => {
    expect(() =>
      planAgentAppFetchRuntimeFactories({
        entries: [
          { name: "fast", relativePath: ".autoctx/runtimes/fast.mjs", extension: ".mjs" },
          { name: "fast", relativePath: ".autoctx/runtimes/other.mjs", extension: ".mjs" },
        ],
      }),
    ).toThrow("Duplicate AutoContext runtime factory name: fast");

    expect(() =>
      planAgentAppFetchRuntimeFactories({
        entries: [{ name: "agent", relativePath: ".autoctx/agents/a.mjs", extension: ".mjs" }],
      }),
    ).toThrow("Agent app Fetch runtime factory entries must be under .autoctx/runtimes");

    expect(() =>
      planAgentAppFetchRuntimeFactories({
        entries: [
          { name: "types", relativePath: ".autoctx/runtimes/types.d.ts", extension: ".ts" },
        ],
      }),
    ).toThrow("Declaration files cannot be Fetch runtime factories");
  });

  it("keeps runtime factory helpers and generated output provider-neutral", () => {
    const helperSource = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "runtime-factory.ts",
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
      {
        runtimeFactoryPlan: planAgentAppFetchRuntimeFactories({
          entries: [
            {
              name: "fast",
              relativePath: ".autoctx/runtimes/fast.mjs",
              extension: ".mjs",
            },
          ],
        }),
      },
    );

    for (const source of [helperSource, generatedSource]) {
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

type GeneratedHostCapabilities = Record<string, unknown>;

type GeneratedEntrypoint = (hostCapabilities?: GeneratedHostCapabilities) => GeneratedHandlerOptions;

interface GeneratedHandlerOptions extends Record<string, unknown> {
  runtime?: unknown;
  lazyFactories?: unknown[];
}

interface GeneratedEntrypointStubs {
  createAgentAppFetchRuntimeFactoryFromModuleMap?: (...args: unknown[]) => unknown;
}

function evaluateGeneratedEntrypoint(
  source: string,
  stubs: GeneratedEntrypointStubs = {},
): GeneratedEntrypoint {
  const lazyFactories: unknown[] = [];
  const rewrittenSource = source
    .replace(/^import \{[^}]+\} from "[^"]+";\n/u, "")
    .replace(/export const /gu, "const ")
    .replace(/export function /gu, "function ")
    .replace(/\nexport default \{ fetch \};\n?/u, "\n");
  const moduleFactory = new Function(
    "createAgentAppFetchCatalogFromModuleMap",
    "createAgentAppFetchHandler",
    "createAgentAppFetchLazyRuntime",
    "createAgentAppFetchRuntimeFactoryFromModuleMap",
    `${rewrittenSource}\nreturn { createAgentAppFetchEntrypoint };`,
  ) as (...args: unknown[]) => { createAgentAppFetchEntrypoint: GeneratedEntrypoint };
  const generatedModule = moduleFactory(
    () => ({}),
    (options: GeneratedHandlerOptions) => ({ ...options, lazyFactories }),
    (factory: unknown) => {
      lazyFactories.push(factory);
      return { lazyRuntime: true, factory };
    },
    stubs.createAgentAppFetchRuntimeFactoryFromModuleMap ?? (() => undefined),
  );
  return generatedModule.createAgentAppFetchEntrypoint;
}
