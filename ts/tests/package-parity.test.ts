/**
 * Tests for AC-362: Package surface parity verification.
 * Ensures the npm package delivers on the claims in the README.
 */

import { describe, it, expect } from "vitest";
import { mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";

const PACKAGE_ROOT = join(import.meta.dirname, "..");
const PACKAGE_JSON = JSON.parse(
  readFileSync(join(PACKAGE_ROOT, "package.json"), "utf-8"),
) as { bin: { autoctx: string } };
let didBuild = false;

function ensureBuiltPackage(): void {
  if (didBuild) return;
  execFileSync("npm", ["run", "build"], {
    cwd: PACKAGE_ROOT,
    encoding: "utf8",
    timeout: 120000,
    env: { ...process.env, NODE_NO_WARNINGS: "1" },
  });
  didBuild = true;
}

function createConsumerWorkspace(): string {
  const workspace = join(
    tmpdir(),
    `autoctx-package-parity-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
  );
  mkdirSync(join(workspace, "node_modules"), { recursive: true });
  symlinkSync(PACKAGE_ROOT, join(workspace, "node_modules", "autoctx"), "dir");
  writeFileSync(join(workspace, "package.json"), JSON.stringify({ name: "package-parity-fixture", type: "module" }), "utf-8");
  return workspace;
}

function withConsumerWorkspace<T>(fn: (workspace: string) => T): T {
  ensureBuiltPackage();
  const workspace = createConsumerWorkspace();
  try {
    return fn(workspace);
  } finally {
    rmSync(workspace, { recursive: true, force: true });
  }
}

function runCli(args: string[]): string {
  return withConsumerWorkspace((workspace) => {
    const cli = join(workspace, "node_modules", "autoctx", PACKAGE_JSON.bin.autoctx);
    try {
      return execFileSync("node", [cli, ...args], {
        cwd: workspace,
        encoding: "utf8",
        timeout: 10000,
        env: { ...process.env, NODE_NO_WARNINGS: "1" },
      });
    } catch (err: unknown) {
      return (err as { stdout?: string }).stdout ?? "";
    }
  });
}

function importPackageExport(exportName: string): boolean {
  return withConsumerWorkspace((workspace) => {
    const output = execFileSync(
      "node",
      [
        "--input-type=module",
        "-e",
        `import("autoctx").then((mod) => { console.log(${JSON.stringify(exportName)} in mod ? "yes" : "no"); });`,
      ],
      {
        cwd: workspace,
        encoding: "utf8",
        timeout: 10000,
        env: { ...process.env, NODE_NO_WARNINGS: "1" },
      },
    );
    return output.trim() === "yes";
  });
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
  it("lists all documented commands in help", () => {
    const help = runCli(["--help"]);
    const expected = [
      "init", "capabilities", "login", "whoami", "logout",
      "run", "list", "replay", "benchmark", "export", "export-training-data",
      "import-package", "new-scenario", "tui", "judge", "improve", "repl",
      "queue", "status", "serve", "mcp-serve", "version",
    ];
    for (const cmd of expected) {
      expect(help).toContain(cmd);
    }
  }, 15000);
});

// ---------------------------------------------------------------------------
// Core module exports are importable
// ---------------------------------------------------------------------------

describe("Package exports", () => {
  it("exports GenerationRunner", async () => {
    expect(importPackageExport("GenerationRunner")).toBe(true);
  });

  it("exports GridCtfScenario", async () => {
    expect(importPackageExport("GridCtfScenario")).toBe(true);
  });

  it("exports SQLiteStore", async () => {
    expect(importPackageExport("SQLiteStore")).toBe(true);
  });

  it("exports createProvider", async () => {
    expect(importPackageExport("createProvider")).toBe(true);
  });

  it("exports EventStreamEmitter", async () => {
    expect(importPackageExport("EventStreamEmitter")).toBe(true);
  });

  it("exports LoopController", async () => {
    expect(importPackageExport("LoopController")).toBe(true);
  });
});
