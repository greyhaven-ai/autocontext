import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const topologyPath = join(repoRoot, "packages", "package-topology.json");

type PackageEntry = {
  name: string;
  path: string;
};

type TsPackageEntry = PackageEntry & {
  source: string;
};

type Topology = {
  typescript: {
    umbrella: PackageEntry & { bin: string };
    core: TsPackageEntry;
    control: TsPackageEntry;
  };
};

function loadTopology(): Topology {
  return JSON.parse(readFileSync(topologyPath, "utf-8")) as Topology;
}

function loadPackageJson(relativePath: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(repoRoot, relativePath, "package.json"), "utf-8")) as Record<string, unknown>;
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
});
