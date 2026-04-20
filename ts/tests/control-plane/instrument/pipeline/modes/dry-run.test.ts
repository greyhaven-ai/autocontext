/**
 * A2-I Layer 6 - dry-run mode unit tests (spec §7.3).
 *
 * Runs the mode directly with a pre-built payload and asserts:
 *   - session dir layout matches spec §9.1 exactly
 *   - plan.json is written verbatim from input (byte-deterministic contract)
 *   - patch file naming: <NNNN>.<flattened-path>.patch
 *   - pr-body.md writes input verbatim
 */
import { describe, test, expect, beforeEach } from "vitest";
import {
  existsSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runDryRunMode } from "../../../../../src/control-plane/instrument/pipeline/modes/dry-run.js";
import type { InstrumentPlan, InstrumentSession } from "../../../../../src/control-plane/instrument/contract/plugin-interface.js";
import type { ContentHash } from "../../../../../src/control-plane/contract/branded-ids.js";

const FIXED_ULID = "01HN0000000000000000000001";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-dryrun-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

function stubSession(): InstrumentSession {
  return {
    cwd: "/tmp/repo",
    flags: {
      mode: "dry-run",
      enhanced: false,
      maxFileBytes: 1_048_576,
      failIfEmpty: false,
      excludes: [],
      output: "pretty",
      force: false,
    },
    startedAt: "2026-04-17T12:00:00.000Z",
    endedAt: "2026-04-17T12:00:00.000Z",
    autoctxVersion: "0.0.0-test",
    registeredPlugins: [],
    gitignoreFingerprint: "sha256:0000000000000000000000000000000000000000000000000000000000000000" as ContentHash,
  };
}

function stubPlan(): InstrumentPlan {
  return {
    schemaVersion: "1.0",
    edits: [],
    sourceFiles: [],
    conflictDecisions: [],
    safetyDecisions: [],
  };
}

describe("runDryRunMode - session directory layout (spec §9.1)", () => {
  test("writes session.json, detections.jsonl, plan.json, patches/, pr-body.md", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, ".autocontext", "instrument-patches", FIXED_ULID);
    runDryRunMode({
      sessionDir,
      session: stubSession(),
      plan: stubPlan(),
      planJson: '{"schemaVersion":"1.0","edits":[],"sourceFiles":[],"conflictDecisions":[],"safetyDecisions":[]}',
      detections: [],
      patches: [],
      prBody: "# pr body",
    });
    expect(existsSync(join(sessionDir, "session.json"))).toBe(true);
    expect(existsSync(join(sessionDir, "detections.jsonl"))).toBe(true);
    expect(existsSync(join(sessionDir, "plan.json"))).toBe(true);
    expect(existsSync(join(sessionDir, "patches"))).toBe(true);
    expect(existsSync(join(sessionDir, "pr-body.md"))).toBe(true);
  });

  test("plan.json is written verbatim from input (byte-deterministic contract)", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "s");
    const planJson = '{"foo":"bar"}';
    runDryRunMode({
      sessionDir,
      session: stubSession(),
      plan: stubPlan(),
      planJson,
      detections: [],
      patches: [],
      prBody: "",
    });
    const written = readFileSync(join(sessionDir, "plan.json"), "utf-8");
    expect(written).toBe(planJson + "\n");
  });

  test("patches directory contains one .patch file per patch, with flattened path + sequence prefix", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "s");
    runDryRunMode({
      sessionDir,
      session: stubSession(),
      plan: stubPlan(),
      planJson: "{}",
      detections: [],
      patches: [
        { filePath: "src/main.py", patch: "--- a/src/main.py\n+++ b/src/main.py\n@@\n hi\n" },
        { filePath: "src/api.ts", patch: "--- a/src/api.ts\n+++ b/src/api.ts\n@@\n hi\n" },
      ],
      prBody: "",
    });
    const files = readdirSync(join(sessionDir, "patches")).sort();
    expect(files).toEqual(["0001.src.main.py.patch", "0002.src.api.ts.patch"].sort());
  });

  test("detections.jsonl has one line per detection", () => {
    const cwd = scratch();
    const sessionDir = join(cwd, "s");
    runDryRunMode({
      sessionDir,
      session: stubSession(),
      plan: stubPlan(),
      planJson: "{}",
      detections: [
        { pluginId: "p", filePath: "a.py", matchRange: { startByte: 0, endByte: 1 }, editsProduced: 1 },
        { pluginId: "p", filePath: "b.py", matchRange: { startByte: 0, endByte: 2 }, editsProduced: 2 },
      ],
      patches: [],
      prBody: "",
    });
    const body = readFileSync(join(sessionDir, "detections.jsonl"), "utf-8").trim();
    expect(body.split("\n").length).toBe(2);
  });
});
