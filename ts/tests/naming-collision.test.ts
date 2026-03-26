/**
 * Tests for AC-395: npm package naming collision with 'autocontext'.
 *
 * - package.json has both `autoctx` and `autocontext` bin entries
 * - `autocontext` shim prints a naming callout then delegates
 * - Shim resolves the right real CLI path in both source and built layouts
 * - Main help and docs include naming clarification
 */

import { describe, it, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");
const SHIM = join(import.meta.dirname, "..", "src", "cli", "autocontext-shim.ts");

function run(script: string, args: string[]): { stdout: string; stderr: string; exitCode: number } {
  const r = spawnSync("npx", ["tsx", script, ...args], {
    encoding: "utf8",
    timeout: 15000,
    env: { ...process.env, NODE_NO_WARNINGS: "1" },
  });
  return { stdout: r.stdout ?? "", stderr: r.stderr ?? "", exitCode: r.status ?? 1 };
}

// ---------------------------------------------------------------------------
// package.json bin entries
// ---------------------------------------------------------------------------

describe("package.json bin entries", () => {
  const pkg = JSON.parse(readFileSync(join(import.meta.dirname, "..", "package.json"), "utf-8"));

  it("has autoctx bin entry", () => {
    expect(pkg.bin.autoctx).toBeDefined();
  });

  it("has autocontext bin entry for redirect", () => {
    expect(pkg.bin.autocontext).toBeDefined();
  });

  it("autocontext bin points to the redirect shim", () => {
    expect(pkg.bin.autocontext).toContain("autocontext-shim");
  });
});

// ---------------------------------------------------------------------------
// autocontext shim behavior
// ---------------------------------------------------------------------------

describe("autocontext redirect shim", () => {
  it("resolves the source CLI path when run from TypeScript source", async () => {
    const { resolveRealCliPath } = await import("../src/cli/autocontext-shim.ts");
    expect(resolveRealCliPath("/tmp/pkg/src/cli/autocontext-shim.ts")).toBe("/tmp/pkg/src/cli/index.ts");
  });

  it("resolves the built CLI path when run from the published dist layout", async () => {
    const { resolveRealCliPath } = await import("../src/cli/autocontext-shim.ts");
    expect(resolveRealCliPath("/tmp/pkg/dist/cli/autocontext-shim.js")).toBe("/tmp/pkg/dist/cli/index.js");
  });

  it("prints naming callout to stderr", () => {
    const { stderr } = run(SHIM, ["--help"]);
    expect(stderr).toContain("autoctx");
    expect(stderr).toContain("different package");
  });

  it("forwards to the real CLI and produces output", () => {
    const { stdout, exitCode } = run(SHIM, ["version"]);
    expect(exitCode).toBe(0);
    expect(stdout).toMatch(/\d+\.\d+/);
  });

  it("shim --help still shows real help output", () => {
    const { stdout } = run(SHIM, ["--help"]);
    expect(stdout).toContain("autoctx");
  });
});

// ---------------------------------------------------------------------------
// Main help includes naming clarification
// ---------------------------------------------------------------------------

describe("Main help naming clarification", () => {
  it("--help mentions the correct package name", () => {
    const { stdout } = run(CLI, ["--help"]);
    expect(stdout).toContain("autoctx");
  });

  it("--help includes npm install instruction", () => {
    const { stdout } = run(CLI, ["--help"]);
    expect(stdout).toContain("npm");
    expect(stdout).toContain("autoctx");
  });

  it("ts README warns that autocontext on npm is a different package", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).toContain("use `autoctx`, not `autocontext`");
    expect(readme).toContain("different package");
  });
});
