import { describe, test, expect, beforeAll } from "vitest";
import { existsSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Integration test for the ``autoctx/production-traces`` subpath export.
 *
 * Covers:
 *   - `package.json` `exports` map advertises both ``import`` and ``require``
 *     entrypoints pointing into the expected dist layout.
 *   - The ESM entry file exists on disk after a build.
 *   - The CJS entry file exists on disk after a build.
 *   - A minimal CJS fixture (`tests/integration/cjs-fixture/index.cjs`) runs
 *     end-to-end via ``node`` subprocess and returns exit 0, proving the
 *     bundle is actually requireable.
 *   - Root `"."` export still points to ``dist/index.js`` (no regression for
 *     the CLI entry).
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const PKG_JSON = join(ROOT, "package.json");

const pkg = JSON.parse(readFileSync(PKG_JSON, "utf-8")) as {
  exports: Record<string, Record<string, string> | string>;
  sideEffects: string[] | boolean;
  main: string;
  module?: string;
};

describe("package.json exports map (A2-II-a subpath discipline)", () => {
  test("advertises './production-traces' subpath", () => {
    expect(pkg.exports["./production-traces"]).toBeDefined();
  });

  test("subpath advertises ESM (import), CJS (require), and types legs", () => {
    const entry = pkg.exports["./production-traces"] as Record<string, string>;
    expect(entry.import).toBe("./dist/production-traces/sdk/index.js");
    expect(entry.require).toBe("./dist/cjs/production-traces/sdk/index.cjs");
    expect(entry.types).toBe("./dist/production-traces/sdk/index.d.ts");
  });

  test("root '.' entry still points to the CLI package entry (no regression)", () => {
    const root = pkg.exports["."] as Record<string, string>;
    expect(root.import).toBe("./dist/index.js");
    expect(root.types).toBe("./dist/index.d.ts");
  });

  test("sideEffects discipline — granular glob scoped to actuators only", () => {
    expect(Array.isArray(pkg.sideEffects)).toBe(true);
    const glob = pkg.sideEffects as string[];
    expect(glob).toContain("**/control-plane/actuators/**");
    // Must NOT be the blunt `true` that disables tree-shaking wholesale.
    expect(glob.length).toBeGreaterThan(0);
  });

  test("exports map surfaces ./package.json for consumer introspection", () => {
    expect(pkg.exports["./package.json"]).toBe("./package.json");
  });
});

describe("SDK dist files exist after build", () => {
  beforeAll(() => {
    // Expect the CI to have already built; otherwise build now so dev runs work.
    const esmEntry = join(ROOT, "dist", "production-traces", "sdk", "index.js");
    const cjsEntry = join(ROOT, "dist", "cjs", "production-traces", "sdk", "index.cjs");
    if (!existsSync(esmEntry)) {
      // tsc for ESM
      const r1 = spawnSync("npx", ["tsc"], { cwd: ROOT, stdio: "inherit" });
      if (r1.status !== 0) throw new Error("tsc build failed");
    }
    if (!existsSync(cjsEntry)) {
      const r2 = spawnSync(
        "node",
        ["scripts/build-production-traces-sdk-cjs.mjs"],
        { cwd: ROOT, stdio: "inherit" },
      );
      if (r2.status !== 0) throw new Error("cjs build failed");
    }
  });

  test("ESM entry exists", () => {
    expect(existsSync(join(ROOT, "dist", "production-traces", "sdk", "index.js"))).toBe(true);
  });

  test("ESM types file exists", () => {
    expect(existsSync(join(ROOT, "dist", "production-traces", "sdk", "index.d.ts"))).toBe(true);
  });

  test("CJS entry exists", () => {
    expect(existsSync(join(ROOT, "dist", "cjs", "production-traces", "sdk", "index.cjs"))).toBe(true);
  });
});

describe("CJS fixture smoke (require() from a real CJS module)", () => {
  test("node tests/integration/cjs-fixture/index.cjs exits 0 and prints OK", () => {
    const fixture = join(ROOT, "tests", "integration", "cjs-fixture", "index.cjs");
    expect(existsSync(fixture)).toBe(true);
    const result = spawnSync("node", [fixture], { cwd: ROOT, encoding: "utf-8" });
    if (result.status !== 0) {
      console.error("stdout:", result.stdout);
      console.error("stderr:", result.stderr);
    }
    expect(result.status).toBe(0);
    expect(result.stdout).toContain("[cjs-smoke] OK");
  });
});

describe("ESM entry actually imports", () => {
  test("dynamic import resolves the SDK surface", async () => {
    const entry = join(ROOT, "dist", "production-traces", "sdk", "index.js");
    const sdk = (await import(entry)) as Record<string, unknown>;
    expect(typeof sdk.buildTrace).toBe("function");
    expect(typeof sdk.writeJsonl).toBe("function");
    expect(typeof sdk.TraceBatch).toBe("function");
    expect(typeof sdk.hashUserId).toBe("function");
    expect(typeof sdk.hashSessionId).toBe("function");
    expect(typeof sdk.validateProductionTrace).toBe("function");
    expect(typeof sdk.validateProductionTraceDict).toBe("function");
    expect(typeof sdk.ValidationError).toBe("function");
  });
});
