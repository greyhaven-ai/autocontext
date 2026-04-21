import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const SCRIPT = join(ROOT, "scripts", "check-license-compatibility.mjs");

describe("scripts/check-license-compatibility.mjs", () => {
  test("current SDK transitive closure is fully allowlisted (exits 0)", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toContain("[check-license-compatibility] OK");
  });

  test("reports each package and its license", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.stdout).toMatch(/ajv@\d+\.\d+\.\d+ :: MIT/);
    expect(r.stdout).toMatch(/ulid@\d+\.\d+\.\d+ :: MIT/);
  });
});
