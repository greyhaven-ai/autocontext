import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  createAgentAppFetchSessionEventStoreConformanceCases,
  createAgentAppFetchWorkspaceStoreConformanceCases,
  createInMemoryAgentAppFetchSessionEventStore,
  createInMemoryAgentAppFetchWorkspaceStore,
  runAgentAppFetchSessionEventStoreConformance,
  runAgentAppFetchWorkspaceStoreConformance,
} from "../src/control-plane/agent-app-fetch/index.js";

describe("agent app Fetch store conformance suite", () => {
  it("exposes reusable workspace store conformance cases", async () => {
    const cases = createAgentAppFetchWorkspaceStoreConformanceCases({
      createStore: createInMemoryAgentAppFetchWorkspaceStore,
    });

    expect(cases.map((testCase) => testCase.name)).toEqual([
      "workspace store read-your-writes and lexicographic listing",
      "workspace store byte cloning boundaries",
      "workspace root recursive removal preserves root",
      "workspace env shell execution fails closed",
    ]);
    await expect(
      runAgentAppFetchWorkspaceStoreConformance({
        createStore: createInMemoryAgentAppFetchWorkspaceStore,
      }),
    ).resolves.toBeUndefined();
  });

  it("exposes reusable session event-store conformance cases", async () => {
    const cases = createAgentAppFetchSessionEventStoreConformanceCases({
      createStore: createInMemoryAgentAppFetchSessionEventStore,
    });

    expect(cases.map((testCase) => testCase.name)).toEqual([
      "session event store append idempotency and replay ordering",
      "session event store deep-clones metadata and payloads",
      "session event store preserves child-session links",
    ]);
    await expect(
      runAgentAppFetchSessionEventStoreConformance({
        createStore: createInMemoryAgentAppFetchSessionEventStore,
      }),
    ).resolves.toBeUndefined();
  });

  it("keeps conformance helpers provider-neutral and test-runner agnostic", () => {
    const source = readFileSync(
      join(
        import.meta.dirname,
        "..",
        "src",
        "control-plane",
        "agent-app-fetch",
        "store-conformance.ts",
      ),
      "utf-8",
    );

    expect(source).not.toContain('"node:');
    expect(source).not.toContain("'node:");
    expect(source).not.toContain("from \"vitest\"");
    expect(source).not.toContain("from 'vitest'");
    expect(source).not.toContain("process.env");
    expect(source).not.toMatch(
      /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3/i,
    );
  });
});
