import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const SCRIPT = join(ROOT, "scripts", "check-side-effects.mjs");

describe("scripts/check-side-effects.mjs", () => {
  test("current sideEffects glob is consistent with detected registrar calls (exits 0)", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toMatch(/\[check-side-effects\] OK/);
  });

  test("reports the actuator count in the OK message", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.stdout).toMatch(/source files audited/);
    // Five actuator index.ts files register at top level.
    expect(r.stdout).toMatch(/[1-9]\d* with top-level imported-registrar calls/);
  });
});
