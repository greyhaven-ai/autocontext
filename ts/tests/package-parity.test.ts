/**
 * Tests for AC-362: Package surface parity verification.
 * Ensures the npm package delivers on the claims in the README.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";

const CLI = join(import.meta.dirname, "..", "src", "cli", "index.ts");

function runCli(args: string[]): string {
  try {
    return execFileSync("npx", ["tsx", CLI, ...args], {
      encoding: "utf8",
      timeout: 10000,
      env: { ...process.env, NODE_NO_WARNINGS: "1" },
    });
  } catch (err: unknown) {
    return (err as { stdout?: string }).stdout ?? "";
  }
}

// ---------------------------------------------------------------------------
// README no longer describes the package as a narrow toolkit
// ---------------------------------------------------------------------------

describe("README positioning", () => {
  it("does not describe the package as a narrow toolkit", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).not.toContain("lightweight toolkit");
    expect(readme).not.toContain("narrower toolkit");
    expect(readme).not.toContain("use the Python package instead");
  });

  it("describes the full command surface", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).toContain("run --scenario");
    expect(readme).toContain("mcp-serve");
    expect(readme).toContain("serve");
    expect(readme).toContain("export");
    expect(readme).toContain("import-package");
    expect(readme).toContain("benchmark");
    expect(readme).toContain("new-scenario");
  });

  it("documents Python-only exclusions explicitly", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).toContain("Python-Only");
    expect(readme).toContain("train");
    expect(readme).toContain("ecosystem");
    expect(readme).toContain("trigger-distillation");
  });

  it("documents the full provider surface", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).toContain("anthropic");
    expect(readme).toContain("hermes");
    expect(readme).toContain("pi");
    expect(readme).toContain("pi-rpc");
    expect(readme).toContain("deterministic");
  });

  it("documents MCP tools with 40+ count", () => {
    const readme = readFileSync(join(import.meta.dirname, "..", "README.md"), "utf-8");
    expect(readme).toContain("40+");
    expect(readme).toContain("solve_scenario");
    expect(readme).toContain("sandbox_create");
    expect(readme).toContain("capabilities");
  });
});

// ---------------------------------------------------------------------------
// CLI help matches README claims
// ---------------------------------------------------------------------------

describe("CLI help matches README", () => {
  it("lists all 17 commands in help", () => {
    const help = runCli(["--help"]);
    const expected = [
      "run", "list", "replay", "benchmark", "export", "export-training-data",
      "import-package", "new-scenario", "tui", "judge", "improve", "repl",
      "queue", "status", "serve", "mcp-serve", "version",
    ];
    for (const cmd of expected) {
      expect(help).toContain(cmd);
    }
  });
});

// ---------------------------------------------------------------------------
// Core module exports are importable
// ---------------------------------------------------------------------------

describe("Package exports", () => {
  it("exports GenerationRunner", async () => {
    const mod = await import("../src/index.js");
    expect(mod.GenerationRunner).toBeDefined();
  });

  it("exports GridCtfScenario", async () => {
    const mod = await import("../src/index.js");
    expect(mod.GridCtfScenario).toBeDefined();
  });

  it("exports SQLiteStore", async () => {
    const mod = await import("../src/index.js");
    expect(mod.SQLiteStore).toBeDefined();
  });

  it("exports createProvider", async () => {
    const mod = await import("../src/index.js");
    expect(mod.createProvider).toBeDefined();
  });

  it("exports EventStreamEmitter", async () => {
    const mod = await import("../src/index.js");
    expect(mod.EventStreamEmitter).toBeDefined();
  });

  it("exports LoopController", async () => {
    const mod = await import("../src/index.js");
    expect(mod.LoopController).toBeDefined();
  });
});
