/**
 * A2-I Layer 6 - orchestrator property tests.
 *
 * P-session-determinism: given the same (cwd-snapshot, flags, nowIso,
 *   sessionUlid, pluginRegistry), plan.json and every patch file are
 *   byte-identical across repeat runs.
 *
 * P-mode-isolation: dry-run mode never writes outside
 *   .autocontext/instrument-patches/<sessionUlid>/ - the original customer
 *   files are byte-identical after the run.
 *
 * Property-test budget: fast-check's default sampling is 100 runs; here we
 * scale down to 10 runs per property for CI budget (tree-sitter parse is
 * O(file size) and 100 scratch-dir spins in a single test file dominates the
 * instrument test budget). Each run still seeds multiple files.
 */
import { describe, test, expect, beforeEach } from "vitest";
import fc from "fast-check";
import {
  mkdtempSync,
  mkdirSync,
  writeFileSync,
  readFileSync,
  readdirSync,
  rmSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { runInstrument } from "../../../../src/control-plane/instrument/pipeline/orchestrator.js";
import {
  registerDetectorPlugin,
  resetRegistryForTests,
} from "../../../../src/control-plane/instrument/registry/plugin-registry.js";
import { mockOpenAiPythonPlugin } from "../../../_fixtures/plugins/mock-openai-python.js";

const ULID = "01HN0000000000000000000001";
const NOW = "2026-04-17T12:00:00.000Z";

const scratches: string[] = [];
function scratch(): string {
  const d = mkdtempSync(join(tmpdir(), "a2i-prop-"));
  scratches.push(d);
  return d;
}

beforeEach(() => {
  resetRegistryForTests();
  while (scratches.length > 0) {
    const d = scratches.pop()!;
    try {
      rmSync(d, { recursive: true, force: true });
    } catch {
      // ignore
    }
  }
});

/** Seed the given directory with a Python file containing `n` OpenAI(...) call sites. */
function seed(root: string, n: number, extraSuffix: string): void {
  mkdirSync(join(root, "src"), { recursive: true });
  const parts: string[] = ["from openai import OpenAI", ""];
  for (let i = 0; i < n; i += 1) {
    parts.push(`c${i} = OpenAI(api_key='${extraSuffix}_${i}')`);
  }
  parts.push("");
  writeFileSync(join(root, "src", "main.py"), parts.join("\n"), "utf-8");
}

describe("P-session-determinism", () => {
  test("same inputs produce byte-identical plan.json + patches (10 runs)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 1, max: 3 }),
        fc.string({ minLength: 1, maxLength: 8, unit: "grapheme-ascii" }).map((s) =>
          s.replace(/[^a-zA-Z0-9_]/g, "x"),
        ),
        async (n, suffix) => {
          resetRegistryForTests();
          registerDetectorPlugin(mockOpenAiPythonPlugin);
          const cwd1 = scratch();
          const cwd2 = scratch();
          seed(cwd1, n, suffix);
          seed(cwd2, n, suffix);

          const r1 = await runInstrument({
            cwd: cwd1,
            mode: "dry-run",
            sessionUlid: ULID,
            nowIso: NOW,
          });
          const r2 = await runInstrument({
            cwd: cwd2,
            mode: "dry-run",
            sessionUlid: ULID,
            nowIso: NOW,
          });

          const plan1 = readFileSync(join(r1.sessionDir, "plan.json"), "utf-8");
          const plan2 = readFileSync(join(r2.sessionDir, "plan.json"), "utf-8");
          expect(plan2).toBe(plan1);
          expect(r2.planHash).toBe(r1.planHash);

          const patches1 = readdirSync(join(r1.sessionDir, "patches")).sort();
          const patches2 = readdirSync(join(r2.sessionDir, "patches")).sort();
          expect(patches2).toEqual(patches1);
          for (const name of patches1) {
            const a = readFileSync(join(r1.sessionDir, "patches", name), "utf-8");
            const b = readFileSync(join(r2.sessionDir, "patches", name), "utf-8");
            expect(b).toBe(a);
          }
        },
      ),
      { numRuns: 10 },
    );
  });
});

describe("P-mode-isolation", () => {
  test("dry-run never mutates customer files (10 runs)", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.integer({ min: 0, max: 3 }),
        fc.string({ minLength: 1, maxLength: 8, unit: "grapheme-ascii" }).map((s) =>
          s.replace(/[^a-zA-Z0-9_]/g, "x"),
        ),
        async (n, suffix) => {
          resetRegistryForTests();
          registerDetectorPlugin(mockOpenAiPythonPlugin);
          const cwd = scratch();
          seed(cwd, n, suffix);
          const before = readFileSync(join(cwd, "src", "main.py"), "utf-8");
          await runInstrument({
            cwd,
            mode: "dry-run",
            sessionUlid: ULID,
            nowIso: NOW,
          });
          const after = readFileSync(join(cwd, "src", "main.py"), "utf-8");
          expect(after).toBe(before);
        },
      ),
      { numRuns: 10 },
    );
  });
});
