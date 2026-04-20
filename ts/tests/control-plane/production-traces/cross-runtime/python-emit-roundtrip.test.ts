/**
 * P7 cross-runtime round-trip (spec §10.1):
 *
 *   Python `build_trace` → JSON → TS AJV validation → canonical JSON → Python
 *   `validate_production_trace` on the canonical form.
 *
 * Asserts:
 *   1. Python-emitted trace is accepted by TS AJV.
 *   2. Re-canonicalizing the trace (key-sort, utf-8) preserves acceptance on
 *      both sides.
 *   3. Canonical form is stable: two independent canonicalizations of the same
 *      input are byte-identical.
 *
 * Skips gracefully when `uv` or `python` is not available in the environment.
 */
import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { mkdtempSync, readFileSync, readdirSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { validateProductionTrace } from "../../../../src/production-traces/contract/validators.js";
import { canonicalJsonStringify } from "../../../../src/control-plane/contract/canonical-json.js";

const TS_ROOT = resolve(__dirname, "..", "..", "..", "..");
const WORKTREE_ROOT = resolve(TS_ROOT, "..");
const PY_CWD = resolve(WORKTREE_ROOT, "autocontext");

function hasUv(): boolean {
  const r = spawnSync("uv", ["--version"], { encoding: "utf-8" });
  return r.status === 0;
}

const UV_AVAILABLE = hasUv();
const maybeDescribe = UV_AVAILABLE ? describe : describe.skip;

function pythonEmitTrace(outDir: string): unknown {
  // Drives the Python SDK end-to-end: build_trace + write_jsonl to outDir,
  // then stdout carries the absolute path of the emitted .jsonl.
  const script = [
    "import json, sys",
    "from autocontext.production_traces import build_trace, write_jsonl",
    "trace = build_trace(",
    '    provider="anthropic",',
    '    model="claude-sonnet-4-20250514",',
    '    messages=[{"role": "user", "content": "hello", "timestamp": "2026-04-17T12:00:00.000Z"}],',
    '    timing={"startedAt": "2026-04-17T12:00:00.000Z", "endedAt": "2026-04-17T12:00:01.000Z", "latencyMs": 1000},',
    '    usage={"tokensIn": 10, "tokensOut": 5},',
    '    env={"environmentTag": "production", "appId": "my-app"},',
    '    trace_id="01KFDQ9XZ3M7RT2V8K1PHY4BNC",',
    ")",
    `path = write_jsonl(trace, cwd=${JSON.stringify(outDir)})`,
    "print(str(path))",
  ].join("\n");
  const result = spawnSync("uv", ["run", "python", "-c", script], {
    cwd: PY_CWD,
    encoding: "utf-8",
    env: process.env,
  });
  if (result.status !== 0) {
    throw new Error(`python emit exited ${result.status}: ${result.stderr}`);
  }
  const jsonlPath = result.stdout.trim().split("\n").pop() as string;
  const body = readFileSync(jsonlPath, "utf-8").trim();
  return JSON.parse(body);
}

function pythonValidate(input: unknown): { valid: boolean; error?: string } {
  const script = [
    "import json, sys",
    "from pydantic import ValidationError",
    "from autocontext.production_traces import validate_production_trace",
    "data = json.loads(sys.stdin.read())",
    "try:",
    "    validate_production_trace(data)",
    "    print(json.dumps({'valid': True}))",
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
  return JSON.parse(result.stdout.trim().split("\n").pop() as string);
}

maybeDescribe("P7 cross-runtime round-trip (Python emit → TS validate → canonical → Python validate)", () => {
  test("python-emitted trace is accepted by TS AJV", () => {
    const outDir = mkdtempSync(join(tmpdir(), "autocontext-p7-"));
    try {
      const trace = pythonEmitTrace(outDir);
      const result = validateProductionTrace(trace);
      expect(result.valid).toBe(true);
      // Also confirm the on-disk layout spec §6.1: incoming/YYYY-MM-DD/<ulid>.jsonl
      const incoming = resolve(outDir, ".autocontext", "production-traces", "incoming");
      const dates = readdirSync(incoming);
      expect(dates.length).toBe(1);
      const files = readdirSync(resolve(incoming, dates[0]!));
      expect(files.length).toBe(1);
      expect(files[0]).toMatch(/^[0-9A-HJKMNP-TV-Z]{26}\.jsonl$/);
    } finally {
      rmSync(outDir, { recursive: true, force: true });
    }
  }, 60_000);

  test("canonical-JSON form is stable and both runtimes re-accept it", () => {
    const outDir = mkdtempSync(join(tmpdir(), "autocontext-p7-"));
    try {
      const trace = pythonEmitTrace(outDir);

      // TS canonical encoding — byte-stable, key-sorted.
      const canonical1 = canonicalJsonStringify(trace);
      const canonical2 = canonicalJsonStringify(JSON.parse(canonical1));
      expect(canonical1).toBe(canonical2); // byte-identical

      // The canonical form must still be accepted by both sides.
      const reparsed = JSON.parse(canonical1);
      expect(validateProductionTrace(reparsed).valid).toBe(true);
      const py = pythonValidate(reparsed);
      expect(py.valid).toBe(true);
    } finally {
      rmSync(outDir, { recursive: true, force: true });
    }
  }, 60_000);
});
