import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("package root exports", () => {
  it("re-exports representative public symbols directly through the package root", async () => {
    const pkg = await import("../src/index.js");

    expect(pkg.SQLiteStore).toBeDefined();
    expect(pkg.createProvider).toBeDefined();
    expect(pkg.ActionFilterHarness).toBeDefined();
    expect(pkg.SkillPackage).toBeDefined();
    expect(pkg.DataPlane).toBeDefined();
    expect(pkg.ModelStrategySelector).toBeDefined();
    expect(pkg.createMcpServer).toBeDefined();
    expect(pkg.MissionManager).toBeDefined();
    expect(pkg.chooseModel).toBeDefined();
    expect(pkg.resolveBrowserSessionConfig).toBeDefined();
    expect(pkg.evaluateBrowserActionPolicy).toBeDefined();
    expect(pkg.validateBrowserSessionConfig).toBeDefined();
  });

  it("avoids package catalog barrel hops in ts/src/index.ts", () => {
    const indexSource = readFileSync(join(import.meta.dirname, "..", "src", "index.ts"), "utf-8");

    expect(indexSource).not.toContain('export * from "./package-core-catalog.js";');
    expect(indexSource).not.toContain('export * from "./package-execution-catalog.js";');
    expect(indexSource).not.toContain('export * from "./package-trace-training-catalog.js";');
    expect(indexSource).not.toContain('export * from "./package-platform-catalog.js";');
  });

  it("publishes the control-plane runtime subpath for chooseModel", () => {
    const packageJson = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"),
    ) as { exports?: Record<string, { import?: string; types?: string }> };

    expect(packageJson.exports?.["./control-plane/runtime"]).toEqual({
      import: "./dist/control-plane/runtime/index.js",
      types: "./dist/control-plane/runtime/index.d.ts",
    });
  });

  it("publishes the browser integration subpath", () => {
    const packageJson = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"),
    ) as { exports?: Record<string, { import?: string; types?: string }> };

    expect(packageJson.exports?.["./integrations/browser"]).toEqual({
      import: "./dist/integrations/browser/index.js",
      types: "./dist/integrations/browser/index.d.ts",
    });
  });
});
