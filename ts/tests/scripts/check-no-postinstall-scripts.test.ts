import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const SCRIPT = join(ROOT, "scripts", "check-no-postinstall-scripts.mjs");

describe("scripts/check-no-postinstall-scripts.mjs", () => {
  test("autoctx + transitive deps declare no strict install hooks (exits 0)", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("[check-no-postinstall-scripts] OK");
  });

  test("message references the correct hook names", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    // OK message states no install-time hooks.
    expect(r.stdout).toMatch(/no install-time hooks/);
  });
});
