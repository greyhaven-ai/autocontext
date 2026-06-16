import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const apiReferencePath = join(repoRoot, "docs", "fetch-api-reference.md");
const docsIndexPath = join(repoRoot, "docs", "README.md");
const edgeDocPath = join(repoRoot, "docs", "edge-runtime-compatibility.md");
const packagingDocPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const conformanceDocPath = join(repoRoot, "docs", "fetch-conformance.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");

const REQUIRED_SECTIONS = [
  "# Fetch Adapter API Reference",
  "## Import Path",
  "## Handler Surface",
  "## Catalog And Entrypoint Planning",
  "## Runtime Factory Helpers",
  "## Host Capability Manifest",
  "## Workspace And Session Stores",
  "## Conformance Helpers",
  "## Generated Entrypoint Contract",
  "## Boundary Guarantees",
] as const;

const REQUIRED_API_NAMES = [
  "createAgentAppFetchHandler",
  "handleAgentAppFetchRequest",
  "createStaticAgentAppCatalog",
  "AgentAppFetchHandlerOptions",
  "AgentAppFetchCatalogEntry",
  "AgentAppFetchManifest",
  "AgentAppFetchSuccessEnvelope",
  "AgentAppFetchErrorEnvelope",
  "planAgentAppFetchCatalog",
  "createAgentAppFetchCatalogFromModuleMap",
  "renderAgentAppFetchModuleMapEntrypoint",
  "renderAgentAppFetchEntrypointTemplate",
  "planAgentAppFetchRuntimeFactories",
  "createAgentAppFetchRuntimeFactoryFromModuleMap",
  "createAgentAppFetchLazyRuntime",
  "createAgentAppFetchHostCapabilityManifest",
  "renderAgentAppFetchHostCapabilityManifest",
  "renderAgentAppFetchHostCapabilityManifestSchema",
  "agentAppFetchHostCapabilityManifestSchema",
  "AGENT_APP_FETCH_ACCEPTED_HOST_CAPABILITIES",
  "AGENT_APP_FETCH_UNSUPPORTED_DEFAULTS",
  "createAgentAppFetchWorkspaceEnv",
  "createInMemoryAgentAppFetchWorkspaceStore",
  "createEdgeInMemoryWorkspaceEnv",
  "AgentAppFetchWorkspaceStore",
  "createAgentAppFetchSessionEventStoreBridge",
  "createInMemoryAgentAppFetchSessionEventStore",
  "AgentAppFetchSessionEventStore",
  "createAgentAppFetchWorkspaceStoreConformanceCases",
  "runAgentAppFetchWorkspaceStoreConformance",
  "createAgentAppFetchSessionEventStoreConformanceCases",
  "runAgentAppFetchSessionEventStoreConformance",
  "createAgentAppFetchInvocationConformanceCases",
  "runAgentAppFetchInvocationConformance",
] as const;

const REQUIRED_HOST_CAPABILITY_NAMES = [
  "env",
  "runtime",
  "runtimeFactory",
  "runtimeFactoryName",
  "runtimeFactoryPlan",
  "runtimeFactoryModuleMap",
  "workspace",
  "workspaceStore",
  "commands",
  "tools",
  "eventStore",
  "sessionEventStore",
  "eventSink",
  "maxBodyBytes",
] as const;

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("Fetch adapter API reference documentation", () => {
  it("documents the public Fetch adapter API surface", () => {
    const doc = readFileSync(apiReferencePath, "utf-8");

    for (const section of REQUIRED_SECTIONS) {
      expect(doc).toContain(section);
    }
    for (const apiName of REQUIRED_API_NAMES) {
      expect(doc).toContain(apiName);
    }
  });

  it("documents the generic Fetch routes and accepted host capability keys", () => {
    const doc = readFileSync(apiReferencePath, "utf-8");

    expect(doc).toContain("GET /manifest");
    expect(doc).toContain("GET /agents");
    expect(doc).toContain("POST /agents/:agent/invoke");
    for (const capability of REQUIRED_HOST_CAPABILITY_NAMES) {
      expect(doc).toContain(`\`${capability}\``);
    }
  });

  it("keeps the linked edge capability list in sync with accepted host keys", () => {
    const edgeDoc = readFileSync(edgeDocPath, "utf-8");

    for (const capability of REQUIRED_HOST_CAPABILITY_NAMES) {
      expect(edgeDoc).toContain(`\`${capability}\``);
    }
  });

  it("links the API reference from related Fetch docs", () => {
    for (const path of [
      docsIndexPath,
      edgeDocPath,
      packagingDocPath,
      conformanceDocPath,
      tsReadmePath,
    ]) {
      expect(readFileSync(path, "utf-8")).toContain("fetch-api-reference.md");
    }
  });

  it("keeps the API reference generic and provider-neutral", () => {
    const doc = readFileSync(apiReferencePath, "utf-8");

    expect(doc).not.toContain("process.env");
    expect(doc).not.toContain("discoverAutoctxAgents");
    expect(doc).not.toContain("fs.readdir");
    expect(doc).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
  });
});
