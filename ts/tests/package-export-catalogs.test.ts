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
    expect(pkg.SessionStore).toBeDefined();
    expect(pkg.Session).toBeDefined();
    expect(pkg.PiPersistentRPCRuntime).toBeDefined();
    expect(pkg.compactPromptComponents).toBeDefined();
    expect(pkg.compactPromptComponentsWithEntries).toBeDefined();
    expect(pkg.HookBus).toBeDefined();
    expect(pkg.HookEvents).toBeDefined();
    expect(pkg.loadExtensions).toBeDefined();
    expect(pkg.completeWithProviderHooks).toBeDefined();
    expect(pkg.chooseModel).toBeDefined();
    expect(pkg.evaluateTaskBudget).toBeDefined();
    expect(pkg.reconcileEvalTrials).toBeDefined();
    expect(pkg.probeDirectoryContract).toBeDefined();
    expect(pkg.validateOperationalMemoryPack).toBeDefined();
    expect(pkg.classifyExternalEvalTrial).toBeDefined();
    expect(pkg.assessExternalEvalBoundaryPolicy).toBeDefined();
    expect(pkg.validateExternalEvalBoundaryPolicy).toBeDefined();
    expect(pkg.buildExternalEvalDiagnosticReport).toBeDefined();
    expect(pkg.buildExternalEvalImprovementSignals).toBeDefined();
    expect(pkg.buildOperationalMemoryPackFromDiagnostics).toBeDefined();
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

  it("publishes external-eval helper subpaths", () => {
    const packageJson = JSON.parse(
      readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"),
    ) as { exports?: Record<string, { import?: string; types?: string }> };

    expect(packageJson.exports?.["./control-plane/eval-ledger"]).toEqual({
      import: "./dist/control-plane/eval-ledger/index.js",
      types: "./dist/control-plane/eval-ledger/index.d.ts",
    });
    expect(packageJson.exports?.["./control-plane/contract-probes"]).toEqual({
      import: "./dist/control-plane/contract-probes/index.js",
      types: "./dist/control-plane/contract-probes/index.d.ts",
    });
    expect(packageJson.exports?.["./control-plane/memory-packs"]).toEqual({
      import: "./dist/control-plane/memory-packs/index.js",
      types: "./dist/control-plane/memory-packs/index.d.ts",
    });
    expect(packageJson.exports?.["./control-plane/external-evals"]).toEqual({
      import: "./dist/control-plane/external-evals/index.js",
      types: "./dist/control-plane/external-evals/index.d.ts",
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
