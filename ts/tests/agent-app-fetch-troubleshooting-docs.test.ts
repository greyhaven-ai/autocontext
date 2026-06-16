import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const troubleshootingDocPath = join(repoRoot, "docs", "fetch-troubleshooting.md");
const apiReferencePath = join(repoRoot, "docs", "fetch-api-reference.md");
const conformanceDocPath = join(repoRoot, "docs", "fetch-conformance.md");
const packagingDocPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");

const REQUIRED_SECTIONS = [
  "# Fetch Adapter Troubleshooting",
  "## Runtime Factory Selection Fails",
  "## Direct Runtime Capability Wins Unexpectedly",
  "## Wrapper Drops Host Stores",
  "## Manifest Or Schema Drift",
  "## Named Factory Loads Too Early",
  "## Provider Assumptions Leak Into Generic Fetch",
  "## Quick Checks",
] as const;

const REQUIRED_TERMS = [
  "runtimeFactoryName",
  "runtimeFactoryPlan",
  "runtimeFactoryModuleMap",
  "runtime",
  "runtimeFactory",
  "workspaceStore",
  "sessionEventStore",
  "agentAppFetchHostCapabilityManifestSchema",
  "createAgentAppFetchInvocationConformanceCases",
  "createAgentAppFetchWorkspaceStoreConformanceCases",
  "createAgentAppFetchSessionEventStoreConformanceCases",
  "fetch-conformance.md",
] as const;

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("Fetch troubleshooting documentation", () => {
  it("documents common generic Fetch host wiring failures", () => {
    const doc = readFileSync(troubleshootingDocPath, "utf-8");

    for (const section of REQUIRED_SECTIONS) {
      expect(doc).toContain(section);
    }
    for (const term of REQUIRED_TERMS) {
      expect(doc).toContain(term);
    }
  });

  it("links the troubleshooting guide from related Fetch docs", () => {
    for (const path of [apiReferencePath, conformanceDocPath, packagingDocPath, tsReadmePath]) {
      expect(readFileSync(path, "utf-8")).toContain("fetch-troubleshooting.md");
    }
  });

  it("keeps troubleshooting guidance generic and provider-neutral", () => {
    const doc = readFileSync(troubleshootingDocPath, "utf-8");

    expect(doc).not.toContain("process.env");
    expect(doc).not.toContain("discoverAutoctxAgents");
    expect(doc).not.toContain("fs.readdir");
    expect(doc).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
  });
});
