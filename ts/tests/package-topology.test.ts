import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const packagesDir = join(repoRoot, "packages");
const packageReadmePath = join(packagesDir, "README.md");
const splitManifests = [
  join(packagesDir, "package-topology.json"),
  join(packagesDir, "package-boundaries.json"),
];
const splitPackageDirs = [
  join(packagesDir, "python", "core"),
  join(packagesDir, "python", "control"),
  join(packagesDir, "ts", "core"),
  join(packagesDir, "ts", "control-plane"),
];

type PackageJson = {
  name: string;
  bin?: Record<string, string>;
  dependencies?: Record<string, string>;
};

function readText(path: string): string {
  return readFileSync(path, "utf-8");
}

function readJson<T>(path: string): T {
  return JSON.parse(readText(path)) as T;
}

describe("deferred package topology", () => {
  it("does not keep split manifests around without published packages", () => {
    for (const path of splitManifests) {
      expect(existsSync(path)).toBe(false);
    }
  });

  it("does not keep placeholder Python or TypeScript split packages", () => {
    for (const path of splitPackageDirs) {
      expect(existsSync(path)).toBe(false);
    }
  });

  it("documents the deferred policy in packages/README.md", () => {
    const readme = readText(packageReadmePath);

    expect(readme).toContain("Core/control split packages are deferred");
    expect(readme).toContain("Do not add `packages/python/*`, `packages/ts/*`");
    expect(readme).toContain("autocontext");
    expect(readme).toContain("autoctx");
    expect(readme).toContain("pi-autocontext");
  });

  it("keeps active shipping package names stable", () => {
    const tsPackage = readJson<PackageJson>(join(repoRoot, "ts", "package.json"));
    const piPackage = readJson<PackageJson>(join(repoRoot, "pi", "package.json"));

    expect(tsPackage.name).toBe("autoctx");
    expect(tsPackage.bin?.autoctx).toBe("dist/cli/index.js");
    expect(piPackage.name).toBe("pi-autocontext");
    expect(piPackage.dependencies?.autoctx).toBeDefined();
  });

  it("keeps agent app build targets on the umbrella runtime surface", () => {
    const doc = readText(join(repoRoot, "docs", "core-control-package-split.md"));

    expect(doc).toContain("Status: **deferred**");
    expect(doc).toContain("## Agent App Build Targets");
    expect(doc).toContain("`autoctx/agent-runtime`");
    expect(doc).toContain("future packages uncreated");
  });

  it("uses AutoContext-native vocabulary in public runtime decision docs", () => {
    for (const relativePath of ["docs/core-control-package-split.md", "docs/concept-model.md"]) {
      expect(readText(join(repoRoot, relativePath))).not.toMatch(/\b[Ff]lue\b/);
    }
  });
});
