import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const conformanceDocPath = join(repoRoot, "docs", "fetch-conformance.md");
const edgeDocPath = join(repoRoot, "docs", "edge-runtime-compatibility.md");
const packagingDocPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");

const HELPER_NAMES = [
  "createAgentAppFetchWorkspaceStoreConformanceCases",
  "runAgentAppFetchWorkspaceStoreConformance",
  "createAgentAppFetchSessionEventStoreConformanceCases",
  "runAgentAppFetchSessionEventStoreConformance",
  "createAgentAppFetchInvocationConformanceCases",
  "runAgentAppFetchInvocationConformance",
] as const;

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("Fetch conformance documentation", () => {
  it("documents workspace, session, and invocation conformance helpers", () => {
    const doc = readFileSync(conformanceDocPath, "utf-8");

    expect(doc).toContain("# Fetch Conformance");
    expect(doc).toContain("## Workspace Store Conformance");
    expect(doc).toContain("## Session Event-Store Conformance");
    expect(doc).toContain("## Invocation Conformance");
    expect(doc).toContain("## Host Guarantees");
    expect(doc).toContain("## Failure Modes");
    for (const helperName of HELPER_NAMES) {
      expect(doc).toContain(helperName);
    }
  });

  it("shows runner-agnostic case usage and one-shot runners", () => {
    const doc = readFileSync(conformanceDocPath, "utf-8");

    expect(doc).toContain(
      "for (const testCase of createAgentAppFetchWorkspaceStoreConformanceCases",
    );
    expect(doc).toContain("it(testCase.name, testCase.run)");
    expect(doc).toContain("await runAgentAppFetchWorkspaceStoreConformance");
    expect(doc).toContain("await runAgentAppFetchSessionEventStoreConformance");
    expect(doc).toContain("await runAgentAppFetchInvocationConformance");
    expect(doc).toContain("createHandler: createHostFetchHandler");
  });

  it("documents runtime factory invocation precedence and laziness", () => {
    const doc = readFileSync(conformanceDocPath, "utf-8");

    expect(doc).toContain("runtimeFactory");
    expect(doc).toContain("runtimeFactoryName");
    expect(doc).toContain("`runtime` takes precedence over `runtimeFactory`");
    expect(doc).toContain("`runtimeFactory` takes precedence over `runtimeFactoryName`");
    expect(doc).toContain("named runtime factories load lazily");
  });

  it("links the conformance guide from related Fetch docs", () => {
    for (const path of [edgeDocPath, packagingDocPath, tsReadmePath]) {
      expect(readFileSync(path, "utf-8")).toContain("fetch-conformance.md");
    }
  });

  it("keeps the conformance docs generic and provider-neutral", () => {
    const doc = readFileSync(conformanceDocPath, "utf-8");

    expect(doc).not.toContain("process.env");
    expect(doc).not.toContain("discoverAutoctxAgents");
    expect(doc).not.toContain("fs.readdir");
    expect(doc).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
  });
});
