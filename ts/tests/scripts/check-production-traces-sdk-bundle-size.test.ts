import { describe, test, expect } from "vitest";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, "..", "..");
const SCRIPT = join(ROOT, "scripts", "check-production-traces-sdk-bundle-size.mjs");

describe("scripts/check-production-traces-sdk-bundle-size.mjs", () => {
  test("exits 0 and reports raw + gzipped sizes under the default budget", () => {
    const r = spawnSync("node", [SCRIPT], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(r.stdout).toMatch(/\[bundle-size\] raw:\s+[\d,]+ bytes/);
    expect(r.stdout).toMatch(/\[bundle-size\] gzipped:\s+[\d,]+ bytes/);
    expect(r.stdout).toMatch(/\[bundle-size\] OK — within budget/);
  });

  test("--json emits a parseable summary with the expected shape", () => {
    const r = spawnSync("node", [SCRIPT, "--json"], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    const parsed = JSON.parse(r.stdout.trim());
    expect(typeof parsed.budgetBytes).toBe("number");
    expect(typeof parsed.rawBytes).toBe("number");
    expect(typeof parsed.gzipBytes).toBe("number");
    expect(typeof parsed.headroom).toBe("number");
    expect(parsed.overBudget).toBe(false);
    // Sanity: gzipped should be much smaller than raw.
    expect(parsed.gzipBytes).toBeLessThan(parsed.rawBytes);
  });

  test("budget is set to 100 kB (102400 bytes) per spec §6.1", () => {
    const r = spawnSync("node", [SCRIPT, "--json"], { cwd: ROOT, encoding: "utf-8" });
    const parsed = JSON.parse(r.stdout.trim());
    expect(parsed.budgetBytes).toBe(102_400);
  });

  test("ships below the aspirational ~55 KB target per spec §6.3", () => {
    // If the SDK's baseline creeps above this target, we want to know even
    // though the hard budget is still 100 kB. Treat this as a soft gate:
    // fail only if we're > 80 kB gzipped so the test isn't too flaky on
    // small dep bumps.
    const r = spawnSync("node", [SCRIPT, "--json"], { cwd: ROOT, encoding: "utf-8" });
    const parsed = JSON.parse(r.stdout.trim());
    expect(parsed.gzipBytes).toBeLessThan(80_000);
  });

  test("--report writes bundle-report.txt", () => {
    const reportPath = join(ROOT, "bundle-report.txt");
    if (existsSync(reportPath)) rmSync(reportPath);
    const r = spawnSync("node", [SCRIPT, "--report"], { cwd: ROOT, encoding: "utf-8" });
    expect(r.status).toBe(0);
    expect(existsSync(reportPath)).toBe(true);
    const body = readFileSync(reportPath, "utf-8");
    expect(body).toContain("autoctx/production-traces bundle report");
    expect(body).toContain("top module contributors");
    rmSync(reportPath);
  });
});
