import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ANTHROPIC_ERROR_REASONS } from "../../../src/production-traces/taxonomy/anthropic-error-reasons.js";

const PYTHON_CWD = join(
  dirname(fileURLToPath(import.meta.url)),
  "..", "..", "..", "..", "autocontext",
);

describe("Anthropic taxonomy cross-runtime parity", () => {
  test("Python table matches TS table byte-for-byte", () => {
    const result = spawnSync(
      "uv",
      [
        "run", "python", "-c",
        "import json; from autocontext.production_traces.taxonomy import ANTHROPIC_ERROR_REASONS; print(json.dumps(dict(ANTHROPIC_ERROR_REASONS), sort_keys=True))",
      ],
      { cwd: PYTHON_CWD, encoding: "utf-8" },
    );
    expect(result.status).toBe(0);
    const pyTable = JSON.parse(result.stdout.trim());
    const tsTable = Object.fromEntries(
      Object.entries(ANTHROPIC_ERROR_REASONS).sort(),
    );
    expect(pyTable).toEqual(tsTable);
  });
});
