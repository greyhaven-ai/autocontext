import { describe, test, expect } from "vitest";
import fc from "fast-check";
import { readdirSync, readFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { validateProductionTrace } from "../../../../src/production-traces/contract/validators.js";
import { createProductionTrace } from "../../../../src/production-traces/contract/factories.js";
import type { AppId, EnvironmentTag } from "../../../../src/production-traces/contract/branded-ids.js";

const TS_ROOT = resolve(__dirname, "..", "..", "..", "..");
const WORKTREE_ROOT = resolve(TS_ROOT, "..");
const FIXTURES_DIR = resolve(__dirname, "..", "fixtures");
const PY_CWD = resolve(WORKTREE_ROOT, "autocontext");

type PythonResult = { valid: boolean; error?: string };

// Runs the Python-side validator on an in-memory trace and returns a parsed result.
function runPythonValidate(input: unknown): PythonResult {
  const script = [
    "import json, sys",
    "from pydantic import ValidationError",
    "from autocontext.production_traces import validate_production_trace",
    "data = json.loads(sys.stdin.read())",
    "try:",
    "    trace = validate_production_trace(data)",
    "    out = {'valid': True, 'dumped': trace.model_dump(mode='json', exclude_none=True)}",
    "    print(json.dumps(out))",
    "except ValidationError as e:",
    "    print(json.dumps({'valid': False, 'error': str(e)}))",
  ].join("\n");
  const result = spawnSync("uv", ["run", "python", "-c", script], {
    cwd: PY_CWD,
    input: JSON.stringify(input),
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status !== 0 && !result.stdout) {
    throw new Error(`python validate exited ${result.status}: ${result.stderr}`);
  }
  const line = result.stdout.trim().split("\n").pop() ?? "{}";
  return JSON.parse(line) as PythonResult;
}

// We skip the Python-involving tests if uv is unavailable in the environment.
function hasUv(): boolean {
  const r = spawnSync("uv", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

const UV_AVAILABLE = hasUv();
const maybeDescribe = UV_AVAILABLE ? describe : describe.skip;

maybeDescribe("cross-runtime fixture validation (TS AJV vs Python Pydantic)", () => {
  const fixtureFiles = readdirSync(FIXTURES_DIR).filter((f) => f.endsWith(".json")).sort();

  test("non-empty fixture set", () => {
    expect(fixtureFiles.length).toBeGreaterThanOrEqual(9);
  });

  for (const file of fixtureFiles) {
    const isInvalid = file.startsWith("invalid-");
    test(`${file}: TS and Python agree on ${isInvalid ? "rejection" : "acceptance"}`, () => {
      const body = readFileSync(resolve(FIXTURES_DIR, file), "utf-8");
      const data: unknown = JSON.parse(body);
      const tsResult = validateProductionTrace(data);
      const pyResult = runPythonValidate(data);

      expect(tsResult.valid).toBe(pyResult.valid);
      if (isInvalid) {
        expect(tsResult.valid).toBe(false);
        expect(pyResult.valid).toBe(false);
      } else {
        expect(tsResult.valid).toBe(true);
        expect(pyResult.valid).toBe(true);
      }
    });
  }
});

maybeDescribe("P5 cross-runtime property (factory-built traces validate on both sides)", () => {
  test("factory-built traces accepted by both TS AJV and Python Pydantic", () => {
    // Keep numRuns modest — each run spawns a uv subprocess (~hundreds of ms).
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 1_000_000 }),
        fc.integer({ min: 0, max: 1_000_000 }),
        fc.integer({ min: 0, max: 10_000 }),
        (tokensIn, tokensOut, latencyMs) => {
          const trace = createProductionTrace({
            source: { emitter: "sdk", sdk: { name: "ts", version: "0.4.3" } },
            provider: { name: "openai" },
            model: "gpt-4o-mini",
            env: {
              environmentTag: "production" as EnvironmentTag,
              appId: "my-app" as AppId,
            },
            messages: [{ role: "user", content: "x", timestamp: "2026-04-17T12:00:00.000Z" }],
            timing: {
              startedAt: "2026-04-17T12:00:00.000Z",
              endedAt: "2026-04-17T12:00:01.000Z",
              latencyMs,
            },
            usage: { tokensIn, tokensOut },
          });
          const tsResult = validateProductionTrace(trace);
          const pyResult = runPythonValidate(trace);
          return tsResult.valid && pyResult.valid;
        },
      ),
      { numRuns: 5 },
    );
  }, 60_000);
});

// TS-only variation: exercises the AJV path without spawning Python. Faster,
// still validates the property-style generator approach for follow-up layers.
describe("P5 TS-only property check (AJV + factory)", () => {
  test("factory output always passes AJV for small integer inputs", () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 1_000_000 }),
        fc.integer({ min: 0, max: 1_000_000 }),
        fc.integer({ min: 0, max: 10_000 }),
        (tokensIn, tokensOut, latencyMs) => {
          const trace = createProductionTrace({
            source: { emitter: "sdk", sdk: { name: "ts", version: "0.4.3" } },
            provider: { name: "openai" },
            model: "gpt-4o-mini",
            env: {
              environmentTag: "production" as EnvironmentTag,
              appId: "my-app" as AppId,
            },
            messages: [{ role: "user", content: "x", timestamp: "2026-04-17T12:00:00.000Z" }],
            timing: {
              startedAt: "2026-04-17T12:00:00.000Z",
              endedAt: "2026-04-17T12:00:01.000Z",
              latencyMs,
            },
            usage: { tokensIn, tokensOut },
          });
          return validateProductionTrace(trace).valid;
        },
      ),
      { numRuns: 100 },
    );
  });
});
