import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { buildGeneratedFetchPackageArtifacts } from "../examples/generated-fetch-packaging.ts";

const repoRoot = join(import.meta.dirname, "..", "..");
const docsPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const examplePath = join(import.meta.dirname, "..", "examples", "generated-fetch-packaging.ts");

const PROVIDER_OR_HOSTED_BOUNDARY_TERMS =
  /wrangler|cloudflare|vercel|deno deploy|durable object|r2 bucket|s3|tenant|billing|secret broker|warm pool|hosted orchestration/i;

describe("generated Fetch packaging docs and example", () => {
  it("documents the provider-neutral generated Fetch packaging workflow", () => {
    const docs = readFileSync(docsPath, "utf-8");

    expect(docs).toContain("# Generated Fetch Packaging");
    expect(docs).toContain("planAgentAppFetchCatalog");
    expect(docs).toContain("planAgentAppFetchRuntimeFactories");
    expect(docs).toContain("renderAgentAppFetchEntrypointTemplate");
    expect(docs).toContain("renderAgentAppFetchHostCapabilityManifest");
    expect(docs).toContain("renderAgentAppFetchHostCapabilityManifestSchema");
    expect(docs).toContain("host-created capabilities");
    expect(docs).toContain("No request-time filesystem discovery");
    expect(docs).toContain("No provider deployment configuration");
    expect(docs).toContain("ts/examples/generated-fetch-packaging.ts");
    expect(docs).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
  });

  it("provides a typed packaging example that emits entrypoint, manifest, and schema artifacts", () => {
    const artifacts = buildGeneratedFetchPackageArtifacts();

    expect(artifacts.files.map((file) => file.path).sort()).toEqual([
      "agent-app-fetch-entrypoint.mjs",
      "agent-app-fetch-host-capability-manifest.json",
      "agent-app-fetch-host-capability-manifest.schema.json",
    ]);
    expect(
      artifacts.files.find((file) => file.path.endsWith("entrypoint.mjs"))?.contents,
    ).toContain("createAgentAppFetchEntrypoint");
    expect(
      JSON.parse(artifacts.files.find((file) => file.path.endsWith("manifest.json"))!.contents),
    ).toMatchObject({ target: "fetch" });
    expect(
      JSON.parse(
        artifacts.files.find((file) => file.path.endsWith("manifest.schema.json"))!.contents,
      ),
    ).toMatchObject({ properties: { target: { const: "fetch" } } });
    expect(artifacts.hostCapabilities).toEqual([
      "env",
      "runtimeFactoryName",
      "workspaceStore",
      "sessionEventStore",
    ]);
  });

  it("keeps the example generic Fetch/ESM only", () => {
    const example = readFileSync(examplePath, "utf-8");
    const artifactText = buildGeneratedFetchPackageArtifacts()
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
