import { readFileSync } from "node:fs";
import { join } from "node:path";

import Ajv, { type ValidateFunction } from "ajv";
import { describe, expect, it } from "vitest";

import {
  agentAppFetchHostCapabilityManifestSchema,
  type AgentAppFetchHostCapabilityManifest,
} from "../src/control-plane/agent-app-fetch/index.js";
import { buildFetchHostCapabilityManifestExampleArtifacts } from "../examples/fetch-host-capability-manifest.js";

const repoRoot = join(import.meta.dirname, "..", "..");
const manifestExamplesDocPath = join(repoRoot, "docs", "fetch-host-capability-manifest.md");
const apiReferencePath = join(repoRoot, "docs", "fetch-api-reference.md");
const docsIndexPath = join(repoRoot, "docs", "README.md");
const edgeDocPath = join(repoRoot, "docs", "edge-runtime-compatibility.md");
const packagingDocPath = join(repoRoot, "docs", "generated-fetch-packaging.md");
const tsReadmePath = join(repoRoot, "ts", "README.md");
const examplePath = join(
  import.meta.dirname,
  "..",
  "examples",
  "fetch-host-capability-manifest.ts",
);

const REQUIRED_DOC_TERMS = [
  "# Fetch Host Capability Manifest Examples",
  "agent-app-fetch-host-capability-manifest.json",
  "agent-app-fetch-host-capability-manifest.schema.json",
  "renderAgentAppFetchHostCapabilityManifest",
  "renderAgentAppFetchHostCapabilityManifestSchema",
  "agentAppFetchHostCapabilityManifestSchema",
  "planAgentAppFetchRuntimeFactories",
  "runtimeFactoryPlan",
  "runtimeFactoryModuleMap",
  "acceptedHostCapabilities",
  "requiredHostCapabilities",
  "unsupportedDefaults",
  "GET /manifest",
  "GET /agents",
  "POST /agents/:agent/invoke",
  "new Ajv",
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

describe("Fetch host capability manifest examples", () => {
  it("documents provider-neutral manifest and schema validation examples", () => {
    const doc = readFileSync(manifestExamplesDocPath, "utf-8");

    for (const term of REQUIRED_DOC_TERMS) {
      expect(doc).toContain(term);
    }
    for (const capability of REQUIRED_HOST_CAPABILITY_NAMES) {
      expect(doc).toContain(`\`${capability}\``);
    }
  });

  it("provides a typed example that emits manifest and schema artifacts", () => {
    const artifacts = buildFetchHostCapabilityManifestExampleArtifacts();

    expect(artifacts.files.map((file) => file.path).sort()).toEqual([
      "agent-app-fetch-host-capability-manifest.json",
      "agent-app-fetch-host-capability-manifest.schema.json",
    ]);
    expect(artifacts.hostCapabilityKeys).toEqual(
      expect.arrayContaining([
        "runtimeFactory",
        "runtimeFactoryName",
        "runtimeFactoryPlan",
        "runtimeFactoryModuleMap",
      ]),
    );
    expect(artifacts.runtimeFactoryNames).toEqual(["standard"]);
    expect(artifacts.validateManifest(artifacts.manifest)).toBe(true);
  });

  it("keeps example manifests valid against the exported JSON schema", () => {
    const artifacts = buildFetchHostCapabilityManifestExampleArtifacts();
    const manifestFromJson = JSON.parse(
      artifacts.files.find((file) => file.path.endsWith("manifest.json"))!.contents,
    ) as AgentAppFetchHostCapabilityManifest;
    const schemaFromJson = JSON.parse(
      artifacts.files.find((file) => file.path.endsWith("schema.json"))!.contents,
    );
    const validate = compileManifestSchema();

    expect(schemaFromJson).toEqual(agentAppFetchHostCapabilityManifestSchema);
    expect(validate(manifestFromJson)).toBe(true);
    expect(manifestFromJson.acceptedHostCapabilities).toEqual(
      expect.arrayContaining(["runtimeFactoryPlan", "runtimeFactoryModuleMap"]),
    );
    expect(
      artifacts.validateManifest({
        ...manifestFromJson,
        acceptedHostCapabilities: manifestFromJson.acceptedHostCapabilities.filter(
          (capability) => capability !== "runtimeFactoryModuleMap",
        ),
      }),
    ).toBe(false);
    expect(artifacts.validationErrors()).not.toEqual([]);
  });

  it("links manifest examples from related Fetch docs", () => {
    for (const path of [
      docsIndexPath,
      apiReferencePath,
      edgeDocPath,
      packagingDocPath,
      tsReadmePath,
    ]) {
      expect(readFileSync(path, "utf-8")).toContain("fetch-host-capability-manifest.md");
    }
  });

  it("keeps manifest examples generic Fetch/ESM only", () => {
    const doc = readFileSync(manifestExamplesDocPath, "utf-8");
    const example = readFileSync(examplePath, "utf-8");
    const artifactText = buildFetchHostCapabilityManifestExampleArtifacts()
      .files.map((file) => file.contents)
      .join("\n");

    for (const source of [doc, example, artifactText]) {
      expect(source).not.toContain("process.env");
      expect(source).not.toContain("discoverAutoctxAgents");
      expect(source).not.toContain("fs.readdir");
      expect(source).not.toMatch(PROVIDER_OR_HOSTED_BOUNDARY_TERMS);
    }
  });
});

function compileManifestSchema(): ValidateFunction {
  const ajv = new Ajv({ allErrors: true, strict: true });
  return ajv.compile(agentAppFetchHostCapabilityManifestSchema);
}
