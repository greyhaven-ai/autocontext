import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchHandler,
  createInMemoryAgentAppFetchSessionEventStore,
  createInMemoryAgentAppFetchWorkspaceStore,
  type AgentAppFetchHandlerOptions,
  type AgentAppFetchRuntimeFactory,
} from "../src/control-plane/agent-app-fetch/index.js";
import { buildFetchConformanceHostWrapperExample } from "../examples/fetch-conformance-host-wrapper.js";

const repoRoot = join(import.meta.dirname, "..", "..");
const apiReferencePath = join(repoRoot, "docs", "fetch-api-reference.md");
const conformanceDocPath = join(repoRoot, "docs", "fetch-conformance.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");
const examplePath = join(
  import.meta.dirname,
  "..",
  "examples",
  "fetch-conformance-host-wrapper.ts",
);

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("Fetch conformance host-wrapper example", () => {
  it("runs workspace, session, invocation, and runtime-factory conformance suites", async () => {
    const example = buildFetchConformanceHostWrapperExample({
      createHandler: (options) => createAgentAppFetchHandler({
        ...options,
        allowInsecureUnauthenticated: true,
      }),
    });

    expect(example.workspaceStoreCases.map((testCase) => testCase.name)).toContain(
      "workspace store read-your-writes and lexicographic listing",
    );
    expect(example.sessionEventStoreCases.map((testCase) => testCase.name)).toContain(
      "session event store append idempotency and replay ordering",
    );
    expect(example.invocationCases.map((testCase) => testCase.name)).toEqual(
      expect.arrayContaining([
        "Fetch invocation wires explicit runtime factory capability",
        "Fetch invocation prefers explicit runtime over runtime factories",
        "Fetch invocation prefers explicit runtime factory over named runtime factories",
        "Fetch invocation selects named runtime factories lazily",
      ]),
    );

    await example.runAllConformance();
  });

  it("forwards exact host-created capabilities to the supplied wrapper", async () => {
    let capturedOptions:
      | AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>
      | undefined;
    const example = buildFetchConformanceHostWrapperExample({
      createHandler: (options) => {
        capturedOptions = options;
        return async () => Response.json({ ok: true });
      },
    });
    const workspaceStore = createInMemoryAgentAppFetchWorkspaceStore();
    const sessionEventStore = createInMemoryAgentAppFetchSessionEventStore();
    const runtimeFactory: AgentAppFetchRuntimeFactory = () => ({
      name: "host-runtime",
      generate: async ({ prompt }) => ({ text: `host:${prompt}` }),
      revise: async ({ prompt }) => ({ text: `host:revise:${prompt}` }),
    });
    const options = {
      catalog: [],
      env: { HOST_VALUE: "explicit" },
      workspaceStore,
      sessionEventStore,
      runtimeFactory,
      maxBodyBytes: 123,
    } satisfies AgentAppFetchHandlerOptions<Record<string, unknown>, unknown>;

    const handler = await example.createHandler(options);
    await handler(new Request("https://example.test/manifest"));

    expect(capturedOptions).toBe(options);
    expect(capturedOptions?.workspaceStore).toBe(workspaceStore);
    expect(capturedOptions?.sessionEventStore).toBe(sessionEventStore);
    expect(capturedOptions?.runtimeFactory).toBe(runtimeFactory);
    expect(capturedOptions?.env).toEqual({ HOST_VALUE: "explicit" });
  });

  it("links the host-wrapper example from related Fetch docs", () => {
    for (const path of [apiReferencePath, conformanceDocPath, tsReadmePath]) {
      expect(readFileSync(path, "utf-8")).toContain("fetch-conformance-host-wrapper.ts");
    }
  });

  it("keeps the host-wrapper example generic Fetch/ESM only", () => {
    const example = readFileSync(examplePath, "utf-8");

    expect(example).not.toContain("process.env");
    expect(example).not.toContain("discoverAutoctxAgents");
    expect(example).not.toContain("fs.readdir");
    expect(example).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
  });
});
