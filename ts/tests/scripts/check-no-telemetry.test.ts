import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const SCRIPT = join(ROOT, "scripts", "check-no-telemetry.mjs");

describe("scripts/check-no-telemetry.mjs", () => {
  test("SDK source + transitive deps have no telemetry patterns (exits 0)", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("[check-no-telemetry] OK");
  });

  test("scans at least the SDK source files", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    const match = r.stdout.match(/scanned (\d+) files/);
    expect(match).toBeTruthy();
    const count = Number(match![1]);
    expect(count).toBeGreaterThan(0);
  });
});
