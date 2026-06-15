import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchHandler,
  createAgentAppFetchInvocationConformanceCases,
  runAgentAppFetchInvocationConformance,
} from "../src/control-plane/agent-app-fetch/index.js";

describe("agent app Fetch invocation conformance suite", () => {
  it("exposes reusable Fetch invocation conformance cases", async () => {
    const cases = createAgentAppFetchInvocationConformanceCases({
      createHandler: createAgentAppFetchHandler,
    });

    expect(cases.map((testCase) => testCase.name)).toEqual([
      "Fetch invocation manifests advertise agents without loading handlers",
      "Fetch invocation posts payloads with explicit env and workspace",
      "Fetch invocation wires explicit runtime capability",
      "Fetch invocation wires explicit runtime factory capability",
      "Fetch invocation prefers explicit runtime over runtime factories",
      "Fetch invocation prefers explicit runtime factory over named runtime factories",
      "Fetch invocation selects named runtime factories lazily",
      "Fetch invocation returns stable error envelopes",
    ]);
    await expect(
      runAgentAppFetchInvocationConformance({
        createHandler: createAgentAppFetchHandler,
      }),
    ).resolves.toBeUndefined();
  });

  it("fails when a host wrapper drops the supplied workspace store", async () => {
    await expect(
      runAgentAppFetchInvocationConformance({
        createHandler: (options) => {
          const { workspaceStore, ...delegatedOptions } = options;
          void workspaceStore;
          return createAgentAppFetchHandler(delegatedOptions);
        },
      }),
    ).rejects.toThrow("expected supplied workspaceStore to be used");
  });

  it("fails when a host wrapper drops runtime factory capabilities", async () => {
    await expect(
      runAgentAppFetchInvocationConformance({
        createHandler: (options) => {
          const {
            runtimeFactory,
            runtimeFactoryName,
            runtimeFactoryPlan,
            runtimeFactoryModuleMap,
            ...delegatedOptions
          } = options;
          void runtimeFactory;
          void runtimeFactoryName;
          void runtimeFactoryPlan;
          void runtimeFactoryModuleMap;
          return createAgentAppFetchHandler(delegatedOptions);
        },
      }),
    ).rejects.toThrow("expected explicit runtimeFactory to be used");
  });

  it("keeps invocation conformance helpers provider-neutral and test-runner agnostic", () => {
    const source = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "invocation-conformance.ts",
      ),
      "utf-8",
    );

    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain('from "vitest"');
    expect(source).not.toContain("from 'vitest'");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(
      /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3/i,
    );
  });
});
