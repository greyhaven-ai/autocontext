import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const repoRoot = join(import.meta.dirname, "..", "..");
const packagesDir = join(repoRoot, "packages");
const forbiddenSplitPaths = [
  join(packagesDir, "package-topology.json"),
  join(packagesDir, "package-boundaries.json"),
  join(packagesDir, "python", "core"),
  join(packagesDir, "python", "control"),
  join(packagesDir, "ts", "core"),
  join(packagesDir, "ts", "control-plane"),
];
const forbiddenLicenseMetadata = [
  join(repoRoot, "LICENSING.md"),
  join(packagesDir, "python", "core", "LICENSE"),
  join(packagesDir, "python", "control", "LICENSE"),
  join(packagesDir, "ts", "core", "LICENSE"),
  join(packagesDir, "ts", "control-plane", "LICENSE"),
];

type PackageJson = {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  optionalDependencies?: Record<string, string>;
  peerDependencies?: Record<string, string>;
};

function readText(path: string): string {
  return readFileSync(path, "utf-8");
}

function readPackage(path: string): PackageJson {
  return JSON.parse(readText(path)) as PackageJson;
}

function allDependencyNames(packageJson: PackageJson): string[] {
  return [
    ...Object.keys(packageJson.dependencies ?? {}),
    ...Object.keys(packageJson.devDependencies ?? {}),
    ...Object.keys(packageJson.optionalDependencies ?? {}),
    ...Object.keys(packageJson.peerDependencies ?? {}),
  ];
}

describe("deferred package boundaries", () => {
  it("keeps split-package scaffolding deleted", () => {
    for (const path of forbiddenSplitPaths) {
      expect(existsSync(path)).toBe(false);
    }
  });

  it("keeps dual-license split metadata out of the repo", () => {
    expect(existsSync(join(repoRoot, "LICENSE"))).toBe(true);
    for (const path of forbiddenLicenseMetadata) {
      expect(existsSync(path)).toBe(false);
    }
  });

  it("does not depend on unpublished split package names", () => {
    const tsPackage = readPackage(join(repoRoot, "ts", "package.json"));
    const piPackage = readPackage(join(repoRoot, "pi", "package.json"));
    const dependencyNames = [...allDependencyNames(tsPackage), ...allDependencyNames(piPackage)];

    expect(dependencyNames).not.toContain("@autocontext/core");
    expect(dependencyNames).not.toContain("@autocontext/control-plane");
    expect(dependencyNames).not.toContain("autocontext-core");
    expect(dependencyNames).not.toContain("autocontext-control");
  });

  it("keeps the rights audit as historical context instead of manifest state", () => {
    const doc = readText(join(repoRoot, "docs", "contributor-rights-audit.md"));

    expect(doc).toContain("historical snapshot");
    expect(doc).toContain("existing public repo code remains Apache-2.0");
    expect(doc).not.toContain("packages/package-boundaries.json");
  });

  it("keeps knowledge and trace extraction deferred", () => {
    const doc = readText(join(repoRoot, "docs", "knowledge-production-trace-boundary-map.md"));

    expect(doc).toContain("Status: **deferred**");
    expect(doc).toContain("future package hygiene");
    expect(doc).not.toContain("package-boundaries.json");
    expect(doc).not.toContain("package-topology.json");
  });
});
