// Smoke tests for the control-plane CLI wired through the real autoctx
// entrypoint. These invoke a subprocess (via `npx tsx`) so we exercise argv
// parsing, exit codes, and the stdout/stderr stream separation that CI relies
// on. Kept minimal — per-command behavior is covered by the in-process tests
// in the sibling files.

import { describe, test, expect, beforeEach, afterEach } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const CLI = join(import.meta.dirname, "..", "..", "..", "src", "cli", "index.ts");

function runCli(
  args: string[],
  opts: { cwd?: string } = {},
): { stdout: string; stderr: string; exitCode: number } {
  const r = spawnSync("npx", ["tsx", CLI, ...args], {
    cwd: opts.cwd,
    env: { ...process.env, NODE_NO_WARNINGS: "1" },
    encoding: "utf8",
    timeout: 30000,
  });
  return {
    stdout: r.stdout ?? "",
    stderr: r.stderr ?? "",
    exitCode: r.status ?? 1,
  };
}

let tmp: string;
let payload: string;

beforeEach(() => {
  tmp = mkdtempSync(join(tmpdir(), "autocontext-cli-smoke-"));
  payload = join(tmp, "payload");
  mkdirSync(payload, { recursive: true });
  writeFileSync(join(payload, "prompt.txt"), "hello");
});

afterEach(() => {
  rmSync(tmp, { recursive: true, force: true });
});

describe("subprocess: control-plane --help on each namespace", () => {
  test("autoctx candidate --help", () => {
    const r = runCli(["candidate", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("register");
  });

  test("autoctx eval --help", () => {
    const r = runCli(["eval", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("attach");
  });

  test("autoctx promotion --help", () => {
    const r = runCli(["promotion", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("decide");
  });

  test("autoctx registry --help", () => {
    const r = runCli(["registry", "--help"], { cwd: tmp });
    expect(r.exitCode).toBe(0);
    expect(r.stdout).toContain("repair");
  });
});

describe("subprocess: stdout/stderr separation (json mode)", () => {
  test("candidate register --output json emits a parseable single JSON doc on stdout", () => {
    const r = runCli(
      [
        "candidate",
        "register",
        "--scenario",
        "grid_ctf",
        "--actuator",
        "prompt-patch",
        "--payload",
        payload,
        "--output",
        "json",
      ],
      { cwd: tmp },
    );
    expect(r.exitCode).toBe(0);
    // stdout is pipeable to jq: exactly one JSON doc, optionally followed by trailing newline.
    const parsed = JSON.parse(r.stdout.trim());
    expect(parsed.actuatorType).toBe("prompt-patch");
    expect(parsed.scenario).toBe("grid_ctf");
  });
});
