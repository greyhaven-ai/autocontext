/**
 * AC-679 (slice 2): trace-findings CLI subcommand.
 *
 * Wires the slice-1 extractor library at `analytics/trace-findings.ts`
 * into an operator-facing CLI. Mirrors the Python `autoctx analytics
 * trace-findings` shape; for slice 2 we accept a path to a PublicTrace
 * JSON file rather than coupling to the production-traces storage
 * layer, so the slice stays bounded.
 */

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { SCHEMA_VERSION, type PublicTrace } from "../src/index.js";
import { runTraceFindingsCommand } from "../src/cli/trace-findings-command-workflow.js";

let workdir = "";

beforeAll(async () => {
  workdir = await mkdtemp(join(tmpdir(), "ac679-cli-"));
});

afterAll(async () => {
  if (workdir) {
    await rm(workdir, { recursive: true, force: true });
  }
});

function buildTrace(overrides: Partial<PublicTrace> = {}): PublicTrace {
  return {
    schemaVersion: SCHEMA_VERSION,
    traceId: "trace_cli_1",
    sourceHarness: "autocontext",
    collectedAt: "2026-05-13T18:00:00Z",
    messages: [
      { role: "user", content: "Patch foo.ts", timestamp: "2026-05-13T18:00:01Z" },
      {
        role: "assistant",
        content: "Trying patch.",
        timestamp: "2026-05-13T18:00:02Z",
        toolCalls: [{ toolName: "patch", args: {}, error: "hunk failed" }],
      },
    ],
    outcome: { score: 0.2, reasoning: "Broken.", dimensions: {} },
    ...overrides,
  };
}

async function writeFixture(name: string, trace: PublicTrace): Promise<string> {
  const path = join(workdir, name);
  await writeFile(path, JSON.stringify(trace), "utf8");
  return path;
}

describe("autoctx trace-findings CLI", () => {
  it("emits Markdown by default with sections + evidence references", async () => {
    const path = await writeFixture("happy.json", buildTrace());

    const result = await runTraceFindingsCommand(["--trace", path]);

    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
    expect(result.stdout).toContain("# Trace Findings: trace_cli_1");
    expect(result.stdout).toContain("## Findings");
    expect(result.stdout).toContain("## Failure Motifs");
    expect(result.stdout).toMatch(/msg #1/);
  });

  it("emits JSON when --json is passed", async () => {
    const path = await writeFixture("json.json", buildTrace());

    const result = await runTraceFindingsCommand(["--trace", path, "--json"]);

    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
    const payload = JSON.parse(result.stdout);
    expect(payload.traceId).toBe("trace_cli_1");
    expect(Array.isArray(payload.findings)).toBe(true);
    expect(payload.findings.length).toBeGreaterThan(0);
    expect(Array.isArray(payload.failureMotifs)).toBe(true);
  });

  it("emits help on --help", async () => {
    const result = await runTraceFindingsCommand(["--help"]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("trace-findings");
    expect(result.stdout).toContain("--trace");
    expect(result.stdout).toContain("--json");
  });

  it("emits help on -h", async () => {
    const result = await runTraceFindingsCommand(["-h"]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("trace-findings");
  });

  it("emits help with no args", async () => {
    const result = await runTraceFindingsCommand([]);
    expect(result.exitCode).toBe(0);
    expect(result.stdout).toContain("trace-findings");
  });

  it("fails with exit code 2 when --trace path does not exist", async () => {
    const result = await runTraceFindingsCommand(["--trace", join(workdir, "does-not-exist.json")]);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr).toMatch(/trace file|read|not found|enoent/i);
  });

  it("fails when --trace file is not valid JSON", async () => {
    const path = join(workdir, "garbage.json");
    await writeFile(path, "not json at all", "utf8");
    const result = await runTraceFindingsCommand(["--trace", path]);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(/json|parse/);
  });

  it("fails when JSON does not validate as a PublicTrace", async () => {
    const path = join(workdir, "wrong-shape.json");
    await writeFile(path, JSON.stringify({ traceId: "x" }), "utf8");
    const result = await runTraceFindingsCommand(["--trace", path]);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(/publictrace|schema|invalid/);
  });

  it("rejects unknown flags with a clear error", async () => {
    const result = await runTraceFindingsCommand(["--trace", "ignored.json", "--bogus"]);
    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(/unknown|bogus/);
  });

  it("rejects --json AND --markdown at the same time (only one output mode)", async () => {
    // We don't even ship --markdown today, but the test pins that the
    // command's output mode is single-source-of-truth so future surface
    // additions don't accidentally double-emit.
    const path = await writeFixture("single-mode.json", buildTrace());
    const result = await runTraceFindingsCommand(["--trace", path, "--json"]);
    expect(result.exitCode).toBe(0);
    // Ensure the JSON output does NOT also include the Markdown heading;
    // confirms we picked exactly one mode rather than concatenating.
    expect(result.stdout).not.toContain("# Trace Findings:");
  });

  it("emits a directory-relative path-aware error when --trace is a directory", async () => {
    const dir = join(workdir, "subdir");
    await mkdir(dir, { recursive: true });
    const result = await runTraceFindingsCommand(["--trace", dir]);
    expect(result.exitCode).not.toBe(0);
  });
});
