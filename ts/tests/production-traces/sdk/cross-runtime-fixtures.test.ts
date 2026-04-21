import { describe, test, expect } from "vitest";
import { readdirSync, readFileSync, statSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { buildTrace, type BuildTraceInputs } from "../../../src/production-traces/sdk/build-trace.js";
import { canonicalJsonStringify } from "../../../src/control-plane/contract/canonical-json.js";

/**
 * Cross-runtime fixtures test (spec §5.1).
 *
 * Fast-path guard: iterates every directory under
 * ``tests/_fixtures/cross-runtime-emit/`` that has both ``inputs.json`` and
 * ``python-canonical.json``. Builds the trace in TypeScript via
 * :func:`buildTrace`, canonicalizes it via Foundation B's
 * ``canonicalJsonStringify``, and asserts byte-for-byte equality with the
 * committed Python canonical output.
 *
 * These fixtures run on every CI (<100ms total). Regenerable via
 * ``npm run regenerate-cross-runtime-fixtures``.
 */

const __dirname = dirname(fileURLToPath(import.meta.url));
const FIXTURES_DIR = resolve(__dirname, "..", "..", "_fixtures", "cross-runtime-emit");

function discoverFixtures(): string[] {
  if (!existsSync(FIXTURES_DIR)) return [];
  return readdirSync(FIXTURES_DIR)
    .filter((name) => {
      const p = join(FIXTURES_DIR, name);
      if (!statSync(p).isDirectory()) return false;
      return (
        existsSync(join(p, "inputs.json"))
        && existsSync(join(p, "python-canonical.json"))
      );
    })
    .sort();
}

const FIXTURES = discoverFixtures();

describe("cross-runtime-emit fixtures (TS buildTrace vs committed Python output)", () => {
  test("at least 7 fixtures are committed (spec §3.1)", () => {
    expect(FIXTURES.length).toBeGreaterThanOrEqual(7);
  });

  for (const name of FIXTURES) {
    test(`${name}: TS canonical JSON matches committed Python output`, () => {
      const inputs = JSON.parse(
        readFileSync(join(FIXTURES_DIR, name, "inputs.json"), "utf-8"),
      ) as BuildTraceInputs;
      const pythonCanonical = readFileSync(
        join(FIXTURES_DIR, name, "python-canonical.json"),
        "utf-8",
      ).trim();

      const tsTrace = buildTrace(inputs);
      const tsCanonical = canonicalJsonStringify(tsTrace);

      expect(tsCanonical).toBe(pythonCanonical);
    });
  }
});
