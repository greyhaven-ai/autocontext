import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runProductionTracesCommand } from "../../../../src/production-traces/cli/index.js";

let cwd: string;

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "autocontext-pt-cli-runner-"));
});
afterEach(() => {
  rmSync(cwd, { recursive: true, force: true });
});

describe("runProductionTracesCommand — top-level dispatch", () => {
  test("no argv prints top-level help with all known subcommands", async () => {
    const r = await runProductionTracesCommand([], { cwd });
    expect(r.exitCode).toBe(0);
    for (const sub of [
      "init",
      "ingest",
      "list",
      "show",
      "stats",
      "build-dataset",
      "datasets",
      "export",
      "policy",
      "rotate-salt",
      "prune",
    ]) {
      expect(r.stdout).toContain(sub);
    }
  });

  test("--help prints help with exit 0", async () => {
    const r = await runProductionTracesCommand(["--help"], { cwd });
    expect(r.exitCode).toBe(0);
    expect(r.stdout.toLowerCase()).toContain("production-traces");
  });

  test("unknown subcommand exits with domain-failure", async () => {
    const r = await runProductionTracesCommand(["no-such-verb"], { cwd });
    expect(r.exitCode).toBe(1);
    expect(r.stderr).toContain("no-such-verb");
  });

  test("every subcommand responds to --help with exit 0", async () => {
    const subs = [
      ["init"],
      ["ingest"],
      ["list"],
      ["show"], // without args is treated as help
      ["stats"],
      ["build-dataset"],
      ["datasets"],
      ["export"],
      ["policy"],
      ["rotate-salt"],
      ["prune"],
    ];
    for (const s of subs) {
      const r = await runProductionTracesCommand([...s, "--help"], { cwd });
      expect(r.exitCode).toBe(0);
    }
  });
});
