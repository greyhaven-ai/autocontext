/**
 * AC-679 (slice 3b): `autoctx trace-findings --trace-id <id>` against the
 * ProductionTrace store.
 *
 * Extends the slice-2 CLI to load a stored `ProductionTrace` by id from
 * `.autocontext/production-traces/ingested/<date>/<batch>.jsonl` and adapt
 * it to the `PublicTrace` shape that the slice-1 extractor consumes.
 *
 * `--trace` (file path) and `--trace-id` (store lookup) are alternative
 * input modes; exactly one is required.
 */

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { runTraceFindingsCommand } from "../src/cli/trace-findings-command-workflow.js";

const PRODUCTION_TRACE_FIXTURE = {
  schemaVersion: "1.0",
  traceId: "trace_store_abc",
  source: {
    emitter: "autocontext-test",
    sdk: { name: "autocontext", version: "0.5.0" },
  },
  provider: { name: "anthropic" },
  model: "claude-sonnet-4-5",
  env: { environmentTag: "dev", appId: "ac-tests" },
  messages: [
    { role: "user", content: "Patch foo.ts", timestamp: "2026-05-14T12:00:01Z" },
    {
      role: "assistant",
      content: "Trying patch.",
      timestamp: "2026-05-14T12:00:02Z",
      toolCalls: [
        {
          toolName: "patch",
          args: { path: "foo.ts" },
          error: "hunk failed",
        },
      ],
    },
  ],
  toolCalls: [],
  outcome: {
    label: "failure",
    score: 0.2,
    reasoning: "Tests still failing.",
    signals: { correctness: 0.1, polish: 0.95 },
  },
  timing: {
    startedAt: "2026-05-14T12:00:00Z",
    endedAt: "2026-05-14T12:00:03Z",
    latencyMs: 3000,
  },
  usage: { tokensIn: 100, tokensOut: 200 },
  feedbackRefs: [],
  links: {},
  redactions: [],
};

let workdir = "";

beforeAll(async () => {
  workdir = await mkdtemp(join(tmpdir(), "ac679-3b-"));
  // Plant the trace at the exact path findTraceById will look for.
  const ingestDir = join(workdir, ".autocontext", "production-traces", "ingested", "2026-05-14");
  await mkdir(ingestDir, { recursive: true });
  await writeFile(
    join(ingestDir, "batch-001.jsonl"),
    JSON.stringify(PRODUCTION_TRACE_FIXTURE) + "\n",
    "utf8",
  );
});

afterAll(async () => {
  if (workdir) await rm(workdir, { recursive: true, force: true });
});

describe("autoctx trace-findings --trace-id (ProductionTrace store)", () => {
  it("loads a stored trace by id and emits the Markdown report", async () => {
    const result = await runTraceFindingsCommand(["--trace-id", "trace_store_abc"], {
      cwd: workdir,
    });

    expect(result.exitCode).toBe(0);
    expect(result.stderr).toBe("");
    expect(result.stdout).toContain("# Trace Findings: trace_store_abc");
    // The fixture has a tool_call_failure + low outcome score; both should
    // appear in the rendered Markdown.
    expect(result.stdout).toContain("tool_call_failure");
    expect(result.stdout).toContain("low_outcome_score");
  });

  it("emits JSON shape when --trace-id + --json are combined", async () => {
    const result = await runTraceFindingsCommand(["--trace-id", "trace_store_abc", "--json"], {
      cwd: workdir,
    });

    expect(result.exitCode).toBe(0);
    const payload = JSON.parse(result.stdout);
    expect(payload.traceId).toBe("trace_store_abc");
    expect(payload.sourceHarness).toBe("autocontext-test");
    expect(payload.findings.length).toBeGreaterThan(0);
  });

  it("fails with a clear stderr when the trace id is not found", async () => {
    const result = await runTraceFindingsCommand(["--trace-id", "no_such_trace"], { cwd: workdir });

    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(/not found|no_such_trace|trace id/);
  });

  it("rejects --trace and --trace-id together (mutually exclusive)", async () => {
    const result = await runTraceFindingsCommand(
      ["--trace", "/tmp/foo.json", "--trace-id", "trace_store_abc"],
      { cwd: workdir },
    );

    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(
      /mutually exclusive|cannot use both|both .*--trace/,
    );
  });

  it("fails when neither --trace nor --trace-id is supplied", async () => {
    const result = await runTraceFindingsCommand(["--json"], { cwd: workdir });

    expect(result.exitCode).not.toBe(0);
    expect(result.stderr.toLowerCase()).toMatch(/--trace|--trace-id|required/);
  });
});
