/**
 * Cross-runtime parity fixtures — Task 3.12.
 *
 * For each fixture, verifies that:
 * 1. The TS driver produces the expected canonical JSON.
 * 2. The Python driver produces the same canonical JSON.
 *
 * Normalization (traceId, timing, SDK name/version, message timestamps, error
 * message/type/stack) is done by the drivers themselves so this test can do
 * a plain string comparison.
 *
 * The `chat-streaming-abandoned` fixture requires --expose-gc so it is run
 * with `node --expose-gc` for the TS driver.
 */

import { describe, it, expect } from "vitest";
import { execFileSync, spawnSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "../../../..");
const FIXTURES_DIR = join(__dirname, "fixtures");
const PYTHON_ROOT = resolve(ROOT, "..", "autocontext");
const TS_DRIVER = join(ROOT, "scripts", "drive-parity-fixture.mjs");
const PY_DRIVER = join(PYTHON_ROOT, "scripts", "drive_parity_fixture.py");

const FIXTURES = [
  "minimal-chat-success",
  "chat-with-tool-calls",
  "chat-streaming-with-usage",
  "chat-streaming-abandoned",
  "rate-limit-exception",
  "timeout-exception",
  "content-filter-finish-reason",
  "session-with-user-id-and-session-id",
  "responses-api-success",
] as const;

function runTsDriver(fixtureName: string): string {
  const result = spawnSync(
    process.execPath,
    ["--expose-gc", "--import", "tsx/esm", TS_DRIVER, fixtureName],
    { cwd: ROOT, encoding: "utf-8", timeout: 30_000 },
  );
  if (result.status !== 0) {
    throw new Error(
      `TS driver failed for ${fixtureName}: ${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

function runPyDriver(fixtureName: string): string {
  const result = spawnSync(
    "uv",
    ["run", "python", PY_DRIVER, fixtureName],
    { cwd: PYTHON_ROOT, encoding: "utf-8", timeout: 30_000 },
  );
  if (result.status !== 0) {
    throw new Error(
      `Python driver failed for ${fixtureName}: ${result.stderr || result.stdout}`,
    );
  }
  return result.stdout.trim();
}

describe("cross-runtime parity fixtures", () => {
  for (const fixtureName of FIXTURES) {
    describe(fixtureName, () => {
      it("TS output matches expected canonical JSON", () => {
        const expectedPath = join(FIXTURES_DIR, fixtureName, "expected-trace.canonical.json");
        expect(existsSync(expectedPath), `expected-trace.canonical.json missing for ${fixtureName}`).toBe(true);
        const expected = readFileSync(expectedPath, "utf-8").trim();
        const actual = runTsDriver(fixtureName);
        expect(actual).toBe(expected);
      });

      it("Python output matches expected canonical JSON", () => {
        const expectedPath = join(FIXTURES_DIR, fixtureName, "expected-trace.canonical.json");
        const expected = readFileSync(expectedPath, "utf-8").trim();
        const actual = runPyDriver(fixtureName);
        expect(actual).toBe(expected);
      });

      it("TS and Python outputs are byte-identical", () => {
        const tsOut = runTsDriver(fixtureName);
        const pyOut = runPyDriver(fixtureName);
        expect(tsOut).toBe(pyOut);
      });
    });
  }
});
