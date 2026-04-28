import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const topologyPath = join(repoRoot, "packages", "package-topology.json");
const boundariesPath = join(repoRoot, "packages", "package-boundaries.json");

type PackageEntry = {
  name: string;
  path: string;
};

type TsPackageEntry = PackageEntry & {
  source: string;
};

type Topology = {
  status: string;
  guardrails: Record<string, string>;
  typescript: {
    umbrella: PackageEntry & { bin: string };
    core: TsPackageEntry;
    control: TsPackageEntry;
  };
};

type PackageBoundaries = {
  typescript: {
    core: {
      exactIncludes: string[];
    };
  };
};

function loadTopology(): Topology {
  return JSON.parse(readFileSync(topologyPath, "utf-8")) as Topology;
}

function loadBoundaries(): PackageBoundaries {
  return JSON.parse(readFileSync(boundariesPath, "utf-8")) as PackageBoundaries;
}

function loadPackageJson(relativePath: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(repoRoot, relativePath, "package.json"), "utf-8")) as Record<string, unknown>;
}

function loadTsConfig(relativePath: string): {
  compilerOptions?: Record<string, unknown>;
  include?: string[];
} {
  return JSON.parse(readFileSync(join(repoRoot, relativePath, "tsconfig.json"), "utf-8")) as {
    compilerOptions?: Record<string, unknown>;
    include?: string[];
  };
}

function expectedBuiltEntry(
  entry: TsPackageEntry,
  config: { compilerOptions?: Record<string, unknown> },
  extension: ".js" | ".d.ts",
): string {
  const source = entry.source.replace(/\.ts$/, extension);
  const rootDir = String(config.compilerOptions?.rootDir ?? "./src").replace(/^\.\//, "");

  if (rootDir === "src") {
    return `dist/${source.replace(/^src\//, "")}`;
  }
  if (rootDir === "../../..") {
    return `dist/${entry.path}/${source}`;
  }

  throw new Error(`Unexpected rootDir for ${entry.name}: ${rootDir}`);
}

describe("package topology", () => {
  it("defines a shared topology manifest", () => {
    expect(existsSync(topologyPath)).toBe(true);
  });

  it("defines TypeScript core and control package skeletons", () => {
    const topology = loadTopology();
    for (const entry of [topology.typescript.core, topology.typescript.control]) {
      expect(existsSync(join(repoRoot, entry.path))).toBe(true);
      expect(existsSync(join(repoRoot, entry.path, "package.json"))).toBe(true);
      expect(existsSync(join(repoRoot, entry.path, "tsconfig.json"))).toBe(true);
      expect(existsSync(join(repoRoot, entry.path, entry.source))).toBe(true);
    }
  });

  it("declares Apache boundary wrap-up guardrails", () => {
    const topology = loadTopology();

    expect(topology.status).toBe("apache-boundary-wrap-up");
    expect(topology.guardrails).toMatchObject({
      repoWideLicenseFlip: "out-of-scope-existing-code-remains-apache-2.0",
      dualLicenseMetadata: "do-not-publish-for-existing-repo",
      historicalRelicensing: "out-of-scope",
      futureProprietaryWork: "separate-repository",
      defaultInstallCompatibility: "preserve-autocontext-autoctx-and-autoctx-cli",
    });
  });

  it("matches TypeScript package names to the topology", () => {
    const topology = loadTopology();
    const corePackage = loadPackageJson(topology.typescript.core.path);
    const controlPackage = loadPackageJson(topology.typescript.control.path);

    expect(corePackage.name).toBe(topology.typescript.core.name);
    expect(controlPackage.name).toBe(topology.typescript.control.name);
    expect(corePackage.version).toBe("0.0.0");
    expect(controlPackage.version).toBe("0.0.0");
    expect(corePackage.private).toBe(true);
    expect(controlPackage.private).toBe(true);
  });

  it("preserves the umbrella TypeScript package as the phase-one install surface", () => {
    const topology = loadTopology();
    expect(topology.typescript.umbrella.name).toBe("autoctx");
    expect(topology.typescript.umbrella.path).toBe("ts");
    expect(topology.typescript.umbrella.bin).toBe("autoctx");
  });

  it("configures TypeScript package builds to emit their advertised dist artifacts", () => {
    const topology = loadTopology();
    const corePackage = loadPackageJson(topology.typescript.core.path);
    const controlPackage = loadPackageJson(topology.typescript.control.path);
    const coreConfig = loadTsConfig(topology.typescript.core.path);
    const controlConfig = loadTsConfig(topology.typescript.control.path);

    expect(coreConfig.compilerOptions?.noEmit).toBe(false);
    expect(controlConfig.compilerOptions?.noEmit).toBe(false);
    expect(corePackage.main).toBe(expectedBuiltEntry(topology.typescript.core, coreConfig, ".js"));
    expect(corePackage.types).toBe(expectedBuiltEntry(topology.typescript.core, coreConfig, ".d.ts"));
    expect(controlPackage.main).toBe(expectedBuiltEntry(topology.typescript.control, controlConfig, ".js"));
    expect(controlPackage.types).toBe(expectedBuiltEntry(topology.typescript.control, controlConfig, ".d.ts"));
  });

  it("keeps the TypeScript core external source scope exact", () => {
    const topology = loadTopology();
    const boundaries = loadBoundaries();
    const coreConfig = loadTsConfig(topology.typescript.core.path);
    const externalCoreSources = (coreConfig.include ?? []).filter((entry) =>
      entry.startsWith("../../../ts/src/"),
    );
    const expectedExternalCoreSources = boundaries.typescript.core.exactIncludes.filter((entry) =>
      entry.startsWith("../../../ts/src/"),
    );

    expect(externalCoreSources).toEqual(expectedExternalCoreSources);
    expect(externalCoreSources.every((entry) => !entry.includes("*"))).toBe(true);
  });
});
